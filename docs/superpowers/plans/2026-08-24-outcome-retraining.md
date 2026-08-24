# Outcome Feedback and Retraining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest predicted-versus-actual financing outcomes, report evidence-aware drift, automatically train verified candidate artifacts when triggered, and require an audited human decision before runtime promotion.

**Architecture:** PostgreSQL stores immutable outcome snapshots and a leased job queue. An opt-in research worker reuses the existing synthetic temporal-graph pipeline, overlays explicitly linked outcomes onto copied labels, builds a complete versioned TGNN/XGBoost candidate bundle, and records gates. Deployment uses immutable version directories plus an atomic active-pointer file and a compensating database deployment record.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, PyTorch, XGBoost, ONNX, NumPy/SciPy, Docker Compose research profile, canonical JSON/SHA-256.

**Spec:** `docs/superpowers/specs/2026-08-24-research-evidence-defense-pack-design.md`

## Global Constraints

- Preserve the frozen reference artifact and always retain a rollback pointer.
- Label outcomes `MANUAL_OBSERVATION` or `SYNTHETIC_DEMO`; never aggregate them without source counts.
- A facility outcome may affect the TGNN training overlay only through an explicit synthetic research-entity link `E0001`–`E0500`; this mapping is a simulation boundary, not a real identity match.
- With fewer than 20 new labeled outcomes, drift returns `insufficient_evidence` and automatic threshold triggers do not enqueue a job.
- Training may start automatically, but runtime promotion always requires an auditor-approved human command and passing gates.
- No Redis, Kafka, request-process training, hidden fallback model, or mutation of immutable candidate artifacts.

---

### Task 1: Outcome domain, immutable snapshots, and migration

**Files:**
- Create: `app/domain/feedback.py`
- Create: `app/schemas_feedback.py`
- Create: `app/models_feedback.py`
- Create: `app/repositories/feedback.py`
- Create: `alembic/versions/20260824_0007_feedback_and_training_jobs.py`
- Create: `tests/test_feedback_database.py`
- Modify: `app/models.py`
- Modify: `app/models_facility.py`

**Interfaces:**
- Produces: `OutcomeKind`, `OutcomeSource`, `ObservedOutcomeModel`, `TrainingSnapshotModel`, `TrainingJobModel`, `CandidateEvaluationModel`, `DeploymentAttemptModel`, and repository CRUD/locking methods.

- [ ] **Step 1: Write failing schema/provenance/immutability tests**

```python
def test_outcome_binds_facility_prediction_and_feature_snapshot(session_factory, facility):
    outcome = insert_outcome(session_factory, facility, "defaulted", "MANUAL_OBSERVATION", "E0001")
    assert outcome.prediction_sha256 == canonical_sha256(outcome.feature_snapshot)
    assert outcome.predicted_score == facility.application.risk_score

def test_duplicate_observation_for_facility_horizon_is_rejected(session_factory, facility):
    insert_outcome(session_factory, facility, "repaid_on_time", "MANUAL_OBSERVATION", "E0001")
    with pytest.raises(IntegrityError):
        insert_outcome(session_factory, facility, "defaulted", "MANUAL_OBSERVATION", "E0001")
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_feedback_database.py -q`

Expected: FAIL because feedback schema/migration is absent.

- [ ] **Step 3: Implement normalized evidence/job tables**

Outcomes include facility/request/model IDs, synthetic research entity, feature/prediction snapshot, predicted score, observed class/date, source, creator, and content hash. Unique `(facility_id, observation_horizon)` prevents relabeling. Add job status `queued|leased|running|completed|failed|candidate_rejected`, lease owner/expiry/heartbeat, snapshot hash, attempts, and failure summary. Candidate evaluation stores current/candidate metrics, gate decisions, artifact locator/hash, and provenance counts. Deployment attempts store approver, reason, previous/new hashes, pointer backup, status, and timestamps.

