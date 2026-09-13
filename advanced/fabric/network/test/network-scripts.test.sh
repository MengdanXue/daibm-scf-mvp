#!/usr/bin/env bash
set -euo pipefail

readonly SOURCE_ROOT="/src"
readonly TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

fail() {
  echo "not ok - $*" >&2
  exit 1
}

assert_contains() {
  local file="$1"
  local expected="$2"
  grep -Fq -- "${expected}" "${file}" || fail "${file} did not contain: ${expected}"
}

assert_not_contains() {
  local file="$1"
  local unexpected="$2"
  if grep -Fq -- "${unexpected}" "${file}"; then
    fail "${file} unexpectedly contained: ${unexpected}"
  fi
}

make_bootstrap_tools() {
  local bin_root="$1"
  mkdir -p "${bin_root}"
  cat >"${bin_root}/cryptogen" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--output" ]]; then
    output="$2"; shift 2
  elif [[ "$1" == --output=* ]]; then
    output="${1#--output=}"; shift
  else
    shift
  fi
done
mkdir -p \
  "${output}/ordererOrganizations/example.com/orderers/orderer.example.com/tls" \
  "${output}/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls"
printf 'orderer-cert\n' >"${output}/ordererOrganizations/example.com/orderers/orderer.example.com/tls/server.crt"
printf 'peer-cert\n' >"${output}/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/server.crt"
EOF
  cat >"${bin_root}/configtxgen" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
while [[ $# -gt 0 ]]; do
  case "$1" in
    -outputBlock|-outputCreateChannelTx) output="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf 'channel-artifact\n' >"${output}"
EOF
  chmod +x "${bin_root}/cryptogen" "${bin_root}/configtxgen"
}

test_bootstrap_fails_closed_on_mixed_generation() {
  local case_root="${TEST_ROOT}/bootstrap-partial"
  local output_root="${case_root}/output"
  local bin_root="${case_root}/bin"
  mkdir -p "${output_root}"
  printf 'old-genesis\n' >"${output_root}/genesis.block"
  make_bootstrap_tools "${bin_root}"

  set +e
  PATH="${bin_root}:${PATH}" \
    FABRIC_OUTPUT_ROOT="${output_root}" \
    FABRIC_NETWORK_ROOT="${SOURCE_ROOT}" \
    bash "${SOURCE_ROOT}/bootstrap.sh" >"${case_root}/stdout" 2>"${case_root}/stderr"
  local status=$?
  set -e

  [[ ${status} -ne 0 ]] || fail "partial generation was accepted"
  assert_contains "${case_root}/stderr" "incomplete or inconsistent Fabric generation"
  [[ "$(cat "${output_root}/genesis.block")" == "old-genesis" ]] || fail "existing genesis was changed"
  [[ ! -e "${output_root}/crypto-config" ]] || fail "new crypto was mixed with old genesis"
  echo "ok - bootstrap fails closed on mixed generations"
}

test_bootstrap_hash_locks_one_generation() {
  local case_root="${TEST_ROOT}/bootstrap-generation"
  local output_root="${case_root}/output"
  local bin_root="${case_root}/bin"
  # Compose creates these empty bind-mount targets before bootstrap runs.
  mkdir -p "${output_root}/state/orderer" "${output_root}/state/peer"
  make_bootstrap_tools "${bin_root}"

  PATH="${bin_root}:${PATH}" FABRIC_OUTPUT_ROOT="${output_root}" FABRIC_NETWORK_ROOT="${SOURCE_ROOT}" \
    bash "${SOURCE_ROOT}/bootstrap.sh" >/dev/null
  [[ -s "${output_root}/generation.sha256" ]] || fail "generation hash manifest was not created"

  PATH="${bin_root}:${PATH}" FABRIC_OUTPUT_ROOT="${output_root}" FABRIC_NETWORK_ROOT="${SOURCE_ROOT}" \
    bash "${SOURCE_ROOT}/bootstrap.sh" >/dev/null

  printf 'tampered-cert\n' >"${output_root}/peerOrganizations.invalid" # unrelated files do not invalidate the generation
  printf 'tampered-cert\n' >"${output_root}/crypto-config/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/server.crt"
  set +e
  PATH="${bin_root}:${PATH}" FABRIC_OUTPUT_ROOT="${output_root}" FABRIC_NETWORK_ROOT="${SOURCE_ROOT}" \
    bash "${SOURCE_ROOT}/bootstrap.sh" >/dev/null 2>"${case_root}/stderr"
  local status=$?
  set -e
  [[ ${status} -ne 0 ]] || fail "tampered generation was accepted"
  assert_contains "${case_root}/stderr" "incomplete or inconsistent Fabric generation"
  echo "ok - bootstrap hash-locks one coherent generation"
}

make_peer_mock() {
  local bin_root="$1"
  mkdir -p "${bin_root}"
  cat >"${bin_root}/peer" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${MOCK_LOG}"
if [[ -n "${MOCK_FAIL:-}" && "$*" == "${MOCK_FAIL}"* ]]; then exit 73; fi
if [[ -n "${MOCK_FAIL_RECHECK:-}" && "$*" == "${MOCK_FAIL_RECHECK}"* ]]; then
  if [[ -f "${MOCK_STATE}/recheck-seen" ]]; then exit 74; fi
  touch "${MOCK_STATE}/recheck-seen"
fi

value_after() {
  local wanted="$1"; shift
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "${wanted}" ]]; then printf '%s' "$2"; return; fi
    shift
  done
  return 1
}

