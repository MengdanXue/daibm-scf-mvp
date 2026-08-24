#!/usr/bin/env bash
set -euo pipefail

readonly OUTPUT_ROOT="/network/output/fabric"
readonly ORDERER_CA="${OUTPUT_ROOT}/crypto-config/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem"
readonly CHANNEL_BLOCK="${OUTPUT_ROOT}/scfchannel.block"
readonly PACKAGE_FILE="${OUTPUT_ROOT}/packages/audit-anchor.tar.gz"
readonly LABEL="audit-anchor_1"
readonly CHAINCODE_NAME="audit-anchor"
readonly ENDORSEMENT_POLICY="OR('Org1MSP.peer')"

export CORE_PEER_LOCALMSPID=Org1MSP
export CORE_PEER_ADDRESS=peer0.org1.example.com:7051
export CORE_PEER_MSPCONFIGPATH="${OUTPUT_ROOT}/crypto-config/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp"
export CORE_PEER_TLS_ENABLED=true
export CORE_PEER_TLS_ROOTCERT_FILE="${OUTPUT_ROOT}/crypto-config/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"

for attempt in $(seq 1 30); do
  if peer node status >/dev/null 2>&1; then break; fi
  if [[ "${attempt}" == "30" ]]; then echo "peer did not become ready" >&2; exit 1; fi
  sleep 2
done

if ! peer channel getinfo -c scfchannel >/dev/null 2>&1; then
  peer channel create \
    -o orderer.example.com:7050 \
    -c scfchannel \
    -f "${OUTPUT_ROOT}/scfchannel.tx" \
    --outputBlock "${CHANNEL_BLOCK}" \
    --tls --cafile "${ORDERER_CA}"
  peer channel join -b "${CHANNEL_BLOCK}"
fi

if peer lifecycle chaincode querycommitted --channelID scfchannel --name "${CHAINCODE_NAME}" 2>/dev/null | grep -q 'Sequence: 1'; then
  echo "Chaincode ${CHAINCODE_NAME} sequence 1 is already committed."
  exit 0
fi

rm -f -- "${PACKAGE_FILE}"
peer lifecycle chaincode package "${PACKAGE_FILE}" \
  --path /chaincode \
  --lang node \
  --label "${LABEL}"

if ! peer lifecycle chaincode queryinstalled | grep -q "Label: ${LABEL}"; then
  peer lifecycle chaincode install "${PACKAGE_FILE}"
fi

PACKAGE_ID="$(peer lifecycle chaincode queryinstalled | sed -n "s/^Package ID: \([^,]*\), Label: ${LABEL}$/\1/p" | head -n 1)"
if [[ -z "${PACKAGE_ID}" ]]; then echo "installed package ID was not found" >&2; exit 1; fi

peer lifecycle chaincode approveformyorg \
  -o orderer.example.com:7050 \
  --channelID scfchannel \
  --name "${CHAINCODE_NAME}" \
  --version 1.0 \
  --package-id "${PACKAGE_ID}" \
  --sequence 1 \
  --signature-policy "${ENDORSEMENT_POLICY}" \
  --tls --cafile "${ORDERER_CA}"

peer lifecycle chaincode checkcommitreadiness \
  --channelID scfchannel \
  --name "${CHAINCODE_NAME}" \
  --version 1.0 \
  --sequence 1 \
  --signature-policy "${ENDORSEMENT_POLICY}" \
  --output json

peer lifecycle chaincode commit \
  -o orderer.example.com:7050 \
  --channelID scfchannel \
  --name "${CHAINCODE_NAME}" \
  --version 1.0 \
  --sequence 1 \
  --signature-policy "${ENDORSEMENT_POLICY}" \
  --peerAddresses peer0.org1.example.com:7051 \
  --tlsRootCertFiles "${CORE_PEER_TLS_ROOTCERT_FILE}" \
  --tls --cafile "${ORDERER_CA}"

peer lifecycle chaincode querycommitted --channelID scfchannel --name "${CHAINCODE_NAME}"
