# Research Core v0.4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `test-driven-development` for every behavior change and `verification-before-completion` before each commit. This plan is executed inline because the user already authorized continuous implementation.

**Goal:** Build a reproducible synthetic-data → temporal graph → trained XGBoost/TGNN → policy → PostgreSQL tamper-evident ledger demonstration without adding network services.

**Architecture:** Keep the existing FastAPI/PostgreSQL monolith and add a framework-free research/domain core, an offline `research` package, and a promoted-ONNX runtime adapter. Immutable manifests and PostgreSQL registry rows bind every inference to data, graph, model, policy, and ledger evidence.

**Tech Stack:** Python 3.12, NumPy, PyTorch CPU, XGBoost, ONNX/ONNX Runtime, scikit-learn metrics, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 17, pytest, Docker Compose, native browser JavaScript/CSS.

**Spec:** `docs/superpowers/specs/2026-08-15-research-core-v0.4-design.md`

## Global Constraints

- Reference data is exactly 500 enterprises × 24 months with seed `20260815` and contains no real enterprise data.
- Model input is months `t-11..t`; the label is a severe event in `t+1..t+3`; anchors split into train `12..16`, validation `17..18`, test `19..21`.
- The minimal TGNN is one 32-dimensional dense GCN, a 32-per-direction BiLSTM, and an MLP `64 → 32 → 1`.
- PostgreSQL remains the only database and the only two Compose services remain `postgres` and `mvp`.
- The PostgreSQL hash chain is called a tamper-evident audit ledger, never a blockchain.
- Training is CLI-only; FastAPI startup loads a promoted ONNX artifact and never trains.
- Russian remains the default UI language and every new string has a Chinese translation.
- Allowed traceability statuses are exactly `IMPLEMENTED`, `PARTIAL`, `SIMULATED`, `PLANNED`, and `NOT IMPLEMENTED`.
- No Redis, Kafka, RabbitMQ, microservices, Kubernetes, Fabric, PoA+, ZKP, or silent rule-model fallback.

---

### Task 1: Truth matrix, dependency boundary, and pure domain contracts

**Files:**
- Create: `docs/thesis-traceability.md`
- Create: `requirements-research.txt`
- Create: `app/domain/__init__.py`
- Create: `app/domain/research.py`
- Create: `app/domain/audit.py`
- Create: `app/services/__init__.py`
- Create: `app/services/policy.py`
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Modify: `docs/thesis-to-mvp.md`
- Test: `tests/test_research_domain.py`

**Interfaces:**
- Produces: `PolicyEngine.evaluate(assessment: RiskAssessment) -> PolicyDecision`.
- Produces: immutable domain dataclasses `DatasetVersion`, `GraphSnapshot`, `ModelVersion`, `RiskAssessment`, `PolicyDecision`, `IntegrityIncident`, and `LedgerEvent`.

- [ ] Write failing tests for exact policy boundaries, score validation, immutable domain values, and the absence of SQLAlchemy/FastAPI imports from `app.domain`.
- [ ] Run `python -m pytest tests/test_research_domain.py -q`; verify collection fails because the domain package does not exist.
- [ ] Implement frozen dataclasses and policy behavior:

```python
class PolicyEngine:
    version = "scf-risk-policy-v0.4"
    def evaluate(self, assessment: RiskAssessment) -> PolicyDecision:
        if assessment.risk_score < 0.40:
            return PolicyDecision.from_assessment(assessment, "NORMAL", "normal_monitoring", self.version, 0.40, 0.75)
        if assessment.risk_score < 0.75:
            return PolicyDecision.from_assessment(assessment, "ADDITIONAL_CHECK", "additional_verification", self.version, 0.40, 0.75)
        return PolicyDecision.from_assessment(assessment, "FINANCING_REVIEW", "financing_review", self.version, 0.40, 0.75)
```

- [ ] Add pinned runtime/research dependency files and canonical traceability rows with source, provenance, verification, and limitations.
- [ ] Run the focused tests and existing suite; commit `feat: define Research Core contracts`.

### Task 2: PostgreSQL research registry schema

**Files:**
- Create: `app/models_research.py`
- Create: `alembic/versions/20260815_0002_research_core.py`
- Modify: `app/models.py`
- Modify: `alembic/env.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_research_database.py`

