# DAIBM-SCF Research Core v0.4 Design

**Status:** Approved architecture, awaiting written-spec review

**Date:** 2026-08-15

**Baseline:** Engineering Core commit `98eae3d`
**Scope:** Reproducible synthetic data, temporal supply-chain graph construction, real XGBoost/TGNN training and inference, model lineage, policy decisions, and auditable research feedback

## 1. Purpose

The frozen Engineering Core already provides a reliable FastAPI, PostgreSQL, SQLAlchemy, Alembic, Docker Compose, transaction, and tamper-evident audit foundation. Research Core v0.4 must not add horizontal infrastructure such as Redis, Kafka, microservices, Kubernetes, or a service mesh. Its purpose is to make the thesis-facing vertical path real:

```text
synthetic supply-chain data
    -> temporal graph snapshot
    -> trained risk model
    -> traceable risk assessment
    -> policy decision
    -> control action
    -> tamper-evident audit event
```

The success criterion is a reproducible and inspectable research pipeline. It is not reproduction of the numerical results reported in the original thesis, proof of production effectiveness, or implementation of a blockchain protocol.

## 2. Versioning and Truth Boundary

The existing UI already displays version `0.4`. To avoid overwriting historical meaning:

- `research-core-v0.4` is the version of the new research subsystem;
- the integrated application banner advances to `0.5` only after Research Core acceptance;
- the Engineering Core remains frozen at commit `98eae3d` except for defects or changes required by this design;
- original thesis measurements remain `THESIS_REPORTED` evidence and are never relabeled as 2026 reproduction results.

The PostgreSQL hash chain is named **Tamper-evident audit ledger for the MVP**. It must not be described as a blockchain implementation. Hyperledger Fabric, PoA+, ZKP, smart contracts, and distributed validators remain outside v0.4.

## 3. Chosen Architecture

Research Core uses a monorepo with an isolated research package and an artifact-promotion boundary.

```text
Web frontend
    -> FastAPI
        -> Application services
        -> Research inference service
            -> promoted ONNX artifact
            -> model registry
        -> Policy engine
        -> Audit service
            -> PostgreSQL tamper-evident ledger
            -> future Fabric adapter boundary

Research CLI
    -> synthetic dataset generator
    -> graph builder
    -> XGBoost trainer
    -> PyTorch TGNN trainer
    -> evaluator
    -> ONNX exporter
    -> artifact promotion
```

Training never runs during normal application startup. The default Docker demonstration loads a small promoted reference artifact and performs CPU inference. Training dependencies live in `requirements-research.txt`; the default application runtime receives only the dependencies required for promoted-model inference.

### 3.1 Rejected Alternatives

1. Embedding training directly in FastAPI was rejected because it couples experimental failure, GPU/CPU availability, and package weight to the one-command demonstration.
2. A separate ML microservice was rejected because it adds deployment and network failure modes without improving the scientific claim of this MVP.
3. Reimplementing Fabric, PoA+, ZKP, or the thesis's full TGNN before a reproducible data-model-policy path was rejected as scope expansion.

## 4. Package and File Boundaries

The intended source layout is:

```text
app/
  domain/
    entities.py
    risk.py
    policy.py
    audit.py
  services/
    financing.py
    research_inference.py
    policy.py
    integrity.py
  repositories/
  models.py
  main.py

research/
  data/
    schema.py
    generator.py
    manifest.py
  graph/
    builder.py
    features.py
    split.py
  models/
    xgboost_model.py
    tgnn.py
  training/
    train_xgboost.py
    train_tgnn.py
    evaluate.py
    export_onnx.py
  artifacts/
    registry.py
    verification.py
  cli.py

experiments/
  configs/
  results/

artifacts/reference/
  dataset-manifest.json
  feature-schema.json
  model-manifest.json
  tgnn-v0.4.onnx
  xgboost-v0.4.json

docs/
  thesis-traceability.md
```

`app` may depend on domain contracts and promoted inference artifacts. It must not import trainer modules. `research` may import pure domain schemas, but it must not call FastAPI routes or application repositories.

## 5. Domain Model

The system separates risk calculation, policy authorization, and audit evidence.

