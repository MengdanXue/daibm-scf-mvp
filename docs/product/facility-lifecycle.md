# 融资生命周期状态机 / Facility lifecycle state machine

产品化 Phase 1。定义融资从申请到结清的完整业务状态、每个状态的进入条件与允许操作，以及审计与防篡改保证。机器可读版本：`GET /api/v1/facilities/state-machine`（登录后可用），代码唯一来源：`app/domain/facility.py`。

本模块仍是**受控融资生命周期模拟**：不发起真实银行转账，不计算利息、费用、汇率或会计分录。

## 1. 全链路阶段

| 业务阶段 | 系统对象 / 状态 | 主要角色 |
|---|---|---|
| 申请 | 申请 `draft → submitted`（核心企业可退回 `trade_returned`） | 供应商 |
| 审核 | 申请 `trade_confirmed → risk_assessed → approved/manual_review/rejected → controlled → audited` | 核心企业、融资方、风险经理、审计员 |
| 授信 | 融资设施 `ready_for_disbursement` | 融资方 |
| 放款 | `disbursed → active` | 融资方 |
| 正常还款 | `active`（重组后为 `restructured`） | 供应商提交、融资方确认 |
| 逾期 | `overdue` | 融资方 |
| 风险处置 | `in_disposal` | 风险经理 |
| 重组 | `restructured`（追加新合同版本） | 风险经理 |
| 违约 | `defaulted` | 风险经理 |
| 追偿 | `in_recovery` | 风险经理启动、融资方登记回款 |
| 核销 | `written_off` | 审计员 |
| 结清 | `repaid` / `recovered` / `written_off` → `closed` | 审计员 |

申请阶段的状态机在 `app/domain/workflow.py`，已有角色绑定的转换表与 `workflow_actions` 审计；本阶段的改动集中在授信之后的融资设施。

## 2. 融资设施状态、进入条件与允许操作

| 状态 | 中文 | 进入条件 | 允许操作（角色） |
|---|---|---|---|
| `ready_for_disbursement` | 已授信待放款 | 申请已审计且决策为批准；本金等于申请金额；还款计划合计精确等于本金；每笔申请仅一个设施 | 发起放款（融资方） |
| `disbursed` | 放款处理中 | 融资方发起放款 | 确认放款（融资方） |
| `active` | 正常还款 | 放款已确认；或逾期后所有已到期分期已还清（自动治愈）；或风险处置在无欠款时结束 | 提交还款（供应商）；确认/拒绝还款、标记逾期（融资方） |
| `overdue` | 逾期 | 当前合同版本存在已过到期日且未还清的分期；融资方记录逾期天数与证据哈希 | 提交还款；确认/拒绝还款；发起风险处置（风险经理） |
| `in_disposal` | 风险处置 | 设施处于逾期；当前合同存在真实欠款；风险经理提交原因代码与证据哈希 | 提交/确认/拒绝还款；重组、认定违约、无欠款时结束处置（风险经理） |
| `restructured` | 重组履约 | 从风险处置或违约进入；新计划合计精确等于剩余余额；新到期日均在未来；无待决付款 | 提交/确认/拒绝还款；标记逾期 |
| `defaulted` | 违约 | **只能**从风险处置进入；存在逾期证据；每个合同版本最多一次违约 | 提交/确认/拒绝还款；重组、启动追偿（风险经理） |
| `in_recovery` | 追偿 | 当前合同版本已认定违约；风险经理提交原因与证据 | 提交/确认/拒绝还款；登记追偿回款（融资方）；核销（审计员） |
| `repaid` | 已还清 | 余额通过还款归零，且**从未违约** | 关闭（审计员） |
| `recovered` | 追偿完毕 | 余额归零，且存在任一违约记录 | 关闭（审计员） |
| `written_off` | 已核销 | 处于追偿；存在违约记录；余额为正；无待决付款；审计员批准 | 登记核销后回款（融资方）；关闭（审计员） |
| `closed` | 已关闭 | 状态为 `repaid`/`recovered`/`written_off`；余额为零；审计账本校验通过；关闭原因由不可变历史推导 | 无（终态） |

合法状态变化只有 25 组，例如 `overdue → defaulted`、`overdue → restructured`、`defaulted → written_off`、`restructured → defaulted` 均为**非法跳转**，必须先经过风险处置或追偿阶段。

## 3. 本阶段修复的三个业务问题

### 违约不再依赖余额归零

