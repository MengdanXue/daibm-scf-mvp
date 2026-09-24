# Phase 3 报告：风险运营平台（Risk Operations Platform）

目标：把系统从“AI 风险决策平台”提升为“可运营的供应链金融风险管理平台原型”。本阶段在已有的设施生命周期、风险决策记录、模型注册表、结果治理和审计历史上增量开发。

本阶段没有：

- 引入新的基础设施：无消息队列、无 K8s、无新依赖。结果文件以 base64 JSON 上传，没有新增 multipart 依赖。
- 新增或修改 AI 模型，也没有改动 TGNN。
- 重构数据库或实现真实银行接口。
- 修改任何历史数据。

## 1. 产品功能

### 1.1 风险驾驶舱（`GET /api/v1/risk/dashboard`）

全部指标由已记录的业务数据实时计算，不含任何模拟统计。统计范围按角色划分：管理员与审计员看全部机构，风控人员与融资方只看本机构的业务。

| 区块 | 指标与口径 |
|---|---|
| 资产情况 | 融资总金额（已放款或已进入后续阶段的融资本金之和）、当前余额（各笔未偿余额之和）、企业数量（融资所属供应商机构去重）、融资笔数 |
| 风险分布 | 正常、关注、高风险、违约，按单笔融资确定性分类（`app/domain/risk_operations.risk_class`）：<br>• 违约：已违约、追偿中、违约后回收、核销，或以核销/违约后结清关闭<br>• 高风险：风险处置中；或逾期天数达到“逾期规则”的阈值；或最新风险等级为 high<br>• 关注：逾期、重组，或最新风险等级为 medium<br>• 其余为正常 |
| 生命周期统计 | 正常还款、逾期、风险处置、重组、追偿、核销（按融资去重计数） |
| 损失情况 | 总回收金额；核销金额；净损失（核销金额 − 核销后回收）；回收率（总回收 ÷ 违约敞口）。违约敞口 = 违约前回收 + 核销 + 违约融资的剩余余额 |
| 模型状态 | 当前各 scope 的 ACTIVE 模型、最近模型更新时间、最近训练数据快照、最近失败原因 |
| 运营 | 各严重程度的未关闭预警数，本人未完成与逾期的任务数 |

驾驶舱下方附融资列表：分类、最新评分、未关闭预警数，点击进入风险详情。

### 1.2 风险预警中心

流程：规则识别 → 创建预警 → 指派 → 处理 → 解决 → 审核关闭。状态为 OPEN → ASSIGNED → PROCESSING → RESOLVED → CLOSED。审核人认为处置不充分时，可把 RESOLVED 退回 PROCESSING。

**风险来源**（每条规则对应一个检测器，只读已记录的业务事实）：

| 规则 | 来源 | 严重程度 |
|---|---|---|
| `MODEL_SCORE_THRESHOLD`（阈值 0.60） | 风险决策记录的最终评分达到阈值 | HIGH |
| `OVERDUE_DAYS`（阈值 30 天） | 每条逾期事件 | 达到阈值为 HIGH，否则 MEDIUM |
| `REPAYMENT_ANOMALY`（阈值 2） | 同一融资被拒绝的还款次数达到阈值（连续还款异常） | MEDIUM |
| `LIFECYCLE_ANOMALY` | 融资进入违约 / 风险处置 | CRITICAL / HIGH |
| `DATA_QUALITY` | 有效业务结果因数据质量被审核拒绝 | LOW |

每条预警记录：

- facility_id（模型评分预警在放款前时只有 request_id）
- risk_type、severity
- trigger_reason 与证据（例如评分、阈值、逾期天数）
- 规则及版本
- 创建时间、负责人（owner）、处理结论（resolution）

**同一规则、同一来源记录只产生一次预警**（数据库唯一约束），因此检测可以反复运行。

**检测方式**：

- 应用进程内置周期检测，默认每 60 秒一次（`RISK_MONITOR_INTERVAL_SECONDS`），不引入队列或调度器。
- 风控人员或管理员也可以手动触发检测（`POST /api/v1/risk/alerts/scan`）。

