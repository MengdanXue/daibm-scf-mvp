# Minimum Advanced Thesis Platform — Design

Date: 2026-08-24

## Objective

Strengthen the existing master's-thesis MVP where additional work has the
highest academic and defense value, without expanding the production
architecture or changing the claims of the frozen reference model.

The deliverable has seven bounded parts:

1. an exploratory five-seed research sensitivity package;
2. a repeatable, offline defense preparation and fallback package;
3. small login-page guidance for the real five-role demonstration;
4. maintenance updates for the GitHub Actions runtime;
5. a minimal disbursement, repayment, overdue, and closure lifecycle;
6. an optional real Hyperledger Fabric anchoring profile plus one narrowly
   scoped zero-knowledge proof;
7. actual-outcome ingestion, drift checks, and automatically triggered
   candidate-model training with controlled promotion.

Redis, Kafka, Kubernetes, real bank integration, OCR, unrestricted automatic
model promotion, production identity infrastructure, and production-grade
high availability remain out of scope. The design does not turn the existing
modular monolith into generalized microservices; it adds only two opt-in
specialized processes where process isolation is necessary: a Fabric/ZKP
gateway and a research training worker.

## Compatibility and delivery order

The current PostgreSQL + FastAPI two-container profile remains the reliable
default defense path at `http://127.0.0.1:8010`. Existing workflow states,
promoted ONNX artifact, API contracts, demo credentials, and browser
acceptance route remain compatible.

New work is delivered in four independently verifiable increments:

1. research evidence, defense tooling, login guidance, and CI maintenance;
2. financing lifecycle;
3. Fabric anchoring and ZKP in an opt-in advanced profile;
4. outcome feedback and automatic candidate training in an opt-in research
   profile.

Each increment must leave the default demo green before the next begins. The
advanced launcher may enable all profiles for a longer demonstration, while
the ordinary launcher remains fast and dependency-light.

## Scientific evidence package

### Experiment contract

Add a separate offline command that evaluates TGNN and XGBoost over five
explicit deterministic seeds. It must reuse the existing synthetic-data,
temporal-split, model-training, and metric code instead of creating a second
research implementation.

The existing promoted ONNX reference bundle and runtime policy thresholds are
not modified. Multi-seed output is stored as a new exploratory evidence pack
and labeled `2026_EXPLORATORY_SENSITIVITY`. It must never overwrite or be
presented as the original thesis experiment.

Each seed records:

- dataset seed and content identity;
- temporal train/validation/test boundaries;
- TGNN and XGBoost ROC-AUC, PR-AUC, Brier score, precision, recall, F1, and
  confusion matrix;
- raw test labels and probabilities needed to regenerate figures;
- training configuration and artifact hashes.

The aggregate report records per-model mean, sample standard deviation, and a
Student-t 95% confidence interval for ROC-AUC, PR-AUC, and Brier score. With
only five seeds, these intervals are explicitly exploratory; no significance
or superiority claim is made.

### Threshold explanation

The package distinguishes binary reporting threshold `0.50` from the demo
policy boundaries `0.40` and `0.75`. The latter are management-policy rules,
not statistically optimized classifier cutoffs. A threshold-sensitivity chart
shows precision, recall, and F1 across fixed thresholds without selecting a
threshold on the test set or changing the running policy.

### Figures and tables

Generate deterministic, publication-readable artifacts:

- per-seed metrics CSV;
- aggregate metrics JSON and CSV;
- combined ROC curve;
- combined precision-recall curve;
- calibration/reliability curve;
- threshold-sensitivity curve;
- confusion-matrix panel at threshold `0.50`;
- a Markdown research appendix summarizing scope, results, limitations, and
  exact reproduction command.

Figures use accessible colors, readable Russian/Chinese-neutral English axis
labels, embedded metadata, and no visual claim unsupported by the data.

## Defense preparation and fallback package

### Clean preparation

Add an explicit Windows preparation launcher for the local synthetic demo. It
must clearly state that it removes only the named Docker Compose demo volumes,
then rebuilds PostgreSQL and the application, waits for semantic health, runs
the preflight checks, and leaves the application open at the login page.

The ordinary `start-demo.cmd` remains non-destructive. Reset preparation is a
separate command so normal use cannot accidentally erase demonstration state.

### Preflight

Add a read-only preflight command that verifies:

- PostgreSQL backend and reachability;
- ledger validity;
- promoted research artifact readiness and hash;
- all five demo accounts can authenticate and report the expected role;
- Russian is the default UI language and Chinese switching is present;
- required local documentation and fallback assets exist.

Every temporary authentication session is logged out. Preflight does not
create financing applications or mutate the ledger.

### Fallback evidence

Extend the existing browser acceptance route so an explicit option records a
local WebM video in addition to the final screenshot. The normal CI/acceptance
mode remains screenshot-only. Generated media stays under `output/` and is not
committed to Git.

