// Run inside the built, read-only production image; synthetic inputs only.
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { verifyArtifactManifest, verifyInvoiceLimit } from '../index.mjs';
import { createProverServer } from '../src/server.mjs';

await verifyArtifactManifest();
const server = createProverServer();
server.listen(0, '127.0.0.1');
await once(server, 'listening');
const origin = `http://127.0.0.1:${server.address().port}`;
try {
  const health = await fetch(`${origin}/health`).then(response => response.json());
  assert.equal(health.status, 'ready');
  const response = await fetch(`${origin}/proofs/invoice-limit`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ invoiceAmount: '120000000', financingLimit: '150000000' }),
    signal: AbortSignal.timeout(30_000),
  });
  assert.equal(response.status, 200);
  const evidence = await response.json();
  assert.equal(evidence.circuitVersion, 'invoice_limit@1');
  assert.equal(evidence.publicSignals[1], '150000000');
  assert.equal(await verifyInvoiceLimit(evidence.proof, evidence.publicSignals), true);
  const changedSignals = [...evidence.publicSignals];
  changedSignals[1] = '150000001';
  assert.equal(await verifyInvoiceLimit(evidence.proof, changedSignals), false);
  const changedProof = structuredClone(evidence.proof);
  changedProof.pi_a[0] = '0';
  assert.equal(await verifyInvoiceLimit(changedProof, evidence.publicSignals), false);
  const falseStatement = await fetch(`${origin}/proofs/invoice-limit`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ invoiceAmount: '3', financingLimit: '2' }),
  });
  assert.equal(falseStatement.status, 422);
  assert.equal((await falseStatement.json()).code, 'STATEMENT_FALSE');
  console.log(JSON.stringify({
    provenance: 'SYNTHETIC_CONTAINER_ACCEPTANCE',
    manifest_verified: true, http_proof_verified: true,
    changed_public_signal_rejected: true, changed_proof_rejected: true,
    false_statement_rejected: true,
    demo_trusted_setup: true,
  }));
} finally {
  server.close();
  server.closeAllConnections();
  await once(server, 'close');
}
