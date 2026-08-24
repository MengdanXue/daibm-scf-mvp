#!/usr/bin/env bash
set -euo pipefail

readonly NETWORK_ROOT="${FABRIC_NETWORK_ROOT:-/network}"
readonly OUTPUT_ROOT="${FABRIC_OUTPUT_ROOT:-/network/output/fabric}"
readonly CRYPTO_ROOT="${OUTPUT_ROOT}/crypto-config"
readonly GENERATION_MANIFEST="${OUTPUT_ROOT}/generation.sha256"
readonly ORDERER_TLS_CERT="${CRYPTO_ROOT}/ordererOrganizations/example.com/orderers/orderer.example.com/tls/server.crt"
readonly PEER_TLS_CERT="${CRYPTO_ROOT}/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/server.crt"
readonly GENESIS_BLOCK="${OUTPUT_ROOT}/genesis.block"
readonly CHANNEL_TX="${OUTPUT_ROOT}/scfchannel.tx"
export FABRIC_CFG_PATH="${NETWORK_ROOT}"

mkdir -p "${OUTPUT_ROOT}"

required_artifacts=("${ORDERER_TLS_CERT}" "${PEER_TLS_CERT}" "${GENESIS_BLOCK}" "${CHANNEL_TX}")
present_count=0
for artifact in "${required_artifacts[@]}"; do
  [[ -f "${artifact}" ]] && present_count=$((present_count + 1))
done

has_persisted_state=false
if [[ -d "${OUTPUT_ROOT}/state" ]] && find "${OUTPUT_ROOT}/state" -mindepth 1 \( -type f -o -type l \) -print -quit | grep -q .; then
  has_persisted_state=true
fi

fail_inconsistent_generation() {
  echo "ERROR: incomplete or inconsistent Fabric generation under ${OUTPUT_ROOT}." >&2
  echo "Stop the Fabric demo, archive or remove that entire directory as one unit, then bootstrap again; never regenerate identities over retained blocks or ledger state." >&2
  exit 1
}

if [[ ${present_count} -eq 0 ]]; then
  if [[ -e "${CRYPTO_ROOT}" ]] || [[ -e "${GENERATION_MANIFEST}" ]] || [[ "${has_persisted_state}" == true ]]; then
    fail_inconsistent_generation
  fi

  cryptogen generate --config="${NETWORK_ROOT}/crypto-config.yaml" --output="${CRYPTO_ROOT}"
  configtxgen -profile OneOrgOrdererGenesis -channelID system-channel -outputBlock "${GENESIS_BLOCK}"
  configtxgen -profile OneOrgChannel -channelID scfchannel -outputCreateChannelTx "${CHANNEL_TX}"

  (
    cd "${OUTPUT_ROOT}"
    sha256sum \
      "crypto-config/ordererOrganizations/example.com/orderers/orderer.example.com/tls/server.crt" \
      "crypto-config/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/server.crt" \
      "genesis.block" \
      "scfchannel.tx" >"generation.sha256"
  )
elif [[ ${present_count} -ne ${#required_artifacts[@]} ]] || [[ ! -f "${GENERATION_MANIFEST}" ]]; then
  fail_inconsistent_generation
elif ! (cd "${OUTPUT_ROOT}" && sha256sum --check --status "generation.sha256"); then
  fail_inconsistent_generation
fi

mkdir -p "${OUTPUT_ROOT}/state/orderer" "${OUTPUT_ROOT}/state/peer" "${OUTPUT_ROOT}/packages"
echo "Fabric demo identities and channel artifacts are ready under output/fabric."
