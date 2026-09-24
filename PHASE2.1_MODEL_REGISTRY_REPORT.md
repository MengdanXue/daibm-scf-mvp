# Phase 2.1 报告：模型生命周期管理（Model Registry）

范围：本轮只做模型版本注册表与生命周期。没有数据治理、外部数据接入或 MLOps 的新增工作，没有新增基础设施或算法，也没有修改任何历史审计数据。实现建立在 Phase 2（`20260925_0016`）之上，与其同在 `claude/funny-ptolemy-32pkcy` 分支。

核心变化：**模型版本（`risk_model_versions`）成为部署模型的记录源**。激活、回滚、推理都经过它；`calibration_runs` 退为版本所指向的工件。

```
推理：request scope → 查询该 scope 的 ACTIVE 版本 → scope 兼容矩阵 → 按版本的 artifact_path + artifact_hash 加载并校验工件 → 风险评估
```

## 1. 实现内容

| 要求 | 实现 |
|---|---|
| 版本实体 | `risk_model_versions`：id、model_id（`calibration:{scope}`）、version（按 model_id 递增）、model_type、artifact_path、artifact_hash、scope、training_dataset_version、metrics、created_by、created_at、status；另有 evaluation / evaluation_passed / evaluated_at、activated_at、deactivated_at、promotion_reason、previous_active_version_id |
| 状态机 | DRAFT → EVALUATING → CANDIDATE → ACTIVE → ROLLED_BACK → RETIRED，另有 REJECTED。合法转换同时写在 `app/domain/model_registry.py` 与迁移中，测试断言两处一致。数据库触发器拒绝非法转换 |
| 每个 scope 只有一个 ACTIVE | 部分唯一索引 `uq_risk_model_versions_active_scope` |
| 激活前必须有评估 | 约束 `ck_risk_model_versions_evaluated_contract`：CANDIDATE / ACTIVE 必须 `evaluation_passed IS TRUE`；ACTIVE 还必须有 activated_at 与 promotion_reason。评估记录一经写入不可修改 |
| 激活时校验哈希 | 人工激活时重新计算工件文件的 sha256，与版本登记的哈希比对，并执行完整的工件校验（run_id、scope、数据集哈希）。不一致即拒绝，状态不变 |
| ACTIVE 不能删除 | 触发器 `reject_active_model_version_delete` |
| 回滚到上一个 ACTIVE | `POST /api/v1/model-versions/{id}/rollback`：当前版本 ACTIVE → ROLLED_BACK，上一版本 RETIRED → ACTIVE（数据库只允许曾经激活过的版本恢复） |
| 每次状态变化都有审计 | 只追加表 `risk_model_version_transitions`：from_status、to_status、actor（user_id 与名称）、recorded_at、reason、status_sequence、artifact_hash。所有状态变化都经过 SQL 函数 `transition_model_version`，由它同时写入审计行。延迟约束触发器拒绝任何没有对应审计行的状态写入（包括直接 UPDATE）。审计行不可修改或删除 |
| CANDIDATE → ACTIVE 的额外内容 | 审计行必须带 evaluation_metrics（数据库约束），并记录晋升原因与 artifact_hash。人工晋升时还会记下实测的工件哈希（`artifact_sha256_verified`） |
| 替换原有激活逻辑 | 校准 worker 对每个新工件自动注册版本，再以独立留出集闸门评估（DRAFT → EVALUATING → CANDIDATE / REJECTED）。自动激活、被替代、失效、回滚、下线都通过触发器同步为版本的状态转换。新增开关 `CALIBRATION_AUTO_PROMOTION`（默认 `true`，与原行为一致）；设为 `false` 时，通过评估的版本停在 CANDIDATE，等待审计员人工激活 |
| 推理 | `AdaptiveRiskInferenceService` 按 scope 查询 ACTIVE **版本**，经兼容矩阵判定，再用版本的路径与哈希加载工件。结果带 `model_version_id`。每条风险决策记录（`risk_decision_records.model_version_id`）与 `RISK_ASSESSMENT` 账本事件都记录所用或尝试使用的版本 |

