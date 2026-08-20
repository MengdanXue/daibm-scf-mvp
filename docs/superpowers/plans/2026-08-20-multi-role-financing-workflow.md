# Multi-Role Financing Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real authenticated five-role supply-chain financing workflow from supplier draft creation through auditor closure.

**Architecture:** Extend the existing FastAPI/PostgreSQL modular monolith with an Identity module and a Financing Workflow module. Authentication uses hashed server-side sessions in an HttpOnly cookie; the workflow service owns role, scope, state-transition, concurrency, action-history, and ledger atomicity rules. Existing Research Core remains separate and is exposed only as evidence to financier and auditor roles.

**Tech Stack:** Python 3.12, FastAPI 0.141, SQLAlchemy 2.0, PostgreSQL 17, Alembic 1.19, vanilla HTML/CSS/JavaScript, pytest, Testcontainers, Playwright CLI.

**Spec:** `docs/superpowers/specs/2026-08-20-multi-role-financing-workflow-design.md`

## Global Constraints

- PostgreSQL is the only database; do not add SQLite or `DATABASE_URL` switching.
- Russian is the default UI language and Chinese must cover every new user-facing string.
- Preserve Research Core v0.4 artifacts, hashes, ONNX inference, scenario lineage, and existing research tables.
- Do not add Redis, queues, microservices, Kubernetes, public registration, SSO, or external bank integrations.
- New workflow endpoints live under `/api/v1`; all business authorization is enforced server-side.
- Every workflow transition and its ledger append occur in one PostgreSQL transaction.
- Use optimistic integer `version`; stale transitions return HTTP 409.
- Keep the existing transparent operational risk score distinct from TGNN research evidence.
- Every task follows red → green → regression verification before commit.

---

### Task 1: PostgreSQL Identity and Workflow Schema

**Files:**
- Create: `alembic/versions/20260820_0003_multi_role_workflow.py`
- Create: `app/models_identity.py`
- Create: `app/models_workflow.py`
- Modify: `app/models.py`
- Modify: `alembic/env.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_workflow_database.py`

**Interfaces:**
- Produces `OrganizationModel`, `UserModel`, `UserSessionModel`, and `WorkflowActionModel` SQLAlchemy mappings.
- Extends `FinancingRequestModel` with nullable draft-time risk fields plus `status`, `version`, ownership, trade-document, and update fields.
- Backfills pre-existing requests to `status='audited'` and `version=1`.

- [ ] **Step 1: Write failing migration/model tests**

```python
def test_workflow_schema_has_identity_and_action_tables(migrated_engine):
    names = set(inspect(migrated_engine).get_table_names())
    assert {"organizations", "users", "user_sessions", "workflow_actions"} <= names


def test_draft_request_allows_risk_fields_to_be_null(session_factory):
    request = FinancingRequestModel(
        request_id=uuid.uuid4(), created_at=now, updated_at=now,
        applicant_id="supplier.demo", amount=Decimal("1200000.00"),
        term_days=90, features={}, risk_score=None, decision=None,
        explanations=None, control_action=None, status="draft", version=1,
    )
    with session_factory.begin() as session:
        session.add(request)
```

- [ ] **Step 2: Run the focused tests and verify missing tables/columns fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workflow_database.py -q`

Expected: failure because the new mappings and migration do not exist.

- [ ] **Step 3: Add mappings and Alembic migration**

Use PostgreSQL UUID, JSONB, `TIMESTAMPTZ`, identity bigint, role/status check constraints, and indexes defined in the spec. Import both model modules in `alembic/env.py`. Add new tables to `ALL_DATA_TABLES` before `financing_requests` in the test truncate order.

- [ ] **Step 4: Apply migration to Testcontainers and verify tests pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workflow_database.py tests/test_database.py tests/test_research_database.py -q`

- [ ] **Step 5: Commit schema task**

```powershell
git add alembic app/models.py app/models_identity.py app/models_workflow.py tests/conftest.py tests/test_workflow_database.py
git commit -m "feat: add identity and workflow schema"
```

### Task 2: Identity Domain, Passwords, Sessions, and Demo Accounts

**Files:**
- Create: `app/identity.py`
- Create: `app/repositories/identity.py`
- Create: `app/services/identity.py`
- Create: `tests/test_identity.py`

