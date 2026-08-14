# PostgreSQL-only Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 DAIBM-SCF MVP 从 SQLite 完整重构为 PostgreSQL-only，并在不扩大论文功能范围的前提下，保留俄语默认/中文切换页面、三类风险决策、可解释结果、哈希链审计、篡改演示和一键恢复能力。

**Architecture:** FastAPI 路由只负责 HTTP 映射；`FinancingService` 编排风险计算和原子事务；SQLAlchemy repository 负责 PostgreSQL 数据访问；Alembic 是唯一建表入口。一次融资申请和对应四条账本事件使用同一个 SQLAlchemy `Session` 事务，账本追加前获取 PostgreSQL transaction-level advisory lock，防止并发分叉。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2.x（同步 API）、Psycopg 3、PostgreSQL 17、Alembic、pytest、Testcontainers for Python、Docker Compose、Playwright。

**Spec:** `docs/superpowers/specs/2026-08-14-postgresql-only-architecture-design.md`

## Global Constraints

- PostgreSQL 是唯一运行时和测试数据库；不保留 SQLite adapter、fallback、测试后门或数据库类型开关。
- 不迁移现有 `data/*.db*` 合成数据；实现完成后删除这些本地文件和相关忽略规则。
- 不接受 `DATABASE_URL` 作为数据库类型切换；应用仅从 `POSTGRES_HOST`、`POSTGRES_PORT`、`POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD` 构造 `postgresql+psycopg` URL。
- 不在应用启动时调用 `Base.metadata.create_all()`；所有 schema 变更只通过 Alembic。
- 保持现有 API URL 和成功响应的主要字段兼容；仅扩展 `/api/health` 数据库状态。
- 不新增认证、微服务、消息队列、机器学习训练、SHAP、区块链网络或真实金融接口。
- 每个实现任务遵循 RED → GREEN → REFACTOR：先写失败测试，确认预期失败，再写最小实现并复测。
- 不把真实/部署用 PostgreSQL 密码、完整连接 URL、SQL 异常正文返回给浏览器或提交进 Git；仓库内只允许明确标注的本地合成演示默认值。

## Target File Map

```text
.
├── alembic.ini                                      # 新增：迁移入口，不保存真实 DSN
├── alembic/
│   ├── env.py                                       # 新增：从 PostgresSettings 构造迁移连接
│   ├── script.py.mako                               # 新增：Alembic 模板
│   └── versions/
│       └── 20260814_0001_initial_postgresql.py      # 新增：两张表、约束、索引
├── app/
│   ├── config.py                                    # 重写：PostgreSQL-only 设置与安全 URL
│   ├── database.py                                  # 新增：Engine、Session factory、可达性检查
│   ├── db.py                                        # 删除：SQLite 连接与建表
│   ├── models.py                                    # 新增：SQLAlchemy ORM 模型
│   ├── ledger.py                                    # 重写：仅保留纯哈希/规范 JSON 逻辑
│   ├── repositories/
│   │   ├── __init__.py                              # 新增：公开 repository 类型
│   │   ├── financing.py                             # 新增：融资申请查询与写入
│   │   └── ledger.py                                # 新增：账本追加、验证、篡改、清理
│   ├── service.py                                   # 重写：统一事务、seed/reset/tamper 编排
│   └── main.py                                      # 修改：数据库依赖注入、503 健康响应
├── tests/
│   ├── conftest.py                                  # 新增：真实 PostgreSQL Testcontainer + Alembic
│   ├── test_config.py                               # 新增：配置构造与非法端口测试
│   ├── test_database.py                             # 新增：初始迁移和数据库健康测试
│   ├── test_ledger.py                               # 重写：纯哈希 + PostgreSQL 篡改/并发验证
│   ├── test_service.py                              # 新增：原子提交、强制失败回滚
│   ├── test_api.py                                  # 重写：PostgreSQL-backed API 闭环
│   └── test_risk.py                                 # 保留：科研基线算法回归测试
├── docker-compose.yml                               # 重写：postgres + mvp 两服务
├── Dockerfile                                       # 修改：复制 Alembic 文件、删除 SQLite 目录
├── start-demo.cmd                                   # 重写：Docker 启动、轮询健康、打开页面
├── .env.example                                     # 重写：五个 POSTGRES_* 变量
├── .gitignore                                       # 修改：删除 SQLite 专用规则
├── pyproject.toml                                   # 修改：运行/测试依赖
├── requirements.txt                                 # 修改：运行依赖锁定
├── requirements-dev.txt                             # 修改：测试依赖锁定
├── README.md                                        # 修改：PostgreSQL-only 启动与验证说明
├── docs/demo-script.md                              # 修改：答辩启动、篡改、恢复话术
├── docs/mvp-design.md                               # 修改：数据层与事务架构
└── docs/thesis-to-mvp.md                            # 修改：论文概念到 PostgreSQL 实现映射
```

