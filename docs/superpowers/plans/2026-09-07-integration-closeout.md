# Lifecycle, correction and scope integration implementation plan

> **For agentic workers:** Use subagent-driven-development to implement and review each task. Existing user approval covers execution without intermediate confirmation.

**Goal:** Integrate the remote proof/acceptance fixes and the local lifecycle work, then meet the user's September requirements with temporal validation, exact scope inference, durable history and verified acceptance.

**Architecture:** Keep FastAPI, SQLAlchemy, Alembic and PostgreSQL. Preserve both Git histories and all existing volumes and immutable business evidence. Use the current worker and artifact interfaces, adding versioned temporal validation and explicit scope boundaries.

**Tech Stack:** Python, FastAPI, PostgreSQL 17, SQLAlchemy, Alembic, browser JavaScript, Playwright, Docker Compose, existing Fabric/ZKP sidecars.

**Spec:** User's explicit lifecycle/corrections/independent-validation/scope/compatibility and 20 acceptance requirements in this conversation; this plan supersedes conflicting five-fold validation requirements in the August plan.

## Global constraints

- Preserve original contracts, dossiers, repayment history, scores, audit lineage and calibration artifact bytes.
- Restructure only future installments against current outstanding balance; supersede old schedules and retain multiple restructures. Default can retain positive balance and permit recovery/restructure/write-off.
- Preserve normal settlement separately from write-off and conserve principal, recovered amounts, outstanding balance and loss.
- Correct the five contradictory outcomes through append-only authenticated correction events; never UPDATE/DELETE historical outcomes.
- Training consumes only effective eligible outcomes. Promotion must use strictly later held-out observations, never fitted observations; minimum support and distinct risk-score gates are mandatory.
- Scope values are controlled_demo, external_verified and mixed. Exact scope required in training/deployment/inference; mixed cannot auto-promote. External verified remains a human declaration, not cryptographic provenance.
- Preserve volumes; migrations are additive or guarded. No clearing databases, rewriting history, Redis/Kafka/K8s/microservices or TGNN retraining.
- User authorizes code integration, private Git upload, tests and CI. Keep original thesis and historical experiment artifacts intact; retain reviewed remote documentation changes.

### Task 1: Integrate remote history and resolve migration identity

**Files:** merge origin/codex/fabric-acceptance-20260907 into the integration branch; conflicts expected in app/main.py, app/models.py, app/services/outcome_calibration.py, app/services/outcomes.py and related tests. Migration files under alembic/versions; add tests/test_integration_migration.py and a compatibility runbook.

**Interfaces:** Retain local lifecycle/correction/job APIs and remote invoice-proof settings, canonical encoder and risk policy. Produce one unambiguous migration graph supporting remote 0010, local 0010/0011 and older 0009 without losing data. Do not execute migration on existing user volumes before backup and schema inspection.

- [ ] Inspect deployed revision and schema read-only; distinguish the colliding 0010 implementations by tables/columns, never revision string alone.
- [ ] Baseline: run existing local focused tests and remote CI evidence. Record environment failures separately.
- [ ] Merge reviewed remote commits while preserving local implementation, and write failing real PostgreSQL compatibility tests for both historical shapes. Assert sentinel ledger/outcome bytes unchanged and both feature schemas present after upgrade.
- [ ] Implement a guarded migration/reconciliation path. Unknown or partial contradictory schema fails with instructions; no blind stamp or drop.
- [ ] Run `python -m pytest tests/test_integration_migration.py tests/test_lifecycle_governance_migration.py tests/test_self_training_migration.py -q`, relevant merged-code tests, Ruff and mypy. Commit the integration and report exact commands/results.

### Task 2: Temporal validation and scope isolation

**Files:** app/services/outcome_calibration.py, adaptive_risk.py, outcomes.py, calibration_jobs.py; app/repositories/outcomes.py; app/services/workflow.py; app/service.py; schemas/APIs and tests.

**Interfaces:** `assess(session, baseline_probability, assessment_scope)` must require scope. Produce a new artifact schema carrying disjoint ordered train/validation IDs, correction heads, cutoff and independent metrics; preserve legacy loaders without promoting old candidates.

- [ ] Add RED tests proving every training timestamp precedes every validation timestamp, tied timestamps never straddle the boundary, and validation identities never enter fitting.
- [ ] Use chronological 70/30 holdout with whole-timestamp groups, at least 20 training and 10 validation observations, at least 5 distinct training raw scores, training raw-score span at least 0.05, and 2 examples of each class in each partition. Choose the boundary nearest the 70% target among timestamp boundaries satisfying partition sizes, using no labels or performance to select it; earlier boundary wins ties. If none exists, reject. Persist these policy values and reject insufficient data with stable reasons. Never tune to keep historical 0.5595→0.2499.
- [ ] Fit coefficients using training only; compute promotion Brier/log-loss on validation only. Require neither metric regress and at least one improve beyond numeric tolerance. Legacy artifact bytes remain readable under their explicit safe scope, not newly promotable.
- [ ] Make all active reads, rollback and inference require scope; persist fallback reason and attempted run lineage when incompatibility prevents calibration. Controlled_demo→external_verified and all mixed promotion must reject.
- [ ] Run focused calibration, worker, workflow and API tests; commit and report.

