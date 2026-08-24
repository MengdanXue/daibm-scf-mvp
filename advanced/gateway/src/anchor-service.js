'use strict';

const { canonicalJson, normalizeAnchor } = require('./anchor');
const { PublicError, toPublicError } = require('./errors');

class AnchorService {
  constructor(fabric) {
    if (!fabric) throw new TypeError('fabric adapter is required');
    this.fabric = fabric;
  }

  async health() {
    try {
      await this.fabric.probe('00000000-0000-4000-8000-000000000000');
      return { status: 'ok', fabric: 'ready', channel: 'scfchannel', contract: 'audit-anchor' };
    } catch (error) {
      throw toPublicError(error);
    }
  }

  async read(anchorId) {
    try {
      return normalizeAnchor(await this.fabric.read(anchorId));
    } catch (error) {
      throw toPublicError(error);
    }
  }

  async create(input) {
    let anchor;
    try {
      anchor = normalizeAnchor(input);
    } catch (error) {
      throw new PublicError('INVALID_ANCHOR', { cause: error });
    }

    try {
      const existing = normalizeAnchor(await this.fabric.read(anchor.anchorId));
      if (canonicalJson(existing) !== canonicalJson(anchor)) throw new PublicError('ANCHOR_CONFLICT');
      return { anchor: existing, created: false };
    } catch (error) {
      const publicError = toPublicError(error);
      if (publicError.code !== 'ANCHOR_NOT_FOUND') throw publicError;
    }

    try {
      const result = await this.fabric.submit(anchor);
      return { anchor: normalizeAnchor(typeof result === 'string' ? JSON.parse(result) : result), created: true };
    } catch (error) {
      throw toPublicError(error);
    }
  }
}

module.exports = { AnchorService };