- 违约是一条不可变事件（`facility_defaults`），只能由风险经理在风险处置阶段认定，与余额无关。
- 违约后即使余额被还清或追回，状态也进入 `recovered`（追偿完毕），**不会**变成普通的 `repaid`，`repaid_at` 保持为空。
- 判断依据是“是否存在违约记录”，而不是当前状态或余额；因此“违约 → 重组 → 还清”同样落在 `recovered`。
- 结清分类区分为 `NORMAL_SETTLED`、`SETTLED_AFTER_DEFAULT`、`WRITTEN_OFF`，旧版本把违约后结清也标为 `NORMAL_SETTLED` 的问题已修复。

### 核销不再等同正常结清

- 核销只能在追偿阶段由审计员执行；核销把剩余余额转入 `facility_writeoffs`（表外），不记为现金回收。
- 核销后债权仍然存在（“账销案存”）：融资方可继续登记核销后回款（`applied_to = written_off`），它降低净损失但不重新打开余额。
- 响应中分别给出 `realized_loss`（核销金额）与 `net_loss`（核销金额减核销后回款）。
- 关闭原因为 `written_off`，结清分类为 `WRITTEN_OFF`。

### 重组不再覆盖历史合同

- 每个设施的每个合同版本都有一行不可变快照 `facility_contract_versions`：计划、本金、起始余额、币种与 `terms_sha256`。
- 重组时，被替代分期在标记为 `superseded` **之前**的状态（如 `overdue`）、已还金额写入新版本的 `superseded_schedule`。
- 数据库触发器禁止修改分期的合同条款（序号、版本、到期日、金额）、禁止修改已被替代的分期、禁止删除任何分期。
- 重组账本事件同时记录 `previous_contract_sha256` 与 `contract_sha256`。

## 4. 审计与防非法跳转

| 保证 | 实现 |
|---|---|
| 每次状态变化都有审计记录 | `facility_status_transitions`：from/to、触发动作、操作人、角色、结果版本、原因代码、时间 |
| 数据库拒绝非法跳转 | `trg_financing_facilities_status_guard`：仅允许 25 组合法 (from, to) |
| 数据库拒绝未审计的状态变化 | `trg_financing_facilities_transition_audit`（延迟约束触发器）：状态变化必须在同一版本有对应的转换记录 |
| 历史不可改写 | 转换记录、合同版本、风险决策、追偿回款、`facility_actions` 命令日志均禁止 UPDATE/DELETE |
| 资金守恒 | 本金 = 余额 + 已确认还款 + 追偿回款（计入余额部分）+ 核销金额，由延迟约束触发器校验 |
| 哈希链账本 | 新增 `FACILITY_OVERDUE_CURED`、`FACILITY_DISPOSAL_OPENED/CLOSED`、`FACILITY_RECOVERY_STARTED/RECORDED`、`FACILITY_RECOVERED` 事件 |

迁移 `20260924_0015` 只追加数据：为每个既有设施写入一条 `migration_baseline` 转换记录，并按既有分期的合同条款回填 `migration_backfill` 合同版本；不修改任何既有行。降级在存在新历史时被拒绝。

## 5. API

| 方法 | 路径 | 角色 |
|---|---|---|
| GET | `/api/v1/facilities/state-machine` | 任意已登录角色 |
| POST | `/api/v1/facilities/{id}/open-disposal` | 风险经理 |
| POST | `/api/v1/facilities/{id}/close-disposal` | 风险经理 |
| POST | `/api/v1/facilities/{id}/restructure` | 风险经理 |
| POST | `/api/v1/facilities/{id}/declare-default` | 风险经理 |
| POST | `/api/v1/facilities/{id}/start-recovery` | 风险经理 |
| POST | `/api/v1/facilities/{id}/recoveries` | 融资方 |
| POST | `/api/v1/facilities/{id}/write-off` | 审计员 |
| POST | `/api/v1/facilities/{id}/close` | 审计员 |

设施响应新增：`status_history`、`contract_versions`、`lifecycle_decisions`、`recoveries`、`arrears_amount`、`recovery_collected_amount`、`post_writeoff_recovery_amount`、`net_loss`。

## 6. 已知边界

- 自动治愈只在 `overdue` 状态触发；风险处置中的还款不会自动结束处置，需风险经理确认。
- 追偿回款来源为枚举值，证据只以 SHA-256 存储；系统不核验外部法院、担保或抵押材料。
- 结果回流（`actual_outcomes`）的 `loss_amount` 仍取核销金额（毛损失），不扣减核销后回款，以保持既有校准数据血缘不变。
- 申请阶段与设施阶段是两套状态机；审批权限、机构隔离的完善属于后续 Phase 3。
