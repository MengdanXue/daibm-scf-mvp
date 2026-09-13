#!/usr/bin/env bash
set -euo pipefail

existing_channel_only=false
expected_sequence=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --existing-channel-only) existing_channel_only=true; shift ;;
    --expected-sequence)
      [[ $# -ge 2 && "$2" =~ ^[1-9][0-9]*$ ]] || { echo "--expected-sequence requires a positive integer" >&2; exit 2; }
      expected_sequence="$2"; shift 2 ;;
    *) echo "unknown deployment argument: $1" >&2; exit 2 ;;
  esac
done
if ${existing_channel_only} && [[ -z "${expected_sequence}" ]]; then
  echo "--existing-channel-only requires --expected-sequence from the verified existing definition" >&2
  exit 2
fi
if ! ${existing_channel_only} && [[ -n "${expected_sequence}" ]]; then
  echo "--expected-sequence requires --existing-channel-only" >&2
  exit 2
fi

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

fail() { echo "$*" >&2; exit 1; }

validate_definition() {
  jq -es 'length == 1 and (.[0] | type == "object" and
    (.sequence | type == "number" and . >= 1 and floor == .) and
    (.version | type == "string" and length > 0) and
    (.endorsement_plugin | type == "string" and length > 0) and
    (.validation_plugin | type == "string" and length > 0) and
    (.validation_parameter | type == "string" and length > 0))' >/dev/null
}

