# 风险智能治理层 / Model & data governance

产品化 Phase 2。把"结果反馈 → 校准模型"的演示能力升级为可审计、可控制、可回滚的治理闭环。代码入口：`app/domain/governance.py`（状态、scope 矩阵、审查原因）、`app/services/model_registry.py`、`app/services/outcomes.py`、迁移 `20260925_0016`。

## 1. 模型注册表

| 状态 | 含义 |
|---|---|
| DRAFT | 数据不足以做独立验证的探索性候选，永不可部署 |
| EVALUATING | 工件已发布，等待按时间顺序的留出集独立验证 |
| CANDIDATE | 独立验证通过，等待晋升决策 |
| ACTIVE | 为其声明 scope 提供服务；每个 scope 至多一个（部分唯一索引） |
| ROLLED_BACK | 被受控回滚停用 |
| RETIRED | 被新版本替代、因结果更正失效，或由审计员下线 |
| REJECTED | 训练、验证、完整性或晋升门槛失败 |

- 每个模型记录：model_id、version（`platt-<scope>-vN`）、model_type、artifact_path、artifact_hash、created_time、creator（触发训练的结果/更正提交人）、training_scope、training_dataset_version（数据集快照 SHA-256）、evaluation_metrics（留出集 before/after）、status。
- `model_registry_events` 由 `calibration_runs` 上的触发器写入，任何代码路径的状态变化都不会遗漏；回滚、下线通过 `daibm.actor_user_id` 记录操作人。
- 流水线每一步都记录 dataset snapshot、sample count、scope、metrics、artifact hash：`REGISTERED`（训练与候选工件）→ `VALIDATED`（独立验证证据）→ `PROMOTION_DECISION` → 状态变化。
- 数据库拒绝：未通过独立验证的自动晋升；删除 ACTIVE 模型；同一 scope 两个 ACTIVE。
- ACTIVE 模型不可下线，必须先回滚；回滚只能回到直接前序版本。
- TGNN 研究模型以只读条目出现在注册表中（`synthetic_reference` scope）。

## 2. 晋升依据：独立验证

晋升只依据按时间顺序切分的留出集：训练集与验证集不相交，且训练截止时间严格早于验证开始时间。`VALIDATED` 事件保存训练/验证样本数、截止与开始时间、留出集指标；训练拟合诊断不参与晋升。若证据显示验证集不独立，晋升被拒绝（`validation_not_independent`）。

## 3. 业务结果数据治理

```
CREATED → REVIEWING → ELIGIBLE → TRAINING_USED
CREATED → REVIEWING → REJECTED
```

- 数据库在每条结果写入时执行资格规则（`outcome_eligibility_reason`），不可绕过。拒绝原因：`SCOPE_MISMATCH`、`DATA_QUALITY_INSUFFICIENT`、`BUSINESS_INCONSISTENT`、`BUSINESS_EXCEPTION`、`MANUAL_CORRECTION`、`SUPERSEDED_BY_CORRECTION`。
- 训练集只取最新审查状态为 `ELIGIBLE`/`TRAINING_USED` 的有效结果；被训练使用时自动记录 `TRAINING_USED` 及所用模型。
- EXCLUDE 更正 → `REJECTED(MANUAL_CORRECTION)`；REINSTATE → 重新审查。
- 迁移前的历史结果保持原有资格（`LEGACY_ACCEPTED_BEFORE_REVIEW`），不追溯改变训练行为。

## 4. 更正与替代（不可变）

`POST /api/v1/outcomes/{id}/supersede` 追加新修订：新记录引用旧记录（`supersedes_outcome_id`、`revision`），原记录字节不变并自动失去训练资格，包含旧记录的激活模型被置为失效并重新训练。默认查询只返回有效修订，`include_superseded=true` 与 `/lineage` 展示完整链路（每个修订的更正与审查历史）。

## 5. Scope 兼容矩阵

