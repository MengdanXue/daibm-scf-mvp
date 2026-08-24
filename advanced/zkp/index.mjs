import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

import { curves, groth16 } from 'snarkjs';

import { verifyArtifactManifest as verifyManifest } from './scripts/verify-artifacts.mjs';

const UINT64_MAX = (2n ** 64n) - 1n;
const BN128_SCALAR_FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617n;
const WASM_PATH = fileURLToPath(new URL('./artifacts/invoice_limit.wasm', import.meta.url));
const PROVING_KEY_PATH = fileURLToPath(new URL('./artifacts/invoice_limit_final.zkey', import.meta.url));
const VERIFICATION_KEY_URL = new URL('./artifacts/verification_key.json', import.meta.url);
let singleThreadCurvePromise;
let artifactVerificationPromise;

async function ensureSingleThreadCurve() {
  if (!singleThreadCurvePromise) {
    singleThreadCurvePromise = curves.getCurveFromName('bn128', { singleThread: true });
  }
  // snarkjs verification does not expose a single-thread option. Supplying its
  // documented ffjavascript singleton keeps short-lived CLI/tests worker-free.
  globalThis.curve_bn128 = await singleThreadCurvePromise;
}

async function ensureArtifactsVerified() {
  if (!artifactVerificationPromise) {
    artifactVerificationPromise = verifyManifest();
  }
  return artifactVerificationPromise;
}

function requireBigInt(name, value) {
  if (typeof value !== 'bigint') {
    throw new TypeError(`${name} must be supplied as a bigint`);
  }
}

function requireUint64(name, value) {
  requireBigInt(name, value);
  if (value < 0n || value > UINT64_MAX) {
    throw new RangeError(`${name} must be an unsigned 64-bit integer`);
  }
}

function requireFieldElement(name, value) {
  requireBigInt(name, value);
  if (value < 0n || value >= BN128_SCALAR_FIELD) {
    throw new RangeError(`${name} must be a canonical BN128 scalar field element`);
  }
}

export async function proveInvoiceLimit({ invoiceAmount, financingLimit, salt }) {
  await ensureArtifactsVerified();
  requireUint64('invoiceAmount', invoiceAmount);
  requireUint64('financingLimit', financingLimit);
  requireFieldElement('salt', salt);
  if (invoiceAmount > financingLimit) {
    throw new RangeError('invoice amount exceeds financing limit');
  }

  const { proof, publicSignals } = await groth16.fullProve(
    {
      invoiceAmount: invoiceAmount.toString(),
      financingLimit: financingLimit.toString(),
      salt: salt.toString(),
    },
    WASM_PATH,
    PROVING_KEY_PATH,
    undefined,
    undefined,
    { singleThread: true },
  );

  if (publicSignals.length !== 2 || publicSignals[1] !== financingLimit.toString()) {
    throw new Error('compiled circuit violates the [commitment, financingLimit] public-signal contract');
  }
  return { proof, publicSignals };
}

export async function verifyInvoiceLimit(proof, publicSignals) {
  await ensureArtifactsVerified();
  if (!proof || !Array.isArray(publicSignals) || publicSignals.length !== 2) {
    return false;
  }
  try {
    await ensureSingleThreadCurve();
    const verificationKey = JSON.parse(await readFile(VERIFICATION_KEY_URL, 'utf8'));
    return await groth16.verify(verificationKey, publicSignals, proof);
  } catch {
    return false;
  }
}

export async function verifyArtifactManifest(options) {
  return verifyManifest(options);
}
