# DAIBM-SCF 多角色供应链金融工作流设计

## 1. 目标

把现有单用户科研演示器升级为可实际演示登录、退出、分角色待办和完整业务流转的硕士论文 MVP，同时保留已经冻结的 Research Core v0.4、PostgreSQL 审计哈希链和俄语/中文界面。

系统必须能够由五个不同角色依次完成同一笔融资申请：

```text
供应商创建并提交
  → 核心企业确认贸易背景
  → 金融机构评估并决策
  → 风险管理员执行控制
  → 审计员核验并关闭
```

这条链路必须由真实后端权限、PostgreSQL 状态和审计事件驱动，不能只是前端角色切换。

## 2. 范围边界

### 2.1 本版本包含

- 用户登录、会话恢复、退出和会话过期；
- 五个预置答辩账号及其组织和角色；
- 角色级 API 权限和数据范围权限；
- 供应链融资申请草稿、提交、确认、评估、决策、控制和审计；
- 各角色首页、待办列表、申请详情、时间线和操作面板；
- 每次状态变化的操作者、意见、载荷、时间和哈希链记录；
- 乐观并发控制，防止两个页面覆盖同一状态；
- 俄语默认和完整中文切换；
- 现有 Research Core 和审计完整性验证入口；
- PostgreSQL/Alembic 迁移、自动化测试和真实浏览器验收。

### 2.2 本版本不包含

- 公开注册、找回密码、邮件或短信验证；
- 管理员用户管理界面；
- OAuth、企业 SSO 或外部身份提供商；
- 真实银行、ERP、税票平台或区块链网络集成；
- Redis、消息队列、微服务、Kubernetes；
- 多租户计费或生产级反欺诈安全运营。

角色由系统预置，因为金融业务角色不能通过公开注册自行声明。

## 3. 方案选择

### 3.1 采用方案：FastAPI 模块化单体

在现有 FastAPI/PostgreSQL 应用内增加 Identity 和 Financing Workflow 两个边界明确的模块。HTTP 控制器只负责解析身份和请求；工作流服务负责权限、状态机和事务；Repository 负责 SQLAlchemy 持久化；现有 Research Core 保持独立。

该方案能够真实证明多角色协作和权限控制，又不会为了答辩展示引入微服务部署复杂度。

### 3.2 放弃方案

- 纯前端角色切换：无法证明权限和状态由后端执行，不满足“完整系统”的要求；
- 多微服务拆分：增加部署、网络和一致性成本，对当前硕士论文证据没有成比例收益。

## 4. 角色与数据范围

| 角色代码 | 中文角色 | 数据范围 | 可执行操作 |
|---|---|---|---|
| `supplier` | 供应商 | 本供应商组织创建的申请 | 创建、修改草稿/退回件、提交、查看进度 |
| `core_enterprise` | 核心企业 | 指派给本核心企业的申请 | 确认贸易背景或退回补充材料 |
| `financier` | 金融机构审查员 | 已由核心企业确认的申请 | 执行风险评估、查看解释、作出融资决策 |
| `risk_manager` | 风险管理员 | 已作出融资决策的申请 | 确认并执行控制措施 |
| `auditor` | 审计员 | 全部申请和审计事件，只读业务字段 | 验证证据链、提交审计结论 |

除审计员的全局只读权限外，组织边界在 Repository 查询中执行。前端隐藏按钮不能替代后端授权。

## 5. 预置账号

本地答辩环境幂等创建以下账号，统一初始密码为 `Demo123!`：

| 用户名 | 角色 | 组织 |
|---|---|---|
| `supplier.demo` | `supplier` | `SUPPLIER-001` |
| `core.demo` | `core_enterprise` | `CORE-001` |
| `financier.demo` | `financier` | `BANK-001` |
| `risk.demo` | `risk_manager` | `BANK-001` |
| `auditor.demo` | `auditor` | `AUDIT-001` |

页面允许一键填入账号，但登录仍调用真实认证 API。密码只以带盐 `scrypt` 哈希保存在 PostgreSQL，不把明文密码写入数据库。

## 6. 认证与会话

- 登录成功生成 256 位随机会话令牌；
- 浏览器只接收 `HttpOnly`、`SameSite=Strict` 的 `daibm_session` Cookie；
- PostgreSQL 只保存令牌的 SHA-256 摘要；
- 会话有效期 12 小时；
- 每次受保护请求检查用户启用状态和过期时间；
- 退出时删除服务端会话并清除 Cookie；
- 认证失败统一返回 `401 invalid_credentials`，不泄露用户名是否存在；
- 已登录但角色不符返回 `403 forbidden_role`；
- 所有修改请求保持同源，不开放跨域；本地 HTTP 下 Cookie 不设置 `Secure`，生产配置可开启。

## 7. 业务状态机

### 7.1 状态

```text
draft
submitted
trade_returned
trade_confirmed
risk_assessed
approved | manual_review | rejected
controlled
audited
```

### 7.2 合法转换