**Interfaces:**
- Produces `AuthenticatedUser(user_id, username, display_name, role, organization_id, organization_code, organization_name)`.
- Produces `hash_password(password, salt=None) -> tuple[str, str]` and `verify_password(password, salt_hex, expected_hash) -> bool` using `hashlib.scrypt` and `hmac.compare_digest`.
- Produces `IdentityService.seed_demo_accounts()`, `login(username, password) -> LoginResult`, `authenticate(token)`, and `logout(token)`.
- `LoginResult` contains the plaintext session token only until the response cookie is written; PostgreSQL stores only `sha256(token)`.

- [ ] **Step 1: Write failing unit and database tests**

Cover password hash non-equality to plaintext, unique salts, correct/wrong password verification, generic invalid credentials, 12-hour expiry, disabled users, token digest persistence, logout invalidation, and idempotent creation of the five exact demo accounts.

- [ ] **Step 2: Run and verify red**

Run: `.venv/Scripts/python.exe -m pytest tests/test_identity.py -q`

- [ ] **Step 3: Implement identity repository and service**

Use `secrets.token_urlsafe(32)`, `hashlib.sha256`, timezone-aware UTC timestamps, and a single transaction per login/logout. Never log or return password hashes, salts, or token digests from public serializers.

- [ ] **Step 4: Run focused tests and verify green**

Run: `.venv/Scripts/python.exe -m pytest tests/test_identity.py tests/test_workflow_database.py -q`

- [ ] **Step 5: Commit identity task**

```powershell
git add app/identity.py app/repositories/identity.py app/services/identity.py tests/test_identity.py
git commit -m "feat: add database-backed demo authentication"
```

### Task 3: Authentication API and Authorization Dependencies

**Files:**
- Create: `app/api/auth.py`
- Create: `app/api/dependencies.py`
- Create: `app/schemas_auth.py`
- Modify: `app/main.py`
- Modify: `app/api/research.py`
- Create: `tests/test_auth_api.py`

**Interfaces:**
- Produces `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`, and public `GET /api/v1/auth/demo-accounts`.
- Produces `current_user(request) -> AuthenticatedUser` and `require_roles(*roles)` FastAPI dependencies.
- Cookie name is exactly `daibm_session`; `HttpOnly=True`, `SameSite='strict'`, `Path='/'`, `Max-Age=43200`.

- [ ] **Step 1: Write failing endpoint tests**

```python
def test_login_sets_http_only_cookie(client):
    response = client.post("/api/v1/auth/login", json={
        "username": "supplier.demo", "password": "Demo123!"
    })
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.json()["user"]["role"] == "supplier"


def test_research_status_rejects_supplier(supplier_client):
    assert supplier_client.get("/api/research/status").status_code == 403
```

Also cover unauthenticated `401`, logout cookie removal, generic bad login, financier/auditor research access, auditor-only ledger/demo reset, and no password/hash fields in demo-account output.

- [ ] **Step 2: Run and verify red**

Run: `.venv/Scripts/python.exe -m pytest tests/test_auth_api.py -q`

- [ ] **Step 3: Implement routers and dependencies**

Initialize and seed `IdentityService` in application lifespan. Protect legacy business, research, ledger, and demo routes according to the spec while leaving `/`, static assets, login endpoints, demo-account metadata, and `/api/health` public.

- [ ] **Step 4: Update existing endpoint tests to authenticate with the minimum allowed role**

Add reusable `login(client, username)` and role-client fixtures in `tests/conftest.py`; do not bypass dependencies in application tests.

- [ ] **Step 5: Run auth and existing API/research tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_auth_api.py tests/test_api.py tests/test_research_api.py -q`

- [ ] **Step 6: Commit authentication API task**

```powershell
git add app/api app/main.py app/schemas_auth.py tests
git commit -m "feat: enforce session authentication and role access"
```

### Task 4: Financing Workflow Service and Audit Atomicity

**Files:**
- Create: `app/domain/workflow.py`
- Create: `app/repositories/workflow.py`
- Create: `app/services/workflow.py`
- Create: `app/schemas_workflow.py`
- Modify: `app/models.py`
- Modify: `app/service.py`
- Create: `tests/test_workflow_domain.py`
- Create: `tests/test_workflow_service.py`

**Interfaces:**
- Produces `WorkflowService.create_draft`, `update_draft`, `submit`, `confirm_trade`, `assess_risk`, `decide`, `apply_control`, `audit`, `get`, `list_for_user`, and `tasks_for_user`.
- Produces domain exceptions `ApplicationNotFound`, `ForbiddenScope`, `InvalidTransition`, and `StaleApplication`.
- Produces `allowed_actions(status, user) -> list[str]` derived only from server-side role/scope/state rules.

- [ ] **Step 1: Write pure state-machine tests**

Assert every row in the spec transition table succeeds for the correct role and representative wrong-role/wrong-state combinations fail. Assert returned applications can be edited/resubmitted and audited applications are immutable.

- [ ] **Step 2: Run domain tests and verify red**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workflow_domain.py -q`

