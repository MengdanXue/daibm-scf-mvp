# Task 2: chronological validation and exact scope isolation

Base: `95841d69d2de29220c0eb03d0106c75342305f4f` on `integration/lifecycle-scope-20260907`.

## Implemented policy

- New artifacts use `daibm.platt-calibration.v4`. Sort by canonical UTC observation timestamp, then canonical outcome UUID. Candidate boundary selection consults only timestamp groups and partition sizes: nearest 70% training target, earlier boundary on exact ties. Equal timestamps never straddle the boundary.
- Require at least 20 training and 10 validation observations; training has at least five distinct raw scores and raw-score span at least 0.05; both partitions have at least two positive and two negative labels. Support is checked only after size-only boundary selection; there is no label/performance-driven search for another boundary.
- Span comparison uses decimal representations to avoid rejecting the exact 0.15 minus 0.10 boundary because of binary floating-point subtraction. Artifact numeric serialization retains the established 15-significant-digit policy.
- Fit Platt coefficients exactly once on training observations only. Persist those coefficients; never refit on validation or the combined dataset. Promotion uses held-out Brier/log-loss only: neither may regress beyond 1e-12, and at least one must improve by more than 1e-12. No tuning for historical 0.5595 / 0.2499 results.
- Stable support rejection reasons: `temporal_insufficient_partition_sizes`, `temporal_insufficient_distinct_scores`, `temporal_insufficient_score_span`, `temporal_insufficient_training_class_support`, `temporal_insufficient_validation_class_support`. Nonempty rejected datasets retain a failed run, membership, generic failure code, specific activation reason, and immutable policy evidence; existing empty-dataset worker terminal behavior is unchanged.

## Durable evidence and compatibility

- Artifacts persist all canonical effective eligible observation snapshots, their correction heads, ordered disjoint training/validation UUIDs, training cutoff, validation start, partition counts/class support/distinct scores/span, policy, and validation metrics. Training metrics are diagnostic only.
- Worker membership includes every effective eligible observation and its captured correction head, including validation observations. Excluding any participating observation still invalidates the exact affected deployment. Existing scope locks, lease fencing, staged publication, retries and correction workflows remain in place.
- v4 loader checks canonical bytes, hashes, exact declared scope and observation provenance, dataset/count/correction lineage, canonical training configuration and coefficients, the deterministic time split, and recomputed held-out metrics. Recovery never invokes fitting. DB-backed candidate recovery preserves schema, coefficients, validation metrics, membership and configuration; inconsistent DB metrics still reject.
- Worker ledger saves temporal evidence and failed-data policy. The safe run response exposes `temporal_validation` and `validation_policy`; evidence remains accessible from the immutable worker ledger if the artifact is unavailable. Existing `oof_metrics_*` keys remain populated only for v3, never relabel temporal metrics as OOF.
- Existing v2/v3 loaders remain available at explicit safe scopes. Neither version can be newly promoted. Reusable-dataset lookup is restricted to v4, so a legacy artifact occupying the same dataset hash cannot short-circuit new training.
- Golden v3 fixture was generated without rewriting production artifacts, by executing `git show 95841d6:app/services/outcome_calibration.py` in an isolated in-memory module against the historical deterministic 20-row fixture (scores 0.05 + index * 0.045, labels index >= 10, original UUID/timestamp helper). Canonical payload SHA-256: `f3c41b086c7039c79e4f89123dbec6eac8d7c5df3c2e2392a10597fafba2a529`. The checked-in JSON has a transport newline stripped by the compatibility test. Test proves exact payload bytes stay readable at controlled_demo, reject external_verified, and are not promotable.
- No migration was necessary: new evidence uses existing artifact JSON, ledger JSON and run metadata. No historical migration, contract, outcome, repayment, dossier or user artifact bytes were modified. All tests used the explicit disposable integration database, not user volumes.

## Scope interfaces and downstream UI handoff

- Required inference interface: `assess(session, baseline_probability, assessment_scope)`.
- `OutcomeRepository.get_active_run(..., scope=...)`, `OutcomeService.get_active_deployment(user, scope=...)` and `rollback(expected_active_run_id, user, scope=...)` require explicit scope.
- Auditor GET `/api/v1/calibration-deployments/active?scope=controlled_demo|external_verified` requires the query parameter. Rollback body requires `deployment_scope` alongside `expected_active_run_id`. Missing/mixed scope is rejected by API validation; cross-scope active lookup/rollback cannot select the other deployment.
- Workflow inference uses persisted request scope. Legacy public financing creation is explicitly controlled_demo and now persists raw score and calibration/fallback lineage. Application request bodies cannot authorize external_verified. This remains a trusted human declaration, explicitly not cryptographic provenance.
- When only the other scope has an active deployment, inference retains baseline score with `calibration_scope_mismatch` and the attempted run UUID. Real workflow tests verify persisted fallback audit lineage. A missing deployment in both scopes remains ordinary uncalibrated baseline behavior. Corrupt artifacts retain `active_artifact_invalid` and attempted lineage.
- Rollback additionally verifies predecessor scope, eligible membership and artifact before changing either deployment. Valid previously deployed v2/v3 predecessors remain recoverable under their allowed scope; nonexistent/corrupt or excluded predecessors cannot be restored.
- UI changes are deliberately deferred to the next UI task: active requests must add the scope query; rollback must add its scope; show temporal validation/policy instead of OOF claims. Existing unscoped UI calls will receive 422 until updated.

