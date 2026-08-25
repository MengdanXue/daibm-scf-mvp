# Governed Lifecycle, Corrections, and Scope Isolation Design

## Objective

Extend the current supply-chain-finance MVP with three coherent capabilities:

1. a truthful default, restructuring, recovery, write-off, and closure lifecycle;
2. immutable correction events that control training eligibility without rewriting outcomes;
3. independently validated, scope-isolated adaptive calibration.

The design preserves the existing PostgreSQL-only modular monolith, SQLAlchemy repositories, append-only audit ledger, bilingual Russian/Chinese UI, and baseline-safe inference. It does not introduce Redis, a message broker, microservices, Kubernetes, real payment execution, or online TGNN training.

## Chosen approach

The implementation extends the existing domain and service boundaries instead of applying isolated flags or replacing the system with full event sourcing.

- Lifecycle transitions remain explicit versioned commands.
- Financial events and corrections are immutable rows plus ledger events.
- PostgreSQL is both the source of truth and the durable calibration-job queue.
- Calibration datasets are partitioned by trusted scope.
- Activation uses out-of-fold validation rather than training-set metrics.

## Financing lifecycle

### States

The facility state set becomes:

- `ready_for_disbursement`
- `disbursed`
- `active`
- `overdue`
- `restructured`
- `defaulted`
- `repaid`
- `written_off`
- `closed`

`restructured` is non-terminal. `repaid` and `written_off` are resolved states that require auditor closure before an actual outcome can be recorded.

### Transitions and roles

| From | Command | Role | To | Required invariant |
|---|---|---|---|---|
| `active` | mark overdue | financier | `overdue` | positive days past due and evidence hash |
| `overdue` | restructure | risk manager | `restructured` | replacement future installments sum exactly to the current outstanding balance |
| `restructured` | mark overdue | financier | `overdue` | positive days past due and evidence hash |
| `overdue`, `restructured` | declare default | risk manager | `defaulted` | default date, positive days past due, reason code, and evidence hash |
| `active`, `overdue`, `restructured`, `defaulted` | submit/confirm recovery payment | supplier/financier | same state or `repaid` | confirmed payment never exceeds outstanding balance |
| `defaulted` | write off | auditor | `written_off` | write-off amount equals the remaining outstanding balance |
| `repaid` | close | auditor | `closed` | zero outstanding balance and verified ledger |
| `written_off` | close | auditor | `closed` | zero outstanding balance and verified ledger |

A defaulted facility may still receive recovery payments. If recoveries reduce the balance to zero, it becomes `repaid` but retains its immutable default history.

### Restructuring

Restructuring preserves the original contract, all confirmed payments, and every prior installment. It never resets principal or recognized payments.

- A `facility_restructures` row records the command, actor, reason, evidence SHA-256, old schedule version, new schedule version, and timestamp.
- Existing unpaid future installments become `superseded`; paid installments remain `paid`.
- New installments use the next schedule version and must total the exact outstanding balance.
- Installment identity and schedule version replace any facility-wide uniqueness assumption on sequence number.
- Repeated restructuring is possible only after the restructured facility becomes overdue again.

### Default and write-off evidence

- A `facility_defaults` row records the first default declaration. A facility cannot be declared default twice.
- A `facility_writeoffs` row records the full remaining balance written off, the auditor, reason, evidence hash, and timestamp.
- Write-off reduces accounting outstanding balance to zero without creating a payment row.
- The ledger distinguishes `PAYMENT_CONFIRMED`, `FACILITY_DEFAULTED`, and `FACILITY_WRITTEN_OFF`; write-off can never appear as repayment.

### Closure reason and derived outcome

`financing_facilities` adds an immutable-at-closure `closure_reason`:

- `repaid` — never defaulted, zero loss;
- `settled_after_default` — defaulted and later fully recovered, zero loss;
- `written_off` — defaulted and resolved by write-off, loss equals the write-off amount.

The actual-outcome submission API no longer trusts caller-supplied `defaulted`, `days_past_due`, or `loss_amount`. The server derives these values from lifecycle rows:

- `defaulted` is true when a default declaration exists;
- `days_past_due` is the maximum governed delinquency/default value;
- `loss_amount` is the recognized write-off amount, otherwise zero.

The auditor still supplies observation time, provenance, and evidence SHA-256. Observation time cannot precede closure. The derived result is displayed before confirmation in both languages.

## Immutable corrections and training eligibility

### Correction model