Create and commit two compact documents:

- a one-page bilingual defense sheet covering architecture, thesis mapping,
  the five-step demo, exact scientific boundaries, and emergency fallback;
- a one-page English research brief for PhD applications, covering the
  question, method, implemented evidence, honest limitations, and next
  research step.

Both documents receive verified PDF renderings. Source Markdown remains the
canonical editable form.

## Login-page guidance

Add a compact numbered five-role defense guide to the login page. Selecting a
role may fill that role's demo username and the shared demo password, but it
must not authenticate automatically or bypass the server-side session and
authorization flow.

The guide is fully Russian/Chinese bilingual, keyboard accessible, responsive,
and visually subordinate to the login action. It does not pretend that roles
are a browser-only switch.

## CI maintenance

Update GitHub-maintained Actions to releases using the supported Node.js
runtime indicated by the current GitHub runner warning. Application and
PostgreSQL integration behavior remains unchanged.

The multi-seed experiment is deliberately not run in ordinary CI because it
installs heavyweight research dependencies and trains multiple models. CI
tests its orchestration and aggregation with small fixtures; the real evidence
pack is generated locally and verified by a manifest/hash command.

## Financing lifecycle

### Domain model

Keep the existing application approval state machine unchanged. Add a
separate financing facility aggregate only for an audited, approved
application:

```text
ready_for_disbursement -> disbursed -> active
                                   -> overdue
                         active | overdue -> repaid -> closed
```

A facility owns an immutable principal, disbursement record, repayment
schedule, payments, outstanding balance, currency, version, and timestamps.
All monetary values use PostgreSQL `NUMERIC` and Python `Decimal`; balances
must reconcile exactly to the cent.

### Roles and actions

- The financier creates and confirms disbursement only after the approval and
  audit preconditions are satisfied.
- The supplier submits a repayment against a due installment.
- The financier confirms or rejects the submitted repayment.
- The risk manager may mark a past-due installment overdue and record a
  control action.
- The auditor closes only a fully repaid facility after verifying its trace.

Every action is versioned, idempotent, authorization-checked, and committed in
one PostgreSQL transaction with a linked audit event. Rejected and
manual-review applications cannot be disbursed. Total confirmed repayments
cannot exceed principal plus explicitly represented charges; the minimum MVP
does not calculate interest, fees, FX, or accounting entries.

### Interface

Add a bilingual financing-lifecycle tab and role-specific tasks. The page
shows principal, schedule, paid/outstanding totals, next due item, overdue
status, and a chronological evidence trail. A deterministic approved demo case
allows the full path to be shown without changing the existing five-role
approval demonstration.

## Hyperledger Fabric anchoring and ZKP

### Optional advanced profile

Use an opt-in Docker Compose profile so Fabric cannot destabilize the default
defense mode. The smallest real network contains one ordering service, one
peer organization, one channel, and one chaincode package. Cryptographic
material is demo-only and local; it is never described as production PKI or
decentralized governance.

A narrow Node.js Fabric Gateway adapter is the only new network-facing
process. PostgreSQL remains the system of record. A transactional outbox in
the FastAPI database publishes finalized audit anchors asynchronously through
the gateway. Each chaincode record contains only:

- application/facility identifier;
- PostgreSQL ledger event identifier;
- event hash and chain-head hash;
- model/policy identity when applicable;
- timestamp and proof digest.

No supplier name, invoice number, amount, or other business payload is written
to Fabric. The UI can compare the PostgreSQL event hash with the Fabric anchor
and show pending, anchored, or mismatch status. The system calls this external
hash anchoring, not a replacement of PostgreSQL and not a production
blockchain deployment.

### Minimal zero-knowledge proof

Implement one real Circom/snarkjs Groth16 circuit using audited circomlib
components. Private inputs are invoice amount in integer minor units and a
random salt. Public inputs are a Poseidon commitment and financing limit in
the same units. Explicit 64-bit range constraints prevent field wraparound;
the circuit proves that the committed amount does not exceed the public limit
without revealing the amount.

The proving and verification keys are generated for the fixed demo circuit,
versioned by hash, and loaded locally. The proof is verified by the gateway;
only its public inputs, verification result, circuit version, and proof digest
are written to the application trace/Fabric anchor. This proves only the
mathematical limit statement about the supplied private value. It does not
prove invoice authenticity, tax registration, ownership, or absence of double
financing.

## Outcome feedback and automatic candidate training

### Feedback model

When a financing facility reaches its observation horizon, an authorized
financier records an actual outcome (`repaid_on_time`, `repaid_late`, or
`defaulted`) with observation date and source. The system preserves the exact
feature snapshot and model prediction used for the original decision so
predicted-versus-actual analysis cannot silently use current data.

Synthetic seeded outcomes remain clearly labeled and are used only to make
the demonstration runnable. The UI and exports never merge them invisibly
with manually entered outcomes.