**操作规则**：

- 指派：风控人员或管理员；负责人须为本机构的风控人员或管理员。
- 开始处理、解决：由负责人（或管理员）执行，解决时必须填写处理结论。
- 关闭或退回：由审计员（审核人员）或管理员执行，且**负责人不能审核自己的处置**。
- 任何可见者都可以添加备注。
- 所有修改都带版本号，并发冲突返回 409。

### 1.3 风险任务中心

- 创建任务：风控人员、审核人员或管理员可以创建，可关联预警或融资，并指派负责人（风控人员、审计员或管理员）、设置截止时间和说明。
- 状态：OPEN → IN_PROGRESS → COMPLETED，或 CANCELLED。
- 负责人：开始处理、添加处理备注、上传处理结果文件（≤ 2 MiB，保存 SHA-256，不可修改）、填写结论后完成关闭。
- 创建者或管理员：重新指派、调整截止时间、取消。
- 视图分为：我的任务、待处理、已完成。逾期任务会标红。

### 1.4 风险详情页增强（`GET /api/v1/risk/facilities/{id}`）

打开一笔融资可以看到完整视图：

- **基础信息**：供应商与核心企业（机构名称与代码）、合同与发票号、融资本金、余额、期限、当前状态。
- **风险信息**：风险分类、当前风险等级与评分、历史风险评分（每次评估的分数、等级、所用模型、是否应用校准）、变化趋势（差值与方向，前端绘制折线）。
- **模型信息**：所用模型版本、artifact hash、训练数据快照、引擎版本。
- **生命周期时间线**：申请 → 审核 → 放款 → 还款 → 逾期 → 处置 → 违约 → 追偿 → 核销 → 结清。数据来自工作流操作、设施状态转换、确认的还款、逾期事件和追偿记录，每一项都有操作人、角色、时间和原因。
- **审计**：生命周期、预警事件、任务事件的操作人、时间和原因，以及相关哈希链账本事件的数量。
- 该融资的预警与任务。
- 已有的“融资生命周期”详情页新增“风险详情”按钮。

### 1.5 风险规则中心

- 查看当前生效的规则（阈值、严重程度、是否启用、生效时间、变更人与原因）、待生效版本和完整版本历史。
- 管理员修改规则时生成**新版本**（`risk_rules` 只追加，数据库拒绝修改或删除）：
  - 可以设置未来生效时间；
  - 需要乐观并发（`expected_version`）与变更原因；
  - 每次变更写入 `RISK_RULE_VERSIONED` 账本事件。
- 检测始终使用已生效的最新版本，每条预警记录触发它的规则版本。
- 规则是固定类型加阈值的简单模型，不是规则引擎。

## 2. 数据模型（迁移 `20260928_0019`）

| 表 | 说明 |
|---|---|
| `risk_rules` | (rule_key, version) 主键，只追加；初始 5 条规则作为 v1 写入 |
| `risk_alerts` | (rule_key, source_ref) 唯一，关联规则版本、融资、申请、机构（数据边界），带状态、负责人、处理结论和版本号 |
| `risk_alert_events` | 只追加：动作、from/to 状态、结果版本、操作人、角色、备注、载荷、时间 |
| `risk_tasks` | 关联预警/融资/机构，含标题、类型、说明、状态、负责人、创建者、截止时间、结论和版本号 |
| `risk_task_events` | 只追加：结构同预警事件 |
| `risk_task_attachments` | 不可修改：文件名、类型、大小、SHA-256、内容 |

数据库保证：

- 非法的状态转换被拒绝；
- 每次修改版本号加一；
- 每次状态变化必须有对应版本的审计事件（延迟约束触发器）；
- 预警与任务的身份字段不可修改，也不能删除；
- 负责人与状态、解决与结论、完成与结论保持一致。

其他变化：

