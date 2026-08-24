# Financing Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend an audited approved application through exact-cent disbursement, installments, repayment, overdue control, full repayment, and audited closure.

**Architecture:** Keep the application approval state machine unchanged and add a separate versioned `FinancingFacility` aggregate. PostgreSQL constraints and row locks enforce monetary and concurrency invariants; the service records every transition and ledger event in one transaction, while a new bilingual tab exposes only role-authorized actions.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy 2, PostgreSQL 17 `NUMERIC`, Alembic, pytest/Testcontainers, vanilla HTML/CSS/JavaScript, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-24-research-evidence-defense-pack-design.md`

## Global Constraints

- Only applications with `status=audited` and `decision=approved` can create a facility.
- Use `Decimal` and PostgreSQL `NUMERIC(14,2)` for all money; no float conversion in domain/service/repository code.
- Principal and schedule sum must match exactly; confirmed payments cannot exceed installment or facility outstanding balances.
- Every command carries `version` and a UUID `idempotency_key`; row locking and unique constraints must make retries safe.
- Interest, fees, FX, accounting entries, external transfers, and real payment rails remain out of scope.
- Each action and its audit-ledger event commit atomically.

---

### Task 1: Facility domain state machine and command schemas

**Files:**
- Create: `app/domain/facility.py`
- Create: `app/schemas_facility.py`
- Create: `tests/test_facility_domain.py`

**Interfaces:**
- Produces: `FacilityStatus`, `InstallmentStatus`, `PaymentStatus`, `FacilityAction`, `next_facility_status`, `CreateFacilityRequest`, `VersionedFacilityCommand`, `SubmitPaymentRequest`, and `DecisionPaymentRequest`.

- [ ] **Step 1: Write failing state and decimal-validation tests**

```python
def test_happy_path_requires_real_roles():
    status = next_facility_status(FacilityStatus.READY, FacilityAction.INITIATE_DISBURSEMENT, Role.FINANCIER)
    status = next_facility_status(status, FacilityAction.CONFIRM_DISBURSEMENT, Role.FINANCIER)
    assert status is FacilityStatus.ACTIVE
    assert next_facility_status(FacilityStatus.REPAID, FacilityAction.CLOSE, Role.AUDITOR) is FacilityStatus.CLOSED

def test_schedule_requires_exact_cent_sum():
    with pytest.raises(ValidationError):
        CreateFacilityRequest(principal="100.00", currency="RUB", installments=[{"sequence": 1, "due_date": "2026-10-01", "amount": "99.99"}], idempotency_key=uuid.uuid4())
```

- [ ] **Step 2: Run and verify missing-module failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_facility_domain.py -q`

Expected: FAIL because facility domain/schemas do not exist.

- [ ] **Step 3: Implement explicit states and strict money parsing**

```python
class FacilityStatus(StrEnum):
    READY = "ready_for_disbursement"
    DISBURSED = "disbursed"
    ACTIVE = "active"
    OVERDUE = "overdue"
    REPAID = "repaid"
    CLOSED = "closed"

CENT = Decimal("0.01")

def exact_money(value: Decimal) -> Decimal:
    if value <= 0 or value != value.quantize(CENT):
        raise ValueError("money must be positive with at most two decimal places")
    return value
```

Define transitions: financier `initiate_disbursement` READY→DISBURSED and `confirm_disbursement` DISBURSED→ACTIVE; risk manager `mark_overdue` ACTIVE→OVERDUE; confirmed final payment ACTIVE/OVERDUE→REPAID; auditor `close` REPAID→CLOSED. Payment submission/confirmation updates child state without bypassing the facility transition rules.

- [ ] **Step 4: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_facility_domain.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/domain/facility.py app/schemas_facility.py tests/test_facility_domain.py
git commit -m "feat: define financing facility lifecycle"
```

### Task 2: PostgreSQL facility schema and repository

**Files:**
- Create: `app/models_facility.py`
- Create: `app/repositories/facility.py`
- Create: `alembic/versions/20260824_0005_financing_lifecycle.py`
- Create: `tests/test_facility_database.py`
- Modify: `app/models.py`
- Modify: `alembic/env.py`

**Interfaces:**
- Consumes: Task 1 enum string values.
- Produces: `FinancingFacilityModel`, `InstallmentModel`, `PaymentModel`, `FacilityActionModel`, and `FacilityRepository.get_for_update/add/list_*`.

- [ ] **Step 1: Write failing migration and constraint tests**

```python
def test_clean_migration_creates_exact_money_constraints(migrated_engine):
    tables = inspect(migrated_engine).get_table_names()
    assert {"financing_facilities", "facility_installments", "facility_payments", "facility_actions"} <= set(tables)