| 当前状态 | 操作 | 执行角色 | 目标状态 |
|---|---|---|---|
| 无 | 创建草稿 | `supplier` | `draft` |
| `draft` / `trade_returned` | 修改 | 原供应商 | 状态不变，版本加一 |
| `draft` / `trade_returned` | 提交 | 原供应商 | `submitted` |
| `submitted` | 确认贸易 | 指定 `core_enterprise` | `trade_confirmed` |
| `submitted` | 退回材料 | 指定 `core_enterprise` | `trade_returned` |
| `trade_confirmed` | 执行风险评估 | `financier` | `risk_assessed` |
| `risk_assessed` | 作出决定 | `financier` | `approved` / `manual_review` / `rejected` |
| 三种决定状态 | 执行控制措施 | `risk_manager` | `controlled` |
| `controlled` | 审计核验 | `auditor` | `audited` |

任何非法转换返回 `409 invalid_transition`。每个修改请求必须提交当前 `version`；版本过期返回 `409 stale_application`。

## 8. 业务数据

### 8.1 新表

#### `organizations`

- `organization_id UUID PK`
- `organization_code TEXT UNIQUE`
- `name TEXT`
- `organization_type TEXT CHECK supplier/core_enterprise/financier/auditor`
- `created_at TIMESTAMPTZ`

#### `users`

- `user_id UUID PK`
- `username TEXT UNIQUE`
- `display_name TEXT`
- `password_hash TEXT`
- `password_salt TEXT`
- `role TEXT CHECK supplier/core_enterprise/financier/risk_manager/auditor`
- `organization_id UUID FK`
- `is_active BOOLEAN`
- `created_at TIMESTAMPTZ`

#### `user_sessions`

- `session_id UUID PK`
- `user_id UUID FK`
- `token_hash CHAR(64) UNIQUE`
- `created_at TIMESTAMPTZ`
- `expires_at TIMESTAMPTZ`

#### `workflow_actions`

- `action_id BIGINT IDENTITY PK`
- `request_id UUID FK`
- `actor_user_id UUID FK`
- `actor_role TEXT`
- `action_type TEXT`
- `from_status TEXT NULL`
- `to_status TEXT`
- `comment TEXT NULL`
- `payload JSONB`
- `created_at TIMESTAMPTZ`

### 8.2 扩展 `financing_requests`

- `status TEXT NOT NULL DEFAULT 'audited'`，旧数据回填为 `audited`；
- `version INTEGER NOT NULL DEFAULT 1`；
- `created_by_user_id UUID NULL FK`；
- `supplier_organization_id UUID NULL FK`；
- `core_enterprise_organization_id UUID NULL FK`；
- `contract_number TEXT NULL`；
- `invoice_number TEXT NULL`；
- `updated_at TIMESTAMPTZ`；
- `risk_score`、`decision`、`control_action` 和 `explanations` 对新草稿允许为空；旧记录保持原值。

索引覆盖用户名、会话摘要与过期时间、申请状态、供应商组织、核心企业组织、更新时间和工作流时间线。

## 9. 工作流与审计原子性

每个状态转换在一个 PostgreSQL 事务中完成四件事：

1. 校验用户角色、组织范围、当前状态和 `version`；
2. 更新申请及版本；
3. 写入 `workflow_actions`；
4. 向现有哈希链写入对应审计事件。

新增审计事件类型：

- `APPLICATION_DRAFT_CREATED`
- `APPLICATION_UPDATED`
- `APPLICATION_SUBMITTED`
- `TRADE_CONFIRMED`
- `TRADE_RETURNED`
- `RISK_ASSESSMENT`
- `FINANCING_DECISION`
- `CONTROL_ACTION`
- `AUDIT_REVIEW_COMPLETED`

事件载荷必须包含 `actor_user_id`、`actor_role`、`from_status`、`to_status`、`application_version` 和业务摘要。密码、会话令牌及其摘要不得进入审计事件。

## 10. 风险与科研证据边界

- 业务申请在金融机构步骤调用现有透明基线评估，生成可解释因素贡献；
- 研究页面继续执行真实 TGNN/ONNX 推理并展示数据、图、模型、策略和审计证据；
- 两者在页面上明确区分为“业务评分”和“科研模型证据”，不虚构同一申请已由 TGNN 直接评估；
- 金融机构和审计员可以访问 Research Core；其他角色只看到业务风险结果；
- Research Core 数据库模型和既有哈希保持不变。

## 11. REST API

所有新业务接口使用 `/api/v1`，错误统一返回：

```json
{
  "detail": {
    "code": "machine_readable_code",
    "message": "Human readable message"
  }
}
```

### 11.1 认证

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `GET /api/v1/auth/demo-accounts`，只返回用户名、角色和显示名，不返回密码哈希

### 11.2 工作台

- `GET /api/v1/tasks`：按当前角色和组织返回待办及数量；
- `GET /api/v1/dashboard`：返回角色相关统计；
- 列表使用 `limit` 与 `offset`，默认 `limit=50`，最大 200。

