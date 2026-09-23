# Original 8010 recovery and controlled acceptance — 2026-09-20

This is a fresh local engineering acceptance, not a reuse of September 8/14
results or a paper revision. All new business records are **synthetic**, with
explicit `SYNTHETIC-20260920-original-...` or
`SYNTHETIC-20260920-original-outage-...` contract/invoice identifiers and private
manifests binding every new event/anchor to those applications.

## Result and actual deployed version

The original `127.0.0.1:8010` application is healthy. Its actual existing
deployment already contained main `27e56fdcfa76e4613789dd67323b797473b15c1f`;
therefore no redundant application replacement, database migration or contract
upgrade was performed. PR #8 functionality was not redeveloped. The new code
is operator-only, synthetic-whitelist acceptance tooling, not a new production
dispatch API. PR #9 remains the maintenance publication branch; this report
does not claim it has been merged.

| Component | Verified deployment after this run |
| --- | --- |
| App image | `sha256:8325d9321f4bd35eb3c47b352987559450f87f2e1b7f1a7297ff5b8225983c04` |
| Gateway image | `sha256:d7009e762f35ee6eb398baad7c44cd9674316b4e7c1dfbcf3b2ec4c666fd9746` |
| Prover image | `sha256:cdcb633d6c03dde9a979d952c62a1b15ff5b45f08dd8fff2d94f701ce1fc4185` |
| Complete database revisions | `[20260908_0014]`, unchanged |
| Fabric definition | `audit-anchor`, version `1.0`, sequence `2`, unchanged |
| Fabric package | `audit-anchor_1:850fdf6d8ae02d0b4d6b14a1bd3728273b9f6eadf358e224df7c530f9219c491` |
| Database audit events | 90 → 98; all eight additions are this run's synthetic evidence |
| Outbox | 41 anchored / 49 pending → 49 anchored / **49 historical pending unchanged** |
| Fabric channel height | 46 → 54; exactly eight new synthetic anchors |

Fresh deployment-source verification compared the actual files with Git blobs
from that main SHA: app 123/123 files present (9 byte-identical, 114 differing
only by CRLF/LF), gateway 10/10 runtime files present (1 byte-identical, 9 only
CRLF/LF). No non-line-ending content differences. All eight reference artifacts
are byte-identical. Prover source/artifact files also match the tested image.
This is not a claim that every deployment text file is Git-byte-identical or
that a new reproducible build was performed. Gateway test-vector fixtures are
not copied into the existing production image by its Dockerfile.

The peer automatically recreated its stateless chaincode runtime container
during initial readback. The image, package, network and empty mount list were
verified unchanged; this was not a lifecycle upgrade. Original Compose-managed
container IDs/images/mounts, ledger volumes and identities stayed in place.

## Recovery and backup evidence from this run

Docker initially failed to start with an inaccessible stale runtime socket.
After confirming the backend was stopped and the affected runtime directories
contained only five zero-byte communication entries, their entire directories
were renamed to preserved siblings and normal Docker startup succeeded. Nothing
was deleted; Docker settings, WSL disks, images and volumes were not reset.
This is a local recovery, not a guarantee against recurrence.

While all original writers were stopped, six stores were cold-backed up:
PostgreSQL, calibration artifacts, complete Fabric state/identities, and
peer/orderer/CLI configuration. Every archive was verified against per-file
content and metadata manifests, followed by a second source/freeze check.
All ten listed backup files were rehashed again after acceptance.

Fresh uniquely named volumes, networks, peer ID and the loopback 8040 clone
were created for this run. No old rehearsal destination was overwritten.
All six stores restored with exact content/metadata; PostgreSQL recovery,
existing migration-head startup, per-row/sequence preservation, and old Fabric
readback passed before original acceptance. The clone itself then passed the
same new full journey and gateway outage tests; these are separate from the
original results below.

Private completion-manifest SHA-256 values:

- Cold backup: `dd1175ad88387b2a582b83a78c2f2d7c0a6250845edeba77efbeaac27787df3b`
- Restore: `4757ea47ace9f7abe2a92f0c5e00f97ecbae66d2fc8ab4d400f7d04005674925`

Backups, keys, identities, sessions, screenshots and detailed row manifests
remain local and are not part of this commit.

## Original-environment controlled acceptance

