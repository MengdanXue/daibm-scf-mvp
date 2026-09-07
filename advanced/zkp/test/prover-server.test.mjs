import assert from 'node:assert/strict';
import test from 'node:test';

import { createProverServer, CIRCUIT_VERSION } from '../src/server.mjs';
import { verifyInvoiceLimit } from '../index.mjs';
import { checkProver } from '../src/preflight.mjs';

async function withServer(run) {
  const server = createProverServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  try {
    return await run(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

const post = (base, body, headers = { 'content-type': 'application/json' }) =>
  fetch(`${base}/proofs/invoice-limit`, { method: 'POST', headers, body });

test('prover server', async (t) => {
  await t.test('preflight verifies a real proof, not only process health', async () => {
    await withServer(async (base) => {
      const result = await checkProver(base);
      assert.equal(result.proof_verified, true);
      assert.equal(result.provenance, 'SYNTHETIC_PREFLIGHT');
      assert.equal(result.circuit_version, CIRCUIT_VERSION);
    });
  });

  await t.test('preflight rejects a fake proof behind a healthy endpoint', async () => {
    const transport = async (url) => new Response(JSON.stringify(
      url.endsWith('/health')
        ? { status: 'ready', circuitVersion: CIRCUIT_VERSION }
        : { circuitVersion: CIRCUIT_VERSION, proof: {}, publicSignals: ['7', '2'] }
    ), { status: 200, headers: { 'content-type': 'application/json' } });
    await assert.rejects(checkProver('http://fake.invalid', transport), /verification failed/);
  });

  await t.test('preflight refuses unready or wrong-circuit health', async () => {
    for (const health of [
      { status: 'starting', circuitVersion: CIRCUIT_VERSION },
      { status: 'ready', circuitVersion: 'invoice_limit@2' },
    ]) {
      await assert.rejects(checkProver('http://fake.invalid',
        async () => new Response(JSON.stringify(health))), /not ready/);
    }
  });
  await t.test('proves an invoice within the acknowledged ceiling', async () => {
    await withServer(async (base) => {
      const response = await post(base, JSON.stringify({ invoiceAmount: '45000000', financingLimit: '50000000' }));
      assert.equal(response.status, 200);
      const body = await response.json();
      assert.equal(body.circuitVersion, CIRCUIT_VERSION);
      assert.equal(body.publicSignals.length, 2);
      assert.equal(body.publicSignals[1], '50000000');
      assert.equal(await verifyInvoiceLimit(body.proof, body.publicSignals), true);
    });
  });

  await t.test('hides the amount: equal statements yield different commitments', async () => {
    await withServer(async (base) => {
      const statement = JSON.stringify({ invoiceAmount: '45000000', financingLimit: '50000000' });
      const [first, second] = await Promise.all([post(base, statement), post(base, statement)]);
      const a = await first.json();
      const b = await second.json();
      assert.notEqual(a.publicSignals[0], b.publicSignals[0]);
    });
  });

  await t.test('refuses to prove an invoice above the ceiling', async () => {
    await withServer(async (base) => {
      const response = await post(base, JSON.stringify({ invoiceAmount: '50000001', financingLimit: '50000000' }));
      assert.equal(response.status, 422);
      assert.equal((await response.json()).code, 'STATEMENT_FALSE');
    });
  });

  await t.test('rejects non-string amounts that would round', async () => {
    await withServer(async (base) => {
      const response = await post(base, JSON.stringify({ invoiceAmount: 45000000, financingLimit: '50000000' }));
      assert.equal(response.status, 400);
      assert.equal((await response.json()).code, 'INVALID_STATEMENT');
    });
  });

  await t.test('rejects a non-JSON content type and unknown routes', async () => {
    await withServer(async (base) => {
      const wrongType = await post(base, 'invoiceAmount=1', { 'content-type': 'text/plain' });
      assert.equal(wrongType.status, 415);
      assert.equal((await fetch(`${base}/nope`)).status, 404);
      assert.equal((await fetch(`${base}/health`)).status, 200);
    });
  });
});
