# Phase 4 — Enterprise Readiness 报告

范围：在不改变现有架构（FastAPI + PostgreSQL 单体、进程内后台任务）的前提下，为多租户、统一权限、安全、运维、
配置管理与部署补齐企业级能力。未引入 Kubernetes、Kafka、Redis、微服务拆分；未重写数据库、未修改核心风险算法与 Fabric 架构。

实现提交：`e80b766`（分支 `claude/funny-ptolemy-32pkcy`，PR #13）。

---

## 1. 多租户设计

**组织实体**：沿用已有 `organizations`（id、code、name、type、created_at），新增 `status`（`active` / `suspended`）。
用户已有 `organization_id`。停用组织会立即删除其全部会话，并拒绝其用户登录。

**业务对象归属**

| 对象 | 归属方式 |
|---|---|
| 融资 facility | 新列 `financing_facilities.organization_id`（放款机构）。迁移按创建人所属组织回填；`NOT NULL` 并建索引。数据库 `BEFORE INSERT` 触发器在未提供时按创建人补齐；`BEFORE UPDATE` 触发器禁止修改归属 |
| 预警 / 任务 | Phase 3 已有 `organization_id`（来自 facility） |
| 业务结果 outcome | 经 facility 归属（不可变的结果行不做修改） |
| 数据快照 | 快照明细行与决策血缘中的训练结果按 facility 可见性过滤 |
| 模型 | 平台级共享资产（见第 7 节限制） |

**访问规则**：统一由 `PermissionService.scope()` / `organization_filter()` 给出：

| 角色 | 可见范围 |
|---|---|
| admin | 所有组织 |
| financier / risk_manager | 本机构的 facility、预警、任务、结果 |
| supplier / core_enterprise | 以本企业为供应商 / 核心企业的融资 |
| auditor | `audit_grants` 中有效授权的组织（授权只能新增和撤销，不能修改或删除；迁移为既有审计员授予原有全部融资机构的范围） |

**防止通过修改 id 绕过隔离**：详情和命令接口都先加载对象，再做范围检查。超出范围时返回与“不存在”相同的 404，
不泄露对象是否存在。列表、统计与驾驶舱使用同一个 SQL 条件，因此页面数据与 API 一致。

## 2. 权限模型

`app/services/permissions.py` 是唯一的权限来源：

- `PERMISSIONS`：动作 → 角色表，例如 `facility:read`、`alert:assign`、`task:use`、`rule:write`、`model:change`、
  `snapshot:read`、`outcome:read`、`config:write`、`organization:manage`、`security:read`、`ops:read`。
- `require()` / `allowed()`：角色检查。
- `scope()`、`organization_filter()`、`facility_condition()`、`visible_facility_ids()`：组织检查。
- `can_view_facility()` / `require_facility()`：资源归属检查。

facility、alert、task、rule、dashboard、model registry、outcome、outcome governance 与 snapshot 服务都改为调用它，
不再在各接口中各自判断。服务抛出的 `PermissionDenied` 由全局处理器映射为 403。
`GET /api/v1/admin/permissions` 输出当前权限矩阵，运维页面展示该矩阵。

## 3. 安全改造

