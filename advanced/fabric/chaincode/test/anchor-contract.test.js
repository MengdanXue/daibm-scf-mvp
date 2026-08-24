'use strict';

const assert = require('node:assert/strict');
const { describe, it } = require('node:test');

const { AnchorContract } = require('../lib/anchor-contract');

const HASH_A = 'a'.repeat(64);
const HASH_B = 'b'.repeat(64);
const VALID_ANCHOR = Object.freeze({
  subjectId: 'd80b6032-6793-4db4-b680-c70a052ed6b3',
  eventId: '71371e2f-6410-48d9-8c61-5f7ef5403eb8',
  anchorId: 'bb44b6c5-6765-4e2a-9153-45f0bf76d548',
  chainHeadHash: HASH_B,
  eventHash: HASH_A,
  recordedAt: '2026-08-24T12:34:56.789Z',
  schemaVersion: 1,
});
const EXPECTED_CANONICAL = `{"anchorId":"bb44b6c5-6765-4e2a-9153-45f0bf76d548","chainHeadHash":"${HASH_B}","eventHash":"${HASH_A}","eventId":"71371e2f-6410-48d9-8c61-5f7ef5403eb8","recordedAt":"2026-08-24T12:34:56.789Z","schemaVersion":1,"subjectId":"d80b6032-6793-4db4-b680-c70a052ed6b3"}`;

class MemoryWorldState {
  constructor() {
    this.values = new Map();
  }

  async getState(key) {
    return this.values.get(key) ?? Buffer.alloc(0);
  }

  async putState(key, value) {
    this.values.set(key, Buffer.from(value));
  }
}

function context() {
  return { stub: new MemoryWorldState() };
}

describe('audit anchor contract', () => {
  it('stores and returns a byte-stable canonical hash envelope', async () => {
    const contract = new AnchorContract();
    const ctx = context();

    await contract.CreateAnchor(ctx, JSON.stringify(VALID_ANCHOR));

    assert.equal(await contract.ReadAnchor(ctx, VALID_ANCHOR.anchorId), EXPECTED_CANONICAL);
    assert.equal(EXPECTED_CANONICAL.includes('invoiceNumber'), false);
    assert.equal(EXPECTED_CANONICAL.includes('amount'), false);
  });

  it('accepts an identical retry even when JSON property order changes', async () => {
    const contract = new AnchorContract();
    const ctx = context();
    const reordered = {
      schemaVersion: 1,
      recordedAt: VALID_ANCHOR.recordedAt,
      eventHash: VALID_ANCHOR.eventHash,
      chainHeadHash: VALID_ANCHOR.chainHeadHash,
      anchorId: VALID_ANCHOR.anchorId,
      eventId: VALID_ANCHOR.eventId,
      subjectId: VALID_ANCHOR.subjectId,
    };

    await contract.CreateAnchor(ctx, JSON.stringify(VALID_ANCHOR));
    assert.equal(await contract.CreateAnchor(ctx, JSON.stringify(reordered)), EXPECTED_CANONICAL);
    assert.equal(await contract.ReadAnchor(ctx, VALID_ANCHOR.anchorId), EXPECTED_CANONICAL);
  });

  it('rejects conflicting reuse of an existing anchor ID', async () => {
    const contract = new AnchorContract();
    const ctx = context();
    await contract.CreateAnchor(ctx, JSON.stringify(VALID_ANCHOR));

    await assert.rejects(
      () => contract.CreateAnchor(ctx, JSON.stringify({ ...VALID_ANCHOR, eventHash: 'c'.repeat(64) })),
      /anchor ID already exists with different content/i,
    );
  });

  it('rejects undeclared business or credential properties', async () => {
    const contract = new AnchorContract();
    for (const property of ['invoiceNumber', 'invoiceAmount', 'supplierName', 'credential', 'salt']) {
      await assert.rejects(
        () => contract.CreateAnchor(context(), JSON.stringify({ ...VALID_ANCHOR, [property]: 'secret' })),
        new RegExp(`undeclared property.*${property}`, 'i'),
      );
    }
  });

  it('rejects malformed identifiers, hashes, timestamps, and versions', async () => {
    const contract = new AnchorContract();
    const invalidCases = [
      [{ ...VALID_ANCHOR, anchorId: 'invoice-2026-001' }, /anchorId.*UUID/i],
      [{ ...VALID_ANCHOR, subjectId: 'not-an-opaque-uuid' }, /subjectId.*UUID/i],
      [{ ...VALID_ANCHOR, eventHash: 'A'.repeat(64) }, /eventHash.*lowercase SHA-256/i],
      [{ ...VALID_ANCHOR, chainHeadHash: 'a'.repeat(63) }, /chainHeadHash.*lowercase SHA-256/i],
      [{ ...VALID_ANCHOR, recordedAt: '2026-02-30T10:00:00Z' }, /recordedAt.*RFC3339/i],
      [{ ...VALID_ANCHOR, schemaVersion: 2 }, /schemaVersion.*1/i],
      [{ ...VALID_ANCHOR, modelVersion: 'model name with spaces' }, /modelVersion.*opaque/i],
    ];

    for (const [anchor, message] of invalidCases) {
      await assert.rejects(() => contract.CreateAnchor(context(), JSON.stringify(anchor)), message);
    }
  });

  it('supports optional hash-only model and proof evidence', async () => {
    const contract = new AnchorContract();
    const ctx = context();
    const anchor = {
      ...VALID_ANCHOR,
      modelVersion: 'tgnn-v1.0',
      policyVersion: 'scf-policy-2026.08',
      proofSha256: 'c'.repeat(64),
      circuitVersion: 'invoice-limit-v1',
    };

    await contract.CreateAnchor(ctx, JSON.stringify(anchor));
    assert.deepEqual(JSON.parse(await contract.ReadAnchor(ctx, anchor.anchorId)), anchor);
  });

  it('reports existence and rejects reads of absent anchors', async () => {
    const contract = new AnchorContract();
    const ctx = context();

    assert.equal(await contract.AnchorExists(ctx, VALID_ANCHOR.anchorId), false);
    await assert.rejects(() => contract.ReadAnchor(ctx, VALID_ANCHOR.anchorId), /does not exist/i);
    await contract.CreateAnchor(ctx, JSON.stringify(VALID_ANCHOR));
    assert.equal(await contract.AnchorExists(ctx, VALID_ANCHOR.anchorId), true);
  });
});
