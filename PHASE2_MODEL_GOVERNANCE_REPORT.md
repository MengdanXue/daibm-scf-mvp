# Phase 2 报告：风险智能治理层（Model & Data Governance）

目标：把“会根据结果调整风险分数”升级为“有模型生命周期控制、数据资格审查、审计追踪和安全晋升机制的风险智能平台”。本阶段只在既有 risk assessment、outcome、calibration artifact、审计历史、哈希校验与 scope 信息上增量开发；没有新增基础设施、模型算法、在线训练或数据库，没有修改任何历史审计数据。

详细规格：[docs/product/model-governance.md](docs/product/model-governance.md)。

## 1. 实现功能

| 要求 | 实现 |
|---|---|
| 模型注册与版本 | `calibration_runs` 即校准模型注册表，统一投影为 DRAFT / EVALUATING / CANDIDATE / ACTIVE / ROLLED_BACK / RETIRED / REJECTED；每个模型给出 model_id、version、model_type、artifact_path、artifact_hash、created_time、creator、training_scope、training_dataset_version、evaluation_metrics、status。TGNN 研究模型以只读条目并列展示 |
| ACTIVE 唯一 | 既有部分唯一索引保证每个 scope 至多一个 ACTIVE；测试覆盖版本切换 |
| ACTIVE 切换有审计 | `model_registry_events` 由数据库触发器写入，任何代码路径的状态变化都有事件；回滚、下线记录操作人 |
| 回滚 | 既有回滚接口现在把被停用模型标记为 ROLLED_BACK（`rolled_back_at`）并记录操作人 |
| 禁止删除 ACTIVE | 数据库触发器拒绝删除 ACTIVE 校准模型或已部署的研究模型；ACTIVE 模型也不能下线，必须先回滚 |
| 下线 | `POST /api/v1/model-registry/calibration/{id}/retire`（审计员），写 `CALIBRATION_MODEL_RETIRED` 账本事件 |
| 结果资格审查 | 数据库在每条结果写入时执行资格规则：CREATED → REVIEWING → ELIGIBLE / REJECTED（原因：scope 不匹配、数据质量不足、业务不一致、业务异常、人工纠正、被更正替代）；训练只使用 ELIGIBLE/TRAINING_USED，使用后自动记录 TRAINING_USED 与所用模型 |
| 更正 / 替代 | `POST /api/v1/outcomes/{id}/supersede` 追加新修订并引用旧记录；原记录字节不变、自动失去训练资格；包含旧记录的激活模型置为失效并重新训练；默认查询只显示有效结果，`include_superseded` 与 `/lineage` 展示完整链路 |
| 校准流水线治理 | 每一步保存数据集快照 id、样本数、scope、指标、工件哈希：REGISTERED → VALIDATED → CANDIDATE → PROMOTION_DECISION → ACTIVE |
| 禁止用训练集晋升 | 晋升只依据按时间顺序切分的留出集，并校验训练/验证不相交、训练截止早于验证开始；否则拒绝（`validation_not_independent`）。数据库拒绝没有通过独立验证记录的自动晋升 |
| Scope 隔离 | 模型声明 `model_scope`、请求声明 `request_scope`，经兼容矩阵判定 allow / allow_with_warning / reject；即使存在 ACTIVE 工件也必须通过矩阵；拒绝时回退基线分数、记录原因并写入账本 |
| 风险决策审计 | 每次业务风险评估写一条不可变的 `risk_decision_records`：输入版本与快照、引擎版本、所用模型与工件哈希、scope 判定、原始分/最终分/等级、操作人与角色、时间 |

## 2. 数据模型变化（迁移 `20260925_0016`）

- `calibration_runs` 新增 `rolled_back_at`、`retired_at`、`retired_by_user_id`、`retirement_reason`，以及约束：ACTIVE 不能同时是已下线或已回滚。
- 新表 `model_registry_events`（只追加）：注册、状态变化、验证、晋升决策。
- 新表 `outcome_review_events`（只追加）：结果审查生命周期。
- 新表 `risk_decision_records`（只追加）：每次评估一条。
- `actual_outcomes` 新增 `revision`、`supersedes_outcome_id`、`correction_reason_code`、`correction_comment`。唯一约束从“每个设施一条”改为“每个设施每个修订一条”，并加修订链校验触发器。
- 新增 SQL 函数：`calibration_registry_status`、`outcome_eligibility_reason`、`review_outcome`，以及触发器：注册表日志、晋升须验证、禁止删除 ACTIVE、结果审查、训练使用记录。
- 账本事件类型新增 `ACTUAL_OUTCOME_SUPERSEDED`、`CALIBRATION_MODEL_RETIRED`。
- 回填只追加：既有模型写入基线注册事件；既有结果保持原有训练资格（`LEGACY_ACCEPTED_BEFORE_REVIEW`，或原有的 EXCLUDE → REJECTED）并补记 TRAINING_USED。降级在存在新治理历史时被拒绝。

## 3. API 变化

| 方法 | 路径 |
|---|---|
| GET | `/api/v1/model-registry`（可按 scope 过滤）、`/api/v1/model-registry/{kind}/{id}`（含事件历史与工件哈希实时校验） |
| GET | `/api/v1/model-registry/scope-compatibility` |
| POST | `/api/v1/model-registry/calibration/{id}/retire` |
| GET | `/api/v1/outcome-governance/summary` |
| GET | `/api/v1/outcomes?include_superseded=`（默认只返回有效结果），`/api/v1/outcomes/{id}/lineage` |
| POST | `/api/v1/outcomes/{id}/supersede` |
| GET | `/api/v1/applications/{id}/risk-decisions` |