## Verification record

Working directory for all commands: `D:/毕业论文/daibm-scf-mvp/.worktrees/lifecycle-corrections-scope`.

Test runtime: `D:/毕业论文/daibm-scf-mvp/.venv/Scripts/python.exe` (abbreviated `$testPython` below). Every DB test command sets:

```powershell
$env:TEST_POSTGRES_URL='postgresql+psycopg://integration_test@127.0.0.1:55437/integration_test_temporal'
```

RED evidence, before corresponding implementation:

1. `& $testPython -m pytest tests/test_temporal_calibration.py -q`: 9 failures against OOF/unscoped code (identity leakage/chronology, grouped boundary, size/ties/distinct/span/class support, required scope).
2. Same command after initial implementation plus mismatch/body tests: 9 passed, 2 failed (missing attempted mismatch lineage, rollback accepted omitted scope).
3. Same command after legacy service test: 11 passed, 1 failed (raw score absent on legacy financing model).
4. `... -m pytest tests/test_temporal_calibration.py -k temporal_fit -q --tb=short`: failed because persisted partition summaries were absent.
5. `... -m pytest tests/test_calibration_jobs.py::test_legacy_dataset_candidate_is_never_reused_for_temporal_training -q --tb=short`: failed because reusable lookup returned a legacy-schema record.
6. `... -m pytest tests/test_temporal_calibration.py -k exact_decimal -q --tb=short`: failed because exact 0.05 decimal span was rejected.

GREEN runs:

- Focused trainer/inference/scope run reached 87 passed before the final exact-span case.
- Full focused suite (trainer, inference, scope, repository/schema, worker, outcome service/API, workflow service/API): **182 passed in 175.44s**. Command:

```powershell
& $testPython -m pytest tests/test_temporal_calibration.py tests/test_outcome_calibration.py tests/test_adaptive_risk.py tests/test_outcome_repository.py tests/test_outcome_schema.py tests/test_calibration_jobs.py tests/test_outcome_service.py tests/test_outcome_api.py tests/test_workflow_service.py tests/test_workflow_api.py -o addopts='' -q --tb=short
```

- Legacy financing service/API/database plus current temporal/trainer/inference suites: **99 passed in 13.90s**, including the final exact-span case:

```powershell
& $testPython -m pytest tests/test_service.py tests/test_api.py tests/test_database.py tests/test_temporal_calibration.py tests/test_outcome_calibration.py tests/test_adaptive_risk.py -o addopts='' -q --tb=short
```

- Final combined run after semantic-preserving diff cleanup: **194 passed in 185.46s (0:03:05)**. This is the combined non-duplicated acceptance count; the preceding 182/99 runs overlap and are not additive.

```powershell
& $testPython -m pytest tests/test_temporal_calibration.py tests/test_outcome_calibration.py tests/test_adaptive_risk.py tests/test_outcome_repository.py tests/test_outcome_schema.py tests/test_calibration_jobs.py tests/test_outcome_service.py tests/test_outcome_api.py tests/test_workflow_service.py tests/test_workflow_api.py tests/test_service.py tests/test_api.py tests/test_database.py -o addopts='' -q --tb=short
```

- Final golden-checksum assertion: `& $testPython -m pytest tests/test_temporal_calibration.py -k v3_bytes -o addopts='' -q`: **1 passed, 15 deselected in 0.21s**.
- `C:/Python313/python.exe -m ruff check app tests`: passed.
- `C:/Python313/python.exe -m mypy app`: passed, all 63 source files.
- `& $testPython -m compileall -q app tests`: passed.
- `git diff --check`: passed. `git diff --name-only -- alembic/versions`: empty.

The venv lacks Ruff/mypy; the pre-existing `C:/Python313/python.exe` tooling was used without installing dependencies. An early broad run overlapped a small DB smoke test and produced two fixture/identity interference failures; those runs are not acceptance evidence. All final DB suites ran sequentially. Obsolete 20-row OOF and nonexistent-rollback-artifact fixture assumptions were updated to chronological 40-row data and a valid legacy predecessor, without relaxing the new policy.

## Files and limits

Production: `app/services/outcome_calibration.py`, `adaptive_risk.py`, `calibration_jobs.py`, `outcomes.py`, `workflow.py`; `app/repositories/outcomes.py`; `app/service.py`; `app/api/outcomes.py`; `app/schemas_outcome.py`. Regression coverage: temporal, calibration, adaptive inference, jobs, outcome service/API, workflow service, and the golden v3 fixture. Parent-owned integration plan update may accompany this commit.