- `users.role` 新增 `admin`，演示账号为 `admin.demo`，属于 BANK-001。
- 账本新增事件类型 `RISK_ALERT_CREATED`、`RISK_ALERT_TRANSITIONED`、`RISK_TASK_RECORDED`、`RISK_RULE_VERSIONED`。
- 降级只在没有预警、任务、规则新版本和管理员账号时才允许，已实测 升级 → 降级 → 再升级。

## 3. API

| 方法 | 路径 |
|---|---|
| GET | `/api/v1/risk/dashboard`、`/api/v1/risk/facilities`、`/api/v1/risk/facilities/{id}` |
| POST | `/api/v1/risk/alerts/scan` |
| GET | `/api/v1/risk/alerts?status=&severity=&facility_id=`、`/api/v1/risk/alerts/{id}` |
| POST | `/api/v1/risk/alerts/{id}/assign`、`/start`、`/resolve`、`/close`、`/reopen`、`/comments` |
| GET | `/api/v1/risk/assignees`、`/api/v1/risk/tasks?view=mine\|pending\|completed\|all`、`/api/v1/risk/tasks/{id}` |
| POST | `/api/v1/risk/tasks`、`/api/v1/risk/tasks/{id}/reassign`、`/due`、`/notes`、`/start`、`/result`、`/complete`、`/cancel` |
| GET | `/api/v1/risk/tasks/{id}/attachments/{attachment_id}` |
| GET | `/api/v1/risk/rules` |
| POST | `/api/v1/risk/rules/{rule_key}/versions` |

错误码约定：403（角色不允许）、404（不存在或不在数据边界内，不泄露存在性）、409（状态、版本或业务冲突）、422（输入校验）。

## 4. 页面

新增 5 个页面，导航按角色显示：

| 页面 | 角色 |
|---|---|
| 风险驾驶舱 | 管理员、风控人员、审计员、融资方 |
| 风险预警中心（列表、状态筛选、规则检测、详情、按状态与角色出现的操作、处理历史、从预警创建任务） | 管理员、风控人员、审计员 |
| 风险任务中心（我的任务 / 待处理 / 已完成、创建、处理、上传结果、下载文件、历史） | 管理员、风控人员、审计员 |
| 风险详情（融资选择器 + 完整视图） | 所有角色，企业只能看到自己的融资 |
| 风险规则中心（管理员可编辑并保存为新版本） | 管理员、风控人员、审计员 |

登录页新增管理员账号，管理员登录后默认进入风险驾驶舱。

实机截图（Docker 栈，真实 PostgreSQL；融资经由真实的设施生命周期服务推进；页面操作通过 Playwright 在 UI 中完成）：

- [风险驾驶舱](docs/product/phase3/risk-dashboard.png)
- [预警中心](docs/product/phase3/alert-center.png)
- [任务中心](docs/product/phase3/task-center.png)
- [风险详情](docs/product/phase3/risk-detail.png)
- [规则中心](docs/product/phase3/risk-rules.png)

## 5. 权限

| 角色 | 平台角色 | 能力 |
|---|---|---|
| `admin` | 管理员 | 查看全部机构；配置规则；指派、处理、审核预警；管理任何任务 |
| `risk_manager` | 风控人员 | 查看本机构风险；运行检测；指派与处理预警；创建和处理任务 |
| `auditor` | 审核人员 | 查看全部；审核关闭或退回预警（不能审核自己的处置）；处理指派给自己的任务 |
| `financier` | 融资方 | 查看本机构驾驶舱与风险详情 |
| `supplier`、`core_enterprise` | 企业用户 | 只能查看自己的融资；看不到模型、预警与任务等内部信息 |

数据边界：预警与任务记录所属机构，融资沿用既有的机构可见性规则。其他机构的风控人员看不到这些数据，访问时统一返回 404。本阶段没有实现完整的多租户系统。

## 6. 测试结果

新增 `tests/test_risk_operations.py`（11 个测试），另有 UI 契约测试 `test_risk_operations_pages_are_wired_for_their_roles`：