结果响应新增 `revision`、`supersedes_outcome_id`、`correction_reason_code`、`review_status`、`review_reason`。`RISK_ASSESSMENT` 与 `RISK_CALIBRATION_FALLBACK` 账本事件新增 `scope_result`、`scope_reason`、`calibration_artifact_sha256`。

## 4. 前端页面

- **模型中心**（审计员、风险经理、融资方）：各 scope 的当前激活模型、注册表（版本、类型、scope、状态、数据快照、样本数、留出集 Brier 前 → 后、创建时间、创建者）、模型详情（工件哈希校验、注册表历史、回滚/下线按钮）、scope 兼容矩阵。
- **数据反馈**（审计员、风险经理）：记录总数、有效结果、可训练数、被替代数、审查状态分布、拒绝原因；结果列表与更正链（审计员）。
- **风险决策详情**：申请详情中的“为什么是这个风险等级”面板。

实机截图（Playwright 驱动本地运行的应用，真实 PostgreSQL，由应用自带的校准 worker 训练并激活模型）：[模型中心](docs/product/phase2/model-center-zh.png)、[数据反馈](docs/product/phase2/data-feedback-zh.png)、[风险决策详情](docs/product/phase2/risk-decision-detail.png)。

## 5. 测试结果

新增 `tests/test_model_governance.py`（26 个测试）与一个 UI 契约测试，覆盖要求的 10 类场景：

| # | 场景 | 测试 |
|---|---|---|
| 1 | 模型注册 | `test_trained_model_is_registered_with_full_lineage` |
| 2 | 版本切换 | `test_version_switch_keeps_one_active_and_audits_each_change` |
| 3 | ACTIVE 唯一 | 同上，外加数据库拒绝重新激活第二个版本 |
| 4 | 回滚 | `test_rollback_restores_predecessor_and_records_actor` |
| 5 | 结果资格审查 | `test_every_outcome_is_reviewed_before_it_can_train`、`test_scope_mismatched_outcome_is_rejected_by_review` |
| 6 | 更正后旧数据不可训练 | `test_superseding_correction_keeps_original_and_removes_its_eligibility`、`test_exclusion_correction_rejects_and_reinstatement_rereviews` |
| 7 | scope 不匹配拒绝 | `test_scope_mismatch_is_rejected_recorded_and_audited`、`test_scope_compatibility_matrix` |
| 8 | 晋升需要验证结果 | `test_promotion_rests_on_an_independent_holdout_not_the_training_fit`、`test_database_rejects_promotion_without_a_validation_record`、`test_candidate_without_independent_holdout_is_never_promoted` |
| 9 | 工件哈希一致性 | `test_registry_artifact_hash_consistency_and_tamper_detection` |
| 10 | 重启后状态恢复 | `test_registry_state_survives_a_restart` |

另有：删除或下线 ACTIVE 被禁止、决策记录内容与不可变性、数据库与 Python 状态投影一致、治理 API 与角色权限。

| 检查 | 结果 |
|---|---|
| `ruff check .` | 通过 |
| `mypy` | 68 个源文件无问题 |
| 应用全量测试（真实 PostgreSQL 17，含 ZKP 集成） | 825 passed、11 skipped、0 failed |
| Docker 重建验证 | 通过，见下 |
| CI | 见文末 |

为适配本阶段的约束，改动了 6 个既有测试，均不削弱原有意图：

- 两个迁移测试的清理步骤改用 TRUNCATE，因为数据库现在（按设计）禁止逐行删除 ACTIVE 模型和审查历史。
- 两个迁移测试的字段/触发器清单加入新列与新触发器。
- 字节不变测试对 `actual_outcomes` 排除新追加的 4 列，原有字段字节仍逐字比较。
- 结果列表“单查询”测试仍是单查询：审查状态已并入主查询。

Docker 重建验证（本地 8010）的步骤：

1. 用全新卷启动，迁移在容器内升级到 `20260925_0016`。
2. 写入 41 条结果：容器内的 worker 训练并激活 `platt-controlled_demo-v1`，1 条被判定为 `BUSINESS_INCONSISTENT`。
3. 完成一笔真实风险评估：0.3743 → 0.2812，使用该校准模型。
4. `docker compose down`（保留卷），无缓存重建镜像，再次启动。

重启后以下内容与重建前逐项一致：激活模型、全部模型状态、工件哈希（一致）、注册表事件、决策记录、结果汇总、账本（有效，事件数相同）。

环境说明：沙箱中 Docker Hub 限流（429），基础镜像改从 `mirror.gcr.io` 拉取同一官方镜像。沙箱出口代理需要 CA 证书，构建时用临时 Dockerfile 副本仅在 `pip install` 一步挂载 CA。仓库中的 `Dockerfile` 与 `docker-compose.yml` 没有修改。

## 6. 当前限制

- 治理对象仍是 Platt 校准层；TGNN 研究模型在注册表中只读，不参与注册表状态机。
- CANDIDATE 在自动晋升中是瞬时状态：同一事务内独立验证通过即晋升或拒绝，以事件形式留痕，没有人工审批的晋升步骤。
- 资格规则是确定性的业务一致性检查，不核验外部证据的真实性；`external_verified` 仍是人工声明，而非密码学来源证明。
- `mixed` scope 的模型按门槛永不部署，请求 scope 目前只有两种，因此 `mixed × mixed = allow_with_warning` 只在矩阵中定义与测试。
- 迁移前的历史结果以 `LEGACY_ACCEPTED_BEFORE_REVIEW` 保留原资格，没有追溯重跑资格规则，以免改变历史训练行为。
- 注册表的 creator 取触发训练的结果或更正的提交人；自动流程中的状态变化记为 `system`。

## 7. Commit

- 实现提交：`adcc173`（`claude/funny-ptolemy-32pkcy`）