matches_definition() {
  jq -e --arg version "${CHAINCODE_VERSION}" --arg policy "${ENDORSEMENT_POLICY_PARAMETER}" \
    --argjson sequence "$1" \
    '.sequence == $sequence and .version == $version and
     .endorsement_plugin == "escc" and .validation_plugin == "vscc" and
     .validation_parameter == $policy and (.init_required // false) == false and
     ((.collections // {}) == {} or .collections == {"config":[]})' >/dev/null
}

has_package() {
  awk -F ', Label: ' -v wanted="Package ID: ${PACKAGE_ID}" '$1 == wanted { found=1 } END { exit !found }'
}

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
  if ${existing_channel_only}; then
    fail "existing-channel-only: channel getinfo failed; refusing fetch, create or join"
  fi
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

committed_json=""
approved_json=""
current_sequence=0
installed_output=""
if committed_json="$(peer lifecycle chaincode querycommitted \
    --channelID scfchannel --name "${CHAINCODE_NAME}" --output json)"; then
  validate_definition <<<"${committed_json}" || fail "invalid committed definition; refusing deployment"
  current_sequence="$(jq -er '.sequence' <<<"${committed_json}")"
  if [[ -n "${expected_sequence}" && "${current_sequence}" != "${expected_sequence}" ]]; then
    fail "existing-channel-only: expected sequence ${expected_sequence}, found ${current_sequence}; refusing deployment"
  fi
  if approved_json="$(peer lifecycle chaincode queryapproved \
      --channelID scfchannel --name "${CHAINCODE_NAME}" \
      --sequence "${current_sequence}" --output json)"; then
    validate_definition <<<"${approved_json}" || fail "invalid approved definition; refusing deployment"
    jq -e --argjson sequence "${current_sequence}" \
      '.sequence == $sequence and (.source.Type.LocalPackage.package_id | type == "string" and length > 0)' \
      <<<"${approved_json}" >/dev/null || fail "invalid approved sequence or package source; refusing deployment"
  elif ${existing_channel_only}; then
    fail "existing-channel-only: queryapproved failed; refusing deployment"
  fi
elif ${existing_channel_only}; then
  fail "existing-channel-only: querycommitted failed; refusing deployment"
fi

if ${existing_channel_only}; then
  installed_output="$(peer lifecycle chaincode queryinstalled)" || fail "existing-channel-only: queryinstalled failed; refusing deployment"
  # Keep historical package artifacts intact, including when a later read fails.
  package_temp_dir="$(mktemp -d)"
  trap 'rm -f -- "${package_temp_dir}/audit-anchor.tar.gz"; rmdir -- "${package_temp_dir}"' EXIT
  package_file="${package_temp_dir}/audit-anchor.tar.gz"
else
  package_file="${PACKAGE_FILE}"
  rm -f -- "${package_file}"
fi
peer lifecycle chaincode package "${package_file}" \
  --path "${CHAINCODE_PATH}" \
  --lang node \
  --label "${LABEL}"

PACKAGE_ID="$(peer lifecycle chaincode calculatepackageid "${package_file}")"
if [[ -z "${PACKAGE_ID}" ]]; then echo "calculated package ID was empty" >&2; exit 1; fi

if ! ${existing_channel_only}; then
  installed_output="$(peer lifecycle chaincode queryinstalled)" || fail "queryinstalled failed; refusing installation"
fi
if ! has_package <<<"${installed_output}"; then
  peer lifecycle chaincode install "${package_file}"
  installed_output="$(peer lifecycle chaincode queryinstalled)" || fail "queryinstalled failed after installation"
fi

if ! has_package <<<"${installed_output}"; then
  echo "exact calculated package ID was not installed" >&2
  exit 1
fi

if [[ "${current_sequence}" -gt 0 && -n "${approved_json}" ]]; then
  if matches_definition "${current_sequence}" <<<"${committed_json}" && \
     matches_definition "${current_sequence}" <<<"${approved_json}" && \
     jq -e '.approvals.Org1MSP == true' <<<"${committed_json}" >/dev/null && \
     [[ "$(jq -r '.source.Type.LocalPackage.package_id' <<<"${approved_json}")" == "${PACKAGE_ID}" ]]; then
    echo "Chaincode ${CHAINCODE_NAME} exact package and definition are already committed at sequence ${current_sequence}."
    exit 0
  fi
fi

target_sequence=$((current_sequence + 1))

if ${existing_channel_only}; then
  # Packaging/installing can take time. Re-read the starting definition before
  # submitting an approval, and require the complete observations to be stable.
  latest_committed="$(peer lifecycle chaincode querycommitted \
    --channelID scfchannel --name "${CHAINCODE_NAME}" --output json)" || fail "existing-channel-only: committed recheck failed; refusing approval"
  latest_approved="$(peer lifecycle chaincode queryapproved \
    --channelID scfchannel --name "${CHAINCODE_NAME}" --sequence "${current_sequence}" --output json)" || fail "existing-channel-only: approved recheck failed; refusing approval"
  validate_definition <<<"${latest_committed}" || fail "invalid committed recheck; refusing approval"
  validate_definition <<<"${latest_approved}" || fail "invalid approved recheck; refusing approval"
  [[ "$(jq -Sc . <<<"${latest_committed}")" == "$(jq -Sc . <<<"${committed_json}")" && \
     "$(jq -Sc . <<<"${latest_approved}")" == "$(jq -Sc . <<<"${approved_json}")" ]] || \
    fail "existing-channel-only: existing definition changed during deployment; refusing approval"
fi

peer lifecycle chaincode approveformyorg \
  -o orderer.example.com:7050 \
  --channelID scfchannel \
  --name "${CHAINCODE_NAME}" \
  --version "${CHAINCODE_VERSION}" \
  --package-id "${PACKAGE_ID}" \
  --sequence "${target_sequence}" \
  --signature-policy "${ENDORSEMENT_POLICY}" \
  --tls --cafile "${ORDERER_CA}"

readiness_json="$(peer lifecycle chaincode checkcommitreadiness \
  --channelID scfchannel \
  --name "${CHAINCODE_NAME}" \
  --version "${CHAINCODE_VERSION}" \
  --sequence "${target_sequence}" \
  --signature-policy "${ENDORSEMENT_POLICY}" \
  --output json)"
jq -e '.approvals.Org1MSP == true' <<<"${readiness_json}" >/dev/null || fail "Org1MSP is not ready; refusing commit"

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
matches_definition "${target_sequence}" <<<"${committed_json}" || fail "committed definition does not match requested definition"
approved_json="$(peer lifecycle chaincode queryapproved \
  --channelID scfchannel --name "${CHAINCODE_NAME}" --sequence "${target_sequence}" --output json)"
matches_definition "${target_sequence}" <<<"${approved_json}" || fail "approved definition does not match requested definition"
jq -e --arg package_id "${PACKAGE_ID}" '.source.Type.LocalPackage.package_id == $package_id' \
  <<<"${approved_json}" >/dev/null || fail "approved package does not match requested package"
echo "Chaincode ${CHAINCODE_NAME} committed with exact package ${PACKAGE_ID} at sequence ${target_sequence}."
