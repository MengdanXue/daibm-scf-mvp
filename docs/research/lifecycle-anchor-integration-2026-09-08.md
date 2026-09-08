# Lifecycle + anchor integration acceptance — 2026-09-08

## Scope and source identity

Unified two independently developed histories in a new reviewable branch:

- PR #5 lifecycle/outcome/correction/calibration work:
  `a1de79ee4a88caa704bfc13a589f2a7b98b2e8f6`.
- PR #7, including PR #6 version-token contract and outage acceptance:
  `281234263c34881f79163943fb52f6ebe2fce131`.

Both source heads were rechecked unchanged before publication. The PR #5
source was retrieved through the GitHub connector because direct Git access
was unavailable. Every imported blob and subtree was checked against its
Git object hash; reconstructed root tree:
`154d09ed24a45191a748a900562cd8019fcac688`.

The local integration checkout is separate from the user's main research
checkout and the earlier Fabric worktree. Original running services at 8010,
their database/artifact volumes, and Fabric identities were not upgraded.
This report does not independently re-verify the other computer's original
volume; its earlier evidence remains in the dated PR #5 closeout report.

## Integration findings and resolutions

1. The source branches produced two Alembic heads. Added a no-op merge
   revision `20260908_0014`, retaining both published revision identities and
   parents. The historical proof-only `0010` still follows PR #5's structural
   compatibility checks before missing lifecycle DDL is applied.
2. Tests that assumed one historical revision was forever `head` now assert
   ancestry, or isolate that specific migration. Failed-migration assertions
   compare the complete version-row set, not an arbitrary scalar.
3. Added upgrade tests from actual PR #5 and PR #6 schema histories. They
   preserve all original columns of financing requests, outcomes, ledger and
   outbox rows, including Unicode payloads, old null circuit versions and
   `invoice_limit@1`; repeat upgrades are idempotent.
4. The real HTTP/Groth16 workflow test now checks that the persisted proof's
   full circuit version and hash reach the pending outbox in the same
   confirmation transaction.
5. Browser acceptance now accepts an explicit target URL and browser channel.
   Its narrow optional-404 filter recognizes the two exact deployment scopes
   and still rejects malformed scope, unexpected payloads, 500s and page errors.

The integration did not rewrite PR #5 financial, correction, worker or
deployment-gate business logic. Cross-module source review and regression
coverage retained these boundaries:

| Boundary | Evidence checked |
| --- | --- |
| Financial facts | Principal conservation; repeated default/restructure episodes preserve history; outcomes derive from governed closure |
| Exclusion | Append-only correction; exact persisted outcome scope and membership; active-member invalidation and job creation are atomic |
| Concurrency | Scope lock shared with activation; changed snapshots and expired worker leases cannot publish stale candidates |
| Rollback | Expected active ID; predecessor state and same scope; current eligible membership and verified artifact required |
| Inference failure | Invalid/missing active artifact falls back to the explicit baseline; no fabricated calibrated score |
| Training | Chronological 70/30 partition; fit earlier data only; exact scope, support, score-diversity and artifact-lineage checks |

Relevant suites are `test_outcome_service`, `test_calibration_jobs`,
`test_outcome_calibration`, `test_adaptive_risk`, `test_default_episode_migration`
and `test_unified_migration`. This is the integrating agent's review plus
automated/runtime evidence, not a separate independent human review.

## Local verification

Final full regression result: **710 passed in 447.91 seconds**, with
`RUN_ZKP_WORKFLOW_INTEGRATION=1`, zero failures/errors and zero skipped tests.
Ruff passed; mypy passed for 63 application files. The first complete run had
704 passes and one real-prover setup error caused by missing local `snarkjs`.
After restoring the locked dependencies, the focused real-proof/browser
helper run passed 12 tests; the 18 Node Groth16/server tests also passed.

Isolated stack: PostgreSQL 17, Node 24, Fabric 2.5.16, Docker Server 29.7.2,
Compose 5.5.0; application URL `http://127.0.0.1:8019`. Running database head
was read back as `20260908_0014`; the application requires a real proof.