case "${1:-} ${2:-} ${3:-}" in
  "node status ") exit 0 ;;
  "channel getinfo -c") [[ -f "${MOCK_STATE}/joined" ]] ;;
  "channel fetch 0")
    [[ "${MOCK_CHANNEL_EXISTS:-0}" == "1" ]] || exit 1
    printf 'channel-block\n' >"$4"
    ;;
  "channel create -o")
    output="$(value_after --outputBlock "$@")"
    printf 'channel-block\n' >"${output}"
    ;;
  "channel join -b") touch "${MOCK_STATE}/joined" ;;
  "lifecycle chaincode package") printf 'package\n' >"$4" ;;
  "lifecycle chaincode calculatepackageid") printf '%s\n' "${MOCK_PACKAGE_ID}" ;;
  "lifecycle chaincode queryinstalled")
    if [[ -f "${MOCK_STATE}/installed" ]]; then cat "${MOCK_STATE}/installed"; fi
    ;;
  "lifecycle chaincode install")
    printf 'Package ID: %s, Label: audit-anchor_1\n' "${MOCK_PACKAGE_ID}" >>"${MOCK_STATE}/installed"
    ;;
  "lifecycle chaincode querycommitted")
    [[ -f "${MOCK_STATE}/committed.json" ]] || exit 1
    cat "${MOCK_STATE}/committed.json"
    ;;
  "lifecycle chaincode queryapproved")
    [[ -f "${MOCK_STATE}/approved.json" ]] || exit 1
    cat "${MOCK_STATE}/approved.json"
    ;;
  "lifecycle chaincode approveformyorg")
    sequence="$(value_after --sequence "$@")"
    package_id="$(value_after --package-id "$@")"
    printf '{"sequence":%s,"version":"1.0","endorsement_plugin":"escc","validation_plugin":"vscc","validation_parameter":"ChkSCBIGCAESAggAGg0SCwoHT3JnMU1TUBAD","source":{"Type":{"LocalPackage":{"package_id":"%s"}}}}\n' "${sequence}" "${package_id}" >"${MOCK_STATE}/approved.json"
    ;;
  "lifecycle chaincode checkcommitreadiness") printf '{"approvals":{"Org1MSP":%s}}\n' "${MOCK_READY:-true}" ;;
  "lifecycle chaincode commit")
    sequence="$(value_after --sequence "$@")"
    printf '{"sequence":%s,"version":"1.0","endorsement_plugin":"escc","validation_plugin":"vscc","validation_parameter":"ChkSCBIGCAESAggAGg0SCwoHT3JnMU1TUBAD","approvals":{"Org1MSP":true}}\n' "${sequence}" >"${MOCK_STATE}/committed.json"
    ;;
  *) echo "unexpected peer invocation: $*" >&2; exit 90 ;;
esac
EOF
  chmod +x "${bin_root}/peer"
}