| 要求 | 实现 |
|---|---|
| 禁止固定密码 / 硬编码 secret | 删除代码、前端与脚本中的 `Demo123!` 和 `daibm_demo_password`。`POSTGRES_PASSWORD` 无默认值：配置加载与 Compose `${POSTGRES_PASSWORD:?}` 都会拒绝启动。演示账号密码来自 `DAIBM_DEMO_PASSWORD`，未设置时不创建演示账号。`init-env.sh` / `.ps1` 生成随机密钥写入被忽略的 `.env`；`start-demo.cmd` 首次启动时自动生成 |
| 登录失败限制 | 连续失败 `DAIBM_LOGIN_MAX_FAILURES`（默认 5）次后锁定 `DAIBM_LOCKOUT_MINUTES`（默认 15）分钟，锁定期间正确密码也会被拒绝（HTTP 423 `account_locked`）。登录页显示锁定提示；管理员启用账号时清除锁定 |
| 会话超时 | 空闲超时（默认 30 分钟，滑动续期）与绝对有效期（默认 12 小时）。过期会话被删除并记录 `SESSION_EXPIRED` |
| 安全 Cookie | `HttpOnly`、`SameSite=Strict`；`DAIBM_COOKIE_SECURE=true` 时附加 `Secure`；`Max-Age` 与绝对有效期一致 |
| 密码策略 | 至少 12 位，同时含大写、小写、数字与符号，不得包含用户名片段。适用于管理员创建用户、改密接口 `POST /api/v1/auth/password`（改密后吊销该用户全部会话），以及 production 环境的演示密码 |
| 安全审计 | 不可变的 `security_events` 表（触发器禁止修改和删除）记录 `LOGIN_SUCCESS`、`LOGIN_FAILURE`、`LOGIN_LOCKED`、`LOGOUT`、`SESSION_EXPIRED`、`PERMISSION_DENIED`（中间件记录所有 403，含用户、路由与 IP）和 `ADMIN_ACTION`。管理员操作同时写入哈希链账本（`ADMIN_ACTION_RECORDED`） |

## 4. 运维能力

- **Health**：`GET /api/v1/ops/health`（无需登录）返回三部分：
  - 应用状态；
  - 数据库：可连接、迁移版本等于代码 head，并给出延迟；
  - 后台任务：校准 worker 与风险监控的心跳。

  状态分三级：`ok`；`degraded`（后台任务停滞）；`down`（数据库不可用，返回 503）。原有的 `/api/health` 保留不变。
- **Metrics**：`GET /api/v1/ops/metrics`（管理员，或持 `DAIBM_METRICS_TOKEN` 的 Bearer 请求）返回：
  - 业务指标：facility、预警、任务按状态计数，以及组织数、用户数、活动会话数；
  - 系统指标：请求数、5xx 错误数、按状态与路由的分布、平均耗时、worker 状态。

  `GET /metrics` 提供 Prometheus 文本格式。指标全部在进程内统计，不依赖外部组件。
- **Backup / Restore**（`app/ops/backup.py`，脚本为 `scripts/ops/backup.sh` 和 `restore.sh`）：
  - 备份：在同一个 REPEATABLE READ 快照内导出全部表；审计数据（账本、安全事件）另存为 JSONL；模型工件打成 tar。`manifest.json` 记录每个文件的 SHA-256、每表行数、迁移版本、账本链头哈希和账本校验结果。
  - 恢复：先逐文件校验哈希（文件被改、多出或缺失都会拒绝），迁移到备份时的版本，在单个事务中载入数据并重置序列，然后恢复工件（安全解包）。
  - 恢复后验证：行数、版本与账本链头哈希必须和 manifest 一致，并且账本哈希链重新校验通过，否则失败。
- **恢复演练**：在 Docker Compose 环境实际执行了一次，结果见第 6 节。

**配置中心（4.5）**：`system_config` 按版本追加，历史不可修改。
- 管理的参数：登录与会话策略、风险检测间隔、结果人工审核、候选模型自动激活。
- 每次修改记录修改人与原因，并写入安全事件和账本（`CONFIG_VERSIONED`）；使用乐观版本控制。
- 回滚会生成新版本（`rollback_of_version`），不改写历史。
- 运行中的服务每 5 秒读取一次生效值，修改无需重启。
- 风险阈值与预警规则仍在 Phase 3 的版本化规则中心维护，新增回滚接口 `POST /api/v1/risk/rules/{key}/rollback`。
- 没有引入任何配置中心产品。

**管理界面**：新增“平台管理”页，含组织与用户、审计授权、安全事件、配置中心、运维监控（健康、指标、权限矩阵）。
admin 可见全部标签；auditor 只能查看安全事件和配置（只读）；其他角色看不到该入口。

