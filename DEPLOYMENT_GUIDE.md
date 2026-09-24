# DAIBM-SCF 部署指南 / Deployment Guide

本指南说明如何以单机 Docker Compose 方式部署 DAIBM-SCF（FastAPI 应用 + PostgreSQL 17），
以及日常运维中的配置、初始化、健康检查、备份与恢复。架构保持不变：一个应用容器、一个数据库容器，
没有消息队列、没有额外的配置中心或缓存集群。

---

## 1. 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Linux x86_64（推荐 Ubuntu 22.04+），或 Windows 10/11 + Docker Desktop（演示环境） |
| Docker | Docker Engine 24+，Docker Compose v2（`docker compose version`） |
| 资源 | 2 vCPU、4 GB 内存、10 GB 磁盘（另按业务量预留备份空间） |
| 网络 | 应用只监听 `127.0.0.1:${MVP_PORT}`；对外发布请经由 HTTPS 反向代理（Nginx/Caddy） |
| 备份脚本 | `sh`、`python3`（仅 `init-env.sh` 生成随机密钥时需要） |

可选组件（默认关闭，按需启用）：ZKP prover（`ZKP_PROOF_REQUIRED`）、Hyperledger Fabric 锚定
（`start-fabric-demo.cmd`）。它们与本阶段的部署方式无关。

## 2. 配置

所有配置来自环境变量（或项目根目录下的 `.env`，已被 `.gitignore` 忽略）。**代码中不包含任何固定密码或密钥**：
`POSTGRES_PASSWORD` 缺失时应用与 Compose 都会拒绝启动。

生成配置：

```bash
scripts/ops/init-env.sh          # Linux/macOS：从 .env.example 生成 .env，随机数据库密码、演示密码、metrics token
# Windows：powershell -File scripts\ops\init-env.ps1（start-demo.cmd 首次启动时会自动调用）
```

| 变量 | 说明 | 默认 |
|---|---|---|
| `POSTGRES_PASSWORD` | 数据库密码（**必填**，无默认值） | — |
| `POSTGRES_DB` / `POSTGRES_USER` | 数据库名 / 用户 | `daibm_scf` / `daibm` |
| `DAIBM_DEMO_PASSWORD` | `*.demo` 演示账号的密码；**生产环境留空**，此时不创建任何演示账号 | 空 |
| `DAIBM_ENV` | `production` 时演示密码也必须满足密码策略 | `demo` |
| `DAIBM_COOKIE_SECURE` | 经 HTTPS 访问时设为 `true`，会话 Cookie 带 `Secure` | `false` |
| `DAIBM_COOKIE_SAMESITE` | 会话 Cookie 的 SameSite | `strict` |
| `DAIBM_LOGIN_MAX_FAILURES` | 连续登录失败多少次后锁定 | `5` |
| `DAIBM_LOCKOUT_MINUTES` | 锁定时长 | `15` |
| `DAIBM_SESSION_IDLE_MINUTES` | 会话空闲超时 | `30` |
| `DAIBM_SESSION_ABSOLUTE_HOURS` | 会话最长有效期 | `12` |
| `DAIBM_METRICS_TOKEN` | 可选：Prometheus 以 `Authorization: Bearer <token>` 抓取 `/metrics` | 空 |
| `RISK_MONITOR_INTERVAL_SECONDS` | 风险规则周期检测间隔 | `60` |
| `OUTCOME_MANUAL_REVIEW` / `CALIBRATION_AUTO_PROMOTION` | 结果人工审核 / 候选模型自动激活 | `false` / `true` |
| `MVP_PORT` | 本机发布端口 | `8010` |
| `DAIBM_BACKUP_DIR` | 备份目录（挂载到容器 `/backups`） | `./backups` |

**运行期配置（配置中心）**：登录/会话策略、风险检测间隔、人工审核与自动激活开关可以在
“平台管理 → 配置中心”中修改（管理员）。每次修改生成新的不可变版本并记录修改人、原因，
同时写入安全事件与审计账本；“回滚到此版本”会生成一个新版本（`rollback_of_version`），历史不被改写。
环境变量只作为尚未在配置中心设置时的默认值。风险阈值与预警规则在“风险规则中心”中以同样的版本化方式维护，
并支持 `POST /api/v1/risk/rules/{rule_key}/rollback`。

密码策略：至少 12 位，同时包含大写、小写、数字与符号，且不得包含用户名片段。

## 3. 启动

```bash
scripts/ops/init-env.sh                      # 仅首次
docker compose -p daibm-scf-mvp up -d --build
docker compose -p daibm-scf-mvp ps           # 两个服务都应为 healthy
curl -s http://127.0.0.1:8010/api/v1/ops/health
```

- 应用容器启动时先执行 `alembic upgrade head`，再启动 uvicorn；数据库迁移是幂等的。
- 两个服务都配置 `restart: unless-stopped`：进程崩溃或宿主机重启后自动拉起。会话存储在数据库中，
  重启不会使已登录用户掉线；后台校准任务使用租约，重启后未完成的任务会被重新领取。
