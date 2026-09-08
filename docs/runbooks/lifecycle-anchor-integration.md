# Unified lifecycle and anchor acceptance

This is an isolated development acceptance stack, not a production deployment.
Keep the original demo, PostgreSQL volumes, artifacts and Fabric identities.
The peer's development Docker socket still grants host-level container control.

## Migration contract

Published lifecycle path:
`20260824_0010 -> 20260824_0011 -> 20260907_0012 -> 20260907_0013`.

Published anchor-version path:
`20260824_0010 -> 20260907_0011`.

Merge revision `20260908_0014` has both terminal revisions as parents. It has
no DDL or data mutation of its own. Alembic applies the missing path before
recording the merge. A partially traversed graph can have two version rows;
do not treat an arbitrary scalar from `alembic_version` as the full state.

The historical `0010` proof/lifecycle collision still uses the existing
schema fingerprints and transactional fail-closed checks. The merge does
not bypass them or rewrite either published revision. No historical null
version is backfilled and no old event hash is recomputed.

Before upgrading an existing database, follow the backup, restored-clone,
writer quiescence and immutable-manifest checks in
[0010 compatibility](0010-compatibility.md). The tests here are not a backup
of any user's original volume. Do not use `stamp`, `down -v`, or schema edits
to make an incompatible history appear valid. Do not use downgrade as data
recovery; keep the failed database and restore a verified backup separately.

## Start a fresh isolated stack

Requirements: Docker Desktop Linux engine, Compose supporting `!reset` and
`!override`, Python dependencies, Node 24 and the locked npm dependencies.
Run from this checkout's root. Use a fresh worktree so `output/fabric` does
not contain another environment's identities or chain data.

PowerShell:

```powershell
$integrationApp = @('--context', 'desktop-linux', 'compose', '-f', 'docker-compose.yml', '-f', 'scripts/acceptance/compose.app.yml', '-p', 'daibm-integration-app')
$integrationFabric = @('--context', 'desktop-linux', 'compose', '-f', 'advanced/fabric/network/docker-compose.fabric.yml', '-f', 'scripts/acceptance/compose.fabric.yml', '-p', 'daibm-integration-fabric')
docker @integrationApp config
docker @integrationFabric config
docker @integrationApp up -d --build
docker @integrationFabric build gateway zkp-prover
docker @integrationFabric run --rm --no-deps bootstrap
docker @integrationFabric up -d --no-build --no-deps orderer.example.com peer0.org1.example.com cli gateway zkp-prover
docker @integrationFabric exec -T cli bash /network/deploy-chaincode.sh
docker @integrationFabric exec -T zkp-prover node src/preflight.mjs http://127.0.0.1:8091
```

Review resolved config before starting: app port is `127.0.0.1:8019`, app
network is `daibm-integration-app-network`, Fabric network is
`daibm-integration-fabric-network`, and the peer ID is
`integration.peer0.org1.example.com`. Database and artifact volumes have the
`daibm-integration-app_` prefix. Fabric state is this worktree's
`output/fabric`; orderer/peer ports are not published. The app requires real
proofs (`ZKP_PROOF_REQUIRED=true`). The original port 8010 and original
container names are not used by these overlays.

Bootstrap is for a fresh environment only. Never regenerate cryptographic
identities over an existing ledger. Keep existing `output/fabric` when
restarting the isolated stack, and start the named services with `--no-deps`.

## Verify

```powershell
$env:DOCKER_HOST = 'npipe:////./pipe/dockerDesktopLinuxEngine'
$env:RUN_ZKP_WORKFLOW_INTEGRATION = '1'
python -m ruff check .
python -m mypy app
python -m pytest tests -o addopts='' -q
python -m scripts.defense_preflight --base-url http://127.0.0.1:8019
python -m scripts.browser_acceptance --base-url http://127.0.0.1:8019 --browser-channel msedge
python -m scripts.facility_browser_acceptance --base-url http://127.0.0.1:8019 --browser-channel msedge
python -m scripts.outcome_browser_acceptance --base-url http://127.0.0.1:8019 --browser-channel msedge
python -m scripts.integration_anchor_acceptance --dispatch-synthetic-outbox --browser-channel msedge
```

Omit `--browser-channel msedge` where Playwright's Chromium is installed.
On Linux use the engine's normal Docker endpoint rather than the Windows
named pipe. Tests use disposable PostgreSQL databases, not the app volume.
Install `advanced/zkp` dependencies with `npm ci` before the opt-in real
HTTP/Groth16 test. No skipped test is evidence for a real proof.

Browser journeys create clearly synthetic records. The anchor acceptance
requires a pending proof event from a new journey and fewer than 200 recent
outbox records; use a fresh isolated environment for large repeated runs.
It verifies the persisted proof, rejects a changed public signal, dispatches
the outbox, compares gateway and independent peer CLI records, and checks
that an identical repeat POST creates no additional block. Its mobile UI
check uses a latest record because the panel renders only twelve records.

The outcome journey deliberately simulates one 503 response to test client
retry/idempotency and a failed-run response to test UI rendering; those are
not real service-outage evidence. The earlier gateway outage experiment is
documented separately and is not repeated by this script.

## Release boundary and handoff

Review the unified PR and require all four CI jobs on its final head.
Publishing this branch does not merge it or authorize an original-volume
upgrade. Any original-volume release needs its own verified backup/clone
acceptance and maintenance decision.

Routine follow-up work can use this explicit command set and the tests as
acceptance criteria. Keep changes to small tests, UI defects, documentation
or reproducibility fixes. Changes to migration ancestry, financial facts,
correction eligibility, deployment gates or research claims require another
cross-module review. Do not relax a gate just to turn a small demo dataset
into an active model.