run_deploy_case() {
  local case_name="$1"
  local channel_exists="$2"
  local package_id="$3"
  local case_root="${TEST_ROOT}/${case_name}"
  mkdir -p "${case_root}/bin" "${case_root}/state" "${case_root}/output/packages" "${case_root}/chaincode"
  printf 'ca\n' >"${case_root}/ca.pem"
  printf 'tls\n' >"${case_root}/tls.pem"
  mkdir -p "${case_root}/msp"
  : >"${case_root}/calls.log"
  make_peer_mock "${case_root}/bin"
  PATH="${case_root}/bin:${PATH}" \
    MOCK_LOG="${case_root}/calls.log" \
    MOCK_STATE="${case_root}/state" \
    MOCK_CHANNEL_EXISTS="${channel_exists}" \
    MOCK_PACKAGE_ID="${package_id}" \
    FABRIC_OUTPUT_ROOT="${case_root}/output" \
    FABRIC_CHAINCODE_PATH="${case_root}/chaincode" \
    FABRIC_ORDERER_CA="${case_root}/ca.pem" \
    FABRIC_PEER_MSP="${case_root}/msp" \
    FABRIC_PEER_TLS_ROOT="${case_root}/tls.pem" \
    bash "${SOURCE_ROOT}/deploy-chaincode.sh" >"${case_root}/stdout" 2>"${case_root}/stderr"
  printf '%s' "${case_root}"
}

test_existing_channel_is_fetched_and_joined() {
  local case_root
  case_root="$(run_deploy_case existing-channel 1 'audit-anchor_1:newhash')"
  assert_contains "${case_root}/calls.log" "channel fetch 0"
  assert_contains "${case_root}/calls.log" "channel join -b"
  assert_not_contains "${case_root}/calls.log" "channel create"
  echo "ok - an existing channel is fetched and joined"
}

test_exact_package_identity_not_label_is_used() {
  local case_root="${TEST_ROOT}/stale-label"
  mkdir -p "${case_root}/state"
  printf 'Package ID: audit-anchor_1:oldhash, Label: audit-anchor_1\n' >"${case_root}/state/installed"
  touch "${case_root}/state/joined"
  local result_root
  result_root="$(run_deploy_case stale-label 1 'audit-anchor_1:newhash')"
  # Recreate the partial state with only a stale same-label package.
  touch "${result_root}/state/joined"
  rm -f "${result_root}/state/committed.json" "${result_root}/state/approved.json"
  printf 'Package ID: audit-anchor_1:oldhash, Label: audit-anchor_1\n' >"${result_root}/state/installed"
  : >"${result_root}/calls.log"
  PATH="${result_root}/bin:${PATH}" MOCK_LOG="${result_root}/calls.log" MOCK_STATE="${result_root}/state" \
    MOCK_CHANNEL_EXISTS=1 MOCK_PACKAGE_ID='audit-anchor_1:newhash' \
    FABRIC_OUTPUT_ROOT="${result_root}/output" FABRIC_CHAINCODE_PATH="${result_root}/chaincode" \
    FABRIC_ORDERER_CA="${result_root}/ca.pem" FABRIC_PEER_MSP="${result_root}/msp" \
    FABRIC_PEER_TLS_ROOT="${result_root}/tls.pem" bash "${SOURCE_ROOT}/deploy-chaincode.sh" >/dev/null
  assert_contains "${result_root}/calls.log" "lifecycle chaincode calculatepackageid"
  assert_contains "${result_root}/calls.log" "lifecycle chaincode install"
  echo "ok - package identity is matched exactly"
}