### Drift and job trigger

The application records an immutable training snapshot and creates a
PostgreSQL training job when either:

- the configured minimum number of new labeled outcomes is reached; or
- an authorized user explicitly requests the same check for demonstration.

The minimum drift report contains label-rate change, score-distribution PSI,
and Brier-score change where enough labels exist. With too few real outcomes,
the report returns `insufficient_evidence` rather than a misleading number.

### Training worker and promotion

An opt-in Python research worker claims jobs with PostgreSQL row locking,
trains candidate TGNN/XGBoost artifacts through the existing offline pipeline,
emits canonical manifests, and verifies all hashes before registering a
candidate. It does not run inside the FastAPI request process and does not
require Redis or a message broker.

Training starts automatically after a valid job trigger, but a candidate
cannot replace the runtime model automatically. Promotion requires an
authorized human action and all gates:

- compatible feature schema and dataset lineage;
- complete artifact verification and ONNX parity;
- finite metrics on an untouched holdout;
- no configured degradation beyond the current model;
- recorded approver, reason, old/new hashes, and rollback pointer.

This is truthful automatic retraining with controlled deployment, not
self-modifying production credit decisioning. The default frozen thesis model
remains available for rollback and for the four-minute defense route.

## Data flow

```text
explicit seed list
    -> existing synthetic generator
    -> existing temporal graph/split
    -> existing TGNN + XGBoost trainers
    -> per-seed immutable evidence
    -> aggregate statistics + figures
    -> exploratory appendix + manifest verification

reset-defense-demo.cmd
    -> named Docker Compose project reset
    -> existing start/health contract
    -> read-only five-account preflight
    -> browser login page

approved + audited application
    -> financing facility
    -> disbursement -> repayments -> overdue/repaid -> closure
    -> PostgreSQL audit + optional Fabric hash anchor

private amount + salt, public commitment + limit
    -> Circom Groth16 proof
    -> local verification
    -> proof digest in PostgreSQL/Fabric evidence

actual outcome feedback
    -> immutable snapshot + drift check
    -> PostgreSQL training job
    -> opt-in worker trains verified candidate
    -> human promotion or rejection + rollback record
```

## Failure handling

- A failed seed leaves its run directory and error record but cannot publish a
  complete evidence manifest.
- Aggregate publication uses the existing canonical JSON and staged-directory
  verification pattern so a failed run does not replace prior valid evidence.
- Preflight failures identify the exact database, ledger, model, account, UI,
  or asset check and exit non-zero without resetting again.
- Video recording failure does not invalidate the normal screenshot-based
  browser acceptance result.
- PDF sources remain usable if a generated PDF must be recreated on another
  machine.
- A Fabric outage leaves the outbox pending and never rolls back or blocks a
  committed financing action. Retrying an anchor is idempotent.
- A ZKP proving or verification failure records no successful proof claim and
  cannot advance a protected financing action.
- Concurrent or repeated repayments cannot overpay a facility; transaction
  rollback preserves the prior balance and ledger head.
- Worker crashes return an expired training lease to the queue. Failed or
  degraded candidates never replace the active runtime artifact.

## Verification

Implementation is accepted only when all of the following pass:

- test-driven unit tests for seed propagation, aggregation, confidence
  intervals, canonical manifests, and failure publication;
- a reduced-fixture orchestration test covering both models without a
  five-run CI cost;
- figure tests checking required panels, labels, dimensions, and finite data;
- preflight tests proving all five roles and logout cleanup;
- launcher contract tests proving ordinary startup is non-destructive and
  reset targets only the Compose demo volume;
- UI contract tests and real Playwright login-guide/role-flow acceptance;
- PDF render and visual inspection for both one-page documents;
- state-machine, authorization, idempotency, concurrency, decimal-invariant,
  and rollback tests for disbursement through closure;
- Fabric chaincode and gateway integration tests for anchor submission,
  lookup, retries, duplicate anchors, and mismatch detection;
- known-valid and known-invalid ZKP vectors, proof privacy checks, circuit/key
  hash verification, and browser evidence display;
- feedback provenance, insufficient-evidence, drift, job-lease, worker crash,
  candidate-gate, manual-promotion, and rollback tests;
- separate advanced-profile health checks that do not alter the default
  two-container health contract;
- full existing pytest suite, research artifact verification, Docker health,
  and GitHub Actions CI;
- final independent code review with no unresolved Critical or Important
  findings.

## Acceptance boundaries

This work strengthens reproducibility, presentation reliability, research
communication, and the breadth of the demonstrable prototype. The financing
lifecycle is a controlled simulation. Fabric is a one-organization local
anchoring network. The ZKP proves one narrow numeric statement. Retraining
produces a verified candidate and never bypasses human deployment approval.
None of these additions creates production financial, blockchain, privacy, or
autonomous-learning evidence; none changes the thesis's unavailable original
data or metrics into reproduced evidence.
