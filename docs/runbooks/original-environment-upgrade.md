# Existing demo: verified cold backup and original-ledger upgrade

This procedure updates only an explicitly selected local demo. Publishing a
script does not authorize another project's maintenance. Keep the exact source
container IDs, image IDs, mounted stores and private evidence on the local host.

## Freeze and back up one state

Identify the original app/database, Fabric peer/orderer/gateway/CLI/prover and
any original chaincode container. Verify the Docker context, IDs, published
ports, Compose ownership, mounts and restart policies before any operation.
Stop only the authorized project's writers in dependency order. If they were
already stopped, keep them stopped. Account seeding, research initialization,
deployment reconciliation and calibration workers run at application startup:
do not start the application before collecting the historical baseline.

Use `scripts/maintenance_backup.py` with an explicit private JSON plan:

```json
{
  "schema": "daibm.cold-backup.v1",
  "context": "desktop-linux",
  "helper_image": "sha256:<verified local Python/tar image ID>",
  "containers": {
    "<exact source name>": {"id": "<full container ID>", "image": "sha256:<image ID>"}
  },
  "stores": [
    {"name": "database", "type": "volume", "source": "<original PostgreSQL volume>",
     "container": "<exact source database>", "destination": "/var/lib/postgresql/data"}
  ]
}
```

Include the PostgreSQL volume, calibration artifacts, complete original Fabric
state/identity directory, and peer/orderer/CLI configuration volumes. The source
store must match an inspected mount of a pinned source container. Include all
project writers in `containers`, including those without a store entry.

```sh
python -m scripts.maintenance_backup --plan PRIVATE/plan.json --destination PRIVATE/new-backup
```

The script requires all pinned source containers stopped and restart disabled;
it does not change restart policies. It rejects other running containers with
writable overlapping stores. Each archive is streamed in binary, compared to a
per-file manifest including contents, owner, mode and modification time; all
stores are rechecked after the entire backup. The completion manifest is written
only after every archive and final freeze check pass. A failed directory is
incomplete evidence: preserve it and use a new output directory for a retry.

There is no automatic restart in success or failure paths. Keep the complete
source frozen throughout all database, artifact and Fabric backup operations.
Do not call the historical independent backup wrappers: they resume app writes
between steps. For an unclean prior shutdown, the physical backup retains its
WAL; PostgreSQL crash recovery is tested on an isolated restored copy first.

## Restore and verify before source mutation

Verify every completed-backup hash. Restore into newly created volumes and a
new Fabric directory; refuse any existing target. Verify the entire restored
file manifest before starting PostgreSQL or Fabric. Use the same PostgreSQL
major version/image for physical restore. External tablespaces/escaping links
require a separately complete restore plan and must not be silently omitted.

Give the clone a unique Compose project, network, peer ID, container names and
loopback-only port. Replace all Fabric state/identity/config mounts explicitly,
disable bootstrap and remove its dependency edges. Start named services using
`--no-deps`. Never overwrite or restart a previous rehearsal environment.

Capture the restored database using `scripts/maintenance_snapshot.py`; it reads
all original columns, per-primary-key row hashes, complete sequence state
including `is_called`, all Alembic version rows and ledger verification in a
read-only repeatable-read transaction. Keep this baseline and a logical dump
from the recovered clone privately. Repeat a logical restore into another new
test database if needed to independently verify logical recovery.

Run migration before starting the app. Pass the original column map through
`SNAPSHOT_COLUMNS` and use `compare_history` to verify original rows and sequence
states unchanged, excluding only `alembic_version`. Repeat migration and verify
idempotence. No `stamp`, forced downgrade, old-version backfill or event-hash
rewrite is allowed. Capture each old Fabric anchor and channel/definition state
from the restored old chain before applying any contract upgrade.

## Upgrade an existing channel only

Use the reviewed strict mode of `deploy-chaincode.sh` with the verified current
sequence, on the clone first and the original only after recovery checks pass.
An existing-channel read or deployment error must fail closed. No bootstrap,
identity generation, channel create/join or local history replacement is a
recovery fallback. Verify every historical anchor before/after upgrade and
repeat deployment to check the exact-package no-op causes no new blocks.

## Acceptance and handoff

Apply only the tested changes to the original, keeping its existing volumes,
identity files and ledger. Compare the original's pre-migration snapshot to the
restored baseline before applying migrations. Verify historical rows before app
startup, after startup and after explicitly synthetic acceptance records.
New records/sequence advances are permitted in acceptance; modifications or
deletions of baseline rows are not masked by total counts. Compare original
raw Fabric anchors individually, preserving timestamps as raw JSON values.

Verify five roles, actual Groth16 proof generation and independent verification,
tamper rejection, complete circuit version and proof hash in the outbox/chain,
gateway and peer CLI readback agreement, duplicate submission with no new block,
and only the selected gateway's bounded outage/retry/finally-restoration path.
Tests with truncation fixtures must use disposable test databases, never the
original or a restored business database. `/api/health` alone does not validate
the prover or Fabric gateway. Keep all private backups, keys and session evidence
local. Commit only necessary code and sanitized reports, verify CI for that
exact commit, and state which runtime stages remain incomplete.