test_changed_package_increments_sequence() {
  local case_root
  case_root="$(run_deploy_case changed-package 1 'audit-anchor_1:newhash')"
  touch "${case_root}/state/joined"
  printf 'Package ID: audit-anchor_1:oldhash, Label: audit-anchor_1\nPackage ID: audit-anchor_1:newhash, Label: audit-anchor_1\n' >"${case_root}/state/installed"
  printf '{"sequence":1,"version":"1.0","endorsement_plugin":"escc","validation_plugin":"vscc","validation_parameter":"ChkSCBIGCAESAggAGg0SCwoHT3JnMU1TUBAD","approvals":{"Org1MSP":true}}\n' >"${case_root}/state/committed.json"
  printf '{"sequence":1,"version":"1.0","endorsement_plugin":"escc","validation_plugin":"vscc","validation_parameter":"ChkSCBIGCAESAggAGg0SCwoHT3JnMU1TUBAD","source":{"Type":{"LocalPackage":{"package_id":"audit-anchor_1:oldhash"}}}}\n' >"${case_root}/state/approved.json"
  : >"${case_root}/calls.log"
  PATH="${case_root}/bin:${PATH}" MOCK_LOG="${case_root}/calls.log" MOCK_STATE="${case_root}/state" \
    MOCK_CHANNEL_EXISTS=1 MOCK_PACKAGE_ID='audit-anchor_1:newhash' \
    FABRIC_OUTPUT_ROOT="${case_root}/output" FABRIC_CHAINCODE_PATH="${case_root}/chaincode" \
    FABRIC_ORDERER_CA="${case_root}/ca.pem" FABRIC_PEER_MSP="${case_root}/msp" \
    FABRIC_PEER_TLS_ROOT="${case_root}/tls.pem" bash "${SOURCE_ROOT}/deploy-chaincode.sh" >/dev/null
  assert_contains "${case_root}/calls.log" "approveformyorg"
  assert_contains "${case_root}/calls.log" "--sequence 2"
  assert_contains "${case_root}/calls.log" "commit"
  echo "ok - changed package increments lifecycle sequence"
}

test_exact_committed_package_is_idempotent() {
  local case_root
  case_root="$(run_deploy_case exact-committed 1 'audit-anchor_1:newhash')"
  touch "${case_root}/state/joined"
  printf 'Package ID: audit-anchor_1:newhash, Label: audit-anchor_1\n' >"${case_root}/state/installed"
  printf '{"sequence":1,"version":"1.0","endorsement_plugin":"escc","validation_plugin":"vscc","validation_parameter":"ChkSCBIGCAESAggAGg0SCwoHT3JnMU1TUBAD","approvals":{"Org1MSP":true}}\n' >"${case_root}/state/committed.json"
  printf '{"sequence":1,"version":"1.0","endorsement_plugin":"escc","validation_plugin":"vscc","validation_parameter":"ChkSCBIGCAESAggAGg0SCwoHT3JnMU1TUBAD","source":{"Type":{"LocalPackage":{"package_id":"audit-anchor_1:newhash"}}}}\n' >"${case_root}/state/approved.json"
  : >"${case_root}/calls.log"
  PATH="${case_root}/bin:${PATH}" MOCK_LOG="${case_root}/calls.log" MOCK_STATE="${case_root}/state" \
    MOCK_CHANNEL_EXISTS=1 MOCK_PACKAGE_ID='audit-anchor_1:newhash' \
    FABRIC_OUTPUT_ROOT="${case_root}/output" FABRIC_CHAINCODE_PATH="${case_root}/chaincode" \
    FABRIC_ORDERER_CA="${case_root}/ca.pem" FABRIC_PEER_MSP="${case_root}/msp" \
    FABRIC_PEER_TLS_ROOT="${case_root}/tls.pem" bash "${SOURCE_ROOT}/deploy-chaincode.sh" >/dev/null
  assert_not_contains "${case_root}/calls.log" "lifecycle chaincode approveformyorg"
  assert_not_contains "${case_root}/calls.log" "lifecycle chaincode commit"
  echo "ok - exact committed package is an idempotent no-op"
}

prepare_strict_case() {
  local case_root="$1"
  mkdir -p "${case_root}/bin" "${case_root}/state" "${case_root}/output/packages" "${case_root}/chaincode"
  make_peer_mock "${case_root}/bin"
  touch "${case_root}/state/joined"
  printf 'historical-package\n' >"${case_root}/output/packages/audit-anchor.tar.gz"
  printf 'historical-block\n' >"${case_root}/output/scfchannel.block"
  printf 'Package ID: audit-anchor_1:newhash, Label: audit-anchor_1\n' >"${case_root}/state/installed"
  printf '{"sequence":1,"version":"1.0","endorsement_plugin":"escc","validation_plugin":"vscc","validation_parameter":"ChkSCBIGCAESAggAGg0SCwoHT3JnMU1TUBAD","approvals":{"Org1MSP":true}}\n' >"${case_root}/state/committed.json"
  printf '{"sequence":1,"version":"1.0","endorsement_plugin":"escc","validation_plugin":"vscc","validation_parameter":"ChkSCBIGCAESAggAGg0SCwoHT3JnMU1TUBAD","source":{"Type":{"LocalPackage":{"package_id":"audit-anchor_1:newhash"}}}}\n' >"${case_root}/state/approved.json"
  : >"${case_root}/calls.log"
}