### 5.1 Core Objects

- `Enterprise`: participant identity and slowly changing financial/operational attributes.
- `SupplyRelationship`: directed supplier-to-customer relationship with an active interval.
- `Transaction`: payer/payee transaction with amount, due date, payment date, and status.
- `RiskEvent`: synthetic observable event attached to an enterprise and timestamp.
- `DatasetVersion`: immutable generator configuration and content identity.
- `SyntheticScenario`: versioned overlay of visible simulated changes applied to an immutable dataset.
- `GraphSnapshot`: one temporal input window built from a dataset version.
- `ModelVersion`: promoted model identity, artifact hash, feature schema, metrics, and lifecycle state.
- `ModelRun`: one reproducible training execution with configuration, seed, and output metrics.
- `RiskAssessment`: inference result produced by one model for one enterprise and snapshot.
- `PolicyDecision`: allowed action derived from one assessment under one policy version.
- `IntegrityIncident`: durable record of detected audit corruption and recovery evidence.
- `LedgerEvent`: tamper-evident evidence of a completed domain transition.

### 5.2 Required Separation

```text
RiskAssessment
  enterprise_id
  risk_score
  model_version_id
  graph_snapshot_id
  input_hash

PolicyDecision
  risk_assessment_id
  policy_version
  decision
  thresholds_used
  reason_codes

LedgerEvent
  event_type
  entity_id
  immutable payload
  previous_hash
  event_hash
```

A model cannot update financing state, ledger history, database configuration, or future consensus parameters. Only the Policy Engine may convert an assessment into an allowed action.

## 6. Synthetic Dataset Contract

### 6.1 Fixed Reference Dataset

The reference dataset is:

- 500 enterprises;
- 24 monthly timestamps;
- deterministic generation seed `20260815`;
- four sectors: manufacturing, wholesale/retail, services, and agriculture;
- four size classes: large, medium, small, and micro;
- directed supply relationships from `supplier` to `customer`;
- synthetic transactions and observable risk events;
- no real enterprise, bank, or personally identifiable data.

Industry and size proportions may be inspired by the distributions reported in the thesis, but the generated data is labeled `SYNTHETIC_DEMO` and is not described as representative empirical data.

### 6.2 Minimum Entity Schema

```text
Enterprise
  enterprise_id
  industry
  size_class
  credit_history_score
  liquidity_ratio
  leverage_ratio
  cash_flow_index
  operational_stability

SupplyRelationship
  relationship_id
  supplier_id
  customer_id
  relation_type
  start_month
  end_month

Transaction
  transaction_id
  payer_id
  payee_id
  relationship_id
  amount
  issued_at
  due_at
  paid_at
  status

RiskEvent
  risk_event_id
  enterprise_id
  event_type
  occurred_at
  severity
  simulated
```

Constraints include no self-relationships, unique active relationship identity, positive transaction amounts, monotonic dates, allowed categorical values, and foreign-key integrity.

### 6.3 Generative Semantics

The generator creates a stable supply network, monthly financial state, transaction behavior, and explicit shocks from one canonical configuration. A latent enterprise-risk process evolves over time through persistence, enterprise-specific shocks, and upstream/downstream exposure. Observable features are noisy functions of that state; labels are derived only from future severe outcomes. A registered dataset version is immutable after generation; demonstrations apply changes through a separately versioned `SyntheticScenario` overlay.

The generator must prevent trivial label leakage:

- the future label is not stored in model input features;
- future payment completion and future risk events are unavailable at anchor time;
- identifiers do not encode the label;
- risk injection changes observable current/history data, not the prediction field;
- normalization statistics are fitted only on the training period.

### 6.4 Dataset Manifest

Every generated dataset writes a canonical manifest containing:

- dataset name and version;
- generator code version;
- seed and full parameters;
- entity, relationship, transaction, and event counts;
- temporal range;
- feature schema version;
- label definition version;
- file hashes and combined content SHA-256;
- generation timestamp and software versions.

The reference identity is `synthetic-scf-v1`. Re-running the same code with the same manifest must produce byte-identical canonical records and the same content hash.

## 7. Graph Construction Contract

### 7.1 Graph Definition

