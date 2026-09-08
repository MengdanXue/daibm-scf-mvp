# Reconcile the historical revision 0010 collision

The local lifecycle branch and remote invoice-proof branch both shipped
`20260824_0010` with different DDL. A version string alone cannot distinguish
them. Do not stamp, drop a column, clear a volume, or recreate a database to
resolve this collision.

## Supported upgrade paths

The historical lifecycle path continues through `20260907_0012` and
`20260907_0013`. The independently published anchor-version revision
`20260907_0011` still branches from `20260824_0010`. Both paths now converge at
the single merge head `20260908_0014`; neither published parent was rewritten.
See [the unified acceptance runbook](lifecycle-anchor-integration.md) for the
full graph, both deployed-head upgrade checks, and the isolated stack commands.

| Existing marker and verified schema | Upgrade behavior |
| --- | --- |
| 0009, intact baseline | Original lifecycle DDL, recovery index, proof column/checks |
| 0010, intact remote proof schema | Apply missing original lifecycle DDL before recovery; preserve proof column/values |
| 0010, intact local lifecycle schema | Recovery index, then proof column/checks |
| 0011, intact local lifecycle/recovery schema | Proof column/checks only |
| 20260907_0013, PR #5 lifecycle head | Anchor-version checks, then merge head |
| 20260907_0011, PR #6/#7 anchor-version head and historical proof schema | Missing lifecycle/recovery/proof/episode path, then merge head |

`0012` is the historical remote proof migration under an unambiguous identity.
It verifies an existing proof contract instead of adding the same column twice.
There is no direct `alembic_version` rewrite: Alembic advances it transactionally.

## Before a deployed upgrade

1. Stop application writers and workers for a maintenance window. Locate the
   actual named database and artifact volumes. Keep all original artifacts.
2. Take a verified PostgreSQL backup and artifact-volume backup. Record file
   hashes, database version, `SELECT version_num FROM alembic_version`, and
   `pg_dump --schema-only` from that exact database. If the original engine is
   unavailable, stop here; a new test database is not a backup of the old one.
3. Restore the backup into a separately named clone. Record ordered ledger,
   outcome, correction and artifact manifests before upgrading the clone.
4. Run `python -m alembic upgrade head` against the clone with the normal
   `POSTGRES_*` settings. Verify the version, both feature schemas,
   immutable manifests, monetary totals and proof ceilings. Test application
   and worker behavior and recreation with the same cloned volumes.
5. Only after clone verification and backup verification, upgrade the original
   database in the same maintenance window. Do not delete the source volumes.

## Fail-closed structural checks

`app/migration_compatibility.py` compares the affected historical tables against
frozen PostgreSQL 17 catalog fingerprints in
`alembic/historical_0010_contract.json`. These cover column names/types/nullability/
defaults, exact named constraint definitions and validation state, index
definitions/validity, and trigger definitions/functions/enabled state. Proof
and recovery objects have independent fingerprints. The original lifecycle
DDL remains in `upgrade_lifecycle_schema`; it does not use current ORM metadata.

The check takes a transaction-scoped advisory lock and exclusive locks on the
affected existing tables before inspecting them. Any partial or modified
contract, including disabled history triggers or changed check definitions,
fails with `incompatible historical schema` and rollback. Unknown 0010 with
neither feature family is explicitly rejected. Do not regenerate fingerprints
from a deployed database to make an error disappear.

The frozen contracts were captured from the historical migration DDL on an
isolated PostgreSQL 17.11 server. They intentionally reject unreviewed schema
customizations. Non-public schemas or another PostgreSQL major version may
deparse catalog definitions differently and require separate review on a
clone, not bypassing the guard. Offline SQL generation is not supported for
this schema-sensitive reconciliation; run online against the inspected clone.

`python -m scripts.capture_historical_migration_contract` reproduces the
reference JSON on an EMPTY `integration_test_*` database selected by
`TEST_POSTGRES_URL`; it rolls its DDL back and prints JSON for review. This is
not an automatic update command.

## Tests and rollback

The default test backend remains a disposable PostgreSQL 17 testcontainer.
Alternatively, `TEST_POSTGRES_URL` can select an isolated real PostgreSQL
database with driver `postgresql+psycopg` and a `daibm_test_` or
`integration_test_` prefix. Never point it at an application database: ordinary
test fixtures truncate their test data. Migration tests create and drop only
new random `integration_test_*` databases on that server.

Run `python -m pytest tests/test_integration_migration.py
tests/test_lifecycle_governance_migration.py tests/test_self_training_migration.py
tests/test_unified_migration.py -q`.

Downgrade is not a data-recovery strategy. Proof downgrade locks before checking
and refuses to drop any non-null historical payable ceiling. Lifecycle
downgrade already refuses governed history; recovery downgrade refuses duplicate
dataset runs. Restore a verified backup into another database if operational
rollback is needed; keep the failed-upgrade database for diagnosis.
