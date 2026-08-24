#!/usr/bin/env bash
set -euo pipefail

readonly OUTPUT_ROOT="${FABRIC_OUTPUT_ROOT:-/network/output/fabric}"
readonly ORDERER_CA="${FABRIC_ORDERER_CA:-${OUTPUT_ROOT}/crypto-config/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem}"
readonly CHANNEL_BLOCK="${OUTPUT_ROOT}/scfchannel.block"
readonly PACKAGE_FILE="${OUTPUT_ROOT}/packages/audit-anchor.tar.gz"
readonly LABEL="audit-anchor_1"
readonly CHAINCODE_NAME="audit-anchor"
readonly CHAINCODE_VERSION="1.0"
readonly CHAINCODE_PATH="${FABRIC_CHAINCODE_PATH:-/chaincode}"
readonly ENDORSEMENT_POLICY="OR('Org1MSP.peer')"
# Fabric 2.5.16 canonical protobuf for the fixed signature policy above.
readonly ENDORSEMENT_POLICY_PARAMETER="ChkSCBIGCAESAggAGg0SCwoHT3JnMU1TUBAD"

export CORE_PEER_LOCALMSPID=Org1MSP
export CORE_PEER_ADDRESS=peer0.org1.example.com:7051
export CORE_PEER_MSPCONFIGPATH="${FABRIC_PEER_MSP:-${OUTPUT_ROOT}/crypto-config/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp}"
export CORE_PEER_TLS_ENABLED=true
export CORE_PEER_TLS_ROOTCERT_FILE="${FABRIC_PEER_TLS_ROOT:-${OUTPUT_ROOT}/crypto-config/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt}"

for attempt in $(seq 1 30); do
  if peer node status >/dev/null 2>&1; then break; fi
  if [[ "${attempt}" == "30" ]]; then echo "peer did not become ready" >&2; exit 1; fi
  sleep 2
done

if ! peer channel getinfo -c scfchannel >/dev/null 2>&1; then
  rm -f -- "${CHANNEL_BLOCK}"
  if ! peer channel fetch 0 "${CHANNEL_BLOCK}" \
      -o orderer.example.com:7050 -c scfchannel \
      --tls --cafile "${ORDERER_CA}"; then
    peer channel create \
      -o orderer.example.com:7050 \
      -c scfchannel \
      -f "${OUTPUT_ROOT}/scfchannel.tx" \
      --outputBlock "${CHANNEL_BLOCK}" \
      --tls --cafile "${ORDERER_CA}"
  fi
  peer channel join -b "${CHANNEL_BLOCK}"
fi

rm -f -- "${PACKAGE_FILE}"
peer lifecycle chaincode package "${PACKAGE_FILE}" \
  --path "${CHAINCODE_PATH}" \
  --lang node \
  --label "${LABEL}"

PACKAGE_ID="$(peer lifecycle chaincode calculatepackageid "${PACKAGE_FILE}")"
if [[ -z "${PACKAGE_ID}" ]]; then echo "calculated package ID was empty" >&2; exit 1; fi

if ! peer lifecycle chaincode queryinstalled | awk -F ', Label: ' -v wanted="Package ID: ${PACKAGE_ID}" '$1 == wanted { found=1 } END { exit !found }'; then
  peer lifecycle chaincode install "${PACKAGE_FILE}"
fi

if ! peer lifecycle chaincode queryinstalled | awk -F ', Label: ' -v wanted="Package ID: ${PACKAGE_ID}" '$1 == wanted { found=1 } END { exit !found }'; then
  echo "exact calculated package ID was not installed" >&2
  exit 1
fi

committed_json=""
current_sequence=0
if committed_json="$(peer lifecycle chaincode querycommitted \
    --channelID scfchannel --name "${CHAINCODE_NAME}" --output json 2>/dev/null)"; then
  current_sequence="$(jq -er '.sequence | select(type == "number" and . >= 1 and floor == .)' <<<"${committed_json}")"
  current_version="$(jq -er '.version | select(type == "string")' <<<"${committed_json}")"

  approved_json=""
  if approved_json="$(peer lifecycle chaincode queryapproved \
      --channelID scfchannel --name "${CHAINCODE_NAME}" \
      --sequence "${current_sequence}" --output json 2>/dev/null)" && \
     [[ "${current_version}" == "${CHAINCODE_VERSION}" ]] && \
     [[ "$(jq -r '.source.Type.LocalPackage.package_id // empty' <<<"${approved_json}")" == "${PACKAGE_ID}" ]] && \
     [[ "$(jq -r '.version // empty' <<<"${approved_json}")" == "${CHAINCODE_VERSION}" ]] && \
     [[ "$(jq -r '.validation_parameter // empty' <<<"${committed_json}")" == "${ENDORSEMENT_POLICY_PARAMETER}" ]] && \
     [[ "$(jq -r '.validation_parameter // empty' <<<"${approved_json}")" == "${ENDORSEMENT_POLICY_PARAMETER}" ]] && \
     [[ "$(jq -r '.endorsement_plugin // empty' <<<"${committed_json}")" == "escc" ]] && \
     [[ "$(jq -r '.validation_plugin // empty' <<<"${committed_json}")" == "vscc" ]]; then
    echo "Chaincode ${CHAINCODE_NAME} exact package and definition are already committed at sequence ${current_sequence}."
    exit 0
  fi
fi

target_sequence=$((current_sequence + 1))

peer lifecycle chaincode approveformyorg \
  -o orderer.example.com:7050 \
  --channelID scfchannel \
  --name "${CHAINCODE_NAME}" \
  --version "${CHAINCODE_VERSION}" \
  --package-id "${PACKAGE_ID}" \
  --sequence "${target_sequence}" \
  --signature-policy "${ENDORSEMENT_POLICY}" \
  --tls --cafile "${ORDERER_CA}"

peer lifecycle chaincode checkcommitreadiness \
  --channelID scfchannel \
  --name "${CHAINCODE_NAME}" \
  --version "${CHAINCODE_VERSION}" \
  --sequence "${target_sequence}" \
  --signature-policy "${ENDORSEMENT_POLICY}" \
  --output json

peer lifecycle chaincode commit \
  -o orderer.example.com:7050 \
  --channelID scfchannel \
  --name "${CHAINCODE_NAME}" \
  --version "${CHAINCODE_VERSION}" \
  --sequence "${target_sequence}" \
  --signature-policy "${ENDORSEMENT_POLICY}" \
  --peerAddresses peer0.org1.example.com:7051 \
  --tlsRootCertFiles "${CORE_PEER_TLS_ROOTCERT_FILE}" \
  --tls --cafile "${ORDERER_CA}"

committed_json="$(peer lifecycle chaincode querycommitted \
  --channelID scfchannel --name "${CHAINCODE_NAME}" --output json)"
jq -e --arg version "${CHAINCODE_VERSION}" --argjson sequence "${target_sequence}" \
  '.version == $version and .sequence == $sequence and .approvals.Org1MSP == true' \
  <<<"${committed_json}" >/dev/null
echo "Chaincode ${CHAINCODE_NAME} committed with exact package ${PACKAGE_ID} at sequence ${target_sequence}."