For monthly anchor `t`:

```text
G_t = (V, E_t, X_t)
```

- `V` contains the 500 enterprises.
- `E_t` contains active directed `supplier -> customer` relationships.
- Node features contain financial state, operational state, rolling payment behavior, inbound/outbound transaction aggregates, and network summary features available by `t`.
- Edge features contain monthly transaction amount, transaction count, overdue ratio, and average payment delay.

The first TGNN uses a symmetrically normalized adjacency matrix for GCN aggregation. The original source, target, and direction remain in the snapshot. Edge features are aggregated into inbound/outbound node features and may also weight adjacency; v0.4 does not claim an edge-aware graph-convolution architecture.

### 7.2 Input and Label

Each sample is identified by enterprise and anchor month. At anchor `t`, it uses graph snapshots `t-11` through `t` and predicts whether that enterprise experiences a severe risk event during months `t+1` through `t+3`.

A severe event is one of:

- default or severe delinquency;
- liquidity deterioration beyond the generator's registered threshold;
- a high-severity synthetic transaction or operational risk event.

With a 24-month dataset, valid anchor months are 12 through 21. The fixed temporal split is:

- training anchors: months 12-16;
- validation anchors: months 17-18;
- test anchors: months 19-21.

The prediction horizon for the final test anchor ends at month 24. Random row splitting is prohibited.

### 7.3 Snapshot Identity

A `GraphSnapshot` records dataset version, optional scenario revision and overlay hash, anchor month, window start/end, feature schema, normalization identity, node ordering hash, adjacency hash, feature tensor hash, and combined content hash. Inference first verifies that the stored hashes match the actual snapshot content. It then checks compatibility fields required by the promoted model: feature schema, normalization identity, node ordering contract, tensor shape, and supported dataset lineage. A scenario snapshot is expected to have different adjacency or feature hashes from its training snapshots; different content is not itself an incompatibility.

## 8. Model Contract

### 8.1 Rule Baseline

The current transparent score is retained as `RULE_LOGISTIC_BASELINE`. It is a hand-authored logistic-shaped rule score, not fitted logistic regression and not a TGNN. It remains useful for explanation and regression comparison, but must be labeled accurately in UI, documentation, and metrics.

### 8.2 XGBoost v0.4

XGBoost is trained on the same temporal anchors and labels as TGNN. Features include current node features, 3/6/12-month aggregates and changes, inbound/outbound transaction statistics, and graph summary statistics. Initial reproducible parameters are:

```text
max_depth=4
learning_rate=0.05
n_estimators=200
subsample=0.8
colsample_bytree=0.8
eval_metric=auc
random_state=<run seed>
```

The exact frozen reference configuration is stored in the model manifest rather than inferred from code defaults. XGBoost is a research comparison in v0.4: its evaluated JSON artifact and metrics are registered, but it is not loaded by the default application runtime.

### 8.3 Minimal TGNN v0.4

The first trained TGNN is deliberately smaller than the full thesis architecture:

```text
12 monthly graphs
    -> one dense GCN layer per month, 32-dimensional embedding
    -> node-wise BiLSTM across time, 32 hidden units per direction
    -> MLP 64 -> 32 -> 1
    -> sigmoid risk probability
```

Default training configuration:

```text
dropout=0.2
optimizer=Adam
learning_rate=0.001
loss=BCEWithLogitsLoss with training-only class weight
max_epochs=100
early_stopping_patience=10
```

Training uses plain PyTorch tensor operations and a dense normalized adjacency matrix. The reference graph size makes this feasible without PyTorch Geometric or DGL. Attention, GRU history encoders, differential attention, specialized three-hop propagation, GraphSAGE, and edge-aware message passing remain `PLANNED`.

### 8.4 Reproducibility and Metrics

The reference artifact is trained from a fixed dataset manifest and a registered run seed. The runner accepts arbitrary seeds, but v0.4 acceptance requires one deterministic reference artifact rather than publication-grade multi-seed claims. The conference experiment layer later performs the fixed multi-seed comparison and reports mean, standard deviation, confidence intervals, and corrected significance tests.