- [ ] **Step 3: Implement pure transition policy**

Keep framework and SQLAlchemy imports out of `app/domain/workflow.py`. Represent actions/statuses/roles as `StrEnum`; return the target status or raise `InvalidTransition`.

- [ ] **Step 4: Write failing service integration tests**

Create one request and pass it through all five accounts. Assert status/version after each action, organization scope filtering, risk fields populated only at assessment, decision matching the requested valid decision, control action preserved, workflow-action count, ledger event types, actor metadata, and rollback when ledger append raises.

- [ ] **Step 5: Implement repositories and service transaction boundaries**

Use `UPDATE ... WHERE request_id=:id AND version=:expected_version` or equivalent row-count checking for optimistic concurrency. Append `WorkflowActionModel` and `LedgerEventSpec` before commit in the same `session_factory.begin()` block.

- [ ] **Step 6: Run workflow plus ledger/service regression tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workflow_domain.py tests/test_workflow_service.py tests/test_service.py tests/test_ledger.py -q`

- [ ] **Step 7: Commit workflow core task**

```powershell
git add app/domain app/repositories app/services app/schemas_workflow.py app/models.py app/service.py tests
git commit -m "feat: add role-controlled financing state machine"
```

### Task 5: Versioned Workflow REST API

**Files:**
- Create: `app/api/workflow.py`
- Modify: `app/main.py`
- Create: `tests/test_workflow_api.py`

**Interfaces:**
- Exposes all `/api/v1/tasks`, `/api/v1/dashboard`, and `/api/v1/applications` routes specified in the design.
- Application detail contains `allowed_actions` and ordered `timeline`.
- Maps domain exceptions to exact structured 404/403/409 responses from the spec.

- [ ] **Step 1: Write failing API journey and authorization tests**

Use five separately authenticated `TestClient` instances. Assert supplier creates/submits; core sees it in tasks and confirms; financier assesses/decides; risk manager controls; auditor verifies/audits. Direct calls from every wrong role must return 403 or non-leaking 404. Reusing an old `version` must return `409 stale_application`.

- [ ] **Step 2: Run and verify red**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workflow_api.py -q`

- [ ] **Step 3: Implement thin route adapters and structured errors**

Controllers parse schemas, call `WorkflowService`, and serialize results; they do not contain transition logic or ORM queries.

- [ ] **Step 4: Run API, auth, workflow, and research regressions**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workflow_api.py tests/test_auth_api.py tests/test_workflow_service.py tests/test_research_api.py -q`

- [ ] **Step 5: Commit workflow API task**

```powershell
git add app/api/workflow.py app/main.py tests/test_workflow_api.py
git commit -m "feat: expose versioned financing workflow API"
```

### Task 6: Bilingual Login, Role Workbenches, and Application Timeline

**Files:**
- Create: `app/static/workflow.css`
- Create: `app/static/workflow.js`
- Modify: `app/static/index.html`
- Modify: `tests/test_ui_contract.py`

**Interfaces:**
- `workflow.js` owns session bootstrap, login/logout, current-user state, task/application API calls, allowed-action forms, and role-specific rendering.
- Existing `index.html` retains Research Core and ledger renderers; authenticated shell calls them only for allowed roles.
- `workflow.css` owns login, role badge, task cards, application workspace, status timeline, and responsive behavior.

- [ ] **Step 1: Write failing UI contract tests**

Assert login form and logout button IDs, five account cards, role/workbench/status/action i18n keys exactly twice, no plaintext password/hash in API-rendered data, `workflow.css`/`workflow.js` imports, use of `/api/v1`, and absence of client-side hardcoded permission decisions.

- [ ] **Step 2: Run and verify red**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_contract.py -q`

