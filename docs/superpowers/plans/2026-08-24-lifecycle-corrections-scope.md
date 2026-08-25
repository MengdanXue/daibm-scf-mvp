# Governed Lifecycle, Corrections, and Scope Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a truthful restructuring/default/write-off lifecycle, immutable training corrections, durable PostgreSQL calibration jobs, independent five-fold validation, and exact deployment-scope isolation.

**Architecture:** Extend the existing PostgreSQL modular monolith through versioned facility commands and append-only evidence tables. Outcome submission and correction transactions enqueue scoped calibration jobs; a lifespan worker performs deterministic out-of-fold validation, publishes v3 Platt artifacts, and activates at most one verified run per scope. Workflow inference always selects an exact request scope and falls back to the transparent baseline on absence, invalidation, corruption, or mismatch.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, PostgreSQL 17, Alembic, NumPy, pytest, Playwright, vanilla JavaScript/CSS, Docker Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-24-lifecycle-corrections-scope-design.md`

## Global Constraints

- Preserve the existing PostgreSQL-only modular monolith, SQLAlchemy repositories, append-only audit ledger, and baseline-safe inference.
- Do not introduce Redis, a message broker, microservices, Kubernetes, real payment execution, or online TGNN training.
- Keep Russian as the default language, provide complete Chinese translations, and preserve the 390 px mobile contract.
- Only `controlled_demo` and `external_verified` are deployable scopes; `mixed` is legacy-readable and never trainable or activatable.
- Require at least 20 eligible observations, 5 defaulted observations, 5 non-defaulted observations, and 5 distinct original scores.
- Use deterministic stratified five-fold out-of-fold Brier score and log loss for activation; final-fit metrics never control activation.
- Never rewrite or delete actual outcomes, lifecycle evidence, corrections, run membership, or audit-ledger events.
- Invalidate an affected active artifact in the same transaction as an `EXCLUDE` correction and use the baseline until a valid replacement is active.
- Do not change or move Git tag `v1.0.0-defense`, and do not modify frozen thesis/defense artifacts.
- Keep all changes on private repository `MengdanXue/daibm-scf-mvp`; every task ends with focused verification and a commit.

## File responsibility map

- `app/domain/facility.py`: pure statuses, actions, transitions, money rules, and closure classification.
- `app/schemas_facility.py`: strict versioned lifecycle command payloads.
- `app/models_lifecycle.py`: immutable delinquency, restructure, default, and write-off ORM records.
- `app/models_governance.py`: immutable corrections, calibration jobs, and run-to-outcome membership.
- `app/models_facility.py`, `app/models_outcome.py`, `app/models.py`: aggregate, calibration-run, and request columns/checks.
- `app/repositories/facility.py`: locked facility/lifecycle persistence queries.
- `app/repositories/outcomes.py`: corrections, scoped datasets, jobs, membership, and per-scope deployment locks.
- `app/services/facility.py`: versioned lifecycle orchestration and derived facility presentation.
- `app/services/outcomes.py`: lifecycle-derived outcome recording, correction transactions, deployment governance, and API serialization.
- `app/services/outcome_calibration.py`: deterministic five-fold OOF fitting and v3 artifact construction.
- `app/services/calibration_jobs.py`: durable claim/lease/retry worker and one-scope-at-a-time training.
- `app/services/adaptive_risk.py`: exact-scope artifact selection, verification, activation gates, and baseline fallback.
- `app/services/workflow.py`: assigns request scope and passes it into adaptive inference.
- `app/api/facility.py`, `app/api/outcomes.py`: role-gated lifecycle/correction/job/deployment HTTP contracts.
- `app/static/workflow.js`, `app/static/workflow.css`: Russian/Chinese lifecycle and governance workbench.
- `scripts/outcome_browser_acceptance.py`: real browser lifecycle, correction, retraining, and scoped-inference proof.

---

### Task 1: Pure lifecycle state machine and strict commands

**Files:**
- Modify: `app/domain/facility.py`
- Modify: `app/schemas_facility.py`
- Modify: `app/api/facility.py`
- Test: `tests/test_facility_domain.py`

**Interfaces:**
- Produces `FacilityStatus.RESTRUCTURED`, `FacilityStatus.DEFAULTED`, `FacilityStatus.WRITTEN_OFF`, and `InstallmentStatus.SUPERSEDED`.
- Produces `MarkOverdueRequest`, `RestructureFacilityRequest`, `DeclareDefaultRequest`, and `WriteOffRequest`.
- Produces `derive_closure_reason(*, has_default: bool, has_writeoff: bool) -> str` for Task 3 and Task 4.

- [ ] **Step 1: Write failing transition and schema tests**

```python
def test_restructure_default_recovery_writeoff_and_close_transitions():
    assert next_facility_status(FacilityStatus.OVERDUE, FacilityAction.RESTRUCTURE, Role.RISK_MANAGER) == FacilityStatus.RESTRUCTURED
    assert next_facility_status(FacilityStatus.RESTRUCTURED, FacilityAction.DECLARE_DEFAULT, Role.RISK_MANAGER) == FacilityStatus.DEFAULTED
    assert next_facility_status(FacilityStatus.DEFAULTED, FacilityAction.CONFIRM_PAYMENT, Role.FINANCIER) == FacilityStatus.DEFAULTED
    assert next_facility_status(FacilityStatus.DEFAULTED, FacilityAction.CONFIRM_FINAL_PAYMENT, Role.FINANCIER) == FacilityStatus.REPAID
    assert next_facility_status(FacilityStatus.DEFAULTED, FacilityAction.WRITE_OFF, Role.AUDITOR) == FacilityStatus.WRITTEN_OFF
    assert next_facility_status(FacilityStatus.WRITTEN_OFF, FacilityAction.CLOSE, Role.AUDITOR) == FacilityStatus.CLOSED


def test_restructure_schedule_requires_contiguous_exact_outstanding_total():
    payload = RestructureFacilityRequest.model_validate({
        "version": 7,
        "idempotency_key": str(uuid.uuid4()),
        "reason_code": "BORROWER_CASH_FLOW",
        "comment": "Verified revised repayment capacity",
        "evidence_sha256": "a" * 64,
        "installments": [
            {"sequence": 1, "due_date": "2027-12-01", "amount": "400000.00"},
            {"sequence": 2, "due_date": "2028-01-01", "amount": "500000.00"},
        ],
    })
    assert payload.schedule_total == Decimal("900000.00")
```

- [ ] **Step 2: Run the focused tests and verify the new symbols are absent**

Run: `python -m pytest tests/test_facility_domain.py -q`

Expected: FAIL during collection because the new statuses and request models do not exist.

- [ ] **Step 3: Implement the lifecycle enums, transition map, and closure classification**

```python
class FacilityStatus(StrEnum):
    READY = "ready_for_disbursement"
    DISBURSED = "disbursed"
    ACTIVE = "active"
    OVERDUE = "overdue"
    RESTRUCTURED = "restructured"
    DEFAULTED = "defaulted"
    REPAID = "repaid"
    WRITTEN_OFF = "written_off"
    CLOSED = "closed"


class FacilityAction(StrEnum):
    RESTRUCTURE = "restructure"
    DECLARE_DEFAULT = "declare_default"
    WRITE_OFF = "write_off"


def derive_closure_reason(*, has_default: bool, has_writeoff: bool) -> str:
    if has_writeoff:
        if not has_default:
            raise ValueError("write-off requires default history")
        return "written_off"
    return "settled_after_default" if has_default else "repaid"
