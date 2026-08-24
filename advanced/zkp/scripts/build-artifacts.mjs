import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { cp, mkdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BUILD = join(ROOT, 'build');
const ARTIFACTS = join(ROOT, 'artifacts');
const CIRCUIT = join(ROOT, 'circuits', 'invoice_limit.circom');
const CIRCOM = join(ROOT, 'node_modules', 'circom2', 'cli.js');
const SNARKJS = join(ROOT, 'node_modules', 'snarkjs', 'build', 'cli.cjs');
const BEACON = 'd7f5f0bf43fe3db7a7915d7ba2f674e6633eaed83beca78166cbe1f6301f4f2a';

function run(program, args) {
  const result = spawnSync(program, args, { cwd: ROOT, encoding: 'utf8' });
  if (result.status !== 0) {
    throw new Error(`command failed: ${program} ${args.join(' ')}\n${result.stdout}\n${result.stderr}`);
  }
}

async function hashEntry(path) {
  const bytes = await readFile(path);
  return {
    sha256: createHash('sha256').update(bytes).digest('hex'),
    bytes: (await stat(path)).size,
  };
}

await rm(BUILD, { recursive: true, force: true });
await rm(ARTIFACTS, { recursive: true, force: true });
await mkdir(BUILD, { recursive: true });
await mkdir(ARTIFACTS, { recursive: true });

run(process.execPath, [CIRCOM, CIRCUIT, '--r1cs', '--wasm', '--sym', '-o', BUILD]);

const ptau0 = join(BUILD, 'powersOfTau_0000.ptau');
const ptauBeacon = join(BUILD, 'powersOfTau_beacon.ptau');
const ptauPhase2 = join(BUILD, 'powersOfTau_phase2.ptau');
const zkey0 = join(BUILD, 'invoice_limit_0000.zkey');
const zkeyFinal = join(BUILD, 'invoice_limit_final.zkey');
const r1cs = join(BUILD, 'invoice_limit.r1cs');

run(process.execPath, [SNARKJS, 'powersoftau', 'new', 'bn128', '12', ptau0]);
run(process.execPath, [SNARKJS, 'powersoftau', 'beacon', ptau0, ptauBeacon, BEACON, '10']);
run(process.execPath, [SNARKJS, 'powersoftau', 'prepare', 'phase2', ptauBeacon, ptauPhase2]);
run(process.execPath, [SNARKJS, 'groth16', 'setup', r1cs, ptauPhase2, zkey0]);
run(process.execPath, [SNARKJS, 'zkey', 'beacon', zkey0, zkeyFinal, BEACON, '10']);

const destinations = {
  'invoice_limit.r1cs': r1cs,
  'invoice_limit.sym': join(BUILD, 'invoice_limit.sym'),
  'invoice_limit.wasm': join(BUILD, 'invoice_limit_js', 'invoice_limit.wasm'),
  'invoice_limit_final.zkey': zkeyFinal,
};
for (const [name, source] of Object.entries(destinations)) {
  await cp(source, join(ARTIFACTS, name));
}

run(process.execPath, [SNARKJS, 'zkey', 'export', 'verificationkey', join(ARTIFACTS, 'invoice_limit_final.zkey'), join(ARTIFACTS, 'verification_key.json')]);

const artifactNames = [...Object.keys(destinations), 'verification_key.json'].sort();
const artifacts = {};
for (const name of artifactNames) {
  artifacts[name] = await hashEntry(join(ARTIFACTS, name));
}

const manifest = {
  schema_version: 1,
  circuit: 'invoice_limit',
  protocol: 'groth16',
  curve: 'bn128',
  compiler: { name: 'circom', version: '2.2.3', npm_wrapper: 'circom2@0.2.23' },
  prover: { name: 'snarkjs', version: '0.7.6' },
  circomlib_version: '2.0.5',
  amount_encoding: 'unsigned-64-bit-minor-units',
  private_inputs: ['invoiceAmount', 'salt'],
  public_signals: ['commitment', 'financingLimit'],
  demo_trusted_setup: true,
  trust_boundary: 'Fixed local demo parameters; do not treat them as production ceremony evidence.',
  artifacts,
};
await writeFile(join(ARTIFACTS, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');

console.log(`Built and hashed ${artifactNames.length} invoice-limit artifacts in ${relative(ROOT, ARTIFACTS)}.`);
