'use strict';

const assert = require('node:assert/strict');
const { afterEach, describe, it } = require('node:test');

const { createGatewayServer } = require('../src/server');

const VALID_ANCHOR = Object.freeze({
  anchorId: '11111111-2222-4333-8444-555555555555',
  chainHeadHash: 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789',
  eventHash: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
  eventId: '12345678-1234-4567-89ab-1234567890ab',
  recordedAt: '2026-08-24T12:55:00Z',
  schemaVersion: 1,
  subjectId: 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',
});

function canonical(value) {
  return JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))));
}

class FakeFabric {
  constructor() {
    this.records = new Map();
    this.probes = [];
    this.submissions = 0;
    this.failure = null;
    this.failAfterCommitOnce = false;
  }

  async probe(anchorId) {
    this.probes.push(anchorId);
    if (this.failure) throw this.failure;
    return false;
  }

  async read(anchorId) {
    if (this.failure) throw this.failure;
    if (!this.records.has(anchorId)) {
      const error = new Error('anchor does not exist');
      error.code = 'ANCHOR_NOT_FOUND';
      throw error;
    }
    return this.records.get(anchorId);
  }

  async submit(anchor) {
    if (this.failure) throw this.failure;
    this.submissions += 1;
    const encoded = canonical(anchor);
    const existing = this.records.get(anchor.anchorId);
    if (existing && canonical(existing) !== encoded) {
      const error = new Error('anchor ID already exists with different content');
      error.code = 'ANCHOR_CONFLICT';
      throw error;
    }
    this.records.set(anchor.anchorId, JSON.parse(encoded));
    if (this.failAfterCommitOnce) {
      this.failAfterCommitOnce = false;
      throw new Error('response lost after Fabric committed');
    }
    return encoded;
  }
}

const servers = new Set();

async function start(fabric = new FakeFabric()) {
  const server = createGatewayServer({ fabric });
  servers.add(server);
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const { port } = server.address();
  return { fabric, baseUrl: `http://127.0.0.1:${port}` };
}

afterEach(async () => {
  await Promise.all(Array.from(servers, (server) => new Promise((resolve) => server.close(resolve))));
  servers.clear();
});

