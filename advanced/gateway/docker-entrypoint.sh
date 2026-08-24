#!/usr/bin/env bash
set -euo pipefail

readonly SOURCE_CERT_DIR="${FABRIC_SOURCE_CERT_DIR:-/fabric/msp/signcerts}"
readonly SOURCE_KEY_DIR="${FABRIC_SOURCE_KEY_DIR:-/fabric/msp/keystore}"
readonly SOURCE_TLS_CERT="${FABRIC_SOURCE_TLS_CERT:-/fabric/tls/ca.crt}"
readonly RUNTIME_ROOT="${FABRIC_RUNTIME_ROOT:-/run/fabric-credentials}"

shopt -s nullglob
certificates=("${SOURCE_CERT_DIR}"/*)
private_keys=("${SOURCE_KEY_DIR}"/*)
if [[ ${#certificates[@]} -ne 1 ]] || [[ ! -f "${certificates[0]}" ]]; then
  echo "Fabric gateway requires exactly one mounted identity certificate" >&2
  exit 1
fi
if [[ ${#private_keys[@]} -ne 1 ]] || [[ ! -f "${private_keys[0]}" ]]; then
  echo "Fabric gateway requires exactly one mounted identity private key" >&2
  exit 1
fi
if [[ ! -f "${SOURCE_TLS_CERT}" ]]; then
  echo "Fabric gateway requires one mounted peer TLS root certificate" >&2
  exit 1
fi

install -d -m 0700 "${RUNTIME_ROOT}" "${RUNTIME_ROOT}/signcerts" "${RUNTIME_ROOT}/keystore" "${RUNTIME_ROOT}/tls"
install -m 0400 "${certificates[0]}" "${RUNTIME_ROOT}/signcerts/cert.pem"
install -m 0400 "${private_keys[0]}" "${RUNTIME_ROOT}/keystore/key.pem"
install -m 0400 "${SOURCE_TLS_CERT}" "${RUNTIME_ROOT}/tls/ca.crt"
chown node:node \
  "${RUNTIME_ROOT}/signcerts/cert.pem" \
  "${RUNTIME_ROOT}/keystore/key.pem" \
  "${RUNTIME_ROOT}/tls/ca.crt"
chown node:node "${RUNTIME_ROOT}/signcerts" "${RUNTIME_ROOT}/keystore" "${RUNTIME_ROOT}/tls"
chown node:node "${RUNTIME_ROOT}"

export FABRIC_IDENTITY_CERT_DIR="${RUNTIME_ROOT}/signcerts"
export FABRIC_IDENTITY_KEY_DIR="${RUNTIME_ROOT}/keystore"
export FABRIC_TLS_ROOT_CERT="${RUNTIME_ROOT}/tls/ca.crt"

exec setpriv --reuid=node --regid=node --init-groups "$@"
