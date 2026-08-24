'use strict';

const REQUIRED_FIELDS = Object.freeze([
  'anchorId',
  'chainHeadHash',
  'eventHash',
  'eventId',
  'recordedAt',
  'schemaVersion',
  'subjectId',
]);
const OPTIONAL_VERSION_FIELDS = Object.freeze(['circuitVersion', 'modelVersion', 'policyVersion']);
const OPTIONAL_HASH_FIELDS = Object.freeze(['proofSha256']);
const ALLOWED_FIELDS = new Set([...REQUIRED_FIELDS, ...OPTIONAL_VERSION_FIELDS, ...OPTIONAL_HASH_FIELDS]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const VERSION = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const RFC3339 = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-](\d{2}):(\d{2}))$/;

function isCanonicalUuid(value) {
  return typeof value === 'string' && UUID.test(value);
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

function assertAnchor(anchor) {
  if (anchor === null || Array.isArray(anchor) || typeof anchor !== 'object') {
    throw new Error('anchor JSON must encode one object');
  }
  for (const name of Object.keys(anchor)) {
    if (!ALLOWED_FIELDS.has(name)) throw new Error(`anchor contains undeclared property ${name}`);
  }
  for (const name of REQUIRED_FIELDS) {
    if (!Object.hasOwn(anchor, name)) throw new Error(`anchor is missing required property ${name}`);
  }
  for (const name of ['anchorId', 'eventId', 'subjectId']) {
    if (!isCanonicalUuid(anchor[name])) throw new Error(`${name} must be a canonical opaque UUID`);
  }
  for (const name of ['eventHash', 'chainHeadHash']) {
    if (typeof anchor[name] !== 'string' || !SHA256.test(anchor[name])) {
      throw new Error(`${name} must be a 64-character lowercase SHA-256 hash`);
    }
  }
  if (!isValidRfc3339(anchor.recordedAt)) throw new Error('recordedAt must be a valid RFC3339 timestamp');
  if (anchor.schemaVersion !== 1) throw new Error('schemaVersion must equal 1');
  for (const name of OPTIONAL_VERSION_FIELDS) {
    if (Object.hasOwn(anchor, name) && (typeof anchor[name] !== 'string' || !VERSION.test(anchor[name]))) {
      throw new Error(`${name} must be an opaque version token`);
    }
  }
  for (const name of OPTIONAL_HASH_FIELDS) {
    if (Object.hasOwn(anchor, name) && (typeof anchor[name] !== 'string' || !SHA256.test(anchor[name]))) {
      throw new Error(`${name} must be a 64-character lowercase SHA-256 hash`);
    }
  }
}

function canonicalJson(value) {
  return JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))));
}

function normalizeAnchor(anchor) {
  assertAnchor(anchor);
  return JSON.parse(canonicalJson(anchor));
}

module.exports = { canonicalJson, isCanonicalUuid, normalizeAnchor };