async function json(response) {
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

describe('internal Fabric anchor HTTP contract', () => {
  it('uses a read-only health probe and never creates ledger state', async () => {
    const { fabric, baseUrl } = await start();

    const response = await fetch(`${baseUrl}/health`);

    assert.equal(response.status, 200);
    assert.deepEqual(await json(response), {
      status: 'ok',
      fabric: 'ready',
      channel: 'scfchannel',
      contract: 'audit-anchor',
    });
    assert.deepEqual(fabric.probes, ['00000000-0000-4000-8000-000000000000']);
    assert.equal(fabric.submissions, 0);
    assert.equal(fabric.records.size, 0);
  });

  it('creates, reads, and reports an identical retry without resubmitting', async () => {
    const { fabric, baseUrl } = await start();

    const created = await fetch(`${baseUrl}/anchors`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(VALID_ANCHOR),
    });
    assert.equal(created.status, 201);
    assert.deepEqual(await json(created), VALID_ANCHOR);

    const read = await fetch(`${baseUrl}/anchors/${VALID_ANCHOR.anchorId}`);
    assert.equal(read.status, 200);
    assert.deepEqual(await json(read), VALID_ANCHOR);

    const reordered = Object.fromEntries(Object.entries(VALID_ANCHOR).reverse());
    const retried = await fetch(`${baseUrl}/anchors`, {
      method: 'POST',
      headers: { 'content-type': 'application/json; charset=utf-8' },
      body: JSON.stringify(reordered),
    });
    assert.equal(retried.status, 200);
    assert.deepEqual(await json(retried), VALID_ANCHOR);
    assert.equal(fabric.submissions, 1);
  });

  it('reports an unexpected internal error as retryable and submits once after recovery', async () => {
    const { fabric, baseUrl } = await start();
    fabric.failure = new Error('private backend failure');

    const failed = await fetch(`${baseUrl}/anchors`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(VALID_ANCHOR),
    });
    assert.equal(failed.status, 500);
    assert.deepEqual(await json(failed), {
      code: 'INTERNAL_ERROR',
      message: 'Internal gateway error',
      retryable: true,
    });
    assert.equal(fabric.submissions, 0);

    fabric.failure = null;
    for (const expectedStatus of [201, 200]) {
      const retried = await fetch(`${baseUrl}/anchors`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(VALID_ANCHOR),
      });
      assert.equal(retried.status, expectedStatus);
      assert.deepEqual(await json(retried), VALID_ANCHOR);
    }
    assert.equal(fabric.submissions, 1);
    assert.equal(fabric.records.size, 1);
  });

  it('reads a committed anchor after a lost 500 response without a second submit', async () => {
    const { fabric, baseUrl } = await start();
    fabric.failAfterCommitOnce = true;

    const first = await fetch(`${baseUrl}/anchors`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(VALID_ANCHOR),
    });
    assert.equal(first.status, 500);
    assert.deepEqual(await json(first), {
      code: 'INTERNAL_ERROR',
      message: 'Internal gateway error',
      retryable: true,
    });
    assert.equal(fabric.submissions, 1);
    assert.equal(fabric.records.size, 1);

    const retried = await fetch(`${baseUrl}/anchors`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(VALID_ANCHOR),
    });
    assert.equal(retried.status, 200);
    assert.deepEqual(await json(retried), VALID_ANCHOR);
    assert.equal(fabric.submissions, 1);
    assert.equal(fabric.records.size, 1);
  });

  it('maps conflicting reuse to a stable non-retryable 409 error', async () => {
    const { baseUrl } = await start();
    await fetch(`${baseUrl}/anchors`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(VALID_ANCHOR),
    });

    const response = await fetch(`${baseUrl}/anchors`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...VALID_ANCHOR, eventHash: 'f'.repeat(64) }),
    });

    assert.equal(response.status, 409);
    assert.deepEqual(await json(response), {
      code: 'ANCHOR_CONFLICT',
      message: 'Anchor ID already exists with different content',
      retryable: false,
    });
  });

  it('rejects extra, sensitive, malformed, and path-confused input at the HTTP boundary', async () => {
    const { fabric, baseUrl } = await start();
    const invalidBodies = [
      { ...VALID_ANCHOR, invoiceNumber: 'INV-SECRET' },
      { ...VALID_ANCHOR, authorization: 'Bearer secret' },
      { ...VALID_ANCHOR, anchorId: 'AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE' },
      { ...VALID_ANCHOR, eventHash: 'A'.repeat(64) },
      { ...VALID_ANCHOR, recordedAt: '2026-02-30T00:00:00Z' },
    ];

    for (const body of invalidBodies) {
      const response = await fetch(`${baseUrl}/anchors`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      assert.equal(response.status, 422);
      assert.equal((await json(response)).code, 'INVALID_ANCHOR');
    }

    const confused = await fetch(`${baseUrl}/anchors/%2e%2e%2fhealth`);
    assert.equal(confused.status, 422);
    assert.equal((await json(confused)).code, 'INVALID_ANCHOR');
    assert.equal(fabric.submissions, 0);
  });

  it('enforces media type, JSON syntax, and a 16 KiB body limit', async () => {
    const { baseUrl } = await start();

    const wrongType = await fetch(`${baseUrl}/anchors`, { method: 'POST', body: '{}' });
    assert.equal(wrongType.status, 415);
    assert.equal((await json(wrongType)).code, 'UNSUPPORTED_MEDIA_TYPE');

    const malformed = await fetch(`${baseUrl}/anchors`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{',
    });
    assert.equal(malformed.status, 400);
    assert.equal((await json(malformed)).code, 'INVALID_JSON');

    const oversized = await fetch(`${baseUrl}/anchors`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ padding: 'x'.repeat(17 * 1024) }),
    });
    assert.equal(oversized.status, 413);
    assert.equal((await json(oversized)).code, 'PAYLOAD_TOO_LARGE');
  });

  it('returns stable not-found, method, route, timeout, and unavailable errors without path leakage', async () => {
    const { fabric, baseUrl } = await start();

    const missing = await fetch(`${baseUrl}/anchors/${VALID_ANCHOR.anchorId}`);
    assert.equal(missing.status, 404);
    assert.equal((await json(missing)).code, 'ANCHOR_NOT_FOUND');

    const method = await fetch(`${baseUrl}/health`, { method: 'POST' });
    assert.equal(method.status, 405);
    assert.equal((await json(method)).code, 'METHOD_NOT_ALLOWED');

    const route = await fetch(`${baseUrl}/internal/keys`);
    assert.equal(route.status, 404);
    assert.equal((await json(route)).code, 'ROUTE_NOT_FOUND');

    for (const [code, status] of [['FABRIC_TIMEOUT', 504], ['FABRIC_UNAVAILABLE', 503]]) {
      const error = new Error(`ENOENT /network/output/fabric/private/${code}.pem`);
      error.code = code;
      fabric.failure = error;
      const response = await fetch(`${baseUrl}/health`);
      const body = await json(response);
      assert.equal(response.status, status);
      assert.equal(body.code, code);
      assert.equal(body.retryable, true);
      assert.equal(JSON.stringify(body).includes('/network/'), false);
    }
  });
});