| 模型 scope | 请求 scope | 结果 |
|---|---|---|
| controlled_demo | controlled_demo | allow |
| external_verified | external_verified | allow |
| mixed | mixed | allow_with_warning |
| 其余组合 | | reject |

推理时即使存在 ACTIVE 工件也必须先通过矩阵；拒绝时回退基线分数，决策记录与 `RISK_CALIBRATION_FALLBACK` 账本事件写入 `scope_result`/`scope_reason`。`mixed` 模型按晋升门槛永不部署，请求 scope 目前只有两种，因此 `mixed×mixed` 只在矩阵中定义。

## 6. 风险决策审计

每次业务风险评估写入一条不可变的 `risk_decision_records`：输入快照与 SHA-256、引擎版本、原始分与最终分、等级、所用校准模型与工件哈希、模型 scope、scope 检查结果与原因、尝试使用的模型、回退代码、操作人与角色、时间。`GET /api/v1/applications/{id}/risk-decisions` 回答"这家企业当时为什么得到这个风险等级"，并关联对应的账本事件哈希。

## 7. API

| 方法 | 路径 | 角色 |
|---|---|---|
| GET | `/api/v1/model-registry`、`/api/v1/model-registry/{kind}/{id}` | 审计员、风险经理、融资方 |
| GET | `/api/v1/model-registry/scope-compatibility` | 已登录 |
| POST | `/api/v1/model-registry/calibration/{id}/retire` | 审计员 |
| POST | `/api/v1/calibration-deployments/rollback`（既有） | 审计员 |
| GET | `/api/v1/outcome-governance/summary` | 审计员、风险经理 |
| GET | `/api/v1/outcomes?include_superseded=`、`/api/v1/outcomes/{id}/lineage` | 审计员 |
| POST | `/api/v1/outcomes/{id}/supersede` | 审计员 |
| GET | `/api/v1/applications/{id}/risk-decisions` | 可见该申请的角色 |

## 8. 界面

- 模型中心：各 scope 当前激活模型、注册表、模型详情（工件哈希实时校验、注册表历史、回滚/下线按钮仅对审计员且在允许时显示）、scope 兼容矩阵。
- 数据反馈：各 scope 记录总数、有效结果、可训练数、被替代数、审查状态分布、拒绝原因；结果列表与更正链。
- 申请详情："为什么这个风险等级"面板。

## 9. 边界

- 仍是 Platt 校准层治理，不新增模型算法、不在线训练、不替换 TGNN；TGNN 条目只读。
- 资格规则是确定性的业务一致性检查，不验证外部证据真实性；`external_verified` 仍是人工声明而非密码学来源证明。
- CANDIDATE 在自动晋升中是瞬时状态（同一事务内验证通过即晋升或拒绝），以注册表事件形式记录。

## Phase 2.1：模型版本注册表

自迁移 `20260926_0017` 起，`risk_model_versions` 是部署模型的记录源：每个校准工件注册为一个版本（`calibration:{scope}@vN`），生命周期为 DRAFT → EVALUATING → CANDIDATE → ACTIVE → ROLLED_BACK → RETIRED（另有 REJECTED）。每次状态变化都经过 `transition_model_version` 写入不可变的 `risk_model_version_transitions`；推理按 scope 查询 ACTIVE 版本。人工激活会重新校验工件哈希。详见 [PHASE2.1_MODEL_REGISTRY_REPORT.md](../../PHASE2.1_MODEL_REGISTRY_REPORT.md)。

## Phase 2.2：结果数据治理

自迁移 `20260927_0018` 起，训练只经过 `OutcomeEligibilityService`：每次校准先冻结一份不可变的训练数据快照（纳入与排除的结果及原因、数据集哈希），训练只读取快照中纳入的行；calibration run 与模型版本都关联该快照。每条审核事件记录 from/to 状态、操作人、角色、时间与原因；审计员或风险经理可以人工通过或拒绝结果。详见 [PHASE2.2_OUTCOME_GOVERNANCE_REPORT.md](../../PHASE2.2_OUTCOME_GOVERNANCE_REPORT.md)。