## 2. 数据库变化（迁移 `20260926_0017`）

- 新表 `risk_model_versions`。约束：每个 scope 一个 ACTIVE、状态取值、哈希格式、评估三字段同时为空或同时有值、CANDIDATE/ACTIVE 必须评估通过、ACTIVE 必须有激活时间与原因、每个工件最多注册一次、(model_id, version) 唯一。
- 新表 `risk_model_version_transitions`：只追加，(version, status_sequence) 唯一，转入 ACTIVE 必须带评估指标。
- `risk_decision_records` 新增 `model_version_id`（可空）。已有决策记录不回填，保持原字节不变。
- 新增 SQL 函数：
  - `transition_model_version`：唯一的改状态入口，同时写审计行
  - `guard_model_version_status`：检查转换是否合法；标识、工件与评估不可修改
  - `require_model_version_transition_audit`：延迟触发器
  - `reject_active_model_version_delete`
  - `sync_model_version_from_artifact`：把工件的部署变化映射为版本转换
  - `governance_actor_label`
- 账本事件类型新增 `CALIBRATION_MANUALLY_ACTIVATED`；`activation_mode` 新增 `manual_promotion`。
- 回填只追加：每个已发布的工件生成一个版本，状态沿用 Phase 2 的投影，审计行 reason 为 `migration_backfill`。曾激活过的版本把原激活记录作为评估证据（`legacy_activation_record`）。
- 降级：只有回填数据时允许降级，一旦存在新的版本历史就拒绝。已实测 升级 → 降级 → 再升级。

## 3. API 变化

| 方法 | 路径 | 角色 |
|---|---|---|
| POST | `/api/v1/model-versions`（注册未登记的工件并立即评估） | 审计员 |
| GET | `/api/v1/model-versions?scope=`（列表，含 active_by_scope、candidates、未注册工件） | 审计员、风险经理、融资方 |
| GET | `/api/v1/model-versions/{id}`（详情，含实时工件哈希校验与完整转换历史） | 同上 |
| POST | `/api/v1/model-versions/{id}/activate`（`reason` 8–500 字） | 审计员 |
| POST | `/api/v1/model-versions/{id}/rollback`（`reason_code`） | 审计员 |
| GET | `/api/v1/model-versions/activation-history?scope=` | 审计员、风险经理、融资方 |

`GET /api/v1/applications/{id}/risk-decisions` 新增 `model_version_id`。Phase 2 已有的 `/api/v1/model-registry*` 与 `/api/v1/calibration-deployments/rollback` 保持不变、继续可用。

## 4. 页面变化（模型中心）

模型中心改为以版本为中心：

- 各 scope 当前 ACTIVE 版本卡片：版本、激活时间、工件哈希、晋升原因。
- 待激活候选列表：版本、样本数、留出集 Brier 前 → 后、工件哈希。
- 激活历史：所有进出 ACTIVE 的转换，含操作人、原因、哈希、时间。
- 版本注册表：版本、类型、scope、状态、数据快照、n、指标、工件哈希、创建时间、创建者、激活时间。
- 版本详情：实时工件哈希校验、评估结论、完整转换历史（序号、从 → 到、原因、操作人、是否带评估指标）。
  - 审计员操作：CANDIDATE 显示“激活”并必须填写晋升原因；有上一版本的 ACTIVE 显示“回滚”；非 ACTIVE 版本显示“下线”；存在未注册工件时可一键注册。

实机截图（Docker 栈，真实 PostgreSQL，数据从 Phase 2 卷迁移而来）：[docs/product/phase2_1/model-center-versions.png](docs/product/phase2_1/model-center-versions.png)。

## 5. 测试结果

新增 `tests/test_model_version_registry.py`（16 个测试），覆盖要求的 8 类场景：