- [ ] **Step 4: Run migration/database tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_feedback_database.py tests/test_database.py -q`

Run: `.\.venv\Scripts\python.exe -m alembic check`

Expected: PASS and no metadata drift.

- [ ] **Step 5: Commit**

```powershell
git add app/domain/feedback.py app/schemas_feedback.py app/models_feedback.py app/repositories/feedback.py app/models.py app/models_facility.py alembic tests/test_feedback_database.py
git commit -m "feat: persist immutable financing outcomes"
```

### Task 2: Outcome API and evidence-aware drift

**Files:**
- Create: `app/services/feedback.py`
- Create: `app/services/drift.py`
- Create: `app/api/feedback.py`
- Create: `tests/test_feedback_service.py`
- Create: `tests/test_feedback_api.py`
- Modify: `app/main.py`

**Interfaces:**
- Produces: `FeedbackService.record_outcome`, `seed_synthetic_outcomes`, `DriftService.evaluate(since) -> DriftReport`, `POST /api/v1/feedback/outcomes`, `GET /drift`, and `POST /demo-seed`.

- [ ] **Step 1: Write failing provenance, insufficiency, and metric tests**

```python
def test_drift_refuses_numbers_below_evidence_floor(drift_service):
    report = drift_service.evaluate(outcomes(19))
    assert report.status == "insufficient_evidence"
    assert report.minimum_required == 20
    assert report.metrics == {}

def test_drift_reports_sources_and_correct_metrics(drift_service):
    report = drift_service.evaluate(outcomes(20, manual=3, synthetic=17))
    assert report.source_counts == {"MANUAL_OBSERVATION": 3, "SYNTHETIC_DEMO": 17}
    assert report.label_rate_change == pytest.approx(expected_label_change)
    assert report.score_psi == pytest.approx(expected_psi)
    assert report.brier_change == pytest.approx(expected_brier_change)
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_feedback_service.py tests/test_feedback_api.py -q`

Expected: FAIL because feedback/drift services are absent.

- [ ] **Step 3: Implement authorized outcome recording and stable drift bins**

Only financier records `MANUAL_OBSERVATION`; risk manager/auditor can view; only auditor can seed deterministic demo outcomes. Require facility status `closed` or an explicitly matured overdue/default case. Snapshot the exact workflow risk evidence and fields at record time. Use fixed score bins `[0,.1,...,1]`, epsilon `1e-6`, and reference proportions from the active model evaluation manifest. Brier change requires both classes; otherwise return the metric as unavailable with a reason.

- [ ] **Step 4: Run service/API tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_feedback_service.py tests/test_feedback_api.py -q`

Expected: PASS; unauthorized, duplicate, immature, and source-forging requests fail.

- [ ] **Step 5: Commit**

```powershell
git add app/services/feedback.py app/services/drift.py app/api/feedback.py app/main.py tests/test_feedback_service.py tests/test_feedback_api.py
git commit -m "feat: ingest outcomes and report drift"
```

### Task 3: Leased training queue and automatic trigger

**Files:**
- Create: `app/services/training_queue.py`
- Create: `tests/test_training_queue.py`
- Modify: `app/services/feedback.py`
- Modify: `app/config.py`

**Interfaces:**
- Produces: `TrainingQueue.maybe_enqueue`, `request_demo_job`, `claim(worker_id, lease_seconds)`, `heartbeat`, `complete`, `fail`, and `recover_expired`.

- [ ] **Step 1: Write failing threshold, lease, and concurrency tests**

```python
def test_twentieth_new_outcome_enqueues_exactly_one_job(queue):
    record_outcomes(19)
    assert queue.maybe_enqueue() is None
    record_outcomes(1)
    first = queue.maybe_enqueue()
    second = queue.maybe_enqueue()
    assert first is not None and second.training_job_id == first.training_job_id

def test_two_workers_cannot_claim_same_job(queue, barrier):
    results = claim_concurrently(queue, barrier, ("worker-a", "worker-b"))
    assert sum(result is not None for result in results) == 1

def test_expired_lease_returns_job_to_queue(queue, clock):
    job = queue.claim("dead-worker", lease_seconds=30)
    clock.advance(seconds=31)
    queue.recover_expired()
    assert queue.claim("new-worker", lease_seconds=30).training_job_id == job.training_job_id
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_queue.py -q`

Expected: FAIL because queue service is absent.

- [ ] **Step 3: Implement snapshot identity and PostgreSQL lease semantics**

Build canonical snapshot JSON from sorted new outcome IDs/content hashes, active model hash, feature schema, source counts, and cutoff timestamp. Use a unique snapshot SHA-256 to deduplicate jobs. Claim with `SELECT ... FOR UPDATE SKIP LOCKED`, set 10-minute lease and heartbeat every 30 seconds, cap attempts at 3, and store bounded failure summaries without raw private data.