**Interfaces:**
- Produces ORM models for `dataset_versions`, `synthetic_scenarios`, `graph_snapshots`, `model_runs`, `model_versions`, `risk_assessments`, `policy_decisions`, and `integrity_incidents`.
- Extends `ledger_events` with `stream_id` and research event types while preserving the global stream.

- [ ] Write migration tests that inspect all tables, PostgreSQL-native types, FK indexes, score constraints, lifecycle constraints, and the partial unique default deployment slot.
- [ ] Run `python -m pytest tests/test_research_database.py -q`; verify failure because migration `0002` is absent.
- [ ] Implement normalized SQLAlchemy models using UUID identities, `TIMESTAMPTZ`, `JSONB`, `DOUBLE PRECISION`, explicit FK indexes, and row-local checks.
- [ ] Implement migration `0002`, including removal of the financing-only ledger FK, new event types, and `stream_id='global'`.
- [ ] Upgrade a clean Testcontainers PostgreSQL database, run schema tests and Alembic metadata comparison; commit `feat: add research registry schema`.

### Task 3: Deterministic synthetic dataset and manifests

**Files:**
- Create: `research/__init__.py`
- Create: `research/data/__init__.py`
- Create: `research/data/schema.py`
- Create: `research/data/generator.py`
- Create: `research/data/manifest.py`
- Test: `tests/research/test_data_generator.py`

**Interfaces:**
- Produces: `generate_dataset(config: GeneratorConfig = GeneratorConfig.reference()) -> SyntheticDataset`.
- Produces: `SyntheticDataset.canonical_bytes() -> bytes` and `build_dataset_manifest(dataset) -> DatasetManifest`.
- Array contract: states `[24, 500, 7]`, relationships `[edge_count, 2]`, edge observations `[24, edge_count, 4]`, severe events `[24, 500]`.

- [ ] Write failing tests for exact dimensions, seed reproducibility, different-seed divergence, relationship constraints, future-label absence, and canonical SHA-256.
- [ ] Run `python -m pytest tests/research/test_data_generator.py -q`; verify import failure.
- [ ] Implement deterministic NumPy generation with stable node ordering, directed supplier/customer edges, evolving latent risk, observable noise, transactions, and future severe events.
- [ ] Implement canonical little-endian array serialization and a JSON manifest containing code/config/schema/count/hash identities.
- [ ] Run the focused tests twice in separate processes and compare the reference content hash; commit `feat: generate reproducible synthetic SCF data`.

### Task 4: Temporal graph builder and leakage-safe split

**Files:**
- Create: `research/graph/__init__.py`
- Create: `research/graph/features.py`
- Create: `research/graph/builder.py`
- Create: `research/graph/split.py`
- Test: `tests/research/test_graph_builder.py`

**Interfaces:**
- Produces: `build_graph_series(dataset, scenario=None) -> GraphSeries`.
- Produces: `build_samples(series) -> TemporalSamples` with `x [10,12,500,F]`, `adjacency [10,12,500,500]`, and `y [10,500]`.
- Produces: `temporal_split(samples) -> (train, validation, test)` with fixed anchor indices.

- [ ] Write failing tests using hand-derived three-node fixtures for direction, active intervals, monthly aggregation, adjacency symmetry, window/label boundaries, and training-only normalization.
- [ ] Run the focused test; verify failure on missing graph package.
- [ ] Implement feature aggregation, dense symmetric normalization with self-loops, and immutable snapshot hashes while retaining original directed edges.
- [ ] Implement one-based anchor semantics and normalization fitted only on anchors 12–16.
- [ ] Run data and graph tests; commit `feat: build temporal supply chain graphs`.

### Task 5: XGBoost comparison pipeline

**Files:**
- Create: `research/models/__init__.py`
- Create: `research/models/xgboost_model.py`
- Create: `research/training/__init__.py`
- Create: `research/training/metrics.py`
- Create: `research/training/train_xgboost.py`
- Test: `tests/research/test_xgboost_training.py`

**Interfaces:**
- Produces: `train_xgboost(samples, config) -> TrainedXGBoost`.
- Produces metrics `roc_auc`, `pr_auc`, `f1`, `precision`, `recall`, `confusion_matrix`, and `brier_score` without a score gate.