Three real Edge/Playwright browser journeys passed:

- five-role creation, confirmation, risk assessment, decision and audit;
- financing creation, disbursement, two repayments and closure;
- result evidence submission, same-payload 503 retry, asynchronous job polling,
  lineage display, RU/ZH and mobile layout, failed-run UI rendering.

Outcome acceptance facility: `9fcb3704-236c-48c6-b694-cdfa98b4c4df`.
Its one negative controlled outcome produced a non-deployed candidate with
`temporal_insufficient_partition_sizes`, which is the expected fail-closed
result. This is not a demonstrated accuracy improvement or a live deployment.

Read-only defense preflight passed health, documents, UI and all five roles.
Screenshots are retained locally under `output/`: `five-role-acceptance.png`,
`facility-lifecycle-clone-acceptance.png`, `outcome-feedback-acceptance.png`,
and `integration-anchor-acceptance.png`.

The local prover image's clean npm build stalled on dependency installation.
For this local runtime acceptance only, existing dependency image
`sha256:232bbf91f9b477ffc33b032c7e859f7686527d238dcb224b6f874aaacabc40d3`
was reused after matching the current lock file SHA-256
`c7c6d3c0612837e0c4ce0d5b0d13c140179d09385ce73400211713ea019aa3b9`.
Current source, scripts and committed circuit artifacts were copied into a
separately tagged integration image; running `src/server.mjs` hash matched
the checkout (`e45b028aeb5b891351f38b05b20a5e3af38bc4d9e4977bb87fd2b564084ff756`).
Real proof preflight and persisted-proof verification passed. No dependency
version or production Dockerfile was changed. CI still uses clean `npm ci`
and the standard read-only prover image build.

## Real Fabric evidence

The isolated channel was freshly bootstrapped with separate identities and
peer ID. Chaincode package:
`audit-anchor_1:850fdf6d8ae02d0b4d6b14a1bd3728273b9f6eadf358e224df7c530f9219c491`.

Final acceptance at `2026-09-08T03:45:54.046745+00:00`:

- anchor: `dd449e3f-625e-5156-b326-f06d60be25b7`;
- application: `e2de82e8-e2ff-4208-be5c-21a5dc31b305`;
- event hash: `64796e0cbd6d91fc8be469d53c1d97bf847015f394234db91f0803a843d945d8`;
- proof hash: `6bbe3c0df5e235eedfcb8186cc5a78cee549dbc67e8e514151b22b785ff1177a`;
- full circuit version: `invoice_limit@1` in PostgreSQL/outbox and Fabric;
- persisted Groth16 proof independently verified; altered public signal rejected;
- 7 claimed, 7 anchored, 0 retryable, 0 permanent failures; subsequent claim 0;
- gateway record exactly matched independent peer CLI query;
- channel height `44 -> 51`; identical POST returned 200 and added zero blocks;
- mobile recent-anchor panel and final PostgreSQL ledger integrity verified.

An earlier run had completed the chain assertions but its UI check targeted a
proof event outside the panel's latest-twelve-record window. The acceptance
script was corrected to verify the proof through API/CLI and separately
check a visible latest UI row. No application history was changed or deleted;
the final run above used a new synthetic browser journey.

The gateway-outage experiment in PR #7 remains separate historical evidence
on the original runtime. This round does not claim a new outage test,
multi-organization endorsement, production security or independent verification
of invoice truth.

## Research and release boundary

This integration is engineering evidence for governed data, controlled model
deployment and auditability. Workflow scoring remains
`transparent_logistic_baseline_v0.1` with optional Platt calibration, not an
online-retrained TGNN. Existing TGNN reference metrics do not establish a
new cross-seed advantage. Synthetic policy outcomes do not establish causal
loss reduction. `external_verified` remains declared provenance, not an
independent external-data verification service.

No new calibration was forced active. No PR was merged or original database
upgraded here. Require the final unified-head CI checks and a release decision
before changing the original runtime. Reproduction and bounded routine-work
handoff are in [the runbook](../runbooks/lifecycle-anchor-integration.md).