- [ ] **Step 4: Run queue and feedback tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_queue.py tests/test_feedback_service.py -q`

Expected: PASS including real barrier concurrency.

- [ ] **Step 5: Commit**

```powershell
git add app/services/training_queue.py app/services/feedback.py app/config.py tests/test_training_queue.py
git commit -m "feat: trigger leased candidate training jobs"
```

### Task 4: Outcome overlay and research worker candidate bundle

**Files:**
- Create: `research/data/outcome_overlay.py`
- Create: `research/worker.py`
- Create: `research/training/candidate.py`
- Create: `Dockerfile.research-worker`
- Create: `tests/research/test_outcome_overlay.py`
- Create: `tests/research/test_candidate_worker.py`
- Modify: `research/cli.py`

**Interfaces:**
- Consumes: Task 3 jobs/snapshots and existing build-reference components.
- Produces: `apply_outcomes(dataset, links) -> SyntheticDataset`, `build_candidate(snapshot, output) -> CandidateBundle`, `python -m research.worker`, and immutable `candidate-manifest.json`.

- [ ] **Step 1: Write failing overlay, source, and worker-crash tests**

```python
def test_overlay_copies_dataset_and_changes_only_linked_labels(reference_dataset):
    updated = apply_outcomes(reference_dataset, [linked_outcome("E0001", month=24, defaulted=True)])
    assert updated is not reference_dataset
    assert reference_dataset.severe_events[23, 0] == original_value
    assert updated.severe_events[23, 0] == 1
    assert np.array_equal(updated.states, reference_dataset.states)

def test_worker_failure_preserves_logs_and_never_registers_candidate(worker, fake_trainer):
    fake_trainer.raise_after_checkpoint()
    worker.run_once()
    assert read_job().status == "failed"
    assert list_candidates() == []
    assert Path(read_job().logs_locator).exists()
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/research/test_outcome_overlay.py tests/research/test_candidate_worker.py -q`

Expected: FAIL because overlay/worker do not exist.

- [ ] **Step 3: Implement copied-label overlay and complete candidate assembly**

Map each explicit `E####`/observation month to a copied `severe_events` array, preserve all other arrays, and add an outcome-overlay hash/provenance section to the dataset manifest. Train TGNN and XGBoost with the existing temporal split, export ONNX, verify parity, write raw/aggregate metrics, and publish only a fully verified version directory under `/artifacts/candidates/{candidate_id}`. Heartbeat between major stages.

Register candidate only after hash verification. Record source counts and state clearly that facility-to-`E####` is a synthetic research projection.

- [ ] **Step 4: Run worker tests and one demo candidate smoke job**

Run: `.\.venv\Scripts\python.exe -m pytest tests/research/test_outcome_overlay.py tests/research/test_candidate_worker.py -q`

Run: `.\.venv\Scripts\python.exe -m research.worker --once`

Expected: test candidate verifies; failed candidate never appears in registry.

- [ ] **Step 5: Commit**

```powershell
git add research/data/outcome_overlay.py research/worker.py research/training/candidate.py research/cli.py Dockerfile.research-worker tests/research/test_outcome_overlay.py tests/research/test_candidate_worker.py
git commit -m "feat: train verified feedback candidates"
```

### Task 5: Candidate gates, human promotion, and rollback pointer

**Files:**
- Create: `app/services/model_deployment.py`
- Create: `app/api/model_management.py`
- Create: `research/artifacts/active_pointer.py`
- Create: `tests/test_model_deployment.py`
- Create: `tests/test_model_management_api.py`
- Modify: `app/services/research_inference.py`
- Modify: `app/main.py`

**Interfaces:**
- Produces: `evaluate_candidate(candidate, current) -> GateReport`, `DeploymentService.promote`, `rollback`, `ResearchInferenceService.reload`, `GET /api/v1/model-candidates`, `POST /{id}/promote`, and `POST /deployments/{id}/rollback`.

- [ ] **Step 1: Write failing gate, approval, pointer, and compensation tests**