## 5. 部署方式

- `docker-compose.yml`：
  - 两个服务都设置 `restart: unless-stopped`；
  - 应用健康检查改为 `/api/v1/ops/health`（带 `start_period`）；
  - 所有安全参数通过环境变量传入；
  - 挂载备份目录 `/backups`。
- `Dockerfile`：新增 `HEALTHCHECK`。
- `.env.example` 列出全部变量，密钥项留空；`scripts/ops/init-env.sh` / `.ps1` 生成随机密钥。
- `DEPLOYMENT_GUIDE.md`：环境要求、配置、启动、数据初始化、备份、恢复、常见运维操作。
- Windows 启动器（`start-demo.cmd`、`start-fabric-demo.cmd`、`reset-defense-demo.cmd`）会自动生成并加载 `.env`，并显示演示密码。

## 6. 测试结果

| 检查 | 结果 |
|---|---|
| `ruff check .` | All checks passed |
| `mypy`（app，91 个文件） | Success: no issues found |
| 全量测试 `pytest tests --ignore=tests/research` | **899 passed, 12 skipped**（Phase 3 结束时为 868 passed） |
| 新增 `tests/test_enterprise_readiness.py` | 31 passed |
| CI | 推送后见 PR #13 |

**要求的 13 类测试与对应用例**

| # | 要求 | 用例 |
|---|---|---|
| 1 | A 组织不能访问 B 组织 facility | `test_organization_a_cannot_read_or_command_organization_b_facility`、`test_every_facility_carries_its_owning_organization_and_the_owner_is_immutable` |
| 2 | A 不能查看 B 的 alert | `test_organization_a_cannot_view_organization_b_alerts_or_task_them` |
| 3 | admin 可以查看全部 | `test_admin_sees_every_organization`、`test_auditor_scope_follows_audit_grants` |
| 4 | API 与页面都有效 | `test_isolation_holds_for_api_and_page_data_with_tampered_ids`（篡改 id 的详情、命令、列表、驾驶舱）。Playwright 实测：supplier 看不到管理入口，admin 各页无前端错误 |
| 5 | 无权限角色被拒绝 | `test_unauthorized_roles_are_rejected_and_every_denial_is_audited`、`test_non_admin_cannot_use_administration_services`、`test_permission_service_is_the_single_role_table` |
| 6 | 拒绝访问写入审计 | 同上（5 次 403 对应 5 条 `PERMISSION_DENIED`，且事件不可修改）；`test_admin_actions_are_audited_in_security_events_and_the_ledger` |
| 7 | 登录失败限制 | `test_login_failure_limit_locks_the_account_until_it_expires`、`test_locked_account_returns_423_over_http` |
| 8 | Session 过期 | `test_sessions_expire_after_idle_timeout_and_absolute_lifetime`、`test_session_cookie_is_http_only_strict_and_secure_when_configured`、`test_suspending_an_organization_ends_its_sessions` |
| 9 | Secret 不来自代码 | `test_no_fixed_passwords_or_secrets_in_application_code`、`test_demo_password_must_satisfy_policy_in_production`、`test_password_policy_and_password_change` |
| 10 | 健康检查 | `test_health_reports_application_database_and_workers`、`test_health_is_503_when_the_database_is_unreachable`、`test_worker_heartbeats_mark_a_stalled_worker_degraded` |
| 11 | 备份执行 | `test_backup_restore_drill_is_hash_verified`、`test_backup_cli_and_scripts_exist` |
| 12 | 恢复验证 | `test_backup_restore_drill_is_hash_verified`（恢复到全新数据库，比对行数、账本链头与工件，恢复后可登录，租户隔离仍然有效）、`test_tampered_backup_is_refused` |
| 13 | Docker 重启恢复 | `test_application_restart_recovers_sessions_data_and_health`，以及下面的 Docker 实测 |

