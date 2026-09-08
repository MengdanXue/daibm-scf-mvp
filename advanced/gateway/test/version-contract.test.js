'use strict';

const assert = require('node:assert/strict');
const { it } = require('node:test');
const vectors = require('../../contracts/anchor-version-tokens.json');
const { normalizeAnchor } = require('../src/anchor');

const anchor = {
  anchorId: '11111111-2222-4333-8444-555555555555',
  eventId: '12345678-1234-4567-89ab-1234567890ab',
  subjectId: 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',
  eventHash: 'a'.repeat(64), chainHeadHash: 'b'.repeat(64),
  recordedAt: '2026-09-07T00:00:00Z', schemaVersion: 1,
};

for (const field of ['circuitVersion', 'modelVersion', 'policyVersion']) {
  it(`preserves valid ${field} tokens and rejects malformed tokens`, () => {
    for (const value of vectors.valid) {
      const input = { ...anchor, [field]: value };
      assert.deepEqual(normalizeAnchor(input), input);
    }
    for (const value of vectors.invalid) {
      assert.throws(() => normalizeAnchor({ ...anchor, [field]: value }), /opaque version token/);
    }
  });
}