```

Add transition entries for payments/rejections in `active`, `overdue`, `restructured`, and `defaulted`; mark-overdue from `active`/`restructured` by `financier`; restructure/default by `risk_manager`; write-off/close by `auditor`.

- [ ] **Step 4: Implement strict command schemas with stable fields**

```python
class EvidenceCommand(VersionedFacilityCommand):
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    comment: str = Field(min_length=1, max_length=500)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class MarkOverdueRequest(VersionedFacilityCommand):
    installment_id: UUID
    days_past_due: int = Field(strict=True, ge=1, le=36500)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RestructureFacilityRequest(EvidenceCommand):
    installments: list[InstallmentRequest] = Field(min_length=1, max_length=120)

    @computed_field
    @property
    def schedule_total(self) -> Decimal:
        return sum((row.amount for row in self.installments), Decimal("0.00"))


class DeclareDefaultRequest(EvidenceCommand):
    defaulted_at: datetime
    days_past_due: int = Field(strict=True, ge=1, le=36500)


class WriteOffRequest(EvidenceCommand):
    pass
```

Normalize non-blank comments, require timezone-aware `defaulted_at`, and keep `extra="forbid"` and decimal-string money behavior.

- [ ] **Step 5: Run the focused tests and commit**

Run: `python -m pytest tests/test_facility_domain.py -q`

Expected: PASS.

```powershell
git add app/domain/facility.py app/schemas_facility.py app/api/facility.py tests/test_facility_domain.py
git commit -m "feat: define governed facility lifecycle"
```

---

### Task 2: Guarded PostgreSQL schema and ORM models

**Files:**
- Create: `alembic/versions/20260824_0010_lifecycle_corrections_scope.py`
- Create: `app/models_lifecycle.py`
- Create: `app/models_governance.py`
- Modify: `alembic/env.py`
- Modify: `app/models.py`
- Modify: `app/models_facility.py`
- Modify: `app/models_outcome.py`
- Test: `tests/test_lifecycle_governance_migration.py`

**Interfaces:**
- Produces lifecycle tables `facility_delinquencies`, `facility_restructures`, `facility_defaults`, and `facility_writeoffs`.
- Produces governance tables `outcome_corrections`, `calibration_jobs`, and `calibration_run_observations`.
- Produces non-null `financing_requests.assessment_scope`, versioned installments, closure metadata, `invalidated` deployment status, and one-active-per-scope uniqueness.
- Produces ORM classes `FacilityDelinquencyModel`, `FacilityRestructureModel`, `FacilityDefaultModel`, `FacilityWriteOffModel`, `OutcomeCorrectionModel`, `CalibrationJobModel`, and `CalibrationRunObservationModel`.

- [ ] **Step 1: Write a failing head-schema contract test**

```python
def test_lifecycle_governance_revision_is_single_head_and_constrained(migrated_engine):
    inspector = sa.inspect(migrated_engine)
    assert {"facility_delinquencies", "facility_restructures", "facility_defaults", "facility_writeoffs", "outcome_corrections", "calibration_jobs", "calibration_run_observations"} <= set(inspector.get_table_names())
    request_columns = {c["name"]: c for c in inspector.get_columns("financing_requests")}
    assert request_columns["assessment_scope"]["nullable"] is False
    indexes = {i["name"]: i for i in inspector.get_indexes("calibration_runs")}
    assert indexes["uq_calibration_runs_active_scope"]["unique"] is True
```

Also assert immutable triggers on lifecycle/correction/membership tables, exact hash checks, correction action checks, job lease/status checks, the replacement `(facility_id, schedule_version, sequence)` uniqueness, and foreign-key indexes.

- [ ] **Step 2: Write failing database-invariant and downgrade-guard tests**

```python
def test_database_rejects_two_active_runs_in_the_same_scope(session_factory):
    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            session.add_all([active_run(scope="controlled_demo"), active_run(scope="controlled_demo")])


def test_downgrade_refuses_new_governance_history(monkeypatch):
    monkeypatch.setattr(migration.op, "get_bind", lambda: FakeBind(count=1))
    with pytest.raises(RuntimeError, match="governed lifecycle or calibration data"):
        migration.downgrade()
```

- [ ] **Step 3: Run the migration tests and verify revision `0010` is absent**

Run: `python -m pytest tests/test_lifecycle_governance_migration.py -q`

Expected: FAIL because the revision, tables, and columns do not exist.

- [ ] **Step 4: Implement focused lifecycle ORM records**

```python
class FacilityDefaultModel(Base):
    __tablename__ = "facility_defaults"
    default_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"), unique=True, nullable=False)
    declared_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False)
    defaulted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    days_past_due: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FacilityWriteOffModel(Base):
    __tablename__ = "facility_writeoffs"
    writeoff_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"), unique=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    auditor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Implement delinquency and restructure rows with old/new schedule versions, actor, reason/comment/evidence, and timestamps. Add `current_schedule_version` and `closure_reason` to `FinancingFacilityModel`; add `schedule_version` and `superseded` to installments.

- [ ] **Step 5: Implement focused governance ORM records**