---

### Task 1: Add PostgreSQL dependencies and a single-source configuration object

**Files:**

- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `requirements-dev.txt`
- Rewrite: `.env.example`
- Rewrite: `app/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

```python
# tests/test_config.py
import pytest

from app.config import PostgresSettings


def test_settings_build_psycopg_url_without_exposing_password():
    settings = PostgresSettings(
        host="postgres",
        port=5432,
        database="daibm_scf",
        user="daibm",
        password="p@ss/word",
    )

    url = settings.sqlalchemy_url

    assert url.drivername == "postgresql+psycopg"
    assert url.host == "postgres"
    assert url.database == "daibm_scf"
    assert url.password == "p@ss/word"
    assert "p@ss/word" not in url.render_as_string(hide_password=True)


def test_settings_reject_invalid_port(monkeypatch):
    monkeypatch.setenv("POSTGRES_PORT", "not-a-port")

    with pytest.raises(ValueError, match="POSTGRES_PORT"):
        PostgresSettings.from_env()
```

- [ ] **Step 2: Run the new test and confirm RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_config.py -q
```

Expected: collection fails because `PostgresSettings` does not exist.

- [ ] **Step 3: Add the minimum runtime and test dependencies**

Add equivalent dependency declarations to both packaging paths:

```toml
# pyproject.toml excerpts
dependencies = [
  "alembic>=1.18,<2",
  "fastapi>=0.115,<1",
  "psycopg[binary]>=3.3,<4",
  "sqlalchemy>=2.0,<3",
  "uvicorn[standard]>=0.30,<1",
]

[project.optional-dependencies]
dev = [
  "httpx>=0.27,<1",
  "pytest>=8,<10",
  "testcontainers[postgres]>=4.13,<5",
]
```

Use the resolved versions from `pip` in `requirements.txt` and `requirements-dev.txt`; keep both files consistent with `pyproject.toml`.

- [ ] **Step 4: Implement PostgreSQL-only settings**

```python
# app/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import URL


@dataclass(frozen=True)
class PostgresSettings:
    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "PostgresSettings":
        values = os.environ if environ is None else environ
        raw_port = values.get("POSTGRES_PORT", "5432")
        try:
            port = int(raw_port)
        except ValueError as error:
            raise ValueError("POSTGRES_PORT must be an integer") from error
        if not 1 <= port <= 65535:
            raise ValueError("POSTGRES_PORT must be between 1 and 65535")
        return cls(
            host=values.get("POSTGRES_HOST", "localhost"),
            port=port,
            database=values.get("POSTGRES_DB", "daibm_scf"),
            user=values.get("POSTGRES_USER", "daibm"),
            password=values.get("POSTGRES_PASSWORD", "daibm_demo_password"),
        )

    @property
    def sqlalchemy_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )
```

The defaults are local demo credentials only. `.env.example` must list the same five variables and label the password as a value that must be replaced outside local demo use.

- [ ] **Step 5: Install dependencies and confirm GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests/test_config.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit the configuration slice**

```powershell
git add pyproject.toml requirements.txt requirements-dev.txt .env.example app/config.py tests/test_config.py
git commit -m "build: add PostgreSQL-only configuration"
```

---

### Task 2: Define PostgreSQL ORM models and the initial Alembic migration

**Files:**

- Create: `app/models.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/20260814_0001_initial_postgresql.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: Write the migration schema test first**

```python
# tests/test_database.py
from sqlalchemy import inspect


def test_initial_migration_creates_postgresql_schema(migrated_engine):
    inspector = inspect(migrated_engine)

    assert set(inspector.get_table_names()) >= {
        "alembic_version",
        "financing_requests",
        "ledger_events",
    }
    request_columns = {column["name"]: column for column in inspector.get_columns("financing_requests")}
    ledger_columns = {column["name"]: column for column in inspector.get_columns("ledger_events")}

    assert str(request_columns["request_id"]["type"]) == "UUID"
    assert str(request_columns["features"]["type"]) == "JSONB"
    assert str(ledger_columns["payload"]["type"]) == "JSONB"
```

- [ ] **Step 2: Run the test and confirm RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_database.py -q
```

Expected: fixture/migration files are missing. If Docker is not running, first record that environmental prerequisite, start Docker Desktop, then rerun to obtain the intended schema failure.

