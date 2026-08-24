'use strict';

function readPort(value) {
  const port = Number(value || 8090);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('invalid gateway port configuration');
  return port;
}

function loadConfig(environment = process.env) {
  return Object.freeze({
    host: environment.GATEWAY_HOST || '0.0.0.0',
    port: readPort(environment.GATEWAY_PORT),
    peerEndpoint: environment.FABRIC_PEER_ENDPOINT || 'peer0.org1.example.com:7051',
    peerHostAlias: environment.FABRIC_PEER_HOST_ALIAS || 'peer0.org1.example.com',
    tlsRootCertPath: environment.FABRIC_TLS_ROOT_CERT || '/fabric/tls/ca.crt',
    identityCertificateDirectory: environment.FABRIC_IDENTITY_CERT_DIR || '/fabric/msp/signcerts',
    identityPrivateKeyDirectory: environment.FABRIC_IDENTITY_KEY_DIR || '/fabric/msp/keystore',
    mspId: 'Org1MSP',
    channelName: 'scfchannel',
    chaincodeName: 'audit-anchor',
    contractName: 'audit-anchor',
  });
}

module.exports = { loadConfig };
