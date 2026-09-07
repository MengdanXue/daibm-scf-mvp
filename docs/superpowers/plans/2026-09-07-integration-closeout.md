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
- [ ] Use chronological 70/30 holdout with whole-timestamp groups, at least 20 training and 10 validation observations, at least 5 distinct training raw scores and 2 examples of each class in each partition. Persist these policy values and reject insufficient data with stable reasons. Never tune to keep historical 0.5595→0.2499.
- [ ] Fit coefficients using training only; compute promotion Brier/log-loss on validation only. Require neither metric regress and at least one improve beyond numeric tolerance. Legacy artifact bytes remain readable under their explicit safe scope, not newly promotable.
- [ ] Make all active reads, rollback and inference require scope; persist fallback reason and attempted run lineage when incompatibility prevents calibration. Controlled_demo→external_verified and all mixed promotion must reject.
- [ ] Run focused calibration, worker, workflow and API tests; commit and report.

### Task 3: Bilingual lifecycle and governance UI and acceptance

**Files:** app/static/workflow.js, templates/styles, scripts/*browser_acceptance.py, tests; docs/research/integration-acceptance-2026-09-07.md.

**Interfaces:** Render backend allowed actions, old/new schedules, balance/recovery/loss, correction history, job polling and temporal validation gates in Russian/Chinese. No user-controlled switch authorizes external_verified applications.

- [ ] Add observable browser tests for lifecycle commands, correction/exclusion, job status and temporal diagnostics; expose server errors without losing form context.
- [ ] Execute normal settlement; overdue/default/recovery/write-off; restructure/settle; restructure/default; multiple restructure history and money conservation.
- [ ] Identify the actual five contradictory historical outcomes read-only; append correction events through public APIs with precise reasons and record before/after immutable hashes. New controlled samples are marked demo and do not substitute for historical evidence.
- [ ] Fix invoice_limit@1 circuit version compatibility without modifying old anchors; verify existing proof/browser flow survives.
- [ ] Commit behavior and evidence.

### Task 4: Durable upgrade, final audit, CI and closeout

**Files:** acceptance scripts, CI if needed, docs/research/integration-closeout-2026-09-07.md.

- [ ] Back up named existing PostgreSQL and artifact volumes. Test upgrade on a clone first; assert lifecycle/corrections/artifacts/scope persist across `up --force-recreate` with the same volumes.
- [ ] Run all original/new regressions, static analysis, browser flows and relevant proof suites. Audit default balance assumptions, outcome mutation paths, scope bypasses, training-metric promotion, migration compatibility. Fix actual issues and retest.
- [ ] Push integration branch to private origin, create reviewable PR, inspect GitHub Actions and fix failures until checks pass. Do not erase source branches or user volumes.
- [ ] Produce closeout report covering changes, state machine, migrations, correction/eligibility, temporal policy, promotion, scope matrix, historical outcomes, test counts, container evidence, CI links, risks and commit hash. Do not claim unexecuted checks passed.