- [ ] Write a failing reduced-fixture test proving real fitting changes predictions and emits all metrics and a JSON model hash.
- [ ] Run it and verify missing implementation failure.
- [ ] Flatten temporal node/edge summaries without mixing temporal splits, fit the exact registered parameters, and persist candidate/evaluated manifests.
- [ ] Add deterministic CLI output under ignored `output/research/`.
- [ ] Run focused tests; commit `feat: train XGBoost research baseline`.

### Task 6: Minimal TGNN, evaluation, and ONNX promotion

**Files:**
- Create: `research/models/tgnn.py`
- Create: `research/training/train_tgnn.py`
- Create: `research/training/export_onnx.py`
- Create: `research/artifacts/__init__.py`
- Create: `research/artifacts/registry.py`
- Create: `research/artifacts/verification.py`
- Create: `research/cli.py`
- Create: `artifacts/reference/*` through the CLI
- Test: `tests/research/test_tgnn_training.py`
- Test: `tests/research/test_artifact_verification.py`

**Interfaces:**
- Produces: `TemporalGCNBiLSTM.forward(x, adjacency) -> logits [batch,node]`.
- Produces: `promote_tgnn(run_dir, reference_dir) -> ModelManifest` after ONNX parity.
- Produces: `verify_reference_artifact(reference_dir) -> VerifiedArtifact`.

- [ ] Write failing shape/gradient/training tests on a small fixture plus artifact corruption, schema mismatch, and ONNX parity tests.
- [ ] Run focused tests and verify missing classes/functions.
- [ ] Implement dense GCN → node-wise BiLSTM → MLP, seeded Adam training, training-only class weight, early stopping, metrics, and checkpoint hashing.
- [ ] Export with named/dynamic batch inputs, verify PyTorch/ONNX parity, and promote only after every manifest/hash check succeeds.
- [ ] Train the 500×24 reference run, freeze small manifests/NPZ/ONNX artifacts, rerun verification and focused tests; commit `feat: train and promote minimal TGNN`.

### Task 7: Runtime artifact adapter and research status API

**Files:**
- Create: `app/repositories/research.py`
- Create: `app/services/research_inference.py`
- Create: `app/schemas_research.py`
- Create: `app/api/__init__.py`
- Create: `app/api/research.py`
- Modify: `app/config.py`
- Modify: `app/main.py`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Test: `tests/test_research_api.py`

**Interfaces:**
- Produces: `ResearchInferenceService.infer(enterprise_id, snapshot_id, model_version_id) -> RiskAssessment`.
- Routes: `GET /api/research/status`, `POST /api/research/inference`, and `GET /api/research/assessments/{id}/trace`.

- [ ] Write failing API tests for status identities, real ONNX inference, sanitized 503, missing model, and no rule fallback.
- [ ] Run them and verify 404/missing-service failures.
- [ ] Implement a verified ONNX Runtime adapter, registry repository, route-level Pydantic validation, and `RESEARCH_CORE_REQUIRED` health semantics.
- [ ] Copy only runtime artifacts into Docker and keep PyTorch/XGBoost out of the image.
- [ ] Run focused API tests and a container health smoke; commit `feat: serve promoted Research Core inference`.

### Task 8: Policy and atomic assessment-decision-ledger persistence

**Files:**
- Create: `app/services/research_decision.py`
- Modify: `app/repositories/research.py`
- Modify: `app/repositories/ledger.py`
- Modify: `app/api/research.py`
- Test: `tests/test_research_decision.py`

**Interfaces:**
- Produces: `ResearchDecisionService.assess(snapshot_request) -> TraceResult`.
- Audit port accepts `stream_id='global'`; PostgreSQL adapter rejects any other stream.

- [ ] Write failing tests for all policy boundaries, complete lineage payload, unsupported stream, and rollback when the third ledger event fails.
- [ ] Run them and verify missing orchestration failure.
- [ ] Persist assessment, decision, `MODEL_INFERENCE_COMPLETED`, `RISK_POLICY_TRIGGERED`, and `CONTROL_ACTION_REQUESTED` in one transaction.
- [ ] Include checkpoint, dataset, scenario, graph, input, score, thresholds, action, and timestamps in the returned trace and audit payload.
- [ ] Run focused and legacy atomic/concurrency tests; commit `feat: persist traceable model decisions`.

### Task 9: Versioned risk-injection scenario

**Files:**
- Create: `app/services/research_scenario.py`
- Modify: `app/repositories/research.py`
- Modify: `app/api/research.py`
- Modify: `research/graph/builder.py`
- Test: `tests/test_research_scenario.py`