- [ ] **Step 3: Define SQLAlchemy 2.x models with PostgreSQL-native types**

```python
# app/models.py (essential shape)
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Float, ForeignKey, Identity, Index, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class FinancingRequestModel(Base):
    __tablename__ = "financing_requests"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_financing_requests_amount_positive"),
        CheckConstraint("term_days BETWEEN 1 AND 365", name="ck_financing_requests_term_days"),
        CheckConstraint("risk_score BETWEEN 0 AND 1", name="ck_financing_requests_risk_score"),
        CheckConstraint(
            "decision IN ('approved', 'manual_review', 'rejected')",
            name="ck_financing_requests_decision",
        ),
        Index("ix_financing_requests_created_at", "created_at"),
        Index("ix_financing_requests_decision", "decision"),
        Index("ix_financing_requests_applicant_id", "applicant_id"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applicant_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    term_days: Mapped[int] = mapped_column(Integer, nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    explanations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    control_action: Mapped[str] = mapped_column(Text, nullable=False)


class LedgerEventModel(Base):
    __tablename__ = "ledger_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('FINANCING_REQUEST', 'RISK_ASSESSMENT', 'FINANCING_DECISION', 'CONTROL_ACTION')",
            name="ck_ledger_events_event_type",
        ),
        CheckConstraint("char_length(event_hash) = 64", name="ck_ledger_events_hash_length"),
        CheckConstraint("char_length(previous_hash) IN (7, 64)", name="ck_ledger_events_previous_hash_length"),
        Index("ix_ledger_events_entity_id_id", "entity_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_requests.request_id", ondelete="RESTRICT"),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    previous_hash: Mapped[str] = mapped_column(Text, nullable=False)
    event_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
```

- [ ] **Step 4: Create a hand-written initial migration**

Initialize Alembic once, then replace generated content so `alembic/env.py` imports `Base.metadata` and obtains the URL from `PostgresSettings.from_env().sqlalchemy_url`. Do not put a URL in `alembic.ini`.

The initial revision must explicitly create both tables, all named constraints, the three financing indexes, and `(entity_id, id)` ledger index. `downgrade()` drops ledger before financing.

- [ ] **Step 5: Add the real PostgreSQL test fixture required by this test**

Add the session-scoped container bootstrap described in Task 3 just far enough to run Alembic. Normalize the Testcontainers URL to `postgresql+psycopg` before constructing the engine.

- [ ] **Step 6: Run schema tests and inspect generated DDL**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_database.py -q
.venv\Scripts\python.exe -m alembic upgrade head --sql
```

Expected: schema test passes; offline SQL contains PostgreSQL `UUID`, `JSONB`, `TIMESTAMP WITH TIME ZONE`, `NUMERIC(14, 2)`, identity column, constraints, and indexes. It must contain no SQLite syntax.

- [ ] **Step 7: Commit the schema slice**

```powershell
git add alembic.ini alembic app/models.py tests/test_database.py
git commit -m "feat: define PostgreSQL schema with Alembic"
```

---

### Task 3: Add the database resource boundary and reusable PostgreSQL fixtures

**Files:**

- Create: `app/database.py`
- Create: `tests/conftest.py`
- Modify: `tests/test_database.py`

- [ ] **Step 1: Extend the failing database tests**

```python
# tests/test_database.py additions
from sqlalchemy import text

from app.database import Database


def test_database_session_factory_reaches_postgresql(postgres_url):
    database = Database.create(postgres_url)
    try:
        with database.session_factory() as session:
            assert session.scalar(text("SELECT current_database()"))
        assert database.is_reachable() is True
    finally:
        database.dispose()
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_database.py::test_database_session_factory_reaches_postgresql -q
```

Expected: fails because `app.database.Database` is missing.

- [ ] **Step 3: Implement an explicit database resource object**

```python
# app/database.py
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, URL, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


@dataclass
class Database:
    engine: Engine
    session_factory: sessionmaker[Session]

    @classmethod
    def create(cls, url: URL | str) -> "Database":
        engine = create_engine(url, pool_pre_ping=True)
        return cls(
            engine=engine,
            session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        )

    def is_reachable(self) -> bool:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True

    def dispose(self) -> None:
        self.engine.dispose()
```

Do not swallow database exceptions here; API code will translate them into a sanitized 503.

- [ ] **Step 4: Complete Testcontainers + Alembic fixtures**

```python
# tests/conftest.py (essential flow)
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer("postgres:17-alpine") as postgres:
        yield make_url(postgres.get_connection_url()).set(
            drivername="postgresql+psycopg"
        )