### 11.3 申请

- `POST /api/v1/applications`：供应商创建草稿；
- `GET /api/v1/applications`：按权限列出；
- `GET /api/v1/applications/{id}`：返回详情、允许动作和时间线；
- `PATCH /api/v1/applications/{id}`：供应商修改草稿或退回件；
- `POST /api/v1/applications/{id}/submit`；
- `POST /api/v1/applications/{id}/trade-confirmation`，载荷含 `confirmed`、`comment`、`version`；
- `POST /api/v1/applications/{id}/risk-assessment`，载荷含 `version`；
- `POST /api/v1/applications/{id}/decision`，载荷含 `decision`、`comment`、`version`；
- `POST /api/v1/applications/{id}/control-action`，载荷含 `comment`、`version`；
- `POST /api/v1/applications/{id}/audit-review`，载荷含 `comment`、`version`。

状态转换接口不使用通用状态 PATCH，避免客户端绕过状态机。

### 11.4 兼容性

- 健康检查保持公开；现有 `/api/research/*` 仅允许 `financier` 和 `auditor`，审计查看与验证仅允许 `auditor`；
- 原 `/api/requests` 标记为演示兼容接口并限制为 `financier`/`auditor`，`/api/demo/*` 限制为 `auditor`，不允许通过旧接口绕过登录或读取越权业务数据；
- 新页面的所有创建和流转均使用受保护的 `/api/v1` 接口。

## 12. 页面设计

### 12.1 未登录

- 全屏登录页，俄语为默认语言；
- 用户名、密码、登录按钮和五个答辩账号快捷卡；
- 中文切换；
- 错误信息不泄露账户存在性。

### 12.2 已登录框架

- 顶部显示姓名、组织、角色、语言和退出按钮；
- 左侧导航随角色变化；
- 首页首先显示“我的待办”，而不是科研 KPI；
- 现有总览、Research Core、账本和方法页作为角色允许的辅助页面保留。

### 12.3 申请工作区

- 供应商看到创建按钮和自己的申请；
- 其他角色看到其状态对应的待办；
- 详情页固定展示申请摘要、当前状态、版本和完整时间线；
- 右侧操作面板只渲染后端返回的 `allowed_actions`；
- 操作成功后刷新待办、状态和时间线；
- 俄语和中文必须覆盖登录、角色、状态、操作、错误与空状态。

页面沿用现有深蓝、蓝绿和科研证据暗色视觉系统。登录页和角色待办是新增视觉主角，五阶段轨道升级为随真实状态变化的八阶段业务时间线。

## 13. 演示数据

- 应用启动时幂等创建四个组织和五个账号；
- Alembic 迁移把现有完成案例回填为 `audited`，不删除 Research Core 数据；
- `/api/demo/reset` 继续重建三组对比案例，并将其标为已完成演示记录；
- 从供应商账号创建的新申请必须能够由五个账号连续完成，不依赖重置接口。

## 14. 错误处理

- `401 invalid_credentials` / `authentication_required`；
- `403 forbidden_role` / `forbidden_scope`；
- `404 application_not_found`，越权对象也返回 404，避免泄露；
- `409 invalid_transition` / `stale_application`；
- `422` 字段校验错误；
- `503 database_unavailable` / `research_model_unavailable`；
- 事务失败时申请、动作记录和哈希事件必须一起回滚。

## 15. 测试与验收

### 15.1 自动化测试

- 密码哈希、会话创建、过期、退出和禁用用户；
- 每个接口的未登录、错误角色、错误组织和允许角色；
- 状态机所有合法转换和代表性非法转换；
- 版本冲突；
- 每个转换的原子工作流动作与审计事件；
- 旧完成案例迁移与 Research Core 回归；
- 俄语/中文 UI 契约；
- 全量既有测试继续通过。

### 15.2 浏览器验收

使用五个独立浏览器会话完成：

1. 供应商登录、创建、修改、提交并退出；
2. 核心企业登录、确认贸易并退出；
3. 金融机构登录、执行风险评估、查看解释、决定并退出；
4. 风险管理员登录、执行控制并退出；
5. 审计员登录、验证账本、完成审计并退出；
6. 最终申请状态为 `audited`，时间线操作者与角色完整；
7. PostgreSQL 可达、账本有效、Research Core ready；
8. 俄语默认、中文切换有效、桌面与移动端无溢出；
9. 浏览器控制台 0 error、0 warning。

## 16. 完成标准

- 未登录用户无法访问任何新业务数据；
- 前端隐藏按钮和直接调用 API 都不能越权；
- 同一申请可以由五个账号完整流转；
- 每个状态变化均可在工作流时间线和哈希账本中追溯；
- 现有研究证据链未被削弱或虚假绑定；
- 数据库迁移、全量测试、Docker 构建和浏览器验收全部通过；
- 代码、规格、演示脚本和账号说明推送至私有 GitHub `main`。