v0.4 reports at least ROC-AUC, PR-AUC, F1, precision, recall, confusion matrix, and Brier score on the fixed test period. No minimum score is a release gate. A low or negative result is recorded rather than hidden.

### 8.5 Artifact Promotion

Training produces candidate artifacts. Runtime promotion of the default TGNN ONNX artifact requires:

- completed dataset and feature manifests;
- successful evaluation on the fixed test period;
- checkpoint SHA-256;
- ONNX export and parity check against PyTorch within a registered tolerance;
- deterministic inference smoke test;
- lifecycle status change from `candidate` to `promoted`.

The application loads only the promoted TGNN ONNX artifact assigned to the default deployment slot. It verifies artifact hash, feature schema, compatible dataset lineage, normalization identity, node-ordering contract, input names, tensor shapes, and model version before serving inference. Supporting XGBoost runtime inference or additional deployment slots is deferred.

## 9. Model Registry and Persistence

Alembic migrations add PostgreSQL tables for the new domain. Minimum fields are:

### 9.1 `dataset_versions`

`dataset_version_id`, `name`, `version`, `generation_seed`, `schema_version`, `manifest JSONB`, `content_sha256`, and `created_at`, with a unique content hash and no update path for generated content.

### 9.2 `synthetic_scenarios`

`synthetic_scenario_id`, `dataset_version_id`, scenario name, revision, overlay JSONB, overlay SHA-256, status, creator, and timestamps. Each revision is immutable; reset selects the registered revision-zero overlay rather than rewriting the reference dataset.

### 9.3 `graph_snapshots`

`graph_snapshot_id`, `dataset_version_id`, optional scenario revision and overlay hash, anchor/window months, `feature_schema_version`, normalization identity, node/adjacency/feature hashes, combined content hash, storage locator, and `created_at`.

### 9.4 `model_runs`

`model_run_id`, model family, run seed, dataset version, configuration JSONB, status, start/end timestamps, metrics JSONB, logs locator, and failure summary.

### 9.5 `model_versions`

`model_version_id`, `model_name`, semantic version, model family, source run, dataset version, feature schema, inference format, artifact locator, checkpoint SHA-256, metrics JSONB, lifecycle status, and timestamps. Lifecycle values are `candidate`, `evaluated`, `promoted`, `retired`, and `failed`. v0.4 permits exactly one `promoted` model in the default deployment slot; the evaluated XGBoost reference is not in that slot.

### 9.6 `risk_assessments`

`risk_assessment_id`, `enterprise_id`, `graph_snapshot_id`, `model_version_id`, input SHA-256, risk score, band, explanations JSONB, and inference timestamp. Scores are constrained to `[0,1]`.

### 9.7 `policy_decisions`

`policy_decision_id`, unique assessment reference, `policy_version`, decision, low/high thresholds used, reason codes JSONB, permitted action, and `created_at`.

### 9.8 `integrity_incidents`

`integrity_incident_id`, affected ledger event, detection timestamp, expected hash, recomputed actual hash, captured corrupted payload, recovery status, trusted recovery source, recovery method, operator, and recovery timestamp.

Large training tensors and logs are not stored in PostgreSQL. The database stores identities, manifests, metrics, and locators. Reference artifacts small enough for the demo may be committed under `artifacts/reference`; generated datasets, checkpoints, and repeated experiment outputs remain ignored unless explicitly frozen as a release artifact.

## 10. Inference and Policy Flow

### 10.1 Inference

The inference service receives `enterprise_id`, `graph_snapshot_id`, and `model_version_id`. It verifies the promoted artifact and snapshot identities, runs real inference, then returns an immutable result object. It does not write policy or ledger state itself.

If artifact verification, feature compatibility, or inference fails:

- the API returns a sanitized model-unavailable error;
- no `RiskAssessment`, `PolicyDecision`, or successful inference ledger event is committed;
- the system does not silently substitute the rule baseline;
- operators may call an explicitly named baseline endpoint for diagnosis.

### 10.2 Policy Engine

Initial policy `scf-risk-policy-v0.4` is:

```text
risk < 0.40       -> NORMAL
0.40 <= risk < 0.75 -> ADDITIONAL_CHECK
risk >= 0.75      -> FINANCING_REVIEW
```