```python
class OutcomeCorrectionModel(Base):
    __tablename__ = "outcome_corrections"
    correction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    outcome_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("actual_outcomes.outcome_id", ondelete="RESTRICT"), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    auditor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    request_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CalibrationJobModel(Base):
    __tablename__ = "calibration_jobs"
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    deployment_scope: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_type: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_outcome_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("actual_outcomes.outcome_id", ondelete="RESTRICT"))
    trigger_correction_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("outcome_corrections.correction_id", ondelete="RESTRICT"))
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(Text)
    result_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("calibration_runs.calibration_run_id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

The job check constraint requires exactly one trigger FK, a lease only while running, completion fields only for terminal states, and `attempt_count BETWEEN 0 AND 3`. `CalibrationRunObservationModel` uses `(calibration_run_id, outcome_id)` as its primary key and stores `correction_head_id`; `CalibrationRunModel.trigger_outcome_id` becomes nullable and gains nullable unique `trigger_job_id` plus nullable `artifact_schema`; deployment accepts `invalidated`; the active index becomes unique on `deployment_scope` where status is active. Backfill existing successful artifacts as `daibm.platt-calibration.v2`; failed runs keep a null schema.

- [ ] **Step 6: Implement the guarded Alembic upgrade/backfill/downgrade**

```python
def upgrade() -> None:
    op.add_column("financing_requests", sa.Column("assessment_scope", sa.Text(), nullable=False, server_default="controlled_demo"))
    op.create_check_constraint("ck_financing_requests_assessment_scope", "financing_requests", "assessment_scope IN ('controlled_demo', 'external_verified')")
    op.add_column("facility_installments", sa.Column("schedule_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("calibration_runs", sa.Column("artifact_schema", sa.Text(), nullable=True))
    op.execute("UPDATE calibration_runs SET artifact_schema='daibm.platt-calibration.v2' WHERE artifact_sha256 IS NOT NULL")
    op.drop_index("uq_calibration_runs_single_active", table_name="calibration_runs")
    op.create_index("uq_calibration_runs_active_scope", "calibration_runs", ["deployment_scope"], unique=True, postgresql_where=sa.text("deployment_status = 'active'"))


def downgrade() -> None:
    count = op.get_bind().scalar(sa.text("SELECT (SELECT count(*) FROM facility_restructures) + (SELECT count(*) FROM facility_defaults) + (SELECT count(*) FROM facility_writeoffs) + (SELECT count(*) FROM outcome_corrections) + (SELECT count(*) FROM calibration_jobs WHERE status <> 'queued')"))
    if count:
        raise RuntimeError("Cannot downgrade while governed lifecycle or calibration data exists")
```

The full migration must recreate every affected check constraint and ledger event type, remove temporary server defaults after backfill, and never mutate actual-outcome rows.
It must also install a trigger that rejects changing a non-null `financing_facilities.closure_reason`, and it must backfill `calibration_run_observations` using each legacy synchronous run's trigger-outcome recording boundary so correction invalidation can find legacy membership without reading artifact files.

- [ ] **Step 7: Upgrade a real PostgreSQL database, run tests, and commit**

Run: `python -m alembic upgrade head`

Run: `python -m pytest tests/test_lifecycle_governance_migration.py tests/test_facility_migration.py tests/test_outcome_migration.py tests/test_self_training_migration.py -q`

Expected: PASS.

```powershell
git add alembic app/models.py app/models_facility.py app/models_outcome.py app/models_lifecycle.py app/models_governance.py tests/test_lifecycle_governance_migration.py
git commit -m "feat: persist lifecycle and calibration governance"
```

---

### Task 3: Transactional restructuring, default, recovery, write-off, and closure

**Files:**
- Modify: `app/repositories/facility.py`
- Modify: `app/services/facility.py`
- Modify: `app/api/facility.py`
- Test: `tests/test_facility_service.py`
- Test: `tests/test_facility_api.py`
- Test: `tests/test_facility_database.py`

**Interfaces:**
- Produces `FacilityService.restructure(...)`, `declare_default(...)`, and `write_off(...)`.
- Extends `FacilityService.mark_overdue(...)`, `submit_payment(...)`, `decide_payment(...)`, `close(...)`, `_serialize(...)`, and `_allowed_actions(...)`.
- Exposes `POST /api/v1/facilities/{id}/restructure`, `/declare-default`, and `/write-off`.
- Produces serialized `closure_reason`, `schedule_version`, `delinquencies`, `restructures`, `default_event`, and `writeoff_event` consumed by Task 4 and Task 8.

- [ ] **Step 1: Write failing service tests for financial invariants and immutable history**

```python
def test_restructure_supersedes_only_unpaid_future_schedule_and_preserves_balance(facility_context):
    result = service.restructure(facility_id, restructure_payload(version=5, total="900000.00"), users["risk_manager"])
    assert result["status"] == "restructured"
    assert result["outstanding_amount"] == "900000.00"
    assert result["current_schedule_version"] == 2
    assert [row["status"] for row in result["installments"] if row["schedule_version"] == 1] == ["paid", "superseded"]
    assert sum(Decimal(row["amount"]) for row in result["installments"] if row["schedule_version"] == 2) == Decimal("900000.00")


def test_default_recovery_writeoff_and_closure_preserve_truthful_loss(facility_context):
    defaulted = service.declare_default(facility_id, default_payload(version=6, days_past_due=45), users["risk_manager"])
    assert defaulted["status"] == "defaulted"
    recovered = confirm_recovery(defaulted, amount="200000.00")
    written_off = service.write_off(facility_id, writeoff_payload(version=recovered["version"]), users["auditor"])
    assert written_off["status"] == "written_off"
    assert written_off["outstanding_amount"] == "0.00"
    assert written_off["writeoff_event"]["amount"] == "700000.00"
    closed = service.close(facility_id, command(closed_version), users["auditor"])
    assert closed["closure_reason"] == "written_off"
```

Cover repeated default/write-off, schedule mismatch, paid-installment mutation, over-recovery, wrong role, stale version, idempotent replay, and rollback of action/ledger rows on conflict.

- [ ] **Step 2: Run focused service tests and verify missing methods fail**

Run: `python -m pytest tests/test_facility_service.py tests/test_facility_database.py -q`

Expected: FAIL because lifecycle repository queries and commands are absent.

- [ ] **Step 3: Add locked lifecycle repository queries**

```python
def get_default(self, session: Session, facility_id: uuid.UUID) -> FacilityDefaultModel | None:
    return session.scalar(select(FacilityDefaultModel).where(FacilityDefaultModel.facility_id == facility_id))


def get_writeoff(self, session: Session, facility_id: uuid.UUID) -> FacilityWriteOffModel | None:
    return session.scalar(select(FacilityWriteOffModel).where(FacilityWriteOffModel.facility_id == facility_id))


def list_installments(self, session: Session, facility_id: uuid.UUID) -> list[InstallmentModel]:
    return list(session.scalars(select(InstallmentModel).where(InstallmentModel.facility_id == facility_id).order_by(InstallmentModel.schedule_version, InstallmentModel.sequence)))
```

Add ordered list methods for delinquencies and restructures. Aggregate locking remains on `financing_facilities`; unique constraints provide the final concurrency barrier.

- [ ] **Step 4: Implement transactional lifecycle commands**

```python
def write_off(self, facility_id: str | uuid.UUID, request: WriteOffRequest, user: AuthenticatedUser) -> dict[str, Any]:
    semantic = self._command_semantic(FacilityAction.WRITE_OFF.value, {"facility_id": str(self._normalize_uuid(facility_id))}, request)
    with self.session_factory.begin() as session:
        replay = self._replay(session, request.idempotency_key, user, semantic)
        if replay is not None:
            return replay
        facility = self._load_for_command(session, facility_id, user)
        self._require_role(user, Role.AUDITOR)
        self._check_version(facility, request.version)
        target = self._next(facility, FacilityAction.WRITE_OFF, user)
        amount = Decimal(facility.outstanding_amount)
        if amount <= 0 or self.repository.get_writeoff(session, facility.facility_id) is not None:
            raise FacilityConflict("Write-off requires one positive defaulted balance")
        session.add(FacilityWriteOffModel(writeoff_id=uuid.uuid4(), facility_id=facility.facility_id, amount=amount, auditor_user_id=user.user_id, reason_code=request.reason_code, comment=request.comment, evidence_sha256=request.evidence_sha256, recorded_at=self._now()))
        facility.outstanding_amount = Decimal("0.00")
        self._advance(facility, target, self._now())
        self._record(session, facility, user, action=FacilityAction.WRITE_OFF, idempotency_key=request.idempotency_key, expected_version=request.version, semantic=semantic, event_types=[("FACILITY_WRITTEN_OFF", {"amount": self._money(amount), "evidence_sha256": request.evidence_sha256})])
        return self._serialize(session, facility, user)
```

Implement restructure by marking only unpaid future installments `superseded`, incrementing `current_schedule_version`, and inserting exact-balance replacements. Implement default as a once-only row. Allow recovery payments in `defaulted`; zero recovery produces `repaid` while the default row remains. Close derives `repaid`, `settled_after_default`, or `written_off` from immutable rows.

- [ ] **Step 5: Add API contracts and stable errors**

```python
@router.post("/{facility_id}/declare-default", response_model=FacilityResponse)
def declare_default(facility_id: str, payload: DeclareDefaultRequest, user: CurrentUser, request: Request):
    return _execute(lambda: request.app.state.facility_service.declare_default(facility_id, payload, user))


@router.post("/{facility_id}/write-off", response_model=FacilityResponse)
def write_off(facility_id: str, payload: WriteOffRequest, user: CurrentUser, request: Request):
    return _execute(lambda: request.app.state.facility_service.write_off(facility_id, payload, user))
```

Expand `FacilityResponse` with the produced lifecycle fields and add the restructure route. Map invariant conflicts to existing `facility_conflict` 409 responses.

- [ ] **Step 6: Run facility regression tests and commit**

Run: `python -m pytest tests/test_facility_domain.py tests/test_facility_database.py tests/test_facility_service.py tests/test_facility_api.py -q`

Expected: PASS.

```powershell
git add app/repositories/facility.py app/services/facility.py app/api/facility.py tests/test_facility_service.py tests/test_facility_api.py tests/test_facility_database.py
git commit -m "feat: complete governed financing lifecycle"
```

---

### Task 4: Lifecycle-derived outcomes and immutable correction transactions

**Files:**
- Modify: `app/schemas_outcome.py`
- Modify: `app/repositories/outcomes.py`
- Modify: `app/services/outcomes.py`
- Modify: `app/api/outcomes.py`
- Test: `tests/test_outcome_schema.py`
- Test: `tests/test_outcome_repository.py`
- Test: `tests/test_outcome_service.py`
- Test: `tests/test_outcome_api.py`

**Interfaces:**
- Replaces caller-controlled outcome facts with `derive_outcome_facts(session, facility) -> DerivedOutcomeFacts`.
- Produces `OutcomeCorrectionCreate`, `OutcomeCorrectionResponse`, `OutcomeSubmissionResponse.calibration_job`, and correction history/effective eligibility.
- Produces `OutcomeService.create_correction(outcome_id, payload, user)` and `list_corrections(...)`.
- Enqueues a scoped job in the same transaction through `OutcomeRepository.add_job(...)`; Task 6 consumes it.

- [ ] **Step 1: Write failing derived-outcome and correction tests**

```python
def test_outcome_ignores_client_facts_and_derives_written_off_loss(outcome_context):
    payload = ActualOutcomeCreate(idempotency_key=uuid.uuid4(), observed_at=closed_at, evidence_sha256="b" * 64, provenance="CONTROLLED_DEMO")
    result = service.submit(facility_id, payload, auditor)
    assert result["outcome"]["defaulted"] is True
    assert result["outcome"]["days_past_due"] == 45
    assert result["outcome"]["loss_amount"] == "700000.00"
    assert result["calibration_job"]["status"] == "queued"


def test_exclude_is_append_only_invalidates_active_run_and_queues_replacement(outcome_context):
    result = service.create_correction(outcome_id, correction(action="EXCLUDE"), auditor)
    assert result["effective_training_eligible"] is False
    assert result["calibration_job"]["deployment_scope"] == "controlled_demo"
    assert get_run(active_run_id).deployment_status == "invalidated"
```

Cover exact replay, conflicting idempotency reuse, repeated no-op actions, reinstatement lifecycle validation, correction authorization, cross-scope safety, and correction rollback when job insertion fails.

- [ ] **Step 2: Run outcome tests and verify caller-fact/correction failures**

Run: `python -m pytest tests/test_outcome_schema.py tests/test_outcome_repository.py tests/test_outcome_service.py tests/test_outcome_api.py -q`

Expected: FAIL because outcome facts are required from callers and correction APIs do not exist.

- [ ] **Step 3: Narrow submission and define correction schemas**

```python
class ActualOutcomeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID
    observed_at: datetime
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: Literal["CONTROLLED_DEMO", "EXTERNAL_VERIFIED"]


class OutcomeCorrectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID
    action: Literal["EXCLUDE", "REINSTATE"]
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    comment: str = Field(min_length=1, max_length=500)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
```

Preserve aware-datetime and non-blank normalization validators. Reject payload fields `defaulted`, `days_past_due`, and `loss_amount` with HTTP 422.

- [ ] **Step 4: Add repository operations for effective correction heads, membership invalidation, and job insertion**

```python
def get_correction_head(self, session: Session, outcome_id: uuid.UUID) -> OutcomeCorrectionModel | None:
    return session.scalar(select(OutcomeCorrectionModel).where(OutcomeCorrectionModel.outcome_id == outcome_id).order_by(OutcomeCorrectionModel.recorded_at.desc(), OutcomeCorrectionModel.correction_id.desc()).limit(1))


def invalidate_active_runs_containing(self, session: Session, outcome_id: uuid.UUID, scope: str, now: datetime) -> list[CalibrationRunModel]:
    runs = list(session.scalars(select(CalibrationRunModel).join(CalibrationRunObservationModel).where(CalibrationRunObservationModel.outcome_id == outcome_id, CalibrationRunModel.deployment_scope == scope, CalibrationRunModel.deployment_status == "active").with_for_update()))
    for run in runs:
        run.deployment_status = "invalidated"
        run.deactivated_at = now
        run.activation_reason = "outcome_excluded"
    return runs
```

Add `list_eligible_outcomes(scope)` with a latest-correction subquery, `list_corrections`, `get_correction_by_idempotency_key`, and `add_job`. All scope parameters are required keyword-only values.
Correction creation locks the target `actual_outcomes` row before reading its head, so simultaneous `EXCLUDE`/`REINSTATE` commands have one deterministic append order.

- [ ] **Step 5: Implement derived facts and atomic correction orchestration**

```python
@dataclass(frozen=True)
class DerivedOutcomeFacts:
    defaulted: bool
    days_past_due: int
    loss_amount: Decimal


def derive_outcome_facts(session: Session, facility: FinancingFacilityModel) -> DerivedOutcomeFacts:
    defaults = session.scalars(select(FacilityDefaultModel).where(FacilityDefaultModel.facility_id == facility.facility_id)).all()
    delinquencies = session.scalars(select(FacilityDelinquencyModel).where(FacilityDelinquencyModel.facility_id == facility.facility_id)).all()
    writeoff = session.scalar(select(FacilityWriteOffModel).where(FacilityWriteOffModel.facility_id == facility.facility_id))
    return DerivedOutcomeFacts(bool(defaults), max([0, *(row.days_past_due for row in delinquencies), *(row.days_past_due for row in defaults)]), Decimal(writeoff.amount) if writeoff else Decimal("0.00"))
```

Submission requires closed/zero balance, verifies provenance equals the request scope mapping, persists derived facts, appends `ACTUAL_OUTCOME_RECORDED`, and enqueues `outcome_submitted`. `EXCLUDE` appends the correction, invalidates affected active membership, appends `OUTCOME_TRAINING_EXCLUDED` plus invalidation events, and enqueues `correction_exclude` in one transaction. `REINSTATE` first re-runs lineage/lifecycle validation, appends `OUTCOME_TRAINING_REINSTATED`, and enqueues without directly activating an old run.

- [ ] **Step 6: Expose correction, preview, and queued-job HTTP responses**

```python
@router.get("/api/v1/outcomes/{outcome_id}/corrections", response_model=list[OutcomeCorrectionResponse])
def list_corrections(outcome_id: str, user: CurrentAuditor, request: Request):
    return _execute(lambda: request.app.state.outcome_service.list_corrections(outcome_id, user))


@router.post("/api/v1/outcomes/{outcome_id}/corrections", response_model=CorrectionSubmissionResponse, status_code=201)
def create_correction(outcome_id: str, payload: OutcomeCorrectionCreate, user: CurrentAuditor, request: Request):
    return _execute(lambda: request.app.state.outcome_service.create_correction(outcome_id, payload, user))
```

Add auditor-only `GET /api/v1/facilities/{facility_id}/actual-outcome-preview` returning `DerivedOutcomeFacts`, closure time, and expected provenance. The browser uses this response to display facts before confirmation and never calculates loss client-side.

- [ ] **Step 7: Run outcome regression tests and commit**

Run: `python -m pytest tests/test_outcome_schema.py tests/test_outcome_repository.py tests/test_outcome_service.py tests/test_outcome_api.py -q`

Expected: PASS.

```powershell
git add app/schemas_outcome.py app/repositories/outcomes.py app/services/outcomes.py app/api/outcomes.py tests/test_outcome_schema.py tests/test_outcome_repository.py tests/test_outcome_service.py tests/test_outcome_api.py
git commit -m "feat: derive outcomes and govern corrections"
```

---

### Task 5: Deterministic independent five-fold calibration

**Files:**
- Modify: `app/services/outcome_calibration.py`
- Modify: `app/services/adaptive_risk.py`
- Test: `tests/test_outcome_calibration.py`
- Test: `tests/test_adaptive_risk.py`

**Interfaces:**
- Extends `CalibrationObservation` with `correction_head_id: str | None`.
- Produces `assign_stratified_folds(observations, folds=5) -> dict[str, int]`.
- Keeps `build_calibration_candidate(...) -> CalibrationCandidate` but produces schema `daibm.platt-calibration.v3`, OOF metrics, fold hash, distinct-score count, and final coefficients.
- Produces an activation decision that never uses final-fit diagnostics.

- [ ] **Step 1: Write failing no-leakage, determinism, and gate tests**

```python
def test_five_fold_predictions_never_fit_the_held_out_observation():
    observations = varied_observations()
    assignments = assign_stratified_folds(observations)
    for fold in range(5):
        train = {row.outcome_id for row in observations if assignments[row.outcome_id] != fold}
        held_out = {row.outcome_id for row in observations if assignments[row.outcome_id] == fold}
        assert train.isdisjoint(held_out)
        assert held_out
    candidate = build_calibration_candidate(observations)
    assert candidate.fold_assignment_sha256 == build_calibration_candidate(tuple(reversed(varied_observations()))).fold_assignment_sha256


def test_gate_rejects_twenty_identical_scores_even_with_class_support():
    decision = evaluate_activation_gate(candidate_with_scores([0.5595] * 20), artifact_integrity="verified", deployment_scope="controlled_demo")
    assert decision == ActivationDecision(False, "insufficient_distinct_scores", "controlled_demo")
```

Also hand-check Brier/log loss, both-class training partitions, dataset hash changes when a correction head changes, and v3 hash/schema verification.

- [ ] **Step 2: Run focused calibration tests and verify OOF assertions fail**

Run: `python -m pytest tests/test_outcome_calibration.py tests/test_adaptive_risk.py -q`

Expected: FAIL because current metrics are in-sample and artifacts are v2.

- [ ] **Step 3: Extract deterministic fitting and fold assignment**

```python
def assign_stratified_folds(observations: tuple[CalibrationObservation, ...], folds: int = 5) -> dict[str, int]:
    assignment: dict[str, int] = {}
    for label in (False, True):
        ordered = sorted((row for row in observations if row.defaulted is label), key=lambda row: (row.observed_at, row.outcome_id))
        for index, row in enumerate(ordered):
            assignment[row.outcome_id] = index % folds
    return assignment


def _fit_platt(scores: np.ndarray, labels: np.ndarray, config: CalibrationTrainingConfig) -> tuple[float, float]:
    logits = np.log(np.clip(scores, config.probability_epsilon, 1 - config.probability_epsilon) / (1 - np.clip(scores, config.probability_epsilon, 1 - config.probability_epsilon)))
    slope, intercept = 1.0, 0.0
    for _ in range(config.epochs):
        residual = _sigmoid(slope * logits + intercept) - labels
        slope -= config.learning_rate * (float(np.mean(residual * logits)) + config.l2_penalty * slope)
        intercept -= config.learning_rate * float(np.mean(residual))
    return slope, intercept
```

- [ ] **Step 4: Build OOF metrics and the v3 artifact**

```python
assignments = assign_stratified_folds(ordered)
oof = np.empty(len(ordered), dtype=np.float64)
for fold in range(5):
    train_index = np.asarray([assignments[row.outcome_id] != fold for row in ordered])
    hold_index = ~train_index
    if len(set(labels[train_index])) != 2:
        raise ValueError("every OOF training partition requires both classes")
    fold_slope, fold_intercept = _fit_platt(raw_scores[train_index], labels[train_index], config)
    oof[hold_index] = _apply_coefficients(raw_scores[hold_index], fold_slope, fold_intercept, config.probability_epsilon)
final_slope, final_intercept = _fit_platt(raw_scores, labels, config)
artifact = {
    "artifact_schema": "daibm.platt-calibration.v3",
    "coefficients": {"slope": final_slope, "intercept": final_intercept},
    "validation": {"method": "deterministic_stratified_5_fold_oof", "fold_assignment_sha256": fold_hash, "metrics_before": baseline_metrics, "metrics_after": _metrics(labels, oof, config.probability_epsilon)},
    "dataset": {"sha256": dataset_hash, "outcome_ids": [row.outcome_id for row in ordered], "correction_heads": {row.outcome_id: row.correction_head_id for row in ordered}, "distinct_score_count": len(set(raw_scores.tolist()))},
}
```

Final-fit metrics may be stored under `diagnostics.final_fit`; `evaluate_activation_gate` reads only `candidate.metrics_before`, `candidate.metrics_after`, counts, distinct-score count, scope, and integrity.

- [ ] **Step 5: Preserve controlled-demo v2 readability and block new v2 activation**

```python
if schema == "daibm.platt-calibration.v2":
    if deployment_scope != "controlled_demo":
        raise ValueError("legacy v2 calibration is controlled-demo only")
elif schema != "daibm.platt-calibration.v3":
    raise ValueError("calibration artifact schema is not deployable")
```

Existing active v2 controlled-demo artifacts remain loadable until invalidated. Every newly trained artifact is v3 and includes scope/dataset/fold lineage.

- [ ] **Step 6: Run focused tests and commit**

Run: `python -m pytest tests/test_outcome_calibration.py tests/test_adaptive_risk.py -q`

Expected: PASS.

```powershell
git add app/services/outcome_calibration.py app/services/adaptive_risk.py tests/test_outcome_calibration.py tests/test_adaptive_risk.py
git commit -m "feat: validate calibration out of fold"
```

---

### Task 6: Durable PostgreSQL job worker and scoped activation

**Files:**
- Create: `app/services/calibration_jobs.py`
- Modify: `app/repositories/outcomes.py`
- Modify: `app/services/outcomes.py`
- Modify: `app/main.py`
- Modify: `app/api/outcomes.py`
- Test: `tests/test_calibration_jobs.py`
- Test: `tests/test_outcome_service.py`
- Test: `tests/test_outcome_api.py`

**Interfaces:**
- Produces `CalibrationJobService.process_next(worker_id: str) -> bool` and `run(stop_event: asyncio.Event) -> None`.
- Produces repository methods `claim_next_job(...)`, `complete_job(...)`, `fail_or_retry_job(...)`, and `acquire_scope_lock(...)`.
- Produces `GET /api/v1/calibration-jobs/{job_id}` for auditor-visible state.
- Consumes Task 4 queued jobs and Task 5 `build_calibration_candidate`.

- [ ] **Step 1: Write failing claim/lease/retry/activation tests**

```python
def test_workers_claim_each_job_once_with_skip_locked(job_context):
    first = repository.claim_next_job(session_a, worker_id="worker-a", now=clock(), lease_until=clock() + timedelta(minutes=5))
    second = repository.claim_next_job(session_b, worker_id="worker-b", now=clock(), lease_until=clock() + timedelta(minutes=5))
    assert first.job_id != second.job_id


def test_process_next_trains_only_job_scope_and_persists_membership(job_context):
    assert service.process_next("worker-a") is True
    run = latest_run("controlled_demo")
    assert run.deployment_scope == "controlled_demo"
    assert {row.outcome_id for row in run_membership(run.calibration_run_id)} == set(eligible_controlled_ids)
    assert external_outcome_id not in {row.outcome_id for row in run_membership(run.calibration_run_id)}
```

Cover expired lease recovery, maximum three infrastructure attempts, deterministic gate rejection as completed, artifact failure, process restart, same-scope advisory lock, invalidated baseline interval, and active replacement.

- [ ] **Step 2: Run job tests and verify the worker is absent**

Run: `python -m pytest tests/test_calibration_jobs.py tests/test_outcome_service.py -q`

Expected: FAIL because no claim or worker service exists.

- [ ] **Step 3: Implement safe job claiming and scope locks**

```python
def claim_next_job(self, session: Session, *, worker_id: str, now: datetime, lease_until: datetime) -> CalibrationJobModel | None:
    job = session.scalar(select(CalibrationJobModel).where(or_(CalibrationJobModel.status == "queued", and_(CalibrationJobModel.status == "running", CalibrationJobModel.leased_until < now)), CalibrationJobModel.attempt_count < 3).order_by(CalibrationJobModel.created_at, CalibrationJobModel.job_id).with_for_update(skip_locked=True).limit(1))
    if job is None:
        return None
    job.status = "running"
    job.attempt_count += 1
    job.lease_owner = worker_id
    job.leased_until = lease_until
    return job


def acquire_scope_lock(self, session: Session, scope: str) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"), {"scope": f"calibration:{scope}"})
```

The generated PostgreSQL claim query must contain `FOR UPDATE SKIP LOCKED`; the integration test holds the first claim transaction open while the second session claims a different row.

- [ ] **Step 4: Implement one-job processing with committed artifacts and normalized membership**

```python
def process_next(self, worker_id: str) -> bool:
    job_id = self._claim(worker_id)
    if job_id is None:
        return False
    try:
        candidate, staged = self._train_staged(job_id)
        run_id = self._commit_run_and_membership(job_id, candidate, staged)
        publish_candidate_artifact(staged)
        self.outcome_service.evaluate_scoped_deployment(run_id)
        self._complete(job_id, run_id)
    except DeterministicCalibrationRejection as error:
        self._complete_rejected(job_id, error.code)
    except Exception:
        self._retry_or_fail(job_id, "calibration_infrastructure_failure")
    return True
```

`_train_staged` reloads only currently eligible exact-scope outcomes after acquiring the scope advisory lock. `_commit_run_and_membership` stores correction heads and OOF diagnostics before publication. If a previous job already produced the same exact-scope dataset hash, the later job records that existing `result_run_id` and completes without creating or publishing a duplicate run. Publication/reconciliation follows existing staged-file recovery semantics and never rolls back the already committed outcome/correction.

- [ ] **Step 5: Run the worker in the FastAPI lifespan with bounded shutdown**

```python
stop_event = asyncio.Event()
worker_task = asyncio.create_task(calibration_job_service.run(stop_event))
try:
    outcome_service.reconcile_deployments()
    yield
finally:
    stop_event.set()
    await worker_task
```

`run` calls `await asyncio.to_thread(self.process_next, worker_id)`, uses an interruptible `await asyncio.wait_for(stop_event.wait(), timeout=0.5)` only when no job exists, and exits without abandoning an in-process transaction.

- [ ] **Step 6: Expose job status without secrets or artifact paths**

```python
@router.get("/api/v1/calibration-jobs/{job_id}", response_model=CalibrationJobResponse)
def get_calibration_job(job_id: str, user: CurrentAuditor, request: Request):
    return _execute(lambda: request.app.state.outcome_service.get_job(job_id, user))
```

Return ID, scope, trigger type, status, attempt count, stable failure code, and timestamps. Do not serialize lease owner, artifact locator, filesystem paths, or exception text.
Calibration-run responses also expose persisted `artifact_schema`, OOF metrics, fold-assignment hash, eligible/excluded counts, and scope; they continue to omit `artifact_locator`.

- [ ] **Step 7: Run job/outcome/API tests and commit**

Run: `python -m pytest tests/test_calibration_jobs.py tests/test_outcome_service.py tests/test_outcome_api.py -q`

Expected: PASS.

```powershell
git add app/services/calibration_jobs.py app/repositories/outcomes.py app/services/outcomes.py app/main.py app/api/outcomes.py tests/test_calibration_jobs.py tests/test_outcome_service.py tests/test_outcome_api.py
git commit -m "feat: process durable calibration jobs"
```

---

### Task 7: Exact-scope inference, deployment reads, and rollback

**Files:**
- Modify: `app/services/adaptive_risk.py`
- Modify: `app/services/workflow.py`
- Modify: `app/service.py`
- Modify: `app/repositories/outcomes.py`
- Modify: `app/services/outcomes.py`
- Modify: `app/schemas_outcome.py`
- Modify: `app/api/outcomes.py`
- Test: `tests/test_adaptive_risk.py`
- Test: `tests/test_workflow_service.py`
- Test: `tests/test_service.py`
- Test: `tests/test_outcome_service.py`
- Test: `tests/test_outcome_api.py`

**Interfaces:**
- Changes `AdaptiveRiskInferenceService.assess(session, baseline_probability, assessment_scope) -> AdaptiveRiskResult`.
- Changes `OutcomeRepository.get_active_run(session, *, scope, for_update=False)` so scope is mandatory.
- Changes active deployment read and rollback to require `scope: Literal["controlled_demo", "external_verified"]`.
- Persists `FinancingRequestModel.assessment_scope`; UI/legacy requests use `controlled_demo`, while no browser field can create `external_verified`.

- [ ] **Step 1: Write failing exact-scope and fallback tests**

```python
def test_inference_never_substitutes_another_scope(session, active_external_run):
    result = AdaptiveRiskInferenceService().assess(session, 0.42, "controlled_demo")
    assert result.final_score == 0.42
    assert result.calibration_run_id is None
    assert result.fallback_code == "no_active_calibration_for_scope"


def test_rollback_requires_direct_predecessor_in_same_scope(outcome_context):
    restored = service.rollback(active_controlled_id, "controlled_demo", auditor)
    assert restored["deployment_scope"] == "controlled_demo"
    assert get_active("external_verified").calibration_run_id == active_external_id
```

Cover corrupt artifacts, invalidated artifacts, v2 external rejection, stale expected active ID, direct predecessor only, and startup reconciliation by scope.

- [ ] **Step 2: Run focused scope tests and verify the old global lookup fails**

Run: `python -m pytest tests/test_adaptive_risk.py tests/test_workflow_service.py tests/test_service.py tests/test_outcome_service.py tests/test_outcome_api.py -q`

Expected: FAIL because active reads and assessment have no required scope.

- [ ] **Step 3: Require scope at repository and inference boundaries**

```python
def get_active_run(self, session: Session, *, scope: str, for_update: bool = False) -> CalibrationRunModel | None:
    statement = select(CalibrationRunModel).where(CalibrationRunModel.deployment_status == "active", CalibrationRunModel.deployment_scope == scope)
    return session.scalar(statement.with_for_update() if for_update else statement)


def assess(self, session: Any, baseline_probability: float, assessment_scope: str) -> AdaptiveRiskResult:
    if assessment_scope not in {"controlled_demo", "external_verified"}:
        raise ValueError("assessment scope is not deployable")
    active = self.repository.get_active_run(session, scope=assessment_scope)
    if active is None:
        return self._fallback(baseline_probability, None, "no_active_calibration_for_scope")
```

Artifact verification also checks that artifact scope, run scope, and requested scope are identical.

- [ ] **Step 4: Assign and persist controlled-demo scope at every local request constructor**

```python
application = FinancingRequestModel(
    request_id=uuid.uuid4(),
    assessment_scope="controlled_demo",
    created_at=now,
    updated_at=now,
    applicant_id=user.organization_code,
    amount=amount,
    term_days=payload.term_days,
    features=risk_request.model_dump(),
    status=Status.DRAFT.value,
    version=1,
)

adaptive_result = self.adaptive_risk_service.assess(
    session,
    risk_result.score,
    application.assessment_scope,
)
```

Apply this to `WorkflowService.create_draft` and the legacy `FinancingService.create_request`. Do not add `assessment_scope` to public browser request schemas. Include scope in assessment ledger lineage.

- [ ] **Step 5: Scope deployment reads and rollback APIs**

```python
class CalibrationRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_active_run_id: UUID
    scope: Literal["controlled_demo", "external_verified"]


@router.get("/api/v1/calibration-deployments/active", response_model=CalibrationRunResponse)
def get_active_calibration_deployment(user: CurrentAuditor, request: Request, scope: Literal["controlled_demo", "external_verified"] = Query(...)):
    return _execute(lambda: request.app.state.outcome_service.get_active_deployment(scope, user))
```

Rollback locks only the requested scope and rejects a predecessor whose scope differs or whose ID is not the current run's direct `previous_active_run_id`.

- [ ] **Step 6: Run scope regression tests and commit**

Run: `python -m pytest tests/test_adaptive_risk.py tests/test_workflow_service.py tests/test_service.py tests/test_outcome_service.py tests/test_outcome_api.py -q`

Expected: PASS.

```powershell
git add app/services/adaptive_risk.py app/services/workflow.py app/service.py app/repositories/outcomes.py app/services/outcomes.py app/schemas_outcome.py app/api/outcomes.py tests/test_adaptive_risk.py tests/test_workflow_service.py tests/test_service.py tests/test_outcome_service.py tests/test_outcome_api.py
git commit -m "feat: isolate calibration deployment scopes"
```

---

### Task 8: Russian/Chinese lifecycle and governance workbench

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/workflow.js`
- Modify: `app/static/workflow.css`
- Modify: `tests/test_ui_contract.py`
- Modify: `tests/test_browser_acceptance.py`

**Interfaces:**
- Consumes lifecycle fields/actions from Task 3, correction/job responses from Task 4/6, and explicit controlled-demo deployment reads from Task 7.
- Produces role-gated forms with `data-facility-action` values `restructure`, `declare_default`, and `write_off`.
- Produces auditor correction controls with `data-outcome-correction` values `EXCLUDE` and `REINSTATE`.

- [ ] **Step 1: Write failing bilingual UI contract tests**

```python
def test_lifecycle_and_correction_controls_are_bilingual_and_scoped():
    source = _html() + Path("app/static/workflow.js").read_text(encoding="utf-8")
    for token in ("restructure", "declare_default", "write_off", "OUTCOME_TRAINING_EXCLUDED", "INCONSISTENT_LIFECYCLE"):
        assert token in source
    assert "/api/v1/calibration-deployments/active?scope=controlled_demo" in source
    assert 'name="assessment_scope"' not in source
    assert "Реструктурировать" in source and "重组" in source
    assert "Исключить из обучения" in source and "从训练中排除" in source
```

Add assertions for schedule-version labels, payment/write-off visual separation, derived read-only outcome facts, correction evidence hashing, job polling, OOF labels, and 390 px CSS.

- [ ] **Step 2: Run UI contracts and verify controls/copy are absent**

Run: `python -m pytest tests/test_ui_contract.py tests/test_browser_acceptance.py -q`

Expected: FAIL on the new tokens and request paths.

- [ ] **Step 3: Render lifecycle timeline and role-gated action forms**

```javascript
function renderFacilityAction(facility, action) {
  if (action === 'restructure') return `<form data-facility-action-form="restructure"><input name="reason_code" value="BORROWER_CASH_FLOW" required><input name="evidence_reference" required><div data-restructure-schedule></div><button data-facility-action="restructure">${wfT('restructure')}</button></form>`;
  if (action === 'declare_default') return `<form data-facility-action-form="declare_default"><input name="days_past_due" type="number" min="1" required><input name="evidence_reference" required><button data-facility-action="declare_default">${wfT('declareDefault')}</button></form>`;
  if (action === 'write_off') return `<form data-facility-action-form="write_off"><input name="evidence_reference" required><button data-facility-action="write_off">${wfT('writeOff')}</button></form>`;
  return renderExistingFacilityAction(facility, action);
}
```

`executeFacilityAction` hashes evidence references locally, preserves one idempotency key across retry, sends exact decimal strings, disables pending controls, and refreshes the same facility.

- [ ] **Step 4: Render derived outcomes, correction history, jobs, and OOF deployment state**

```javascript
async function submitOutcomeCorrection(outcomeId, action, form) {
  const key = form.dataset.idempotencyKey || crypto.randomUUID();
  form.dataset.idempotencyKey = key;
  const body = {idempotency_key:key, action, reason_code:form.reason_code.value.trim(), comment:form.comment.value.trim(), evidence_sha256:await hashEvidenceReference(form.evidence_reference.value.trim())};
  const result = await wfApi(`/api/v1/outcomes/${outcomeId}/corrections`, {method:'POST', body:JSON.stringify(body)});
  await pollCalibrationJob(result.calibration_job.job_id);
  await refreshOutcomes();
}
```

The panel labels OOF Brier/log loss separately from final coefficients, displays exact scope and active/invalidated/baseline state, and never exposes artifact paths or allows a browser-created external scope.

- [ ] **Step 5: Add responsive accessible styling**

```css
.schedule-version-grid,.outcome-governance-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.timeline-writeoff{border-inline-start:4px solid var(--danger);background:color-mix(in srgb,var(--danger) 8%,white)}
@media (max-width:390px){.schedule-version-grid,.outcome-governance-grid{grid-template-columns:1fr}.facility-action-form input,.facility-action-form select,.facility-action-form button{max-width:100%}}
```

Keep focus styles, `aria-live`, `aria-busy`, native button semantics, and the established visual system.

- [ ] **Step 6: Run UI contracts and commit**

Run: `python -m pytest tests/test_ui_contract.py tests/test_browser_acceptance.py -q`

Expected: PASS.

```powershell
git add app/static/index.html app/static/workflow.js app/static/workflow.css tests/test_ui_contract.py tests/test_browser_acceptance.py
git commit -m "feat: expose governed lifecycle workbench"
```

---

### Task 9: Real browser correction, valid samples, retraining, and scoped inference proof

**Files:**
- Modify: `scripts/outcome_browser_acceptance.py`
- Modify: `tests/test_release_contract.py`
- Create: `tests/test_lifecycle_browser_contract.py`

**Interfaces:**
- Uses only authenticated browser/API flows; it never edits PostgreSQL rows directly.
- Excludes the five inconsistent historical outcomes with `INCONSISTENT_LIFECYCLE` corrections.
- Creates varied-score valid repaid/defaulted/write-off lifecycle outcomes, waits for a controlled-demo v3 deployment, and proves a new request stores exact run lineage.

- [ ] **Step 1: Write a failing browser acceptance contract**

```python
def test_acceptance_script_proves_correction_oof_scope_and_post_activation_lineage():
    source = Path("scripts/outcome_browser_acceptance.py").read_text(encoding="utf-8")
    for token in ("INCONSISTENT_LIFECYCLE", "declare_default", "write_off", "daibm.platt-calibration.v3", "controlled_demo", "calibration_run_id"):
        assert token in source
    assert "session.execute" not in source
    assert "UPDATE actual_outcomes" not in source
```

- [ ] **Step 2: Run the contract and verify the complete scenario is absent**

Run: `python -m pytest tests/test_lifecycle_browser_contract.py tests/test_release_contract.py -q`

Expected: FAIL because the browser script still submits caller-controlled outcome facts and does not correct history.

- [ ] **Step 3: Add browser helpers for corrections and valid lifecycle variants**

```python
def _exclude_inconsistent_outcome(page, outcome_id: str) -> None:
    payload = {"idempotency_key": str(uuid.uuid4()), "action": "EXCLUDE", "reason_code": "INCONSISTENT_LIFECYCLE", "comment": "Recorded loss conflicts with fully repaid lifecycle", "evidence_sha256": hashlib.sha256(f"correction://{outcome_id}".encode()).hexdigest()}
    response = page.evaluate("([id, body]) => fetch(`/api/v1/outcomes/${id}/corrections`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}).then(async r => ({status:r.status, body:await r.json()}))", [outcome_id, payload])
    assert response["status"] == 201


def _complete_default_writeoff(page, facility_id: str, days_past_due: int) -> None:
    _switch_role(page, "financier.demo", facility_id)
    _mark_overdue(page, days_past_due)
    _switch_role(page, "risk.demo", facility_id)
    _declare_default(page, days_past_due)
    _switch_role(page, "auditor.demo", facility_id)
    _write_off_and_close(page)
```

- [ ] **Step 4: Create a correlated varied-score eligible dataset through real flows**

Use at least five high-risk input cases that proceed through default/write-off and at least five low-risk fully repaid cases. Together with the remaining valid controlled-demo outcomes, assert at least 20 eligible observations, both classes at least five, and at least five distinct `original_risk_score` values. Poll the job endpoint until completed, then poll `GET /api/v1/calibration-deployments/active?scope=controlled_demo` until it returns a v3 run or a stable test timeout fails with job diagnostics.

```python
scores = {round(float(row["original_risk_score"]), 12) for row in eligible_outcomes}
assert len(eligible_outcomes) >= 20
assert sum(row["defaulted"] for row in eligible_outcomes) >= 5
assert sum(not row["defaulted"] for row in eligible_outcomes) >= 5
assert len(scores) >= 5
assert active["deployment_scope"] == "controlled_demo"
assert active["artifact_schema"] == "daibm.platt-calibration.v3"
```

- [ ] **Step 5: Prove post-activation exact run lineage and mobile bilingual state**

```python
new_request = _create_and_assess_varied_application(page, risk_case="medium")
assert new_request["assessment_scope"] == "controlled_demo"
assert new_request["calibration_run_id"] == active["calibration_run_id"]
assert new_request["raw_risk_score"] != new_request["risk_score"]
page.evaluate("window.setLanguage('zh')")
page.set_viewport_size({"width": 390, "height": 844})
assert page.locator("#calibrationCandidate").bounding_box()["width"] <= 390
```

- [ ] **Step 6: Run real-browser acceptance and commit**

Run: `python -m pytest tests/test_lifecycle_browser_contract.py tests/test_release_contract.py -q`

Run with the local PostgreSQL/app stack healthy: `python scripts/outcome_browser_acceptance.py`

Expected: both commands PASS; the browser script writes a screenshot under `output/` and reports the active controlled-demo run.

```powershell
git add scripts/outcome_browser_acceptance.py tests/test_lifecycle_browser_contract.py tests/test_release_contract.py
git commit -m "test: prove governed lifecycle retraining flow"
```

---

### Task 10: Documentation, full verification, private push, and durable restart proof

**Files:**
- Modify: `README.md`
- Modify: `docs/mvp-design.md`
- Modify: `docker-compose.yml` only if the worker/artifact durability test exposes a missing volume or health dependency.
- Modify: `.github/workflows/ci.yml` only if the existing test commands do not collect the new files.

**Interfaces:**
- Documents exact roles, transitions, correction semantics, OOF gates, queue behavior, scopes, and fallback codes.
- Produces a verified private `main` with container restart durability and green GitHub CI.

- [ ] **Step 1: Update operational documentation with exact commands and boundaries**

```markdown
### Governed lifecycle and adaptive calibration

Facilities may be restructured only after delinquency, declared default by the risk manager, recovered through confirmed payments, or fully written off by the auditor. Actual outcomes are derived from immutable lifecycle evidence. Corrections append EXCLUDE/REINSTATE events and immediately invalidate affected deployments. Calibration runs asynchronously from PostgreSQL jobs, uses deterministic stratified five-fold OOF validation, and is isolated to controlled_demo or external_verified.
```

Document the three-attempt job policy, five-minute lease, fixed 20/5/5/5 gates, active deployment query with `scope`, baseline fallback codes, and Russian/Chinese demonstration route.

- [ ] **Step 2: Run migration and full application tests**

Run: `python -m alembic upgrade head`

Run: `python -m pytest tests --ignore=tests/research -q`

Expected: PASS with no skipped new lifecycle, correction, job, scope, or UI tests.

- [ ] **Step 3: Run independent research verification and tests**

Run: `python -m research.cli verify --reference artifacts/reference`

Run: `python -m pytest tests/research -q`

Expected: PASS; governed application changes do not alter the frozen research artifact.

- [ ] **Step 4: Verify Docker restart persistence and health**

Run: `docker compose config --quiet`

Run: `docker compose up -d --build`

Run: `docker compose restart app`

Run: `Invoke-RestMethod http://127.0.0.1:8010/health | ConvertTo-Json -Depth 5`

Expected: compose validation succeeds; health reports PostgreSQL reachable; corrections, jobs, lifecycle rows, active scoped run, and calibration artifact remain usable after restart.

- [ ] **Step 5: Run final repository checks and commit documentation**

Run: `git diff --check`

Run: `git status --short`

Expected before commit: only intended README/design/compose/CI files are modified, with no whitespace errors.

```powershell
git add README.md docs/mvp-design.md docker-compose.yml .github/workflows/ci.yml
git commit -m "docs: document governed adaptive lifecycle"
```

Stage only files that actually changed; omit unchanged optional paths from `git add`.

- [ ] **Step 6: Push privately and wait for final CI**

Run: `gh repo view MengdanXue/daibm-scf-mvp --json visibility --jq .visibility`

Expected: `PRIVATE`.

Run: `git push origin main`

Run: `gh run watch $(gh run list --branch main --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status`

Expected: GitHub Actions application and research jobs both complete successfully, and `git status --short --branch` reports `main...origin/main` with no changes.