`outcome_corrections` is append-only and contains:

- correction ID and outcome ID;
- action: `EXCLUDE` or `REINSTATE`;
- stable reason code and human comment;
- evidence SHA-256;
- auditor user ID;
- idempotency key and request hash;
- recorded timestamp.

No correction updates or deletes the original outcome. The effective eligibility of an outcome is determined by its latest correction event; an outcome with no correction is eligible.

Repeated `EXCLUDE` or `REINSTATE` actions that do not change effective state return a conflict unless they are an exact idempotent replay. `REINSTATE` is permitted only when the lifecycle and lineage pass current validation.

### Active-deployment invalidation

An `EXCLUDE` correction commits atomically with these effects for the outcome's scope:

1. append the correction and `OUTCOME_TRAINING_EXCLUDED` ledger event;
2. invalidate any active calibration whose dataset contains the outcome;
3. enqueue a new calibration job for that scope.

Invalidation happens in the correction transaction, before asynchronous retraining, so a known-invalid artifact is never used for a new assessment. Inference safely uses the baseline until a replacement passes all gates.

`REINSTATE` appends `OUTCOME_TRAINING_REINSTATED` and enqueues retraining. It does not reactivate an old artifact directly.

### Existing inconsistent demo outcomes

The migration does not silently rewrite or auto-correct historical data. After deployment, the auditor uses the correction API/UI to exclude the five current controlled-demo outcomes whose recorded loss conflicts with their fully repaid lifecycle. The reason code is `INCONSISTENT_LIFECYCLE`. Their original rows and audit history remain visible.

## Durable calibration jobs

### Queue

`calibration_jobs` is a PostgreSQL-backed durable queue with:

- job ID, deployment scope, trigger type and trigger ID;
- status: `queued`, `running`, `completed`, or `failed`;
- attempt count, stable failure code, timestamps, and idempotency key.

Outcome submission and correction requests enqueue a job in the same transaction as their business event. They do not train within the HTTP request.

An application-lifespan worker claims jobs with `FOR UPDATE SKIP LOCKED`, processes one scope at a time under a scope-specific PostgreSQL advisory lock, and records a calibration run. Claims abandoned by a stopped process become retryable after a fixed lease timeout. Multiple application instances cannot train the same scope concurrently.

The worker uses bounded retries for infrastructure failures. Deterministic data/gate rejection completes the job successfully with a rejected calibration run; it is not retried.

## Independent calibration validation

### Eligible dataset

Each job loads only outcomes that:

- have complete prediction and lifecycle lineage;
- are currently training-eligible after corrections;
- exactly match the job's deployment scope.

The dataset SHA-256 covers ordered outcome facts plus the effective correction-event head for every included outcome. Excluded outcomes are recorded in run diagnostics but are absent from fitting and validation.

### Deterministic stratified five-fold validation

Activation metrics are computed from deterministic out-of-fold predictions:

1. Partition positive and negative observations independently in stable `(observed_at, outcome_id)` order across five folds.
2. For each fold, fit Platt coefficients on the other four folds.
3. Predict only the held-out fold.
4. Combine all held-out predictions and compute OOF Brier score and OOF log loss.
5. Compare OOF calibrated metrics with the original baseline scores for the same observations.
6. Only after the gate passes, fit final coefficients on all eligible observations and publish the deployable artifact.

No observation is evaluated by coefficients fitted on that observation. Final-fit metrics may be stored as diagnostics but never used for activation.

### Fixed activation gates

- at least 20 eligible observations;
- at least 5 defaulted and 5 non-defaulted observations;
- at least 5 distinct original risk scores;
- every fold has both classes in its training partition;
- finite OOF metrics;
- OOF Brier score no worse than baseline;
- OOF log loss no worse than baseline;
- verified artifact schema, dataset hash, and artifact SHA-256.

Gate thresholds have no environment-variable override. A gate failure leaves the scope on its existing valid deployment unless that deployment was explicitly invalidated by a correction; an invalidated scope remains on baseline.

The artifact schema advances to `daibm.platt-calibration.v3` and stores OOF metrics, fold assignment hash, final coefficients, eligible/excluded counts, scope, and dataset/correction lineage.

## Scope isolation

### Scope model

Only two deployable scopes exist:

- `controlled_demo`
- `external_verified`

`mixed` may remain readable on legacy runs but can never be trained or activated. New training groups observations by provenance and enqueues/produces a run for exactly one scope.