The policy stores the exact thresholds used with every decision. These demonstration thresholds are separate from any classification threshold selected for research metrics.

Allowed actions are declarative. The Policy Engine may request normal monitoring, additional verification, or financing review. It may not alter model weights, database settings, ledger history, validator reputation, consensus depth, or infrastructure parameters.

### 10.3 Atomic Persistence

After inference succeeds, one database transaction persists:

1. `RiskAssessment`;
2. `PolicyDecision`;
3. `MODEL_INFERENCE_COMPLETED` ledger event;
4. `RISK_POLICY_TRIGGERED` ledger event;
5. `CONTROL_ACTION_REQUESTED` ledger event.

Any persistence or ledger failure rolls back all five records. The checkpoint hash, dataset version, snapshot id, input hash, score, thresholds, decision, and action are present in the trace payload.

## 11. Risk-Injection Demonstration

The demonstration operates only on generated data and is permanently labeled `SIMULATED`.

```text
select enterprise
    -> build baseline snapshot
    -> real TGNN inference and persist its trace
    -> create a new synthetic-scenario revision with visible risk events and transaction deterioration
    -> rebuild graph snapshot from immutable dataset plus scenario overlay
    -> real TGNN inference and persist its trace
    -> compare both assessments and policy decisions
    -> append scenario and decision audit events
```

Risk injection must not alter the immutable reference dataset or directly write a risk score, decision, or model output. It creates a new immutable scenario revision, including a `SIMULATED_RISK_INJECTED` event, and the graph builder consumes that overlay. Both before and after inference paths use the atomic assessment-policy-ledger flow. The UI displays changed inputs, snapshots, scores, policy decisions, model lineage, and ledger events. A deterministic reference scenario is selected during artifact preparation so the demonstration reliably crosses the intended policy band without falsifying inference.

## 12. Audit Integrity and Recovery

### 12.1 Ledger Boundary

The current chain remains global in v0.4. A domain `AuditPort` accepts `stream_id="global"` so future adapters can support multiple streams. The PostgreSQL adapter rejects any non-global stream until a separate stream-chain migration is designed. v0.4 does not claim that the global advisory lock is scalable.

### 12.2 Demo Reset vs Recovery

- **Demo reset** explicitly discards synthetic demo state and regenerates it. It may use destructive reset semantics because it is not represented as incident recovery.
- **Integrity recovery** must never silently truncate or replace the complete ledger.

### 12.3 Recovery Sequence

After a simulated tamper has made verification fail, recovery uses two explicit transactions so a failed repair cannot erase evidence that detection occurred:

1. verify and identify the affected event;
2. calculate and capture expected/recomputed hashes and corrupted payload;
3. commit an unresolved `IntegrityIncident` in application state while the ledger remains invalid;
4. begin a recovery transaction under the existing global ledger lock and recheck the incident;
5. validate the registered trusted demo fixture and restore only the affected synthetic event;
6. revalidate the restored base chain;
7. append `INTEGRITY_VIOLATION_DETECTED` with incident evidence;
8. append `LEDGER_RECOVERY_COMPLETED` with method, operator, and timestamps;
9. mark the incident recovered and commit;
10. reverify the extended chain after commit.

The UI shows the incident and recovery events after restoration. This proves that the MVP does not hide its own simulated recovery operation. It does not prove recovery from a real compromised distributed ledger.

## 13. API and UI

Existing financing and ledger APIs remain compatible. Research endpoints are grouped under `/api/research`:

- `GET /api/research/status` returns dataset, graph, promoted model, and policy identities;
- `POST /api/research/inference` performs traceable promoted-model inference;
- `GET /api/research/assessments/{id}/trace` returns assessment-to-policy-to-ledger lineage;
- `POST /api/research/scenarios/{enterprise_id}/inject-risk` creates a new synthetic-scenario revision and runs the traceable path;
- `POST /api/research/scenarios/reset` selects the registered revision-zero scenario without altering the reference dataset;
- training, evaluation, export, and promotion remain CLI operations rather than public API endpoints.

The UI adds one Research Core page showing:

- dataset version and synthetic-data label;
- graph anchor and 12-month input window;
- enterprise and connected suppliers/customers;
- model name/version and checkpoint SHA-256 prefix;
- feature schema and snapshot identity;
- before/after score and visible feature changes;
- policy thresholds, decision, and allowed action;
- corresponding audit event ids and hashes;
- explicit `REAL MODEL INFERENCE`, `SYNTHETIC DATA`, and implementation-status labels.

Russian remains the default language, with complete Chinese translation. The page must not use the word blockchain for the PostgreSQL ledger.

## 14. Traceability Matrix

`docs/thesis-traceability.md` becomes the canonical implementation-status document. `docs/thesis-to-mvp.md` is reduced to a link or removed to prevent conflicting matrices.

Allowed implementation statuses are exactly:

```text
IMPLEMENTED
PARTIAL
SIMULATED
PLANNED
NOT IMPLEMENTED
```

Every row contains:

- thesis section and claim;
- final-thesis source location;
- current implementation component;
- allowed status;
- provenance;
- verification artifact;
- limitation or next action.

Allowed provenance values are:

```text
THESIS_REPORTED
2026_REIMPLEMENTATION
SYNTHETIC_DEMO
CONFERENCE_RERUN
```

An original thesis result with no available data/code is recorded as `THESIS_REPORTED` and `NOT IMPLEMENTED` in the 2026 reimplementation. `SIMULATED` can never be presented as `IMPLEMENTED`.

The initial matrix explicitly includes PostgreSQL application state, the tamper-evident ledger, Hyperledger Fabric, PoA+, smart contracts, ZKP, rule baseline, synthetic generator, graph builder, XGBoost, TGNN, AI feedback, risk injection, model registry, policy engine, original thesis experiments, and conference reruns.

## 15. Dependency and Deployment Policy

The default Compose topology remains PostgreSQL plus the application. No new network service is added.

- Application runtime: NumPy and ONNX Runtime in addition to the existing dependencies.
- Research environment: PyTorch CPU, XGBoost, ONNX export tooling, and scientific evaluation packages in `requirements-research.txt`.
- GPU support is optional and not required for correctness or the reference artifact.
- The default launcher runs migrations, verifies PostgreSQL, validates the promoted artifact, and opens the page. It never trains a model.
- The one-command smoke test remains a mandatory release gate after every Research Core change.

Research Core is required in the shipped Compose demonstration. Its health check fails when the promoted artifact is missing, invalid, or incompatible. An explicit local maintenance setting, `RESEARCH_CORE_REQUIRED=false`, may keep the Engineering Core health check available while `/api/research` returns a sanitized 503 and the UI shows the model as unavailable. The shipped launcher never sets that maintenance override, so no extra user choice is required for normal startup and no fabricated score is used.

## 16. Testing Strategy

### 16.1 Data Tests

- deterministic canonical generation and content hash;
- schema constraints and referential integrity;
- registered counts and distributions;
- absence of label fields and future information in inputs;
- deterministic, immutable scenario revisions for risk injection and reset.

### 16.2 Graph Tests

- supplier-to-customer direction;
- active-interval filtering;
- monthly transaction aggregation;
- symmetric GCN adjacency with retained original direction;
- fixed node ordering and hashes;
- exact 12-month windows and 3-month labels;
- strict temporal split and training-only normalization.

### 16.3 Model Tests

- XGBoost and TGNN execute real fitting;
- loss/evaluation pipelines operate on nontrivial labels;
- checkpoint and manifests are emitted;
- same seed reproduces reference inference within tolerance;
- ONNX and PyTorch outputs meet parity tolerance;
- corrupted checkpoint, mismatched schema, or wrong snapshot is rejected;
- no minimum predictive score is enforced or fabricated.

### 16.4 Application Tests

- real inference creates lineage-complete assessment;
- policy thresholds and boundary values are exact;
- inference/policy/ledger persistence is atomic;
- failed inference writes no successful records;
- baseline use is explicit and cannot be a silent fallback;
- simulated risk injection changes inputs, rebuilds a snapshot, and reruns inference;
- before/after trace reaches the expected reference policy bands.