run_strict_case() {
  local case_root="$1"
  PATH="${case_root}/bin:${PATH}" MOCK_LOG="${case_root}/calls.log" MOCK_STATE="${case_root}/state" \
    MOCK_PACKAGE_ID='audit-anchor_1:newhash' MOCK_FAIL="${2:-}" MOCK_READY="${4:-true}" MOCK_FAIL_RECHECK="${5:-}" \
    FABRIC_OUTPUT_ROOT="${case_root}/output" FABRIC_CHAINCODE_PATH="${case_root}/chaincode" \
    bash "${SOURCE_ROOT}/deploy-chaincode.sh" --existing-channel-only --expected-sequence "${3:-1}" \
    >"${case_root}/stdout" 2>"${case_root}/stderr"
}

assert_history_untouched() {
  local case_root="$1"
  [[ "$(cat "${case_root}/output/packages/audit-anchor.tar.gz")" == 'historical-package' ]] || fail "historical package was changed"
  [[ "$(cat "${case_root}/output/scfchannel.block")" == 'historical-block' ]] || fail "historical block was changed"
  assert_not_contains "${case_root}/calls.log" "channel fetch"
  assert_not_contains "${case_root}/calls.log" "channel create"
  assert_not_contains "${case_root}/calls.log" "channel join"
}

test_strict_read_failures_preserve_history() {
  local operation case_root status
  for operation in 'channel getinfo' 'lifecycle chaincode querycommitted' 'lifecycle chaincode queryapproved' 'lifecycle chaincode queryinstalled'; do
    case_root="${TEST_ROOT}/strict-${operation// /-}"
    prepare_strict_case "${case_root}"
    set +e
    run_strict_case "${case_root}" "${operation}"
    status=$?
    set -e
    [[ ${status} -ne 0 ]] || fail "strict mode accepted failed ${operation}"
    assert_history_untouched "${case_root}"
    assert_not_contains "${case_root}/calls.log" 'lifecycle chaincode package'
    assert_not_contains "${case_root}/calls.log" 'lifecycle chaincode install'
    assert_not_contains "${case_root}/calls.log" 'lifecycle chaincode approveformyorg'
    assert_not_contains "${case_root}/calls.log" 'lifecycle chaincode commit'
    echo "ok - strict ${operation} read failure leaves chain and history untouched"
  done
}

test_strict_bad_definitions_and_sequence_fail_closed() {
  local variant case_root status expected
  for variant in bad-committed bad-approved empty-approved multiple-approved wrong-approved-sequence missing-package-source stale-expected-sequence; do
    case_root="${TEST_ROOT}/strict-${variant}"
    prepare_strict_case "${case_root}"
    expected=1
    case "${variant}" in
      bad-committed) printf 'not-json\n' >"${case_root}/state/committed.json" ;;
      bad-approved) printf '{}\n' >"${case_root}/state/approved.json" ;;
      empty-approved) : >"${case_root}/state/approved.json" ;;
      multiple-approved) printf '{}\n' >>"${case_root}/state/approved.json" ;;
      wrong-approved-sequence) sed -i 's/"sequence":1/"sequence":2/' "${case_root}/state/approved.json" ;;
      missing-package-source) sed -i 's/audit-anchor_1:newhash//' "${case_root}/state/approved.json" ;;
      stale-expected-sequence) expected=2 ;;
    esac
    set +e
    run_strict_case "${case_root}" '' "${expected}"
    status=$?
    set -e
    [[ ${status} -ne 0 ]] || fail "strict mode accepted ${variant}"
    assert_history_untouched "${case_root}"
    assert_not_contains "${case_root}/calls.log" 'lifecycle chaincode package'
    assert_not_contains "${case_root}/calls.log" 'lifecycle chaincode install'
    assert_not_contains "${case_root}/calls.log" 'lifecycle chaincode approveformyorg'
    echo "ok - strict ${variant} fails before packaging or chain mutations"
  done
}

