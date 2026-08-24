import { createHash } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const ARTIFACTS = join(ROOT, 'artifacts');
const CANONICAL_MANIFEST_SHA256 = '3f3bca7e364034fd0baa0c430c23e414749b803cde76e07e0d63c3b87006d70c';
const EXPECTED_FILES = [
  'invoice_limit.r1cs',
  'invoice_limit.sym',
  'invoice_limit.wasm',
  'invoice_limit_final.zkey',
  'verification_key.json',
];

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

export async function verifyArtifactManifest({ artifactsDirectory = ARTIFACTS } = {}) {
  const manifestBytes = await readFile(join(artifactsDirectory, 'manifest.json'));
  const manifestDigest = createHash('sha256').update(manifestBytes).digest('hex');
  assert(manifestDigest === CANONICAL_MANIFEST_SHA256, 'artifact manifest SHA-256 mismatch');
  const manifest = JSON.parse(manifestBytes.toString('utf8'));
  assert(manifest.schema_version === 1, 'unsupported artifact manifest schema');
  assert(manifest.circuit === 'invoice_limit', 'unexpected circuit name');
  assert(manifest.protocol === 'groth16', 'unexpected proof protocol');
  assert(manifest.curve === 'bn128', 'unexpected proof curve');
  assert(manifest.demo_trusted_setup === true, 'manifest must disclose demo trusted setup');
  assert(manifest.compiler?.version === '2.2.3', 'manifest compiler version is not Circom 2.2.3');
  assert(manifest.compiler?.npm_wrapper === 'circom2@0.2.23', 'manifest compiler wrapper changed');
  assert(manifest.prover?.version === '0.7.6', 'manifest prover version is not snarkjs 0.7.6');
  assert(manifest.circomlib_version === '2.0.5', 'manifest circomlib version changed');
  assert(manifest.amount_encoding === 'unsigned-64-bit-minor-units', 'manifest amount encoding changed');
  assert(JSON.stringify(manifest.public_signals) === JSON.stringify(['commitment', 'financingLimit']), 'public signal order is not [commitment, financingLimit]');
  assert(JSON.stringify(manifest.private_inputs) === JSON.stringify(['invoiceAmount', 'salt']), 'private input contract changed');

  const expectedSources = ['circuits/invoice_limit.circom', 'package-lock.json'];
  assert(JSON.stringify(Object.keys(manifest.sources ?? {}).sort()) === JSON.stringify(expectedSources), 'source hash contract changed');
  for (const name of expectedSources) {
    const bytes = await readFile(join(ROOT, name));
    const entry = manifest.sources[name];
    assert(entry.sha256 === createHash('sha256').update(bytes).digest('hex'), `${name} SHA-256 mismatch`);
    assert(entry.bytes === bytes.byteLength, `${name} byte length mismatch`);
  }

  const declared = Object.keys(manifest.artifacts ?? {}).sort();
  assert(JSON.stringify(declared) === JSON.stringify(EXPECTED_FILES), 'artifact manifest has a missing or undeclared file');
  const actualFiles = (await readdir(artifactsDirectory)).filter((name) => name !== 'manifest.json').sort();
  assert(JSON.stringify(actualFiles) === JSON.stringify(EXPECTED_FILES), 'artifact directory has a missing or untracked file');

  for (const name of EXPECTED_FILES) {
    const bytes = await readFile(join(artifactsDirectory, name));
    const entry = manifest.artifacts[name];
    const digest = createHash('sha256').update(bytes).digest('hex');
    assert(/^[0-9a-f]{64}$/.test(entry.sha256), `${name} has an invalid SHA-256 declaration`);
    assert(entry.sha256 === digest, `${name} SHA-256 mismatch`);
    assert(entry.bytes === bytes.byteLength, `${name} byte length mismatch`);
  }
  return manifest;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await verifyArtifactManifest();
  console.log('Invoice-limit artifact manifest verified.');
}