### 16.5 Audit Tests

- existing concurrent append and chain verification tests remain green;
- tamper detection records expected and actual evidence;
- integrity recovery does not truncate unrelated history;
- recovery appends both violation and completion events;
- the extended chain verifies after recovery;
- demo reset remains clearly distinct from incident recovery.

### 16.6 Release Tests

- all existing Engineering Core tests remain green;
- Alembic upgrades a clean PostgreSQL database to head;
- Alembic metadata-drift check passes;
- reference artifact can be regenerated and its manifest checked;
- clean-volume `start-demo.cmd` succeeds;
- Russian-default and Chinese-toggle Research Core paths pass browser acceptance;
- browser console reports zero errors and warnings;
- private repository contains source, manifests, small reference artifacts, documentation, and reproducibility commands, but no real data or uncontrolled experiment output.

## 17. Failure Handling

- Generator invariant failure: abort generation and do not register a dataset version.
- Graph/schema mismatch: abort snapshot creation and expose the exact internal diagnostic only in logs/tests.
- Training failure: mark `ModelRun` failed; do not create/promote `ModelVersion`.
- Artifact hash or ONNX parity failure: refuse promotion.
- Inference incompatibility: return sanitized 503 and write no success trace.
- Policy failure: roll back assessment, decision, and audit records.
- Ledger failure: roll back the complete post-inference transaction.
- Recovery-source mismatch: leave the incident unresolved and the ledger invalid; do not attempt silent reconstruction.

## 18. Implementation Increments

The implementation plan must decompose the work into these independently verifiable increments:

1. Traceability matrix, domain contracts, dependency separation, and failing contract tests.
2. PostgreSQL research schema and Alembic migrations.
3. Deterministic synthetic generator, manifest, and reference dataset tests.
4. Graph builder, temporal split, leakage checks, and snapshot registry.
5. XGBoost trainer/evaluator and candidate artifact workflow.
6. Minimal TGNN trainer, ONNX export, parity, and promoted reference artifact.
7. Runtime artifact verifier and inference service.
8. Policy Engine and atomic assessment-decision-ledger persistence.
9. Risk-injection scenario and lineage APIs.
10. Integrity incident and non-silent recovery semantics.
11. Bilingual Research Core UI and demonstration script.
12. Clean-environment, reproducibility, browser, review, and private-Git freeze gates.

Each increment uses tests first where behavior changes. Model-quality results are reported honestly and never changed merely to reach a desired answer.

## 19. Acceptance Criteria

Research Core v0.4 is accepted only when:

- the traceability matrix accurately separates thesis-reported, 2026-reimplemented, simulated, planned, and absent work;
- `synthetic-scf-v1` is reproducible at 500 enterprises and 24 months;
- graph construction matches the frozen node, edge, window, and label definitions;
- XGBoost and minimal TGNN are genuinely trained and evaluated;
- a promoted TGNN artifact is hash-verified and performs real CPU inference without training at application startup;
- every assessment records model, checkpoint, feature schema, dataset, snapshot, input hash, thresholds, decision, timestamp, and ledger evidence;
- risk injection changes only synthetic inputs and real inference produces the new score;
- policy actions remain restricted and cannot alter infrastructure;
- PostgreSQL is described as application state plus tamper-evident audit storage, not blockchain;
- integrity recovery leaves durable violation and recovery evidence;
- the original one-command PostgreSQL demonstration remains reliable;
- no claim states or implies reproduction of unavailable original thesis data, production performance, Fabric, PoA+, ZKP, or the full thesis TGNN.

## 20. Explicit Non-Goals

- real enterprise or bank data;
- numerical reproduction of the thesis's reported metrics;
- production credit decisioning;
- full thesis TGNN architecture;
- GraphSAGE, standalone LSTM/GCN, Random Forest, attention, or full ablation suite;
- Hyperledger Fabric, validators, chaincode, PoA+, or ZKP;
- direct AI control of consensus or infrastructure parameters;
- online learning, federated learning, or streaming ingestion;
- Redis, Kafka, RabbitMQ, Elasticsearch, GraphQL federation, microservices, Kubernetes, service mesh, or a separate authorization cluster.