def test_database_rejects_duplicate_idempotency_and_overpayment(session_factory, approved_application):
    with pytest.raises(IntegrityError):
        insert_two_actions_with_same_idempotency_key(session_factory, approved_application)
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_facility_database.py -q`

Expected: FAIL because migration and models are absent.

- [ ] **Step 3: Implement normalized tables and constraints**

Create one facility per request, ordered installments unique by `(facility_id, sequence)`, payment references unique per facility, and action idempotency keys globally unique. Add checks for positive amounts, `outstanding_amount BETWEEN 0 AND principal`, valid status values, and hash lengths. Use `ForeignKey(..., ondelete="RESTRICT")` for financial evidence.

Repository signatures:

```python
def get_for_update(self, session: Session, facility_id: UUID) -> FinancingFacilityModel | None: ...
def get_by_request(self, session: Session, request_id: UUID) -> FinancingFacilityModel | None: ...
def find_action(self, session: Session, key: UUID) -> FacilityActionModel | None: ...
def list_installments(self, session: Session, facility_id: UUID) -> list[InstallmentModel]: ...
def list_payments(self, session: Session, facility_id: UUID) -> list[PaymentModel]: ...
```

- [ ] **Step 4: Run migration and database tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_database.py tests/test_facility_database.py -q`

Run: `.\.venv\Scripts\python.exe -m alembic check`

Expected: PASS with schema at head and no metadata drift.

- [ ] **Step 5: Commit**

```powershell
git add app/models.py app/models_facility.py app/repositories/facility.py alembic tests/test_facility_database.py
git commit -m "feat: persist financing facilities and payments"
```

### Task 3: Transactional facility service and concurrency invariants

**Files:**
- Create: `app/services/facility.py`
- Create: `tests/test_facility_service.py`
- Modify: `app/models.py`

**Interfaces:**
- Consumes: Task 1 schemas/domain, Task 2 repository/models, `WorkflowRepository`, `LedgerRepository`, and `AuthenticatedUser`.
- Produces: `FacilityService.create`, `initiate_disbursement`, `confirm_disbursement`, `submit_payment`, `decide_payment`, `mark_overdue`, `close`, `get`, and `list_for_user`.

- [ ] **Step 1: Write failing happy-path, authorization, retry, and concurrency tests**

```python
def test_full_facility_path_commits_balances_and_ledger(facility_service, actors, approved_audited_application):
    facility = facility_service.create(request(...), actors.financier)
    facility = facility_service.initiate_disbursement(facility.id, command(facility), actors.financier)
    facility = facility_service.confirm_disbursement(facility.id, command(facility), actors.financier)
    payment = facility_service.submit_payment(facility.id, submit("500.00", facility.version), actors.supplier)
    facility = facility_service.decide_payment(facility.id, accept(payment, facility.version), actors.financier)
    assert facility["outstanding_amount"] == "500.00"

def test_concurrent_confirmation_cannot_overpay(barrier, two_sessions, facility):
    results = confirm_same_installment_concurrently(barrier, two_sessions, facility)
    assert sorted(result.status for result in results) == [200, 409]
    assert read_facility(facility.id).outstanding_amount == Decimal("0.00")
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_facility_service.py -q`

Expected: FAIL because `FacilityService` does not exist.

- [ ] **Step 3: Implement row-locked, idempotent commands**

At the start of every command check `FacilityActionModel.idempotency_key`; if found, return the current serialized facility without appending a second event. Otherwise lock the facility, check `version`, role, organization ownership, status, installment/payment state, and exact balance before mutating.

Append one facility action and one ledger event with types `FACILITY_CREATED`, `DISBURSEMENT_INITIATED`, `DISBURSEMENT_CONFIRMED`, `REPAYMENT_SUBMITTED`, `REPAYMENT_CONFIRMED`, `REPAYMENT_REJECTED`, `FACILITY_MARKED_OVERDUE`, `FACILITY_REPAID`, and `FACILITY_CLOSED`. If final confirmed payment makes outstanding zero, append repayment confirmation and facility repaid events in the same transaction.

- [ ] **Step 4: Run service tests including the real barrier concurrency case**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_facility_service.py tests/test_ledger.py -q`

Expected: PASS; the concurrency test must use `threading.Barrier`, not sequential calls.

- [ ] **Step 5: Commit**

```powershell
git add app/services/facility.py app/models.py tests/test_facility_service.py
git commit -m "feat: execute facility transactions atomically"
```

### Task 4: Authenticated facility API

**Files:**
- Create: `app/api/facility.py`
- Create: `tests/test_facility_api.py`
- Modify: `app/main.py`
- Modify: `app/api/__init__.py`

**Interfaces:**
- Consumes: Task 3 `FacilityService`.
- Produces: REST resources under `/api/v1/facilities` with consistent 403/404/409 error codes.

- [ ] **Step 1: Write failing API contract tests**

```python
def test_only_financier_can_create_facility(client, login_user, approved_application):
    login_user(client, "supplier.demo")
    assert client.post("/api/v1/facilities", json=create_payload(approved_application)).status_code == 403
    login_user(client, "financier.demo")
    assert client.post("/api/v1/facilities", json=create_payload(approved_application)).status_code == 201

