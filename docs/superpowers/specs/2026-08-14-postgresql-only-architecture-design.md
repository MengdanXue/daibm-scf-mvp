# PostgreSQL-only 后端架构设计

日期：2026-08-14  
状态：等待书面确认  
适用项目：DAIBM-SCF Demo MVP

## 1. 背景与已确认约束

项目仍处于开发阶段，不存在需要保留或迁移的真实数据。现有 SQLite 数据全部为可重新生成的合成演示数据。

本次改造采用以下硬性约束：

- PostgreSQL 是唯一数据库；
- 删除 SQLite 运行模式和兼容代码；
- 不提供数据库类型切换；
- 不迁移现有 SQLite 数据；
- 使用 SQLAlchemy 统一数据访问；
- 使用 Alembic 管理数据库结构；
- 保留现有俄中双语页面、API 功能和演示闭环；
- 删除改造后失去用途的文件、配置、依赖和文档说明。

## 2. 目标

1. 将当前直接使用 `sqlite3` 的实现重构为 PostgreSQL-only 数据层。
2. 让融资申请和四条审计事件在同一个数据库事务中提交，避免部分写入。
3. 使用明确的应用层、仓储层和基础设施层边界，提高架构可解释性与可测试性。
4. 使用 PostgreSQL 原生数据类型、约束和并发控制加强账本实现。
5. 保持答辩演示的一键启动能力，并提供清楚的数据库健康状态。
6. 使用真实 PostgreSQL 执行后端集成测试，不再以 SQLite 代替 PostgreSQL。

## 3. 非目标

- 不迁移任何现有 SQLite 数据；
- 不实现多数据库或运行时数据库切换；
- 不增加用户系统、权限系统或真实银行接口；
- 不在本次改造中引入 NetworkX、机器学习训练、SHAP 或新的科研算法；
- 不把哈希链夸大为生产级区块链；
- 不拆分微服务。

## 4. 方案选择

采用同步 SQLAlchemy 2.x + Psycopg 3。

没有采用异步 SQLAlchemy，原因是当前 FastAPI 路由和业务服务均为同步实现，演示负载很小；异步改造会扩大事务、测试和错误处理范围，却不会提高答辩证据强度。

没有采用原始 Psycopg SQL，原因是项目需要清晰的数据模型、统一事务和 Alembic 迁移。SQLAlchemy 能在不牺牲 PostgreSQL 能力的前提下提供这些边界。

## 5. 分层结构

```text
app/api            FastAPI 路由、请求解析、HTTP 错误映射
       ↓
app/service.py     融资评估与演示用例编排
       ↓
app/repositories   融资申请和账本数据访问接口及 SQLAlchemy 实现
       ↓
app/models.py      SQLAlchemy PostgreSQL 数据模型
       ↓
app/database.py    Engine、Session、事务和健康检查
       ↓
PostgreSQL
```

本次保持目录浅层，不引入多层 DDD 目录。领域规模只有“融资申请”和“审计事件”两个核心概念，过度拆分会降低 MVP 可读性。

依赖方向：API 可以依赖服务；服务可以依赖仓储接口；仓储实现依赖 SQLAlchemy；领域风险计算不依赖 FastAPI、SQLAlchemy 或 PostgreSQL。

## 6. PostgreSQL 数据模型

### 6.1 `financing_requests`

| 字段 | PostgreSQL 类型 | 约束 |
|---|---|---|
| `request_id` | `UUID` | 主键，由应用生成 |
| `created_at` | `TIMESTAMPTZ` | 非空 |
| `applicant_id` | `TEXT` | 非空 |
| `amount` | `NUMERIC(14,2)` | 非空，`amount > 0` |
| `term_days` | `INTEGER` | 非空，7–365 |
| `features` | `JSONB` | 非空 |
| `risk_score` | `DOUBLE PRECISION` | 非空，0–1 |
| `decision` | `TEXT` | 非空，限制为三种决策 |
| `explanations` | `JSONB` | 非空 |
| `control_action` | `TEXT` | 非空 |

索引：`created_at DESC`、`decision`。`applicant_id` 暂不唯一，因为同一企业可以提交多次申请。

### 6.2 `ledger_events`

| 字段 | PostgreSQL 类型 | 约束 |
|---|---|---|
| `id` | `BIGINT GENERATED ALWAYS AS IDENTITY` | 主键 |
| `created_at` | `TIMESTAMPTZ` | 非空 |
| `event_type` | `TEXT` | 非空，限制为四种事件 |
| `entity_id` | `UUID` | 非空，外键指向融资申请 |
| `payload` | `JSONB` | 非空 |
| `previous_hash` | `TEXT` | 非空 |
| `event_hash` | `TEXT` | 非空、唯一、长度 64 检查 |

索引：`(entity_id, id)`。删除融资申请前必须先删除账本事件；正常业务不提供删除接口。

## 7. 事务与哈希链并发控制

创建融资申请时执行一个原子事务：