@pytest.fixture(scope="session")
def migrated_engine(postgres_url):
    engine = create_engine(postgres_url, pool_pre_ping=True)
    config = Config("alembic.ini")
    config.attributes["connection"] = engine.connect()
    try:
        command.upgrade(config, "head")
        yield engine
    finally:
        config.attributes["connection"].close()
        engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(migrated_engine):
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE ledger_events, financing_requests RESTART IDENTITY CASCADE"))
    yield
```

Refine connection ownership in `alembic/env.py` so an externally supplied Alembic connection is reused and never accidentally closed twice.

- [ ] **Step 5: Run all database tests and confirm GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_database.py -q
```

Expected: all tests pass against a real PostgreSQL 17 container.

- [ ] **Step 6: Commit the database boundary**

```powershell
git add app/database.py tests/conftest.py tests/test_database.py alembic/env.py
git commit -m "feat: add PostgreSQL database resource boundary"
```

---

### Task 4: Separate pure ledger hashing from PostgreSQL repositories

**Files:**

- Rewrite: `app/ledger.py`
- Create: `app/repositories/__init__.py`
- Create: `app/repositories/financing.py`
- Create: `app/repositories/ledger.py`
- Rewrite: `tests/test_ledger.py`

- [ ] **Step 1: Preserve the hash contract with pure unit tests**

```python
# tests/test_ledger.py excerpts
from app.ledger import GENESIS_HASH, calculate_hash, canonical_json


def test_canonical_json_is_stable_across_key_order():
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})


def test_hash_changes_when_payload_changes():
    first = calculate_hash(GENESIS_HASH, "2026-08-14T00:00:00+00:00", "FINANCING_REQUEST", "00000000-0000-0000-0000-000000000001", canonical_json({"amount": 100}))
    second = calculate_hash(GENESIS_HASH, "2026-08-14T00:00:00+00:00", "FINANCING_REQUEST", "00000000-0000-0000-0000-000000000001", canonical_json({"amount": 999}))
    assert first != second
```

- [ ] **Step 2: Add failing repository integration tests**

```python
def test_ledger_repository_detects_jsonb_tampering(session_factory, financing_repository, ledger_repository):
    request = financing_repository.example_model()
    with session_factory.begin() as session:
        financing_repository.add(session, request)
        ledger_repository.append(session, "FINANCING_REQUEST", request.request_id, {"amount": 100})
        ledger_repository.append(session, "FINANCING_DECISION", request.request_id, {"decision": "approved"})

    assert ledger_repository.verify(session_factory)["valid"] is True

    with session_factory.begin() as session:
        ledger_repository.tamper_first_risk_or_request_event(session)

    result = ledger_repository.verify(session_factory)
    assert result["valid"] is False
    assert result["invalid_event_id"] == 1
```

- [ ] **Step 3: Run and confirm repository tests are RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ledger.py -q
```

Expected: pure tests may pass after extracting hash functions; repository tests fail because repository modules do not exist.

- [ ] **Step 4: Make `app/ledger.py` database-free**

Keep only:

```python
GENESIS_HASH = "GENESIS"

def canonical_json(payload: dict[str, Any]) -> str: ...

def calculate_hash(
    previous_hash: str,
    created_at: str,
    event_type: str,
    entity_id: str,
    payload_json: str,
) -> str: ...
```

The timestamp passed to hashing must use one stable UTC ISO-8601 representation. Repository verification must call `canonical_json(row.payload)` after JSONB deserialization.

- [ ] **Step 5: Implement focused repositories**

`FinancingRequestRepository` owns `add`, `get`, `list`, `dashboard_aggregates`, and `exists_any`.

`LedgerRepository.append()` must use the caller's session and transaction:

```python
LEDGER_LOCK_KEY = 0x444149424D  # stable signed BIGINT-safe application key

def append(self, session: Session, event_type: str, entity_id: uuid.UUID, payload: dict[str, Any]) -> LedgerEventModel:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": LEDGER_LOCK_KEY},
    )
    previous_hash = session.scalar(
        select(LedgerEventModel.event_hash).order_by(LedgerEventModel.id.desc()).limit(1)
    ) or GENESIS_HASH
    created_at = datetime.now(timezone.utc)
    created_at_text = created_at.isoformat(timespec="microseconds")
    entity_text = str(entity_id)
    payload_json = canonical_json(payload)
    event = LedgerEventModel(
        created_at=created_at,
        event_type=event_type,
        entity_id=entity_id,
        payload=payload,
        previous_hash=previous_hash,
        event_hash=calculate_hash(
            previous_hash, created_at_text, event_type, entity_text, payload_json
        ),
    )
    session.add(event)
    session.flush()
    return event