另有：
- 配置中心：`test_config_changes_are_versioned_audited_reversible_and_applied`、`test_config_center_drives_outcome_review_policy`；
- 规则回滚：`test_risk_rule_rollback_writes_a_new_version`；
- 部署契约：`test_deployment_has_healthcheck_restart_policy_and_guide`；
- 迁移：`test_migration_backfills_ownership_on_a_database_that_already_has_facilities`。

**Docker 实测**（重建镜像，在已有 60 笔融资的演示库上执行）：

1. **迁移**：`20260929_0020` 首次在有数据的库上执行时失败，报 `pending trigger events`。原因是回填触发了 facility 的延迟完整性触发器。修复方法：回填后先执行 `SET CONSTRAINTS ALL IMMEDIATE`，让这些检查立即执行（校验不降低）。已增加回归测试，该测试在修复前失败、修复后通过。
2. **恢复演练**：
   - `backup.sh drill-1` 生成 52 个文件，校验通过，账本链头为 `a70777f7…92a4`；
   - 备份后管理员新建组织 `DRILL-ORG`，组织数 4→5，账本链头变为 `30e42a58…4033`；
   - `restore.sh drill-1 --yes` 通过哈希校验并完成恢复后验证：组织数回到 4，`DRILL-ORG` 不存在，账本链头回到 `a70777f7…92a4`，`ledger_valid=true`；
   - 恢复后登录 200，health 为 `ok`。
3. **重启恢复**：
   - 在容器内 SIGKILL uvicorn 后，restart 策略自动重启（`RestartCount 0→1`），容器恢复 healthy；
   - 崩溃前的会话 Cookie 仍然有效（200）；两个 worker 的心跳都恢复为 alive；
   - 重启 PostgreSQL 后 health 恢复 200，业务接口 200。
4. **安全**：
   - 同一账号连续 4 次密码错误返回 401，第 5 次返回 423，之后正确密码也返回 423；
   - supplier 访问管理 API 返回 403；
   - `security_events` 中出现对应的 `LOGIN_FAILURE`、`LOGIN_LOCKED`、`PERMISSION_DENIED` 记录。

## 7. 当前限制

1. **模型与快照是平台共享资产**：校准模型、数据集快照的计数与哈希描述整个训练池，不按租户拆分。
   按租户过滤的是快照明细行与血缘中的业务结果。按租户训练模型需要修改训练与作用域设计，超出本阶段范围。
2. **申请阶段的可见性保持原样**：融资申请（workflow applications）仍按阶段对银行角色可见。
   按组织隔离从 facility 开始（放款后）。报价与竞争式申请分配不在本阶段范围。
3. **首个生产管理员的创建**：没有独立的初始化命令，需要临时设置演示密码后登录 `admin.demo` 创建，
   或由数据库管理员插入（`DEPLOYMENT_GUIDE.md` 已说明步骤）。
4. **进程内状态**：
   - 请求指标和心跳保存在进程内，重启后清零；多副本部署时需要按实例抓取。
   - 配置在进程内缓存 5 秒，修改最多延迟 5 秒生效。
   - 登录锁定按账号计数，不按 IP 限流；限流应在反向代理层配置。
5. **备份方式**：逻辑备份适用于当前数据量。数据量达到 GB 级以上时应改用 `pg_dump`/`pg_basebackup` 与 WAL 归档，
   本阶段按要求未引入新基础设施。恢复会替换全部数据，需要显式加 `--yes`。
6. **TLS**：应用本身不终止 TLS；`Secure` Cookie 需要 HTTPS 反向代理，并设置 `DAIBM_COOKIE_SECURE=true`。

## 8. 提交

| 提交 | 内容 |
|---|---|
| `e80b766` | Phase 4 实现：迁移、权限服务、安全、运维、配置中心、部署文件、管理界面、测试 |
| 本报告所在提交 | `PHASE4_ENTERPRISE_READINESS_REPORT.md` |

分支：`claude/funny-ptolemy-32pkcy`（PR #13；该 PR 叠加了 Phase 2.2、Phase 3 与 Phase 4）。