def test_rejected_application_cannot_be_disbursed(client, financier, rejected_application):
    response = client.post("/api/v1/facilities", json=create_payload(rejected_application))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "facility_precondition_failed"
```

- [ ] **Step 2: Run and verify missing-route failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_facility_api.py -q`

Expected: FAIL with 404 routes.

- [ ] **Step 3: Implement routes and error mapping**

Add `POST /facilities`, `GET /facilities`, `GET /facilities/{id}`, and action endpoints `/initiate-disbursement`, `/confirm-disbursement`, `/payments`, `/payments/{payment_id}/decision`, `/mark-overdue`, and `/close`. Return string money values to preserve exact cents in JSON. Register service on `application.state` and router in `create_app`.

- [ ] **Step 4: Run API and existing workflow tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_facility_api.py tests/test_workflow_api.py tests/test_auth_api.py -q`

Expected: PASS and no workflow contract changes.

- [ ] **Step 5: Commit**

```powershell
git add app/api/facility.py app/api/__init__.py app/main.py tests/test_facility_api.py
git commit -m "feat: expose role-safe facility APIs"
```

### Task 5: Bilingual lifecycle workbench

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/workflow.css`
- Modify: `app/static/workflow.js`
- Modify: `tests/test_ui_contract.py`

**Interfaces:**
- Consumes: Task 4 APIs and current authenticated user/role state.
- Produces: `view-facilities`, facility list/detail, schedule/balance rail, and action forms with existing error handling.

- [ ] **Step 1: Write failing UI contract tests**

```python
def test_facility_tab_has_bilingual_exact_balance_and_actions():
    html = _html()
    js = WORKFLOW_JS_PATH.read_text("utf-8")
    assert 'id="view-facilities"' in html
    assert html.count("navFacilities:") == 2
    for path in ("/api/v1/facilities", "/initiate-disbursement", "/confirm-disbursement", "/payments", "/mark-overdue", "/close"):
        assert path in js
    assert "Number(facility.outstanding_amount)" not in js
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_ui_contract.py -q`

Expected: FAIL because facility UI is absent.

- [ ] **Step 3: Implement responsive role-aware UI**

Render monetary strings without JavaScript float arithmetic. Use server-provided `allowed_actions`, include `version` and a newly generated `crypto.randomUUID()` idempotency key in each command, disable controls while pending, and refresh tasks/detail after success. Add Russian/Chinese copy for statuses, actions, validation, evidence trail, and non-production boundary.

- [ ] **Step 4: Run UI tests and keyboard smoke check**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_ui_contract.py -q`

Expected: PASS; tab order reaches every action and reduced-motion behavior remains intact.

- [ ] **Step 5: Commit**

```powershell
git add app/static/index.html app/static/workflow.css app/static/workflow.js tests/test_ui_contract.py
git commit -m "feat: add bilingual financing lifecycle workbench"
```

### Task 6: Deterministic demo case, browser route, and release evidence

**Files:**
- Create: `scripts/facility_browser_acceptance.py`
- Create: `tests/test_facility_release_contract.py`
- Modify: `docs/demo-script.md`
- Modify: `docs/thesis-traceability.md`
- Modify: `docs/mvp-design.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Tasks 1–5 and current five-role accepted-application setup.
- Produces: one approved audited facility demo that reaches CLOSED, screenshot `output/facility-lifecycle-acceptance.png`, and exact thesis boundaries.

- [ ] **Step 1: Write failing release-boundary tests**

```python
def test_docs_call_lifecycle_a_simulation_not_real_settlement():
    text = _read("README.md") + _read("docs/thesis-traceability.md")
    assert "controlled financing lifecycle simulation" in text
    assert "does not execute a real bank transfer" in text
```

- [ ] **Step 2: Run and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_facility_release_contract.py -q`

Expected: FAIL because lifecycle evidence/documentation is absent.

- [ ] **Step 3: Implement the real browser path and documentation**

Use separate authenticated contexts for financier, supplier, risk manager, and auditor. Create two installments summing exactly to principal, initiate/confirm disbursement, confirm both repayments, close, assert ledger entries, switch to Chinese, and save the final screenshot. State that transfers, interest, fees, and accounting are simulated/not implemented.

- [ ] **Step 4: Run full increment verification**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Run: `docker compose up --build -d`

Run: `.\.venv\Scripts\python.exe scripts\facility_browser_acceptance.py`

Run: `.\.venv\Scripts\python.exe scripts\browser_acceptance.py`

Expected: all tests and both browser journeys pass; `/api/health` remains green.

- [ ] **Step 5: Commit and push**

```powershell
git add scripts/facility_browser_acceptance.py tests/test_facility_release_contract.py docs README.md
git commit -m "feat: complete simulated financing lifecycle"
git push origin main
```