```

Important refinement: append four events after acquiring the same transaction-level lock once. Implement a private `_acquire_chain_lock()` and either call it once from `append_many()` or rely on PostgreSQL re-entrant acquisition within the same transaction. Prefer `append_many()` so intent is explicit and round trips are minimized.

`verify()` orders by ascending identity; it checks both `previous_hash` continuity and recalculated `event_hash`. `list()` orders newest first. `tamper` changes JSONB only. `clear()` uses `TRUNCATE ... RESTART IDENTITY` only from the demo reset path.

- [ ] **Step 6: Run ledger tests and confirm GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ledger.py -q
```

Expected: pure hash, valid chain, tamper detection, and JSONB round-trip tests pass.

- [ ] **Step 7: Commit the repository slice**

```powershell
git add app/ledger.py app/repositories tests/test_ledger.py
git commit -m "refactor: move ledger persistence to PostgreSQL repositories"
```

---

### Task 5: Make financing creation and ledger append one atomic transaction

**Files:**

- Rewrite: `app/service.py`
- Create: `tests/test_service.py`

- [ ] **Step 1: Write the closed-loop atomicity test**

```python
# tests/test_service.py excerpts
from sqlalchemy import func, select

from app.models import FinancingRequestModel, LedgerEventModel


def test_create_request_commits_one_request_and_four_events(service, session_factory, stable_payload):
    result = service.create_request(stable_payload)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FinancingRequestModel)) == 1
        assert session.scalar(select(func.count()).select_from(LedgerEventModel)) == 4
    assert result["decision"] == "approved"
```

- [ ] **Step 2: Write the forced-failure rollback test**

```python
def test_create_request_rolls_back_request_and_partial_events(
    service, session_factory, stable_payload, monkeypatch
):
    original = service.ledger_repository._build_event
    calls = 0

    def fail_on_third(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("forced ledger failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(service.ledger_repository, "_build_event", fail_on_third)

    with pytest.raises(RuntimeError, match="forced ledger failure"):
        service.create_request(stable_payload)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FinancingRequestModel)) == 0
        assert session.scalar(select(func.count()).select_from(LedgerEventModel)) == 0
```

- [ ] **Step 3: Run and confirm RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_service.py -q
```

Expected: old `FinancingService` still requires a `Path` and opens independent SQLite connections.

- [ ] **Step 4: Rewrite service around `sessionmaker.begin()`**

```python
class FinancingService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        financing_repository: FinancingRequestRepository | None = None,
        ledger_repository: LedgerRepository | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.financing_repository = financing_repository or FinancingRequestRepository()
        self.ledger_repository = ledger_repository or LedgerRepository()

    def create_request(self, request: FinancingRequestCreate) -> dict[str, Any]:
        result = assess(request)
        model = self._build_request_model(request, result)
        events = self._build_ledger_event_specs(model, result)

        with self.session_factory.begin() as session:
            self.financing_repository.add(session, model)
            self.ledger_repository.append_many(session, model.request_id, events)

        return self.get_request(model.request_id)
```

All read methods open short-lived sessions. `reset_demo()` performs clear + three scenarios in one transaction by using an internal `_create_request_in_session()` helper; it must not nest `sessionmaker.begin()` three times. `seed_demo()` remains idempotent. `tamper_demo_ledger()` uses one transaction and returns verification after commit.

- [ ] **Step 5: Add the concurrent fork-prevention test**

Use `ThreadPoolExecutor(max_workers=6)` with a barrier so six requests append concurrently. After all futures complete:

```python
verification = service.ledger_repository.verify(session_factory)
assert verification["valid"] is True
assert verification["event_count"] == 24

with session_factory() as session:
    previous_hashes = session.scalars(
        select(LedgerEventModel.previous_hash).where(
            LedgerEventModel.previous_hash != GENESIS_HASH
        )
    ).all()
assert len(previous_hashes) == len(set(previous_hashes))
```

This is the direct evidence that transaction-level advisory locking prevents two events from attaching to the same non-genesis head.

- [ ] **Step 6: Run service and concurrency tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_service.py tests/test_ledger.py -q
```

Expected: atomicity, rollback, reset, tamper, and concurrency tests all pass.

- [ ] **Step 7: Commit the service transaction slice**

```powershell
git add app/service.py tests/test_service.py tests/test_ledger.py
git commit -m "feat: make financing audit writes atomic"
```

---