- 健康检查：Compose 与 Dockerfile 的 `HEALTHCHECK` 都调用 `GET /api/v1/ops/health`，
  它检查应用、数据库（可连接且迁移版本等于代码的 head）以及后台任务心跳（校准 worker、风险监控）；
  数据库不可用时返回 503（`down`），后台任务停滞时返回 `degraded`。
- 指标：`GET /api/v1/ops/metrics`（管理员会话）返回业务指标（融资/预警/任务按状态计数、组织、用户、活动会话）
  与系统指标（请求数、5xx 错误数、平均耗时、worker 状态）；`GET /metrics` 提供 Prometheus 文本格式。
- Windows 演示：双击 `start-demo.cmd`，它会生成 `.env`、启动 Compose、等待健康检查并打印演示密码。

生产部署建议：在反向代理终止 TLS，并设置 `DAIBM_COOKIE_SECURE=true`、`DAIBM_ENV=production`、
`DAIBM_DEMO_PASSWORD` 留空。

## 4. 数据初始化

1. **表结构**：启动时自动迁移（`alembic upgrade head`）。
2. **默认风险规则**：应用启动时幂等写入（已有版本不会被覆盖）。
3. **组织与账号**：
   - 演示环境：设置 `DAIBM_DEMO_PASSWORD` 后，启动时幂等创建 5 个业务角色 + `admin.demo` 管理员，
     以及它们所属的组织；演示审计员自动获得演示融资机构的审计授权。
   - 生产环境：`DAIBM_DEMO_PASSWORD` 留空时不会创建任何账号。首个管理员可临时设置演示密码启动一次，
     通过 `admin.demo` 登录后在“平台管理 → 组织与用户”中创建正式组织与用户，
     然后禁用 `admin.demo`（或者以数据库管理员身份插入首个管理员），再移除 `DAIBM_DEMO_PASSWORD`。
4. **多租户**：每个组织只能看到自己的数据——融资、预警、任务、业务结果、风险驾驶舱和数据快照明细都按组织过滤；
   企业用户（供应商/核心企业）只能看到涉及本企业的融资；审计员只能看到被授予审计权限（`audit_grants`）的组织；
   管理员可以看到所有组织。

## 5. 备份

备份内容：数据库全部表（同一 REPEATABLE READ 快照内逐表 `COPY`）、审计数据（哈希链账本与安全事件，
另存为 JSONL 便于独立审阅）、校准模型工件目录。`manifest.json` 记录每个文件的 SHA-256 与大小、
每张表的行数、迁移版本、账本链头哈希以及账本校验结果。

```bash
scripts/ops/backup.sh                     # 生成 ./backups/daibm-<UTC 时间戳>/ 并立即校验
scripts/ops/backup.sh before-upgrade      # 指定名称
```

等价的容器内命令：

```bash
docker compose -p daibm-scf-mvp exec -T mvp python -m app.ops.backup create \
    --out /backups/<name> --artifacts /app/artifacts/candidates/calibration
docker compose -p daibm-scf-mvp exec -T mvp python -m app.ops.backup verify /backups/<name>
```

建议：每日定时（cron）执行 `backup.sh`，并将 `./backups` 同步到异地存储；备份目录包含业务数据，需要按敏感数据保管。

## 6. 恢复

```bash
scripts/ops/restore.sh <name> --yes
```

恢复流程（`python -m app.ops.backup restore`）：

1. 校验备份目录：每个文件的 SHA-256 必须与 manifest 一致，不能多文件也不能少文件——被篡改的备份直接拒绝；
2. 把目标数据库迁移到备份时的 schema 版本；
3. 在一个事务中清空并载入所有表（期间暂停触发器，以便原样恢复不可变历史），然后重置自增序列；
4. 恢复模型工件目录（拒绝绝对路径、越界链接等不安全的归档成员）；
5. **验证**：恢复后的每张表行数、迁移版本、账本链头哈希必须与 manifest 完全一致，并且账本哈希链重新校验通过，
   否则命令失败（返回非零）；
6. `restore.sh` 最后重启应用容器，丢弃进程内缓存。

恢复演练（Phase 4 已执行，见 `PHASE4_ENTERPRISE_READINESS_REPORT.md`）：在运行中的 Compose 环境生成备份 →
写入新的业务数据 → 恢复备份 → 验证新数据消失、行数与账本链头哈希与备份一致、演示账号可登录、健康检查为 `ok`。

## 7. 常见运维操作

| 操作 | 命令 |
|---|---|
| 查看日志 | `docker compose -p daibm-scf-mvp logs -f mvp` |
| 重启应用 | `docker compose -p daibm-scf-mvp restart mvp` |
| 升级版本 | `scripts/ops/backup.sh before-upgrade && git pull && docker compose -p daibm-scf-mvp up -d --build` |
| 解锁账号 | 等待锁定时间结束，或管理员在“组织与用户”中停用后再启用该账号 |
| 查看安全事件 | “平台管理 → 安全事件”（登录成功/失败、锁定、会话过期、越权访问、管理员操作） |
| 停止 | `docker compose -p daibm-scf-mvp down`（**不要**加 `-v`，否则会删除数据卷） |