| # | 要求 | 测试 |
|---|---|---|
| 1 | 驾驶舱统计正确性 | `test_dashboard_statistics_come_from_recorded_business_data`（4 笔经真实生命周期推进的融资，逐项核对资产、分布、生命周期、损失与回收率）、`test_risk_class_is_derived_from_lifecycle_and_scores` |
| 2 | 风险事件创建 | `test_rules_raise_alerts_once_per_source_record`、`test_rule_changes_are_versioned_and_drive_detection` |
| 3 | 风险事件状态转换 | `test_alert_lifecycle_is_governed_and_fully_audited`、`test_domain_transitions_equal_the_frozen_database_lists` |
| 4 | 任务分配 | `test_tasks_are_assigned_worked_and_closed_with_results` |
| 5 | 权限访问控制 | `test_access_is_limited_by_role_and_organization`（含其他机构的风控人员与企业用户） |
| 6 | 审计记录 | 预警与任务测试逐条核对事件（操作人、角色、时间、原因）与账本事件，并验证数据库拒绝改写、删除和无审计的状态变化 |
| 7 | 风险详情数据完整性 | `test_risk_detail_assembles_every_view_of_a_facility` |
| 8 | 重启恢复 | `test_operations_state_survives_a_restart`，以及下方的 Docker 重启实测 |
| 9 | API 契约 | `test_risk_operations_api_contract`（各端点状态码、字段集、版本冲突、校验、上传与下载、角色） |

为适配本阶段的变化，改动了以下既有测试与脚本，均不削弱原有意图：

- 三处演示账号计数由 5 改为 6，因为新增了管理员账号。
- 答辩预检脚本仍要求五个论文角色按顺序排在登录指引最前，允许其后追加运营角色。
- 演示账号初始化会跳过数据库还不支持的角色，这样升级到旧版本的迁移测试和维护启动仍可初始化账号。

| 检查 | 结果 |
|---|---|
| `ruff check .` | 通过 |
| `mypy` | 79 个源文件无问题 |
| 应用全量测试（真实 PostgreSQL 17） | **868 passed、12 skipped、0 failed** |
| Docker 重建与重启验证 | 通过，见下 |
| CI | 见 PR |

Docker 验证（本地 8010）的步骤：

1. 新镜像在 Phase 2.2 的数据卷上启动，迁移在容器内升级到 `20260928_0019`。
2. 通过设施生命周期服务推进 4 笔新融资：逾期 18 天、逾期 62 天、违约后追偿 300 并核销 700、两次还款被拒。
3. 在 UI 中完成完整流程：
   - 风控人员运行检测，产生 8 条预警；
   - 风控人员指派并处理“逾期 62 天”预警，填写处理结论后解决，并从该预警创建复核任务给审计员；
   - 审计员开始处理任务、上传结果文件、完成任务，并审核关闭该预警；
   - 管理员把逾期阈值改为 45 天，生成 v2。
4. `docker compose down`（保留卷）后再次 `up`。

重启前后，以下内容逐字节一致：驾驶舱全部指标、8 条预警及其完整事件、任务、规则版本历史。账本校验有效。

## 7. 当前限制

- 检测为周期扫描（默认 60 秒）加手动触发，不是事件驱动。同一来源只报一次；如果之后情况恶化（例如逾期天数继续增加），需要由新的来源记录再次触发。
- 规则只有固定类型加阈值、严重程度、启用和生效时间，没有表达式或组合条件，这是有意为之，本阶段不做规则引擎。
- 模型评分预警在放款前只关联申请，此时机构边界取评估人所属机构。
- 融资方看不到申请阶段的机构归属，这是既有工作流的限制，见 `docs/thesis-traceability.md`。
- 结果文件保存在 PostgreSQL（上限 2 MiB），没有对象存储，也不做病毒扫描。
- 驾驶舱在应用层聚合，适合 MVP 规模，没有物化统计表。
- 没有通知推送（邮件或短信），任务逾期只在页面标红。
- 管理员是运营角色，不参与融资工作流的审批，也不能校验账本（仍为审计员职能）。

## 8. Commit

- 实现提交：见 PR