No browser/UI, external training, TGNN retraining, production-volume migration, user-data correction or deployment was performed. Historical scores are not promised to improve or reproduce. The time split is based on canonical outcome `observed_at`; provenance is a human declaration, not a guarantee of independent real-world collection. Full v4 snapshots increase artifact size linearly with the eligible dataset. Independent review is queued by the parent after this feature commit.

## Fix round 1: sealed synthetic risk-event recovery compatibility

Base: `9d16e5954d6956b91a84c1a0db6362c5d85742f8`. Full CI run `34123582096` found two recovery regressions outside the original focused suite. The old recovery resolver reconstructed only the four-field baseline payload and compared baseline raw score with stored final score; new financing events seal five additional scope/calibration/fallback fields and may have a calibrated final score.

The fix changes only the default risk-payload resolver and imports in `app/services/integrity.py`, with regression tests in `tests/test_integrity_recovery.py`:

- Recognize exactly the historical four-field or new nine-field payload plus the registered synthetic marker. Require the registered corruption (`score=0.9999`, `demo_tampered=true`), and reject additional/missing fields or changes to any retained field.
- Recompute baseline raw score and explanations from validated financing features, compare them with the historical projection, and derive the final band from the persisted assessment-time final score. Reconstruct scope, applied-run ID and fallback code from persisted projection state. Never invoke current active inference or replace a historical score with a present model result.
- The attempted-run UUID exists only in the sealed event, not in a separate projection column. It is restricted to canonical UUID form and compatible fallback states, retained only as an **untrusted candidate**, and accepted only after the unchanged `recover()` expected-hash comparison authenticates the entire reconstructed payload. Altering this UUID fails that original sealed-hash check. Other changed lineage fields fail the exact registered-transform comparison. No artifact, deployment or historical hash is rewritten to make recovery succeed.
- Invalid feature projections now map to `RecoverySourceMismatch`, preserving safe API failure behavior. Incorrect raw/final projections, scope changes, applied/attempted-run tampering, extra fields and unregistered corruption all leave the ledger unchanged and the incident unresolved.
- Tests cover old/new uncalibrated, genuinely calibrated and cross-scope fallback events; the latter two replace the active deployment identity after creation and install a guard forbidding current inference during recovery. Original full event-state equality assertions remain intact, including event IDs, timestamps, payload, previous hash and sealed event hash.

This is the **pre-existing registered synthetic demo restoration exception**: recovery restores the one deliberately corrupted demo event to its original sealed payload and appends detection/recovery evidence. It is not a general ledger rewrite facility and is separate from immutable actual-outcome corrections, which remain append-only. No user database, original artifact, migration or immutable outcome path was modified. Parent-owned plan edits were left outside this fix commit. Only these two edited Python files were normalized to their index LF ending after restoring untouched function formatting.

All test commands used the same test-only `TEST_POSTGRES_URL` and `$testPython` specified above, sequentially:

1. Initial reproduction: `& $testPython -m pytest tests/test_integrity_recovery.py -o addopts='' -q --tb=short`: **2 failed, 2 passed in 5.84s**, matching the two CI failures (sealed-hash mismatch / API 409).
2. RED historical/new matrix: `... tests/test_integrity_recovery.py -k without_current_inference -o addopts='' -q --tb=short`: **3 failed, 1 passed, 10 deselected in 6.75s**. Old four-field recovery passed; new, calibrated and fallback cases failed for the expected resolver mismatch. A separate new unsupported-score case demonstrated the old resolver accepted an unregistered score corruption. Initial fixture setup required adding the mandatory predecessor deactivation timestamp before this meaningful matrix run.
3. Initial GREEN: `... tests/test_integrity_recovery.py -o addopts='' -q --tb=short`: **14 passed in 24.99s**.
4. Additional malformed-projection RED: `... tests/test_integrity_recovery.py -k projection_features -o addopts='' -q --tb=short`: **1 failed, 15 deselected in 3.51s**, demonstrating an unnormalized Pydantic error; corrected to safe recovery rejection.
5. Final requested focused suite:

```powershell
& $testPython -m pytest tests/test_integrity_recovery.py tests/test_service.py tests/test_api.py tests/test_temporal_calibration.py tests/test_adaptive_risk.py -o addopts='' -q --tb=short
```

Result: **85 passed in 42.28s**. No additional broad duplicate suite was run.

After formatting/EOL cleanup: `C:/Python313/python.exe -m ruff check app tests` passed; `C:/Python313/python.exe -m mypy app` passed all 63 source files; `& $testPython -m compileall -q app/services/integrity.py tests/test_integrity_recovery.py` passed; `git diff --check` passed. Full CI rerun/review is handed back to the parent task.