1. 计算风险与决策；
2. 写入融资申请；
3. 对哈希链头部执行 PostgreSQL 事务级 advisory lock；
4. 依次生成并写入四条账本事件；
5. 全部成功后一次提交；任一步失败则全部回滚。

advisory lock 用固定的应用锁键串行化账本追加操作，避免两个并发请求同时读取相同链头并产生分叉。读取和验证账本不需要该锁。

哈希材料继续使用规范化 JSON、事件时间、事件类型、实体 ID 和前一哈希。JSONB 从数据库读出后必须重新规范化，不能依赖 PostgreSQL 的 JSON 文本顺序。

## 8. 演示重置与篡改实验

`POST /api/demo/reset` 在一个事务中清空事件和申请，并重置身份序列，然后重新生成三组案例。该接口只处理合成演示数据。

`POST /api/demo/tamper` 继续故意修改一条风险评估事件的 JSONB 载荷而不重算哈希，用于现场展示篡改检测。恢复操作通过重置演示环境完成。

## 9. 配置

不提供数据库类型开关，也不使用 `DAIBM_DB_PATH`。

应用从以下环境变量读取唯一 PostgreSQL 连接配置：

- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`

连接 DSN 由应用内部构造，不在页面展示。Docker Compose 为本地开发提供演示默认值；真实部署必须覆盖密码。

## 10. 容器与启动流程

Docker Compose 包含两个服务：

- `postgres`：固定 PostgreSQL 主版本，配置持久卷和 `pg_isready` 健康检查；
- `mvp`：等待数据库健康，运行 `alembic upgrade head`，然后启动 Uvicorn。

`start-demo.cmd` 的新流程：

1. 检查 `docker` 和 Docker daemon；
2. 执行 `docker compose up --build -d`；
3. 轮询 `/api/health`，不使用固定睡眠作为成功判断；
4. 健康后打开浏览器；
5. 失败时输出可操作的俄中双语错误提示。

## 11. API 行为

现有 URL 和成功响应结构保持兼容。健康检查增加数据库字段：

```json
{
  "status": "ok",
  "database": {"backend": "postgresql", "reachable": true},
  "ledger": {"valid": true, "event_count": 12}
}
```

数据库不可用时返回 HTTP 503 和统一错误对象，不把密码、主机内部信息或 SQL 文本暴露给客户端。

## 12. Alembic 策略

- 创建一个初始迁移，建立两张表、约束和索引；
- 不创建 SQLite 到 PostgreSQL 的数据迁移；
- 应用启动不调用 `Base.metadata.create_all()`；
- 所有结构变化必须通过 Alembic；
- 测试在应用启动前对临时 PostgreSQL 执行 `alembic upgrade head`。

## 13. 测试策略

### 单元测试

- 风险评分排序和边界；
- 规范化 JSON 与哈希计算；
- 业务服务在仓储失败时回滚的行为；
- 配置缺失和非法值的错误处理。

### PostgreSQL 集成测试

使用 Testcontainers 启动真实 PostgreSQL：

- Alembic 从空库升级成功；
- 创建申请后产生一笔申请和四条事件；
- 三类演示决策完整；
- 哈希链验证通过；
- 篡改 JSONB 后定位异常事件；
- 重置后身份序列回到 1–12 且链恢复；
- 并发创建申请不会产生两个相同 `previous_hash` 的链分支；
- 事务中途失败不会留下部分申请或部分账本事件。

### 浏览器验收

使用现有 Web 测试技能验证俄语默认、中文切换、一键演示、风险解释、篡改、恢复和健康状态。浏览器控制台必须无错误。

## 14. 删除清单

实现完成后删除或替换以下内容：

- 删除旧 `app/db.py` SQLite 连接与建表实现；
- 删除 `database_path()` 和 `DAIBM_DB_PATH`；
- 删除 `sqlite3`、SQLite 占位符和 `sqlite_sequence` 相关逻辑；
- 删除 Compose 中的 SQLite `/app/data` 挂载和 `daibm-data` 卷；
- 删除 `.gitignore` 中仅用于 SQLite 数据库文件的规则；
- 删除 README、设计文档和演示说明中的 SQLite 运行描述；
- 删除任何改造后未被导入的兼容模块、函数和依赖；
- 不保留双数据库适配器或 SQLite 测试后门。

## 15. 验收标准

1. 项目源码中不存在运行时 SQLite 数据层或 SQLite 模式。
2. 空 PostgreSQL 可通过 Alembic 初始化。
3. Docker Compose 一条命令可启动数据库和应用。
4. 页面功能、三类决策、哈希验证和篡改实验保持可用。
5. 申请与四条事件具有原子性。
6. PostgreSQL 并发测试证明哈希链不会分叉。
7. 所有单元、集成和浏览器测试通过。
8. 无未使用兼容代码、无提交的数据库密码、无真实数据。
9. README 和双语演示讲稿与 PostgreSQL-only 架构一致。

