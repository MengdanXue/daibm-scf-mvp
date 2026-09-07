// Synthetic diagnostic only: does not modify application or ledger state.
import { pathToFileURL } from 'node:url';
import { verifyInvoiceLimit } from '../index.mjs';
import { CIRCUIT_VERSION } from './server.mjs';

export async function checkProver(base, transport = fetch) {
  const origin = new URL(base);
  if (!['http:', 'https:'].includes(origin.protocol) || origin.username ||
      origin.password || origin.pathname !== '/' || origin.search || origin.hash) {
    throw new Error('Expected an HTTP(S) service origin without credentials');
  }
  async function request(path, options = {}) {
    const response = await transport(origin.origin + path, {
      ...options, signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) throw new Error('Prover HTTP check failed: ' + response.status);
    return response.json();
  }
  const health = await request('/health');
  if (health.status !== 'ready' || health.circuitVersion !== CIRCUIT_VERSION) {
    throw new Error('Prover is not ready for the expected circuit');
  }
  const evidence = await request('/proofs/invoice-limit', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ invoiceAmount: '1', financingLimit: '2' }),
  });
  if (evidence.circuitVersion !== CIRCUIT_VERSION ||
      evidence.publicSignals?.[1] !== '2' ||
      !await verifyInvoiceLimit(evidence.proof, evidence.publicSignals)) {
    throw new Error('Synthetic proof verification failed');
  }
  return {
    provenance: 'SYNTHETIC_PREFLIGHT', circuit_version: CIRCUIT_VERSION,
    proof_verified: true, statement: '1 <= 2 (minor units)',
    limitations: 'No business workflow, Fabric consensus, or production-security claim',
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  checkProver(process.argv[2] || 'http://127.0.0.1:8091')
    .then(result => console.log(JSON.stringify(result)))
    .catch(error => { console.error(error.message); process.exitCode = 1; });
}