The original full journey completed at 15:35:58 UTC; the original gateway
outage/recovery completed at 15:36:50 UTC on September 20.

| Fresh original check | Observed result |
| --- | --- |
| Five roles | Supplier → core enterprise → financier → risk → auditor completed in the browser |
| Real proof | Persisted Groth16 proof independently verified; its SHA-256 independently recomputed |
| Tampering | Modified public signal rejected by independent verification |
| Circuit and proof on chain | Full `invoice_limit@1` and exact persisted proof hash read back |
| Readback | All seven journey anchors match gateway and independent peer CLI reads |
| First dispatch | Seven new journey anchors, seven new blocks, no retry/permanent failure |
| Duplicate submission | HTTP 200 with identical record, zero new blocks; targeted redispatch claims zero |
| Gateway outage | Only pinned original gateway stopped; first synthetic draft dispatch retryable with `FABRIC_UNAVAILABLE` |
| Recovery | Same anchor succeeds on attempt 2 after retry delay; same gateway restored in `finally` and ready |
| Outage readback/duplicate | Gateway = peer; exactly one new block; duplicate adds zero |
| Migration state | Full revision list unchanged before/after acceptance |

Independent proof digest for this synthetic original journey:
`8db99d2732b84a916749cfb8a337b0178ce0799841c38b08454658909d52e262`.

The existing global dispatcher was never called. Its endpoint only accepts
`limit`; an extra `anchor_ids` field would not restrict dispatch. The new
maintenance adapter filters an immutable manifest whitelist in SQL before
claiming, retains due-time/lease/`SKIP LOCKED` semantics, and reuses the existing
dispatch state machine. Historical IDs, absent/mismatched targets, invalid
proof-envelope bindings and lost leases fail closed.

## Historical preservation, beyond counts

Every pre-maintenance primary key and every original column was compared by
per-row digest. All 290 baseline rows (including the separately checked
revision row) remain unchanged; current total is 316. Sequences only advanced
for new synthetic records. All 49 original pending rows retain every field,
including attempts, leases, retry times and errors. The audit chain is valid,
and no old event hash or circuit version was rewritten.

All 41 historical Fabric anchor raw JSON strings and hashes match. Committed
and approved definitions and installed package listing match. Historical byte
prefixes of all three original block files match their cold-backup hashes:
peer and application-channel orderer files only appended, and the system
channel block file is unchanged.

Calibration artifact storage (an empty existing directory), 123 non-state
Fabric identity/configuration entries, and peer/orderer/CLI configuration
manifests are unchanged. The eight packaged reference artifacts are separately
verified as above. All 43 other pre-existing containers retain their IDs,
images, mounts and running/start/finish states. No other project was stopped.

## Fresh automated verification

- Final complete Python suite: **799 passed, 0 skipped**, with real proof HTTP
  integration and the disposable real-Docker cold-backup test explicitly enabled.
  PostgreSQL fixtures used new Testcontainers resources, never original or
  restored business databases. The new whitelist tests passed red-to-green.
- Advanced suites: **22 network-script fixtures, 11 chaincode, 12 gateway and
  18 ZKP tests passed**, none skipped. Test containers had no network access,
  no historical volumes/identities and no Docker socket. Production prover
  artifact hashes and a separate read-only image acceptance also passed.
- `ruff check .`, application `mypy`, and `git diff --check` passed.
- Initial pre-final run: 705 application tests passed / 1 opt-in fixture skipped,
  and 65 research regression tests passed. These overlapping runs are **not**
  added to the final 799 count; the final suite enabled the skipped fixture.

One early clone preflight ran before readiness and failed to connect; it passed
after readiness. An earlier compound original-start command was rejected before
execution; a subsequent explicit normal start of the same pinned container
succeeded. Neither intermediate failure is represented as an earlier pass.

Remote CI must be checked against the exact commit containing this report after
publication. Historical green runs for main or the previous PR #9 head are not
this publication's result; the task handoff records the final head and checks.

## Boundaries retained

This passes this round's original-8010 local controlled acceptance, not general
production qualification. The 49 historical pending business records remain
intentionally unprocessed. PR #9 is not merged by this task. The new 8040 clone
and its evidence are retained locally; earlier replicas and other projects are
untouched. No public deployment, `down -v`, bootstrap of an existing chain,
historical backfill, ledger/hash rewrite, forced downgrade or `stamp` occurred.
