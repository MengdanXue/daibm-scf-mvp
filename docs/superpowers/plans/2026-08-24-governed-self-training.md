# Governed Self-Training Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use inline execution with strict test-driven development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically activate safe Platt-calibration versions from immutable outcomes and apply them to new workflow assessments with audit and rollback.

**Architecture:** Keep training/deployment orchestration in `OutcomeService`, isolate pure calibration/gate logic in `adaptive_risk.py`, persist a single active deployment in PostgreSQL, and inject adaptive inference into `WorkflowService`. Baseline inference remains the fail-safe.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 17, Alembic, NumPy, pytest, Playwright, vanilla JavaScript/CSS.

**Spec:** `docs/superpowers/specs/2026-08-24-governed-self-training-design.md`

## Global Constraints

- Do not change or move Git tag `v1.0.0-defense`.
- Do not train or claim a TGNN model.
- Keep thresholds fixed at 20 observations and 5 observations per class.
- Preserve existing decisions and scores; apply deployments only to new assessments.
- Never expose artifact paths through APIs, UI, or ledger events.
- One active deployment is enforced in PostgreSQL, not only in Python.
- Controlled-demo data remains labeled controlled demo.

---

### Task 1: Pure adaptive-risk policy

**Files:**
- Create: `app/services/adaptive_risk.py`
- Test: `tests/test_adaptive_risk.py`
- Modify: `app/services/outcome_calibration.py`
- Test: `tests/test_outcome_calibration.py`

**Interfaces:**
- Produces `ActivationDecision`, `ActiveCalibration`, `evaluate_activation_gate(...)`, `load_verified_calibration(...)`, and `apply_platt_calibration(...)`.
- Produces v2 artifacts from `build_calibration_candidate(...)`.

- [ ] Write failing tests with hand-derived score and gate fixtures.
- [ ] Run the focused tests and confirm missing v2/gate behavior fails.
- [ ] Implement the pure value objects, gate, hash verification, and score transform.
- [ ] Update candidate artifact generation to schema v2 without deployment claims.
- [ ] Run focused tests to green and commit.

### Task 2: PostgreSQL deployment state

**Files:**
- Create: `alembic/versions/20260824_0009_governed_self_training.py`
- Modify: `app/models_outcome.py`
- Modify: `app/models_workflow.py`
- Modify: `app/repositories/outcomes.py`
- Test: `tests/test_self_training_migration.py`

**Interfaces:**
- Produces repository methods to lock/read the active run, activate a run, reject a run, and obtain rollback lineage.

- [ ] Write a failing head-migration test for every column, check, FK, FK index, and the partial single-active index.
- [ ] Write a failing downgrade-safety test for deployed or referenced adaptive lineage.
- [ ] Run the migration tests and confirm revision `0009` is missing.
- [ ] Implement the migration, ORM fields, and repository access paths.
- [ ] Upgrade a real PostgreSQL 17 database to head and run migration tests to green.
- [ ] Commit.

### Task 3: Automatic activation and rollback service

**Files:**
- Modify: `app/services/outcomes.py`
- Modify: `app/api/outcomes.py`
- Modify: `app/schemas_outcome.py`
- Modify: `app/main.py`
- Test: `tests/test_self_training_service.py`
- Test: `tests/test_outcome_api.py`

**Interfaces:**
- Produces `OutcomeService.get_active_deployment(...)`, `OutcomeService.rollback_active(...)`, and startup reconciliation.
- Exposes `GET /api/v1/calibration-deployments/active` and `POST /api/v1/calibration-deployments/rollback`.

- [ ] Write failing integration tests for gate pass, gate rejection, unchanged active on training failure, single-active concurrency, reconciliation, rollback, stale expected active ID, and auditor authorization.
- [ ] Run focused tests and confirm deployment APIs/service methods are absent.
- [ ] Implement automatic deployment after artifact publication and ledger events.
- [ ] Implement active-deployment and rollback APIs with path-free responses.
- [ ] Run focused and adjacent outcome tests to green.
- [ ] Commit.

### Task 4: Apply active calibration to new workflow assessments

**Files:**
- Modify: `app/services/workflow.py`
- Modify: `app/main.py`
- Modify: `app/models_workflow.py`
- Test: `tests/test_workflow.py`
- Test: `tests/test_self_training_service.py`

**Interfaces:**
- Consumes the active deployment and returns final score plus exact lineage.
- Serializes `raw_risk_score`, `risk_score`, `calibration_run_id`, deployment scope, and fallback code.

- [ ] Write failing tests proving calibrated scores affect only new assessments.
- [ ] Write a failing corruption test proving baseline fallback and ledger evidence.
- [ ] Implement injected adaptive inference and persistence lineage.
- [ ] Run workflow, integrity, facility, and outcome tests to green.
- [ ] Commit.

### Task 5: Russian/Chinese deployment UI

**Files:**
- Modify: `app/static/workflow.js`
- Modify: `app/static/workflow.css`
- Modify: `scripts/outcome_browser_acceptance.py`
- Test: `tests/test_ui_contract.py`
- Test: `tests/test_release_contract.py`

**Interfaces:**
- Consumes deployment fields and rollback API.
- Produces accessible four-state training plus five-state deployment presentation.

- [ ] Write failing UI contract tests for active lineage, controlled-demo label, rollback optimistic ID, fallback, and bilingual copy.
- [ ] Run UI tests and confirm the deployment presentation is absent.
- [ ] Implement the panel without changing the established design system.
- [ ] Extend browser acceptance to verify automatic active state, score lineage, Chinese at 390 px, and rollback.
- [ ] Run UI contracts and real browser acceptance to green.
- [ ] Commit.

### Task 6: Release verification

**Files:**
- Modify: `README.md`
- Modify: `.github/workflows/ci.yml` only if the existing test selection misses the new tests.

- [ ] Document the exact self-training boundary, thresholds, fallback, and rollback.
- [ ] Run Alembic head on PostgreSQL 17 and verify service health.
- [ ] Run all Python tests, research verification/tests, Node Fabric/ZKP/Gateway tests, audits, compose validation, and browser flows.
- [ ] Run `git diff --check` and confirm a clean worktree after commit.
- [ ] Push `main`, verify the repository remains private, and wait for GitHub CI success.

