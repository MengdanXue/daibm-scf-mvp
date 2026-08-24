# Governed Self-Training Design

## Objective

Turn the existing automatic Platt-calibration candidate training into a real, auditable self-training loop. New financing assessments may use an automatically activated calibration layer only after fixed data-quality and metric gates pass. Existing decisions never change.

## Scope

The implementation trains a two-parameter Platt calibration layer over the existing transparent business baseline. It does not claim TGNN online retraining and does not modify the separately promoted research ONNX model.

The loop is:

1. An auditor records an immutable actual outcome for a closed, zero-balance facility.
2. The existing deterministic trainer rebuilds a calibration artifact from all immutable outcomes.
3. A fixed gate evaluates sample support, class support, artifact integrity, and Brier/log-loss regression.
4. A passing run atomically becomes the single active calibration deployment; the previous active run becomes superseded.
5. New workflow risk assessments store both the baseline score and the calibrated score plus exact deployment lineage.
6. An auditor may roll back to the immediately previous active run.

## Training and activation gates

- Minimum observations: 20.
- Minimum defaulted observations: 5.
- Minimum non-defaulted observations: 5.
- Artifact schema: `daibm.platt-calibration.v2`.
- Artifact SHA-256 must match the bytes read for inference.
- Brier score after training must be no worse than before training.
- Log loss after training must be no worse than before training.
- A failed or exploratory run is never deployed.
- At most one run may have deployment status `active`.

The gate is deterministic and has no environment-variable override. `CONTROLLED_DEMO` outcomes produce an active deployment scoped and labeled `controlled_demo`; any dataset containing controlled demo outcomes cannot be represented as externally verified.

## Persistence

Alembic revision `20260824_0009` extends `calibration_runs` with:

- `deployment_status`: `not_deployed`, `active`, `superseded`, `rejected`, or `activation_failed`.
- `deployment_scope`: `controlled_demo`, `external_verified`, or `mixed`.
- `activation_mode`: `automatic` or `manual_rollback` when deployed.
- `activated_at`, `deactivated_at`.
- `previous_active_run_id`, a self-reference used for rollback lineage.
- `activation_reason`, a stable machine-readable reason.

A PostgreSQL partial unique index enforces one active calibration run. The existing calibration advisory lock serializes training and deployment transitions.

The same migration extends `financing_requests` with:

- `raw_risk_score`.
- `calibration_run_id`, indexed foreign key to `calibration_runs`.
- `calibration_fallback_code`.

`risk_score` remains the score used for the workflow decision; `raw_risk_score` preserves the immutable baseline output.

## Domain and service boundaries

`app/services/adaptive_risk.py` contains framework-independent gate evaluation, artifact parsing, SHA-256 verification, and score calibration. It does not import FastAPI or ORM models.

`OutcomeService` owns training and deployment orchestration. After publishing an artifact it opens a new serialized transaction, evaluates the gate, supersedes the current deployment if needed, activates the passing run, and appends ledger events.

`WorkflowService` consumes an injected adaptive-risk service. It computes the existing transparent baseline first, then asks the adaptive service to apply the active deployment using the same database session. Missing, malformed, or hash-mismatched artifacts cause a baseline fallback and a ledger-visible fallback code.

## API

- Existing calibration-run responses add deployment status, scope, activation timestamps, reason, and previous active run ID.
- `GET /api/v1/calibration-deployments/active` returns the active deployment or HTTP 404.
- `POST /api/v1/calibration-deployments/rollback` is auditor-only. The request includes `expected_active_run_id` for optimistic concurrency. It rolls back only to the active run's `previous_active_run_id`; absence or staleness returns HTTP 409.

No endpoint accepts arbitrary coefficients, activation status, thresholds, or artifact paths.

## Audit events

- `CALIBRATION_AUTO_ACTIVATED`
- `CALIBRATION_AUTO_REJECTED`
- `CALIBRATION_ROLLED_BACK`
- `RISK_CALIBRATION_APPLIED`
- `RISK_CALIBRATION_FALLBACK`

Events contain identifiers, hashes, metrics, scope, and stable reason codes but never local filesystem paths.

## UI

The auditor outcome panel remains Russian-first with Chinese translation. It distinguishes training status from deployment status and shows:

- active/superseded/rejected/not-deployed state;
- controlled-demo versus external-verified scope;
- sample and class counts;
- Brier/log-loss before and after;
- artifact integrity and activation time;
- baseline score and calibrated score lineage on assessed applications;
- rollback control only when a previous active deployment exists.

The existing visual language, mobile 390 px behavior, keyboard focus, and ARIA live/error semantics remain unchanged.

## Failure and recovery

- Training failure leaves the current active deployment unchanged.
- Artifact publication failure leaves the current active deployment unchanged and records a failed run.
- Activation transaction failure leaves the run non-active and records `activation_failed` when recoverable.
- Startup reconciliation may activate the latest eligible, integrity-verified, not-deployed v2 run under the advisory lock.
- Inference never trusts a database hash without re-hashing the artifact bytes.
- Inference failure falls back to the fixed baseline for that assessment and records the reason.

## Verification

- Pure unit tests for gate decisions, v2 artifact parsing, calibration math, and corruption rejection.
- PostgreSQL migration tests for constraints, foreign keys, indexes, and downgrade refusal when adaptive lineage exists.
- Integration tests for automatic activation, single-active concurrency, non-regression rejection, startup reconciliation, and rollback.
- Workflow tests proving raw/final score lineage and corrupted-artifact fallback.
- API authorization and optimistic-concurrency tests.
- Russian/Chinese UI contract and 390 px real-browser acceptance.
- Full application and research regression suites plus private GitHub CI.