**Interfaces:**
- Routes: `POST /api/research/scenarios/{enterprise_id}/inject-risk` and `POST /api/research/scenarios/reset`.
- Produces two lineage-complete before/after traces; never accepts a caller-provided score.

- [ ] Write failing tests that prove reference data remains unchanged, revisions are immutable, visible inputs change, the graph hash changes, ONNX reruns, and the intended policy band changes.
- [ ] Run them and verify route/import failure.
- [ ] Implement deterministic overlay creation, graph rebuild, before/after decision orchestration, `SIMULATED_RISK_INJECTED`, and revision-zero reset selection.
- [ ] Reject score/model-output fields at the API boundary.
- [ ] Run focused tests and inspect trace hashes; commit `feat: add traceable risk injection scenario`.

### Task 10: Non-silent integrity incident recovery

**Files:**
- Create: `app/services/integrity.py`
- Modify: `app/repositories/ledger.py`
- Modify: `app/repositories/research.py`
- Modify: `app/main.py`
- Test: `tests/test_integrity_recovery.py`

**Interfaces:**
- Produces: `IntegrityService.detect() -> IntegrityIncident` and `recover(incident_id, operator) -> RecoveryResult`.
- Routes: `POST /api/demo/tamper`, `POST /api/demo/recover`; reset remains separately destructive.

- [ ] Write failing tests proving detection commits unresolved evidence, bad fixtures leave it unresolved, unrelated history is preserved, and successful recovery appends both required events.
- [ ] Run them and verify missing recovery behavior.
- [ ] Capture the trusted original event fixture before simulated tampering, then implement detection and locked recovery as two explicit transactions.
- [ ] Restore only the affected synthetic event, revalidate, append `INTEGRITY_VIOLATION_DETECTED` and `LEDGER_RECOVERY_COMPLETED`, update the incident, and reverify.
- [ ] Run audit, concurrency, reset, and API tests; commit `feat: preserve audit evidence during recovery`.

### Task 11: Russian/Chinese Research Core demonstration UI

**Files:**
- Modify: `app/static/index.html`
- Modify: `docs/demo-script.md`
- Test: `tests/test_ui_contract.py`

**Interfaces:**
- Adds one Research Core page/section with dataset, graph window, connected enterprises, model hash, before/after inference, policy, and ledger lineage.

- [ ] Write failing DOM-contract tests for Russian default, complete Chinese translation keys, research controls, status labels, and absence of a PostgreSQL-blockchain claim.
- [ ] Run them and verify missing selectors/translations.
- [ ] Implement a compact “research dossier” visual language: graph-flow rail as the signature element, deep navy/ice/amber tokens, restrained motion, keyboard focus, responsive layout, and reduced-motion support.
- [ ] Connect status, inference, injection, reset, trace, error, loading, and empty states to the new APIs with consistent bilingual action copy.
- [ ] Run API/UI tests and Playwright reconnaissance, interactions, screenshots, and console capture; commit `feat: add bilingual Research Core demo`.

### Task 12: Reproducibility, deployment, documentation, and freeze gates

**Files:**
- Modify: `README.md`
- Modify: `docs/mvp-design.md`
- Modify: `docs/demo-script.md`
- Modify: `start-demo.cmd`
- Modify: `launcher-messages.json`
- Modify: `docs/thesis-traceability.md`
- Test: `tests/test_release_contract.py`

**Interfaces:**
- Produces documented commands `python -m research.cli generate`, `train-xgboost`, `train-tgnn`, `promote`, and `verify`.
- Preserves one-command Docker demonstration at `http://127.0.0.1:8010`.

- [x] Write failing executable release-contract tests for CLI help, artifact verification, launcher health payload, and canonical status/provenance values.
- [x] Run them and verify current documentation/launcher gaps.
- [x] Update bilingual startup/demo guidance and traceability evidence without claiming original-thesis reproduction or production validation.
- [x] Run full unit/integration suite, clean-database Alembic upgrade and drift check, deterministic regeneration, Docker clean-volume startup, and Playwright RU/ZH/tamper/recovery flows.
- [x] Run `git diff --check`, request code review, resolve findings, verify the private remote, commit `docs: freeze Research Core v0.4 evidence`, and push `main`.
