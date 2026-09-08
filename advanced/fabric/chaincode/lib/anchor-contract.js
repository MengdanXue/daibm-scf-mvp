'use strict';

const { Contract } = require('fabric-contract-api');

const REQUIRED_FIELDS = Object.freeze([
  'anchorId',
  'chainHeadHash',
  'eventHash',
  'eventId',
  'recordedAt',
  'schemaVersion',
  'subjectId',
]);
const OPTIONAL_VERSION_FIELDS = Object.freeze([
  'circuitVersion',
  'modelVersion',
  'policyVersion',
]);
const OPTIONAL_HASH_FIELDS = Object.freeze(['proofSha256']);
const ALLOWED_FIELDS = new Set([
  ...REQUIRED_FIELDS,
  ...OPTIONAL_VERSION_FIELDS,
  ...OPTIONAL_HASH_FIELDS,
]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const OPAQUE_VERSION = /^[A-Za-z0-9][A-Za-z0-9._-]*(?:@[A-Za-z0-9][A-Za-z0-9._-]*)?$/;
const RFC3339 = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-](\d{2}):(\d{2}))$/;

function assertUuid(name, value) {
  if (typeof value !== 'string' || !UUID.test(value)) {
    throw new Error(`${name} must be a canonical opaque UUID`);
  }
}

function assertSha256(name, value) {
  if (typeof value !== 'string' || !SHA256.test(value)) {
    throw new Error(`${name} must be a 64-character lowercase SHA-256 hash`);
  }
}

function isValidRfc3339(value) {
  if (typeof value !== 'string') return false;
  const match = RFC3339.exec(value);
  if (!match) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, , offsetHourText, offsetMinuteText] = match;
  const [year, month, day, hour, minute, second] = [yearText, monthText, dayText, hourText, minuteText, secondText].map(Number);
  if (year === 0 || hour > 23 || minute > 59 || second > 59) return false;
  if (offsetHourText !== undefined && (Number(offsetHourText) > 23 || Number(offsetMinuteText) > 59)) return false;
  const wallClock = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  return wallClock.getUTCFullYear() === year
    && wallClock.getUTCMonth() === month - 1
    && wallClock.getUTCDate() === day
    && wallClock.getUTCHours() === hour
    && wallClock.getUTCMinutes() === minute
    && wallClock.getUTCSeconds() === second;
}

function validateAnchor(anchor) {
  if (anchor === null || Array.isArray(anchor) || typeof anchor !== 'object') {
    throw new Error('anchor JSON must encode one object');
  }
  for (const name of Object.keys(anchor)) {
    if (!ALLOWED_FIELDS.has(name)) {
      throw new Error(`anchor contains undeclared property ${name}`);
    }
  }
  for (const name of REQUIRED_FIELDS) {
    if (!Object.hasOwn(anchor, name)) {
      throw new Error(`anchor is missing required property ${name}`);
    }
  }

  for (const name of ['anchorId', 'eventId', 'subjectId']) assertUuid(name, anchor[name]);
  for (const name of ['eventHash', 'chainHeadHash']) assertSha256(name, anchor[name]);
  if (!isValidRfc3339(anchor.recordedAt)) {
    throw new Error('recordedAt must be a valid RFC3339 timestamp');
  }
  if (anchor.schemaVersion !== 1) {
    throw new Error('schemaVersion must equal 1');
  }
  for (const name of OPTIONAL_VERSION_FIELDS) {
    if (Object.hasOwn(anchor, name) && (typeof anchor[name] !== 'string' || anchor[name].length > 64 || !OPAQUE_VERSION.test(anchor[name]))) {
      throw new Error(`${name} must be an opaque version token`);
    }
  }
  for (const name of OPTIONAL_HASH_FIELDS) {
    if (Object.hasOwn(anchor, name)) assertSha256(name, anchor[name]);
  }
  return anchor;
}

function canonicalJson(object) {
  const ordered = {};
  for (const key of Object.keys(object).sort()) ordered[key] = object[key];
  return JSON.stringify(ordered);
}

function stateKey(anchorId) {
  return `anchor:${anchorId}`;
}

class AnchorContract extends Contract {
  constructor() {
    super('audit-anchor');
  }

  async AnchorExists(ctx, anchorId) {
    assertUuid('anchorId', anchorId);
    const bytes = await ctx.stub.getState(stateKey(anchorId));
    return Boolean(bytes && bytes.length > 0);
  }

  async CreateAnchor(ctx, anchorJson) {
    if (typeof anchorJson !== 'string') throw new Error('anchorJson must be a JSON string');
    let anchor;
    try {
      anchor = JSON.parse(anchorJson);
    } catch {
      throw new Error('anchorJson must be valid JSON');
    }
    validateAnchor(anchor);
    const canonical = canonicalJson(anchor);
    const key = stateKey(anchor.anchorId);
    const existing = await ctx.stub.getState(key);
    if (existing && existing.length > 0) {
      if (existing.toString('utf8') === canonical) return canonical;
      throw new Error('anchor ID already exists with different content');
    }
    await ctx.stub.putState(key, Buffer.from(canonical, 'utf8'));
    return canonical;
  }

  async ReadAnchor(ctx, anchorId) {
    assertUuid('anchorId', anchorId);
    const bytes = await ctx.stub.getState(stateKey(anchorId));
    if (!bytes || bytes.length === 0) throw new Error(`anchor ${anchorId} does not exist`);
    return bytes.toString('utf8');
  }
}

module.exports = { AnchorContract, canonicalJson, validateAnchor };
