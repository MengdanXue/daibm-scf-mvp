import assert from 'node:assert/strict';
import { cp, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { describe, it } from 'node:test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  proveInvoiceLimit,
  verifyArtifactManifest,
  verifyInvoiceLimit,
} from '../index.mjs';

describe('invoice-limit Groth16 proof', () => {
  it('proves a committed amount within the public limit', async () => {
    const result = await proveInvoiceLimit({
      invoiceAmount: 120000000n,
      financingLimit: 150000000n,
      salt: 73421n,
    });

    assert.equal(await verifyInvoiceLimit(result.proof, result.publicSignals), true);
    assert.equal(result.publicSignals.length, 2);
    assert.equal(result.publicSignals[1], '150000000');
  });

  it('rejects an invoice amount above the financing limit', async () => {
    await assert.rejects(
      () => proveInvoiceLimit({
        invoiceAmount: 150000001n,
        financingLimit: 150000000n,
        salt: 1n,
      }),
      /does not satisfy constraint|exceeds financing limit/i,
    );
  });

  it('rejects unsigned values outside 64 bits before witness generation', async () => {
    const outside64Bits = 2n ** 64n;

    await assert.rejects(
      () => proveInvoiceLimit({
        invoiceAmount: outside64Bits,
        financingLimit: outside64Bits - 1n,
        salt: 1n,
      }),
      /invoiceAmount.*unsigned 64-bit/i,
    );
    await assert.rejects(
      () => proveInvoiceLimit({
        invoiceAmount: 1n,
        financingLimit: outside64Bits,
        salt: 1n,
      }),
      /financingLimit.*unsigned 64-bit/i,
    );
    await assert.rejects(
      () => proveInvoiceLimit({
        invoiceAmount: -1n,
        financingLimit: 1n,
        salt: 1n,
      }),
      /invoiceAmount.*unsigned 64-bit/i,
    );
  });

  it('rejects tampering with either ordered public signal', async () => {
    const result = await proveInvoiceLimit({
      invoiceAmount: 42n,
      financingLimit: 100n,
      salt: 9n,
    });

    const changedCommitment = [...result.publicSignals];
    changedCommitment[0] = (BigInt(changedCommitment[0]) + 1n).toString();
    assert.equal(await verifyInvoiceLimit(result.proof, changedCommitment), false);

    const changedLimit = [...result.publicSignals];
    changedLimit[1] = '101';
    assert.equal(await verifyInvoiceLimit(result.proof, changedLimit), false);
  });

  it('verifies every fixed artifact against the SHA-256 manifest', async () => {
    const manifest = await verifyArtifactManifest();

    assert.equal(manifest.demo_trusted_setup, true);
    assert.equal(manifest.public_signals.join(','), 'commitment,financingLimit');
    assert.ok(Object.keys(manifest.artifacts).length >= 5);

    for (const [relativePath, expected] of Object.entries(manifest.artifacts)) {
      const bytes = await readFile(new URL(`../artifacts/${relativePath}`, import.meta.url));
      const actual = createHash('sha256').update(bytes).digest('hex');
      assert.equal(actual, expected.sha256);
      assert.equal(bytes.byteLength, expected.bytes);
    }
  });

  it('rejects an artifact whose bytes no longer match the manifest', async () => {
    const temporaryRoot = await mkdtemp(join(tmpdir(), 'invoice-limit-artifacts-'));
    const artifactCopy = join(temporaryRoot, 'artifacts');
    try {
      await cp(new URL('../artifacts', import.meta.url), artifactCopy, { recursive: true });
      const wasmPath = join(artifactCopy, 'invoice_limit.wasm');
      const original = await readFile(wasmPath);
      await writeFile(wasmPath, Buffer.concat([original, Buffer.from([0])]));

      await assert.rejects(
        () => verifyArtifactManifest({ artifactsDirectory: artifactCopy }),
        /invoice_limit\.wasm SHA-256 mismatch/,
      );
    } finally {
      await rm(temporaryRoot, { recursive: true, force: true });
    }
  });
});