### Task 3: Complete default restructuring history and financial summaries

**Files:** app/domain/facility.py, app/models_facility.py, app/repositories/facility.py, app/services/facility.py, app/schemas_facility.py, lifecycle/outcome tests; new additive Alembic migration after current head.

**Interfaces:** Defaulted facilities may restructure outstanding future installments, retain prior defaults and later default again on a replacement schedule. Response preserves legacy `default_event` and `closure_reason`, adds complete `default_history`, settlement classification and an exact financial summary.

- [ ] Write RED tests for DEFAULTED→RESTRUCTURED→repayment→closed; DEFAULTED→RESTRUCTURED→OVERDUE→DEFAULTED with two immutable default episodes; multiple restructures preserving old schedules; no pending-payment or stale-version bypass.
- [ ] Replace one-default-per-facility constraint with one default episode per schedule version through a normal migration. Backfill original default schedule version from current facility version, valid because historical code prohibited restructuring after any default. Preserve all original default IDs and bytes of outcome/ledger records.
- [ ] Expose `NORMAL_SETTLED` for fully repaid closed facilities, `WRITTEN_OFF` for write-off closures, and separate historical default facts. Successful replacement-plan completion does not erase prior default evidence or change old outcomes.
- [ ] Expose exact-money `outstanding_balance`, `recovered_amount` (total confirmed cash principal repayments), `written_off_amount`, `realized_loss`. Enforce principal = outstanding + recovered + written_off and realized_loss = written_off; document recovery as cumulative cash, including repayments before default. Rejected/pending payments and superseded schedule balances cannot inflate recovery. Use database and service invariants consistent with existing money tables; do not double-count old schedules or mutate prior payments.
- [ ] Run real PostgreSQL lifecycle/API/outcome/migration tests including downgrade protection with multiple default history, conservation, normal repayment regression and rollback. Commit and review before UI work.

### Task 4: Bilingual lifecycle and governance UI and acceptance

**Files:** app/static/workflow.js, templates/styles, scripts/*browser_acceptance.py, tests; docs/research/integration-acceptance-2026-09-07.md.

**Interfaces:** Render backend allowed actions, old/new schedules, balance/recovery/loss, correction history, job polling and temporal validation gates in Russian/Chinese. No user-controlled switch authorizes external_verified applications.

- [ ] Add observable browser tests for lifecycle commands, correction/exclusion, job status and temporal diagnostics; expose server errors without losing form context.
- [ ] Keep browser acceptance explicitly pointed at an isolated application database and artifact directory. Add working `--help` / `--base-url` handling to the outcome acceptance entry point (currently it starts the flow even for `--help`). Never use generated acceptance outcomes as evidence that the five original outcomes were corrected.
- [ ] Execute normal settlement; overdue/default/recovery/write-off; restructure/settle; restructure/default; multiple restructure history and money conservation.
- [ ] Expose NORMAL_SETTLED as the successful fully repaid closure classification (including successfully completed replacement schedules), distinct from WRITTEN_OFF. Keep historical default facts and legacy closure_reason unchanged; settlement classification must not erase an earlier default label in the immutable outcome lineage.
- [ ] Identify the actual five contradictory historical outcomes read-only; append correction events through public APIs with precise reasons and record before/after immutable hashes. New controlled samples are marked demo and do not substitute for historical evidence.
- [ ] Fix invoice_limit@1 circuit version compatibility in the Python outbox, gateway and chaincode without modifying old anchors; regression tests must preserve the exact version in newly created envelopes and continue accepting legacy envelopes. Verify existing proof/browser flow survives.
- [ ] Commit behavior and evidence.

### Task 5: Durable upgrade, final audit, CI and closeout

**Files:** acceptance scripts, CI if needed, docs/research/integration-closeout-2026-09-07.md.

- [ ] Back up named existing PostgreSQL and artifact volumes. Test upgrade on a clone first; assert lifecycle/corrections/artifacts/scope persist across `up --force-recreate` with the same volumes.
- [ ] Run all original/new regressions, static analysis, browser flows and relevant proof suites. Audit default balance assumptions, outcome mutation paths, scope bypasses, training-metric promotion, migration compatibility. Fix actual issues and retest.
- [ ] Push integration branch to private origin, create reviewable PR, inspect GitHub Actions and fix failures until checks pass. Do not erase source branches or user volumes.
- [ ] Produce closeout report covering changes, state machine, migrations, correction/eligibility, temporal policy, promotion, scope matrix, historical outcomes, test counts, container evidence, CI links, risks and commit hash. Do not claim unexecuted checks passed.
