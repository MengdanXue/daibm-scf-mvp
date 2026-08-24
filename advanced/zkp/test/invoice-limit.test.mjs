import assert from 'node:assert/strict';
import { cp, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { describe, it } from 'node:test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { groth16 } from 'snarkjs';

import {
  proveInvoiceLimit,
  verifyArtifactManifest,
  verifyInvoiceLimit,
} from '../index.mjs';

const BN128_SCALAR_FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617n;
const RAW_WASM = fileURLToPath(new URL('../artifacts/invoice_limit.wasm', import.meta.url));
const RAW_ZKEY = fileURLToPath(new URL('../artifacts/invoice_limit_final.zkey', import.meta.url));

async function expectRawProofRejected(proofFactory) {
  const originalError = console.error;
  console.error = () => {};
  try {
    await assert.rejects(proofFactory);
  } finally {
    console.error = originalError;
  }
}

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

  it('enforces limit and 64-bit decomposition inside the raw circuit', async () => {
    const rawProve = (invoiceAmount, financingLimit) => groth16.fullProve(
      { invoiceAmount, financingLimit, salt: 1n },
      RAW_WASM,
      RAW_ZKEY,
      undefined,
      undefined,
      { singleThread: true },
    );

    await expectRawProofRejected(() => rawProve(101n, 100n));
    await expectRawProofRejected(() => rawProve(2n ** 64n, (2n ** 64n) - 1n));
  });

  it('accepts exact unsigned and canonical-field upper boundaries', async () => {
    const uint64Max = (2n ** 64n) - 1n;
    const result = await proveInvoiceLimit({
      invoiceAmount: uint64Max,
      financingLimit: uint64Max,
      salt: BN128_SCALAR_FIELD - 1n,
    });

    assert.equal(await verifyInvoiceLimit(result.proof, result.publicSignals), true);
    await assert.rejects(
      () => proveInvoiceLimit({ invoiceAmount: 0n, financingLimit: 0n, salt: -1n }),
      /salt.*canonical BN128/i,
    );
    await assert.rejects(
      () => proveInvoiceLimit({ invoiceAmount: 0n, financingLimit: 0n, salt: BN128_SCALAR_FIELD }),
      /salt.*canonical BN128/i,
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

  it('rejects a manifest that changes the fixed encoding contract', async () => {
    const temporaryRoot = await mkdtemp(join(tmpdir(), 'invoice-limit-manifest-'));
    const artifactCopy = join(temporaryRoot, 'artifacts');
    try {
      await cp(new URL('../artifacts', import.meta.url), artifactCopy, { recursive: true });
      const manifestPath = join(artifactCopy, 'manifest.json');
      const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
      manifest.amount_encoding = 'unbounded-field-element';
      await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);

      await assert.rejects(
        () => verifyArtifactManifest({ artifactsDirectory: artifactCopy }),
        /manifest SHA-256 mismatch|amount encoding/i,
      );
    } finally {
      await rm(temporaryRoot, { recursive: true, force: true });
    }
  });
});