### Task 6: Inject PostgreSQL into FastAPI and sanitize availability errors

**Files:**

- Modify: `app/main.py`
- Rewrite: `tests/test_api.py`

- [ ] **Step 1: Rewrite the API fixture and health expectations**

```python
# tests/test_api.py excerpt
def test_health_reports_postgresql(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == {
        "backend": "postgresql",
        "reachable": True,
    }


def test_demo_creates_closed_loop_and_valid_ledger(client):
    seeded = client.post("/api/demo/seed")
    assert seeded.status_code == 200
    assert len(seeded.json()) == 3
    assert {item["decision"] for item in client.get("/api/requests").json()} == {
        "approved", "manual_review", "rejected"
    }
    assert client.get("/api/ledger/verify").json()["event_count"] == 12
```

- [ ] **Step 2: Add a sanitized 503 test**

```python
def test_health_hides_database_exception_details(client, monkeypatch):
    def unavailable():
        raise RuntimeError("postgresql://user:secret@internal-host/database")

    monkeypatch.setattr(client.app.state.database, "is_reachable", unavailable)
    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "database_unavailable",
            "message": "PostgreSQL is unavailable",
        }
    }
    assert "secret" not in response.text
    assert "internal-host" not in response.text
```

- [ ] **Step 3: Run API tests and confirm RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_api.py -q
```

Expected: old app factory still accepts a SQLite path and health lacks the database object.

- [ ] **Step 4: Refactor app creation without creating schema at runtime**

```python
def create_app(database: Database | None = None) -> FastAPI:
    owns_database = database is None
    active_database = database or Database.create(PostgresSettings.from_env().sqlalchemy_url)
    service = FinancingService(active_database.session_factory)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.database = active_database
        application.state.service = service
        yield
        if owns_database:
            active_database.dispose()
```

There must be no `initialize()`, `create_all()`, or migration call inside FastAPI lifespan. The container command runs Alembic before Uvicorn.

Catch `SQLAlchemyError`/connection failures only at the health endpoint boundary and return the fixed 503 object above. Do not catch programming errors from ledger verification as database availability errors.

- [ ] **Step 5: Run the API and risk regression suite**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_risk.py -q
```

Expected: API closed loop, three decision bands, health, tamper, reset, and risk regression pass.

- [ ] **Step 6: Commit the API slice**

```powershell
git add app/main.py tests/test_api.py
git commit -m "refactor: inject PostgreSQL into FastAPI"
```

---

### Task 7: Replace SQLite startup with Docker Compose PostgreSQL orchestration

**Files:**

- Rewrite: `docker-compose.yml`
- Modify: `Dockerfile`
- Rewrite: `start-demo.cmd`
- Modify: `.env.example`
- Modify: `.gitignore`

- [ ] **Step 1: Write the Compose acceptance assertions before editing**

Record these as the RED checks:

```powershell
docker compose config
Select-String -Path docker-compose.yml,Dockerfile,start-demo.cmd,.env.example -Pattern "DAIBM_DB_PATH|sqlite|/app/data"
```

Expected before implementation: Compose has no `postgres` service or healthcheck, and the search finds obsolete SQLite configuration.

- [ ] **Step 2: Define the two-service Compose stack**

```yaml
# docker-compose.yml essential structure
services:
  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-daibm_scf}
      POSTGRES_USER: ${POSTGRES_USER:-daibm}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-daibm_demo_password}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 2s
      timeout: 3s
      retries: 30
    volumes:
      - postgres-data:/var/lib/postgresql/data

  mvp:
    build: .
    ports:
      - "8000:8000"
    environment:
      POSTGRES_HOST: postgres
      POSTGRES_PORT: 5432
      POSTGRES_DB: ${POSTGRES_DB:-daibm_scf}
      POSTGRES_USER: ${POSTGRES_USER:-daibm}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-daibm_demo_password}
    depends_on:
      postgres:
        condition: service_healthy
    command: ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

volumes:
  postgres-data:
```

Do not expose PostgreSQL's port to the host unless a verified development need appears; the application reaches it through the Compose network.

- [ ] **Step 3: Update the image and one-click launcher**

`Dockerfile` must copy `alembic.ini`, `alembic/`, and `app/`; remove `DAIBM_DB_PATH`, `/app/data`, and SQLite directory creation.

`start-demo.cmd` must:

1. verify `docker` exists;
2. verify the Docker daemon with `docker info`;
3. run `docker compose up --build -d`;
4. poll `http://127.0.0.1:8000/api/health` up to 60 times using PowerShell `Invoke-WebRequest`;
5. open the browser only after HTTP 200;
6. on timeout, print bilingual guidance and `docker compose logs --tail 100`, then exit non-zero.

