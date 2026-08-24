'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs/promises');

const grpc = require('@grpc/grpc-js');
const { connect, signers } = require('@hyperledger/fabric-gateway');

const { canonicalJson } = require('./anchor');
const { PublicError } = require('./errors');

const decoder = new TextDecoder('utf8', { fatal: true });

function mapFabricError(error) {
  if (error instanceof PublicError) return error;
  const text = String(error && error.message ? error.message : '');
  if (error && (error.code === grpc.status.DEADLINE_EXCEEDED || error.code === 'DEADLINE_EXCEEDED')) {
    return new PublicError('FABRIC_TIMEOUT', { cause: error });
  }
  if (error && (error.code === grpc.status.UNAVAILABLE || error.code === 'UNAVAILABLE')) {
    return new PublicError('FABRIC_UNAVAILABLE', { cause: error });
  }
  if (/anchor .* does not exist/i.test(text)) return new PublicError('ANCHOR_NOT_FOUND', { cause: error });
  if (/anchor ID already exists with different content/i.test(text)) return new PublicError('ANCHOR_CONFLICT', { cause: error });
  return new PublicError('INTERNAL_ERROR', { cause: error });
}

class FabricAdapter {
  constructor({ contract }) {
    if (!contract) throw new TypeError('contract is required');
    this.contract = contract;
  }

  async probe(anchorId) {
    try {
      const result = decoder.decode(await this.contract.evaluateTransaction('AnchorExists', anchorId));
      if (result !== 'true' && result !== 'false') throw new Error('invalid AnchorExists response');
      return result === 'true';
    } catch (error) {
      throw mapFabricError(error);
    }
  }

  async read(anchorId) {
    try {
      return JSON.parse(decoder.decode(await this.contract.evaluateTransaction('ReadAnchor', anchorId)));
    } catch (error) {
      throw mapFabricError(error);
    }
  }

  async submit(anchor) {
    try {
      return decoder.decode(await this.contract.submitTransaction('CreateAnchor', canonicalJson(anchor)));
    } catch (error) {
      throw mapFabricError(error);
    }
  }
}

async function readOnlyCredential(directory, label) {
  let entries;
  try {
    entries = (await fs.readdir(directory, { withFileTypes: true }))
      .filter((entry) => entry.isFile())
      .map((entry) => entry.name)
      .sort();
  } catch (error) {
    throw new Error(`unable to read ${label} configuration`, { cause: error });
  }
  if (entries.length !== 1) throw new Error(`expected exactly one ${label} file`);
  const bytes = await fs.readFile(`${directory}/${entries[0]}`);
  if (bytes.length === 0 || bytes.length > 64 * 1024) throw new Error(`invalid ${label} file size`);
  return bytes;
}

async function createFabricConnection(config) {
  const [tlsRootCertificate, identityCertificate, privateKeyPem] = await Promise.all([
    fs.readFile(config.tlsRootCertPath),
    readOnlyCredential(config.identityCertificateDirectory, 'identity certificate'),
    readOnlyCredential(config.identityPrivateKeyDirectory, 'identity private key'),
  ]);
  const privateKey = crypto.createPrivateKey(privateKeyPem);
  const signer = signers.newPrivateKeySigner(privateKey);
  const credentials = grpc.credentials.createSsl(tlsRootCertificate);
  const client = new grpc.Client(config.peerEndpoint, credentials, {
    'grpc.ssl_target_name_override': config.peerHostAlias,
    'grpc.default_authority': config.peerHostAlias,
  });
  const gateway = connect({
    client,
    identity: { mspId: config.mspId, credentials: identityCertificate },
    signer,
    evaluateOptions: () => ({ deadline: Date.now() + 5_000 }),
    endorseOptions: () => ({ deadline: Date.now() + 5_000 }),
    submitOptions: () => ({ deadline: Date.now() + 5_000 }),
    commitStatusOptions: () => ({ deadline: Date.now() + 15_000 }),
  });
  const contract = gateway
    .getNetwork(config.channelName)
    .getContract(config.chaincodeName, config.contractName);
  return {
    fabric: new FabricAdapter({ contract }),
    close() {
      gateway.close();
      client.close();
    },
  };
}

module.exports = { FabricAdapter, createFabricConnection, mapFabricError };