- [ ] **Step 3: Implement the unauthenticated login screen**

Russian default copy, Chinese toggle, username/password fields, five quick-fill cards, generic error area, and no business view rendered before `/auth/me` succeeds.

- [ ] **Step 4: Implement authenticated role shell and workbench**

Show display name, organization, translated role, logout, task count, role-filtered list, create button only for supplier, Research navigation only for financier/auditor, and ledger navigation only for auditor.

- [ ] **Step 5: Implement application create/detail/action flow**

Render contract/invoice plus existing risk inputs, status/version, ordered timeline, and only the exact `allowed_actions` returned by the server. After each action refresh current application, task list, dashboard, and session-safe navigation.

- [ ] **Step 6: Run UI contracts and complete RU/ZH key audit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_contract.py -q`

- [ ] **Step 7: Commit UI task**

```powershell
git add app/static tests/test_ui_contract.py
git commit -m "feat: add bilingual role-based financing workbenches"
```

### Task 7: Demo Lifecycle, Documentation, and Release Contract

**Files:**
- Modify: `app/services/identity.py`
- Modify: `app/service.py`
- Modify: `README.md`
- Modify: `docs/demo-script.md`
- Modify: `docs/mvp-design.md`
- Modify: `tests/test_release_contract.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Startup idempotently seeds organizations/accounts without resetting business data.
- Auditor-only demo reset preserves identity and Research Core evidence while recreating three audited contrast cases.
- Documentation lists all five usernames, common local-demo password, role sequence, and explicit non-production identity boundary.

- [ ] **Step 1: Write failing seed/reset and release-contract tests**

Assert repeated startup creates exactly five users, demo reset leaves users/sessions schema intact, contrast cases are audited, README names the five roles, and the launch path still uses PostgreSQL on port 8010.

- [ ] **Step 2: Run and verify red**

Run: `.venv/Scripts/python.exe -m pytest tests/test_release_contract.py tests/test_api.py -q`

- [ ] **Step 3: Implement idempotent startup/demo behavior and update docs**

Do not reset user passwords on every startup after initial creation. Label credentials as local demonstration accounts, not production defaults.

- [ ] **Step 4: Run focused release tests and `git diff --check`**

Run: `.venv/Scripts/python.exe -m pytest tests/test_release_contract.py tests/test_api.py -q`

Run: `git diff --check`

- [ ] **Step 5: Commit documentation/release task**

```powershell
git add app README.md docs tests
git commit -m "docs: add five-role defense workflow"
```

### Task 8: Full Verification and Five-Session Browser Acceptance

**Files:**
- Modify only files required by failures found during acceptance.

**Interfaces:**
- Produces a healthy Docker deployment at `http://127.0.0.1:8010/` and one browser-verified application in final `audited` state.

- [ ] **Step 1: Run the complete automated suite**

Run: `.venv/Scripts/python.exe -m pytest -q`

Expected: exit code 0 with all prior and new tests passing.

- [ ] **Step 2: Rebuild PostgreSQL/FastAPI deployment**

Run: `docker compose up --build -d`

Verify `/api/health` reports PostgreSQL reachable, ledger valid, and Research Core ready.

- [ ] **Step 3: Run five independent Playwright sessions**

Use `supplier.demo`, `core.demo`, `financier.demo`, `risk.demo`, and `auditor.demo`. Complete the same application through all states, logging out after each role. Assert forbidden navigation is absent and direct forbidden API calls fail.

- [ ] **Step 4: Verify Russian, Chinese, desktop, and mobile presentation**

Use a fresh session for Russian default, switch to Chinese, inspect 1440×1000 and 390×844 screenshots, and verify no horizontal overflow.

- [ ] **Step 5: Verify browser and backend evidence**

Assert console has 0 errors/warnings, final status is `audited`, timeline contains all five actors, ledger verification is valid, and Research Core status is ready for financier/auditor.

- [ ] **Step 6: Run final evidence gate**

Run: `.venv/Scripts/python.exe -m pytest -q`

Run: `git diff --check`

Run: `git status --short`

- [ ] **Step 7: Commit fixes, push private main, and compare local/remote heads**

```powershell
git add alembic app tests README.md docs pyproject.toml requirements.txt
git commit -m "feat: complete multi-role financing workflow"
git push origin main
git rev-parse HEAD
git ls-remote origin refs/heads/main
```