- [ ] **Step 4: Remove SQLite-only ignore/config entries**

Delete `data/*.db`, `data/*.db-*`, `DAIBM_DB_PATH`, `daibm-data`, and `/app/data` references. Keep `.env` ignored.

- [ ] **Step 5: Validate and smoke-test the stack**

Run:

```powershell
docker compose config
docker compose down -v
docker compose up --build -d
docker compose ps
curl.exe --fail http://127.0.0.1:8000/api/health
docker compose logs --tail 100 mvp postgres
```

Expected:

- `postgres` is healthy;
- `mvp` is running;
- Alembic reports upgrade to the initial revision;
- health returns `database.backend = postgresql` and `reachable = true`;
- logs contain no traceback.

- [ ] **Step 6: Commit the deployment slice**

```powershell
git add docker-compose.yml Dockerfile start-demo.cmd .env.example .gitignore
git commit -m "build: run demo on PostgreSQL with Docker Compose"
```

---

### Task 8: Delete obsolete SQLite artifacts and align all user-facing documentation

**Files:**

- Delete: `app/db.py`
- Delete locally: `data/daibm_scf.db`
- Delete locally: `data/daibm_scf.db-shm`
- Delete locally: `data/daibm_scf.db-wal`
- Modify: `README.md`
- Modify: `docs/demo-script.md`
- Modify: `docs/mvp-design.md`
- Modify: `docs/thesis-to-mvp.md`
- Check: `app/static/index.html`

- [ ] **Step 1: Capture obsolete-reference failures**

Run:

```powershell
Get-ChildItem -Recurse -File -Exclude *.pyc | Where-Object { $_.FullName -notmatch "\\.git\\|\\.venv\\|output\\|docs\\superpowers\\plans\\|docs\\superpowers\\specs\\" } | Select-String -Pattern "sqlite|SQLite|DAIBM_DB_PATH|database_path|app\.db|create_all"
```

Expected before cleanup: matches in source, deployment files, tests, and docs.

- [ ] **Step 2: Delete exact obsolete runtime artifacts**

Delete `app/db.py` after all imports have moved. Verify each exact local data path is inside `D:\毕业论文\daibm-scf-mvp\data` and then delete the three synthetic SQLite files. These are disposable demo data and are not migrated.

- [ ] **Step 3: Update documentation consistently**

README must include:

- Docker Desktop prerequisite;
- `start-demo.cmd` as recommended Windows path;
- `docker compose up --build -d` manual path;
- `docker compose down` normal stop and `docker compose down -v` explicit demo-data reset;
- PostgreSQL-only architecture summary;
- Alembic commands;
- pytest/Testcontainers requirement;
- warning that the hash chain is an auditable demo mechanism, not a production blockchain.

`docs/demo-script.md` must preserve Russian-first presentation flow and Chinese backup language, including database health, three scenarios, tamper detection, and reset.

`docs/mvp-design.md` and `docs/thesis-to-mvp.md` must describe SQLAlchemy repositories, PostgreSQL JSONB/UUID/TIMESTAMPTZ, Alembic, the atomic request/event transaction, and advisory-lock concurrency evidence without inflating the thesis's innovation claim.

- [ ] **Step 4: Verify the static UI still matches the API contract**

Inspect `app/static/index.html` for hard-coded SQLite text. If none exists, do not redesign the page. If database status is displayed, label it `PostgreSQL` in Russian and Chinese translations. Preserve Russian as default and the existing language toggle.

- [ ] **Step 5: Prove SQLite runtime removal**

Run:

```powershell
Get-ChildItem app,tests -Recurse -File | Select-String -Pattern "sqlite3|SQLite|DAIBM_DB_PATH|database_path|app\.db|create_all"
Test-Path app\db.py
Get-ChildItem data -Force -ErrorAction SilentlyContinue
```

Expected: search returns no matches; `Test-Path` returns `False`; no SQLite files remain.

- [ ] **Step 6: Commit cleanup and documentation**

```powershell
git add -A app tests README.md docs .gitignore
git commit -m "docs: align MVP with PostgreSQL-only architecture"
```

---

### Task 9: Run the full quality, browser, and Git publication gates

**Files:**

- Verify: all source, migrations, tests, deployment files, and documentation
- Refresh if needed: `output/playwright/overview-ru.png`
- Refresh if needed: `output/playwright/ledger-tamper-ru.png`
- Refresh if needed: `output/playwright/decision-report-ru.pdf`