`financing_requests` adds non-null `assessment_scope`. Existing and UI-created requests are backfilled/set to `controlled_demo`. The future external import boundary, not a browser-controlled field, is the only component allowed to create `external_verified` requests.

### Inference and deployment

Adaptive inference receives the request's `assessment_scope` and selects an active run with an exact scope match. If none exists or its artifact fails verification, inference uses the transparent baseline and records a stable fallback code.

PostgreSQL enforces at most one active calibration per scope with a partial unique index. Activation, supersession, invalidation, startup reconciliation, and rollback all operate within one scope. Rollback may target only the direct predecessor in the same scope.

The active-deployment API requires a scope parameter. The current UI queries `controlled_demo`; future external tooling must explicitly query `external_verified`.

## API and UI

### Lifecycle commands

- `POST /api/v1/facilities/{id}/restructure`
- `POST /api/v1/facilities/{id}/declare-default`
- `POST /api/v1/facilities/{id}/write-off`

All commands require expected facility version and idempotency key. Restructure accepts a replacement schedule; default and write-off accept stable reason, comment, and evidence SHA-256.

### Corrections

- `GET /api/v1/outcomes/{id}/corrections`
- `POST /api/v1/outcomes/{id}/corrections`

Correction responses include effective training eligibility and the queued calibration job ID. Only auditors can create corrections.

### Calibration

- Active deployment reads and rollback requests include explicit scope.
- Run responses distinguish OOF validation metrics from final-fit diagnostics.
- Job status and failure code are visible to auditors.

### Bilingual interface

The financing panel adds role-gated restructure, declare-default, recovery, write-off, and closure controls. The timeline visually separates payments from write-offs and shows every schedule version.

The outcome panel shows derived outcome fields, correction history, current eligibility, job state, OOF metrics, scope, active version, and baseline fallback. Russian remains the default; Chinese translations and the 390 px mobile contract remain required.

## Migration and compatibility

A new guarded Alembic revision:

- adds lifecycle event tables and closure metadata;
- versions installments without mutating paid history;
- adds correction and calibration-job tables;
- adds `assessment_scope` and backfills existing requests to `controlled_demo`;
- extends deployment status with `invalidated`;
- replaces the global active-run unique index with a per-scope partial unique index;
- preserves all v2 calibration runs and artifacts as historical records;
- prevents downgrade when new lifecycle, correction, job, or scoped-deployment data would be lost.

The currently active v2 controlled-demo run remains readable during migration. It becomes invalidated when the first conflicting outcome is excluded. V2 artifacts are never activated for `external_verified` scope after this migration.

## Failure handling

- Invalid lifecycle transitions return stable 409 conflicts and change no state.
- Artifact/training failures never roll back an already committed outcome or correction.
- Job failures expose stable codes without filesystem paths or secrets.
- Worker restart resumes leased/queued jobs without duplicate runs.
- Invalidated or corrupt deployments cause baseline fallback, never silent cross-scope substitution.
- Correction and lifecycle commands preserve optimistic concurrency and idempotent replay behavior.

## Verification

Implementation follows test-driven development and includes:

- pure state-machine tests for restructure/default/recovery/write-off/closure;
- money and schedule-version invariants;
- PostgreSQL migration constraints, per-scope uniqueness, job leasing, and guarded downgrade;
- correction replay, exclusion, reinstatement, and immediate invalidation tests;
- deterministic OOF fold, no-leakage, distinct-score, and metric-gate tests;
- exact-scope inference, rollback, corruption fallback, and startup reconciliation tests;
- API role, validation, conflict, idempotency, and job-response tests;
- Russian/Chinese UI contracts and 390 px real-browser lifecycle acceptance;
- an end-to-end flow that excludes the five inconsistent demo outcomes, creates valid varied-score default/write-off examples, retrains, activates a controlled-demo v3 artifact, and proves a new assessment uses it;
- full application/research regression suites and private GitHub CI.

## Rollout acceptance

The feature is accepted only when:

1. the five inconsistent demo outcomes are excluded through auditor correction events rather than direct database edits;
2. their prior active dataset is invalidated and inference temporarily falls back to baseline;
3. valid lifecycle-derived examples with varied baseline scores satisfy the OOF gate;
4. exactly one `controlled_demo` v3 deployment becomes active;
5. a post-activation browser assessment stores raw/final scores and exact scoped run lineage;
6. container rebuild preserves the scoped artifact and all correction/job/lifecycle history.