| # | 场景 | 测试 |
|---|---|---|
| 1 | 模型注册 | `test_trained_artifact_is_registered_as_a_full_model_version`、`test_manual_registration_evaluates_an_unregistered_artifact`、`test_migration_backfills_existing_artifacts_as_versions` |
| 2 | 状态流转 | `test_domain_transitions_equal_the_frozen_database_list`、`test_lifecycle_walks_draft_evaluating_candidate_active`、`test_database_rejects_illegal_and_unaudited_transitions` |
| 3 | ACTIVE 唯一 | `test_version_switch_keeps_exactly_one_active_per_scope` |
| 4 | 激活时哈希校验 | `test_candidate_waits_for_auditor_promotion_with_verified_hash`、`test_activation_refuses_a_tampered_artifact`、`test_activation_requires_a_passed_evaluation` |
| 5 | 回滚 | `test_rollback_restores_the_previous_active_version` |
| 6 | 删除保护 | `test_active_version_cannot_be_deleted_and_history_is_immutable` |
| 7 | 审计记录 | `test_every_status_change_records_from_to_actor_time_and_reason`、`test_risk_decision_records_the_model_version_used` |
| 8 | 重启后状态恢复 | `test_registry_state_survives_a_restart`，以及下方的 Docker 重启实测 |

另有 API 与角色权限测试 `test_model_version_api`，以及 UI 契约测试的扩展。

编写测试时发现并修复了一个缺陷：原 ACTIVE 约束写作 `evaluation_passed AND ...`，当 `evaluation_passed` 为 NULL 时整个检查结果是 NULL，PostgreSQL 会放行，于是未评估的版本也能激活。已改为 `IS TRUE`，并新增 CANDIDATE/ACTIVE 的评估约束。

为适配新的记录源，改动了 3 个既有测试，均不削弱原有意图：

- `test_adaptive_risk.py` 两个推理测试与 `test_temporal_calibration.py` 一个测试的替身对象，从“ACTIVE run 仓库”改为“ACTIVE 版本注册表”。断言不变，另加对 `model_version_id` 的断言。
- `test_outcome_service.py` 中直接插入 ACTIVE run 的夹具，现在同时插入对应的 ACTIVE 版本。

| 检查 | 结果 |
|---|---|
| `ruff check .` | 通过 |
| `mypy` | 70 个源文件无问题 |
| 应用全量测试（真实 PostgreSQL 17） | 840 passed、12 skipped、0 failed |
| Docker 重启验证 | 通过，见下 |
| CI | 见 PR |

Docker 验证（本地 8010）的步骤：

1. 用新镜像启动，保留 Phase 2 的数据卷（其中已有 1 个 ACTIVE 模型和 1 条决策记录）。迁移在容器内升级到 `20260926_0017`，回填出 `calibration:controlled_demo@v1`（ACTIVE，工件哈希一致）。
2. 新做一笔风险评估：分数与迁移前相同（0.2812），决策记录带 `model_version_id`；迁移前的旧决策记录保持 `model_version_id = null`，没有改写。
3. `docker compose down`（保留卷）后再次 `up`。

以下内容在重启前后逐字节一致：版本列表、ACTIVE 版本、工件哈希校验、转换历史、激活历史、决策记录、账本校验（有效）。

## 6. 当前限制

- 注册表只管理 Platt 校准层；TGNN 研究模型仍按 Phase 2 在 `/api/v1/model-registry` 中只读展示，不进入版本状态机。
- 一个工件对应一个版本：重新训练产生新工件和新版本，没有“同一版本换工件”的操作。
- 默认仍自动晋升（`CALIBRATION_AUTO_PROMOTION=true`），以保持既有行为与测试；需要人工审批的环境要显式关闭。
- 回滚只能回到紧邻的上一个 ACTIVE 版本（`previous_active_version_id` 链），不能任意跳到更早的版本；RETIRED 的历史版本不能直接重新激活，只能重新训练。
- 人工晋升仍要求候选的样本数严格多于当前 ACTIVE 版本，与自动晋升的规则一致。
- 回填版本的评估证据来自迁移前的激活记录（`legacy_activation_record`），没有追溯重跑评估闸门。
- 回滚时审计行的原因是固定的 `manual_rollback`；操作人填写的原因代码记在恢复版本的激活原因与账本事件中。

## 7. Commit

- 实现提交：`7f9612d`（`claude/funny-ptolemy-32pkcy`，随 PR #12 一并合并）