- [ ] **Step 1: Run the complete automated suite against PostgreSQL**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expected: all tests pass. The report must include configuration, migration, repository, rollback, concurrency, API, ledger, and risk tests. No PostgreSQL integration test may silently skip when Docker is available.

- [ ] **Step 2: Rebuild from an empty PostgreSQL volume**

Run:

```powershell
docker compose down -v
docker compose build --no-cache
docker compose up -d
docker compose ps
curl.exe --fail http://127.0.0.1:8000/api/health
```

Expected: clean database migrates automatically, both services become ready, and the health endpoint returns HTTP 200.

- [ ] **Step 3: Execute the browser acceptance script using the Playwright/webapp-testing skill**

Verify in a real browser:

1. page loads with Russian selected by default;
2. Chinese toggle updates all visible UI labels;
3. seed/reset creates exactly three cases and 12 ledger events;
4. all three decisions appear;
5. risk explanation details open;
6. tamper action changes the ledger indicator to invalid and identifies the broken event;
7. reset restores a valid 12-event chain;
8. decision report export still works;
9. browser console contains no errors.

Capture new Russian overview and tamper screenshots only if visible content changed. Do not commit raw transient `.playwright-cli` state.

- [ ] **Step 4: Run static cleanup and repository hygiene checks**

```powershell
Get-ChildItem app,tests -Recurse -File | Select-String -Pattern "sqlite3|SQLite|DAIBM_DB_PATH|database_path|create_all"
git diff --check
git status --short
git ls-files | Select-String -Pattern "\.db$|\.db-wal$|\.db-shm$|^\.env$"
```

Expected: no obsolete runtime references, no whitespace errors, no tracked database files, and no tracked `.env`.

- [ ] **Step 5: Apply the requesting-code-review skill**

Review specifically for:

- accidental split transactions;
- advisory lock acquired outside the write transaction;
- JSONB canonicalization mismatch;
- timezone-naive timestamps;
- float use for `amount` instead of `Decimal`/`NUMERIC`;
- secrets in logs or responses;
- `create_all()` or SQLite compatibility leftovers;
- Testcontainers fixtures that leak containers/connections;
- API or bilingual UI regression.

Resolve every P0/P1 issue and rerun affected tests. Record lower-priority deferred items only if they are truly outside the approved MVP scope.

- [ ] **Step 6: Apply the verification-before-completion skill**

Rerun, rather than relying on earlier output:

```powershell
.venv\Scripts\python.exe -m pytest -q
docker compose ps
curl.exe --fail http://127.0.0.1:8000/api/health
git diff --check
git status --short
```

Only state “complete” if each command has current passing evidence and the browser acceptance run has no console errors.

- [ ] **Step 7: Final commit and push to the existing private repository**

```powershell
git add -A
git commit -m "feat: complete PostgreSQL-only DAIBM-SCF demo"
git push origin main
gh repo view MengdanXue/daibm-scf-mvp --json visibility,url
```

Expected: push succeeds and GitHub reports `visibility: PRIVATE` for `https://github.com/MengdanXue/daibm-scf-mvp`.

## Definition of Done

- [ ] The only runtime database is PostgreSQL.
- [ ] A clean database reaches Alembic `head` without application-side `create_all()`.
- [ ] Financing request plus four audit events commit or roll back as one unit.
- [ ] Concurrent writes produce one valid, non-forking hash chain.
- [ ] JSONB tampering is detected and demo reset restores the chain.
- [ ] Russian-default/Chinese-toggle UI and report export pass browser acceptance.
- [ ] `start-demo.cmd` starts Docker Compose, waits for real health, and opens the page.
- [ ] All SQLite code, configuration, files, volumes, and documentation claims are removed.
- [ ] Full pytest, Compose smoke, browser console, cleanup search, and diff checks pass.
- [ ] Final commit is pushed to the verified private GitHub repository.

## Primary References

- SQLAlchemy 2.0 PostgreSQL dialect: <https://docs.sqlalchemy.org/en/20/dialects/postgresql.html>
- SQLAlchemy 2.0 session transaction patterns: <https://docs.sqlalchemy.org/en/20/orm/session_basics.html>
- Alembic tutorial and `upgrade head`: <https://alembic.sqlalchemy.org/en/latest/tutorial.html>
- Alembic connection sharing cookbook: <https://alembic.sqlalchemy.org/en/latest/cookbook.html>
- Psycopg 3 installation: <https://www.psycopg.org/install/>
- PostgreSQL transaction-level advisory locks: <https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS>
- Testcontainers Python PostgreSQL module: <https://testcontainers.com/modules/postgresql/>
