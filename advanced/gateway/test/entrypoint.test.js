'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { it } = require('node:test');

it('copies root-only mounted credentials to tmpfs and drops to the node user', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'gateway-entrypoint-'));
  const certDir = path.join(root, 'certs');
  const keyDir = path.join(root, 'keys');
  const tlsDir = path.join(root, 'tls');
  const runtime = path.join(root, 'runtime');
  fs.chmodSync(root, 0o755);
  fs.mkdirSync(certDir);
  fs.mkdirSync(keyDir);
  fs.mkdirSync(tlsDir);
  fs.writeFileSync(path.join(certDir, 'cert.pem'), 'certificate');
  fs.writeFileSync(path.join(keyDir, 'priv_sk'), 'private-key', { mode: 0o600 });
  fs.writeFileSync(path.join(tlsDir, 'ca.crt'), 'tls-ca');

  const result = spawnSync('bash', ['/work/docker-entrypoint.sh', 'node', '-e', [
    "const fs=require('node:fs')",
    "process.stdout.write([process.getuid(), fs.readFileSync(process.env.FABRIC_IDENTITY_KEY_DIR + '/key.pem','utf8')].join(':'))",
  ].join(';')], {
    encoding: 'utf8',
    env: {
      ...process.env,
      FABRIC_SOURCE_CERT_DIR: certDir,
      FABRIC_SOURCE_KEY_DIR: keyDir,
      FABRIC_SOURCE_TLS_CERT: path.join(tlsDir, 'ca.crt'),
      FABRIC_RUNTIME_ROOT: runtime,
    },
  });

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, '1000:private-key');
  assert.equal(fs.statSync(path.join(keyDir, 'priv_sk')).mode & 0o777, 0o600);
});
