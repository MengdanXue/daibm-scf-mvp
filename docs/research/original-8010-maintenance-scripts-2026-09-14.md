# Existing demo maintenance preparation — 2026-09-14

This report covers the maintenance-script gate before any original-volume
backup or upgrade. It is not evidence that the original demo has been upgraded.
The application baseline is merged main `27e56fdcfa76e4613789dd67323b797473b15c1f`,
tree `5d661b30cfbcb9bfb3b4fdb51f3ebf95512d5a5b`; all four jobs in the historical
[main CI run](https://github.com/MengdanXue/daibm-scf-mvp/actions/runs/34187124288)
passed for that exact head. No application behavior or migration was redeveloped.

## Risks addressed

- Independent historical backup wrappers restarted application writes between
  database and Fabric backups. The new cold-backup procedure instead requires
  every pinned project writer to remain stopped throughout all stores and never
  starts or stops a service itself. Identity, image, mounted source, restart policy,
  foreign writable mounts, archive content/metadata and final source state are
  checked. Failed backups have no completion marker; existing targets are refused.
- The general fresh-network deployment path could fetch/create/join a channel
  after a failed getinfo and treat failed querycommitted as sequence zero.
  `--existing-channel-only --expected-sequence N` now rejects every failed state
  read, checks the known sequence before packaging, preserves historical block and
  package files, rereads state before approval, requires readiness and verifies the
  final definition. Exact package/definition repetition is a no-op.
- Historical preservation evidence now includes per-primary-key hashes of all
  original columns and complete sequence state, including `is_called`. After
  synthetic acceptance, new rows can be admitted without concealing changed or
  deleted original rows.
- Gateway outage acceptance now takes explicit loopback URL/gateway/CLI targets,
  verifies local Compose scope and pins IDs/images. Its finally block restores the
  same gateway and verifies readiness, including a stop-command timeout.

## Fresh validation

From the new maintenance checkout, using the existing Python environment:

```powershell
# Reject any inherited TEST_POSTGRES_URL; do not point fixtures at a business DB.
$env:DOCKER_HOST='npipe:////./pipe/dockerDesktopLinuxEngine'
$env:RUN_ZKP_WORKFLOW_INTEGRATION='1'
$env:DAIBM_RUN_COLD_BACKUP_DOCKER_TEST='1'
$env:DAIBM_BACKUP_HELPER_IMAGE='sha256:8325d9321f4bd35eb3c47b352987559450f87f2e1b7f1a7297ff5b8225983c04'
python -m pytest tests -o addopts='' -q --tb=short --junitxml=output/maintenance-full-tests.xml
```

Result: **759 passed in 425.75 seconds, zero failures/errors/skips**. This includes
real HTTP/Groth16 proof verification, disposable PostgreSQL integration and the
opt-in cold-backup Docker test. The latter used only uniquely labelled synthetic
resources and checked bytes, file and root UID/GID/mode, and an unchanged stopped
source. Its temporary container/volume were verified by exact ID/name and label
before cleanup. No original or pre-existing clone was started for these tests.

The shell deployment suite separately passed **22 cases** in a disposable,
network-disabled Fabric-tools container with the scripts mounted read-only. The
CI workflow now runs this suite. Ruff passed and mypy passed all 63 app files.
Focused Python counts (28 backup unit tests, one real backup test, three snapshot
tests and 17 outage tests) are included in the 759, not additional tests.

## Runtime image provenance

All copied runtime paths from the Dockerfiles were checked against this checkout:

| Image | Checked files | Result |
| --- | ---: | --- |
| app `8325d9321f4b` | 123 | 115 LF-normalized source files and eight exact artifact files match |
| gateway `d7009e762f35` | 10 | Nine LF-normalized source files and exact package lock match |
| prover `cdcb633d6c03` | 16 | Nine LF-normalized source files and seven exact lock/artifact files match |

Only the app merge-migration file had a raw CRLF/LF difference. This source audit
does not claim verification of every installed dependency or base-image file.

## State at this gate

Docker Desktop 4.90.0 / Linux Engine 29.7.2 has recovered. The engine inventory
shows 34 existing containers, 26 volumes and 26 images; existing containers have
restart policy `no`, and none auto-started. The original 8010, prior integration
and prior rehearsal resources remain stopped. Fresh clone configuration uses
port 8030 and distinct names, networks and data targets with bootstrap disabled.

Original database/Fabric backup, restored-clone verification, original migration,
original contract upgrade and deployment acceptance are **not yet performed** at
this gate. Private plans, backups, identity material and session data remain local.
After these scripts are committed, synchronized and reported, follow the
[existing-environment runbook](../runbooks/original-environment-upgrade.md).