```python
def test_degraded_candidate_cannot_be_promoted(deployment_service, degraded_candidate, auditor):
    with pytest.raises(CandidateGateFailed):
        deployment_service.promote(degraded_candidate.id, auditor, reason="demo")
    assert active_pointer_hash() == FROZEN_REFERENCE_HASH

def test_pointer_failure_restores_previous_model(deployment_service, candidate, auditor, monkeypatch):
    monkeypatch.setattr(Path, "replace", fail_on_new_pointer_replace)
    with pytest.raises(OSError):
        deployment_service.promote(candidate.id, auditor, reason="validated candidate")
    assert verify_active_model().artifact_sha256 == FROZEN_REFERENCE_HASH
    assert latest_deployment().status == "failed"
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_model_deployment.py tests/test_model_management_api.py -q`

Expected: FAIL because deployment service/API are absent.

- [ ] **Step 3: Implement gates and compensating pointer switch**

Require exact feature schema, verified ONNX parity, finite untouched-holdout metrics, candidate ROC-AUC not more than `0.01` below current, candidate Brier not more than `0.01` above current, and complete provenance. Only auditor can promote/rollback. Create a `pending` deployment row, atomically replace canonical `active-model.json`, reload a new ONNX session, finalize database model slot and ledger events `MODEL_CANDIDATE_PROMOTED` / `MODEL_DEPLOYMENT_ROLLED_BACK`; on any failure restore the prior pointer/session and mark deployment failed.

- [ ] **Step 4: Run deployment/API and frozen-model tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_model_deployment.py tests/test_model_management_api.py tests/test_research_api.py -q`

Expected: PASS; financier/risk manager cannot promote and degraded candidates remain immutable.

- [ ] **Step 5: Commit**

```powershell
git add app/services/model_deployment.py app/api/model_management.py app/services/research_inference.py app/main.py research/artifacts/active_pointer.py tests/test_model_deployment.py tests/test_model_management_api.py
git commit -m "feat: gate and control model deployment"
```

### Task 6: Research profile, bilingual monitoring UI, and live acceptance

**Files:**
- Create: `docker-compose.research.yml`
- Create: `scripts/retraining_browser_acceptance.py`
- Create: `tests/test_retraining_release_contract.py`
- Modify: `start-advanced-demo.cmd`
- Modify: `app/static/index.html`
- Modify: `app/static/workflow.css`
- Modify: `app/static/workflow.js`
- Modify: `tests/test_ui_contract.py`
- Modify: `README.md`
- Modify: `docs/demo-script.md`
- Modify: `docs/thesis-traceability.md`
- Modify: `docs/mvp-design.md`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: opt-in `research-worker`, feedback/drift/job/candidate UI, and end-to-end outcome→job→candidate→approval→rollback evidence.

- [ ] **Step 1: Write failing profile/UI/boundary tests**

```python
def test_worker_is_opt_in_and_has_no_host_port():
    default = _read("docker-compose.yml")
    research = _read("docker-compose.research.yml")
    assert "research-worker" not in default
    assert "research-worker:" in research
    assert "ports:" not in research.split("research-worker:", 1)[1]

def test_ui_separates_manual_and_synthetic_outcomes():
    html = _html()
    assert html.count("manualObservation:") == 2
    assert html.count("syntheticDemoOutcome:") == 2
    assert html.count("insufficientEvidence:") == 2
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_retraining_release_contract.py tests/test_ui_contract.py -q`

Expected: FAIL because profile/UI is absent.

- [ ] **Step 3: Implement monitoring UI and opt-in worker profile**

Show source counts, drift status, jobs with lease/attempt state, candidate gates, artifact hashes, approve/reject/rollback buttons, and active model. Require confirmation text for promotion and display the frozen model rollback path. Advanced launcher composes default + Fabric/ZKP + research worker only when explicitly selected.

- [ ] **Step 4: Run full default and research-profile verification**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Run: normal Docker health and existing browser acceptance.

Run: research profile; seed 20 labeled synthetic outcomes; confirm automatic job; wait for verified candidate; promote as auditor; run inference; rollback; verify both model hashes and ledger events.

Expected: complete acceptance passes; stopping worker does not affect default API; current reference remains recoverable.

- [ ] **Step 5: Independent scientific/boundary review, commit, and push**

Confirm that UI/docs never call five-seed or feedback results original thesis reproduction, manual/synthetic counts remain visible, automatic training is not called automatic deployment, and the default model remains frozen.

```powershell
git add docker-compose.research.yml scripts/retraining_browser_acceptance.py tests/test_retraining_release_contract.py start-advanced-demo.cmd app/static tests/test_ui_contract.py README.md docs
git commit -m "feat: demonstrate controlled automatic retraining"
git push origin main
```
