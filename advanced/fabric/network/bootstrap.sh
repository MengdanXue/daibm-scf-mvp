#!/usr/bin/env bash
set -euo pipefail

readonly OUTPUT_ROOT="/network/output/fabric"
readonly CRYPTO_ROOT="${OUTPUT_ROOT}/crypto-config"
export FABRIC_CFG_PATH=/network

mkdir -p "${OUTPUT_ROOT}"

if [[ ! -f "${CRYPTO_ROOT}/ordererOrganizations/example.com/orderers/orderer.example.com/tls/server.crt" ]] ||
   [[ ! -f "${CRYPTO_ROOT}/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/server.crt" ]]; then
  rm -rf "${CRYPTO_ROOT}"
  cryptogen generate --config=/network/crypto-config.yaml --output="${CRYPTO_ROOT}"
fi

if [[ ! -f "${OUTPUT_ROOT}/genesis.block" ]]; then
  configtxgen -profile OneOrgOrdererGenesis -channelID system-channel -outputBlock "${OUTPUT_ROOT}/genesis.block"
fi

if [[ ! -f "${OUTPUT_ROOT}/scfchannel.tx" ]]; then
  configtxgen -profile OneOrgChannel -channelID scfchannel -outputCreateChannelTx "${OUTPUT_ROOT}/scfchannel.tx"
fi

mkdir -p "${OUTPUT_ROOT}/state/orderer" "${OUTPUT_ROOT}/state/peer" "${OUTPUT_ROOT}/packages"
echo "Fabric demo identities and channel artifacts are ready under output/fabric."
