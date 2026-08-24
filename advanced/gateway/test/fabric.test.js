'use strict';

const assert = require('node:assert/strict');
const { describe, it } = require('node:test');

const { FabricAdapter, mapFabricError } = require('../src/fabric');

const ANCHOR = Object.freeze({
  anchorId: '11111111-2222-4333-8444-555555555555',
  chainHeadHash: 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789',
  eventHash: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
  eventId: '12345678-1234-4567-89ab-1234567890ab',
  recordedAt: '2026-08-24T12:55:00Z',
  schemaVersion: 1,
  subjectId: 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',
});

describe('Fabric Gateway adapter', () => {
  it('uses only read transactions for health/read and canonical CreateAnchor for submit', async () => {
    const calls = [];
    const contract = {
      async evaluateTransaction(name, ...args) {
        calls.push(['evaluate', name, ...args]);
        if (name === 'AnchorExists') return Buffer.from('false');
        return Buffer.from(JSON.stringify(ANCHOR));
      },
      async submitTransaction(name, ...args) {
        calls.push(['submit', name, ...args]);
        return Buffer.from(JSON.stringify(ANCHOR));
      },
    };
    const adapter = new FabricAdapter({ contract });

    assert.equal(await adapter.probe(ANCHOR.anchorId), false);
    assert.deepEqual(await adapter.read(ANCHOR.anchorId), ANCHOR);
    assert.equal(await adapter.submit(Object.fromEntries(Object.entries(ANCHOR).reverse())), JSON.stringify(ANCHOR));
    assert.deepEqual(calls, [
      ['evaluate', 'AnchorExists', ANCHOR.anchorId],
      ['evaluate', 'ReadAnchor', ANCHOR.anchorId],
      ['submit', 'CreateAnchor', JSON.stringify(ANCHOR)],
    ]);
  });

  it('maps timeout, unavailable, missing, and conflict failures without exposing internal details', () => {
    const cases = [
      [Object.assign(new Error('deadline /fabric/keys/private.pem'), { code: 4 }), 'FABRIC_TIMEOUT'],
      [Object.assign(new Error('socket /fabric/tls/ca.crt'), { code: 14 }), 'FABRIC_UNAVAILABLE'],
      [new Error('anchor 11111111-2222-4333-8444-555555555555 does not exist'), 'ANCHOR_NOT_FOUND'],
      [new Error('anchor ID already exists with different content'), 'ANCHOR_CONFLICT'],
    ];

    for (const [source, expectedCode] of cases) {
      const mapped = mapFabricError(source);
      assert.equal(mapped.code, expectedCode);
      assert.equal(mapped.message.includes('/fabric/'), false);
    }
  });
});