test_strict_exact_definition_is_idempotent() {
  local case_root="${TEST_ROOT}/strict-idempotent"
  prepare_strict_case "${case_root}"
  run_strict_case "${case_root}"
  assert_history_untouched "${case_root}"
  assert_not_contains "${case_root}/calls.log" 'lifecycle chaincode install'
  assert_not_contains "${case_root}/calls.log" 'lifecycle chaincode approveformyorg'
  assert_not_contains "${case_root}/calls.log" 'lifecycle chaincode commit'
  assert_contains "${case_root}/stdout" 'already committed at sequence 1'
  echo 'ok - strict exact definition is idempotent and preserves package history'
}

test_strict_changed_package_upgrades_existing_chain() {
  local case_root="${TEST_ROOT}/strict-upgrade"
  prepare_strict_case "${case_root}"
  sed -i 's/audit-anchor_1:newhash/audit-anchor_1:oldhash/' "${case_root}/state/approved.json"
  run_strict_case "${case_root}"
  assert_history_untouched "${case_root}"
  assert_contains "${case_root}/calls.log" 'lifecycle chaincode approveformyorg'
  assert_contains "${case_root}/calls.log" '--sequence 2'
  assert_contains "${case_root}/calls.log" 'lifecycle chaincode commit'
  [[ "$(jq -r .sequence "${case_root}/state/committed.json")" == '2' ]] || fail 'strict upgrade did not commit sequence 2'
  echo 'ok - strict changed package upgrades existing chain to next sequence'
}

test_strict_readiness_failure_never_commits() {
  local case_root="${TEST_ROOT}/strict-not-ready" status
  prepare_strict_case "${case_root}"
  sed -i 's/audit-anchor_1:newhash/audit-anchor_1:oldhash/' "${case_root}/state/approved.json"
  set +e
  run_strict_case "${case_root}" '' 1 false
  status=$?
  set -e
  [[ ${status} -ne 0 ]] || fail 'strict mode committed despite negative readiness'
  assert_history_untouched "${case_root}"
  assert_contains "${case_root}/calls.log" 'lifecycle chaincode approveformyorg'
  assert_not_contains "${case_root}/calls.log" 'lifecycle chaincode commit'
  echo 'ok - strict negative readiness stops before commit'
}

test_strict_recheck_failure_never_approves() {
  local operation case_root status
  for operation in 'lifecycle chaincode querycommitted' 'lifecycle chaincode queryapproved'; do
    case_root="${TEST_ROOT}/strict-recheck-${operation// /-}"
    prepare_strict_case "${case_root}"
    sed -i 's/audit-anchor_1:newhash/audit-anchor_1:oldhash/' "${case_root}/state/approved.json"
    set +e
    run_strict_case "${case_root}" '' 1 true "${operation}"
    status=$?
    set -e
    [[ ${status} -ne 0 ]] || fail "strict mode accepted failed ${operation} recheck"
    assert_history_untouched "${case_root}"
    assert_contains "${case_root}/calls.log" 'lifecycle chaincode package'
    assert_not_contains "${case_root}/calls.log" 'lifecycle chaincode approveformyorg'
    assert_not_contains "${case_root}/calls.log" 'lifecycle chaincode commit'
    echo "ok - strict ${operation} recheck failure stops before approval"
  done
}

test_bootstrap_fails_closed_on_mixed_generation
test_bootstrap_hash_locks_one_generation
test_existing_channel_is_fetched_and_joined
test_exact_package_identity_not_label_is_used
test_changed_package_increments_sequence
test_exact_committed_package_is_idempotent
test_strict_read_failures_preserve_history
test_strict_bad_definitions_and_sequence_fail_closed
test_strict_exact_definition_is_idempotent
test_strict_changed_package_upgrades_existing_chain
test_strict_readiness_failure_never_commits
test_strict_recheck_failure_never_approves
