# Phase 2.2 报告：业务结果数据治理（Outcome Data Governance）

目标：风险模型只使用经过审核、版本化、可追溯的数据。本阶段在已有的设施生命周期、outcome、更正事件、模型注册表、校准流水线和审计历史上增量开发。没有新增基础设施，没有重构架构，也没有修改或删除任何历史 outcome、更正或审计记录。

```
Outcome ─► 审核（CREATED → REVIEWING → ELIGIBLE / REJECTED）
        ─► OutcomeEligibilityService（统一资格判断）
        ─► Training Dataset Snapshot（冻结、哈希、不可变）
        ─► Calibration Training ─► Evaluation ─► Candidate / Active Model Version
                                              ▲
风险决策 ─► 模型版本 ─► 数据快照 ─► 训练用 outcome（完整可追溯）
```

## 1. 实现内容

### 1.1 Outcome 生命周期治理

- 状态：CREATED → REVIEWING → ELIGIBLE → TRAINING_USED；任一阶段都可能进入 REJECTED。被拒绝的 outcome 可以经恢复更正重新进入 REVIEWING。
- 每条状态变化记录以下字段：
  - `from_status` 与 `to_status`
  - 操作人 `actor_user_id`，系统自动处理时为空
  - 角色 `actor_role`，由数据库按用户表填写，系统处理记为 `system`
  - 时间 `recorded_at`
  - 原因 `reason_code` 与 `comment`
  - 所用模型 `calibration_run_id`（仅 TRAINING_USED）

  `from_status` 与角色由数据库 BEFORE INSERT 触发器填写，应用无法伪造。
- 合法转换写在 `app/domain/outcome_governance.py`，迁移中冻结了一份副本，测试断言两者一致。数据库拒绝非法转换，审核事件不可修改、不可删除。
- 自动规则判定（通过或拒绝）的操作人记为 `system`，不再记为提交人。
- **人工审核**：`POST /api/v1/outcomes/{id}/review`，由审计员或风险经理执行，必须填写审核意见。
  - 通过（APPROVE）：只允许 REVIEWING 状态的结果，并且会重新检查规则。通过后自动排队一次校准任务（`outcome_reviewed`）。
  - 拒绝（REJECT）：只允许尚未用于训练的结果（REVIEWING / ELIGIBLE），必须给出原因，可选 `QUALITY_ANOMALY`、`DATA_QUALITY_INSUFFICIENT`、`BUSINESS_INCONSISTENT`、`BUSINESS_EXCEPTION`、`SCOPE_MISMATCH`。已经用于训练的结果只能走更正流程，因为更正会同时让包含它的激活模型失效。
  - 每次审核写入 `ACTUAL_OUTCOME_REVIEWED` 账本事件。
- **人工审核模式**：`OUTCOME_MANUAL_REVIEW=true`，默认 `false`，保持原有自动行为。开启后，规则通过的结果停在 REVIEWING，等待人工审核。

### 1.2 统一的训练资格入口 `OutcomeEligibilityService`

训练数据只能从 `app/services/outcome_eligibility.py` 获取。一条 outcome 纳入训练必须同时满足：

1. 是有效修订，没有被更正修订替代（`SUPERSEDED_BY_CORRECTION`）；
2. 最新更正不是排除（`EXCLUDED_BY_CORRECTION`）；
3. 审核状态为 ELIGIBLE / TRAINING_USED，否则记为 `REVIEW_PENDING`，或沿用审核的拒绝原因；
4. 数据库规则**实时复核**通过：
   - scope 匹配（`SCOPE_MISMATCH`）
   - 生命周期已结束（`BUSINESS_EXCEPTION`）
   - 数据完整、合理（`DATA_QUALITY_INSUFFICIENT`）
   - 业务一致（`BUSINESS_INCONSISTENT`）
   - 没有质量异常（人工审核的 `QUALITY_ANOMALY`）

每条被排除的 outcome 都附有排除原因。校准 worker 不再直接查询 `actual_outcomes`，一个静态契约测试保证这一点。部署前“数据集是否已变化”的检查也使用同一套规则。

### 1.3 更正与替代

保持不可变原则，并且由数据库强制：`actual_outcomes` 与 `outcome_corrections` 拒绝 UPDATE 和 DELETE。

- **原始记录 → 更正事件 → 有效记录**：更正修订会引用被更正的原记录（`supersedes_outcome_id`），并保存更正原因、说明、操作人、时间和证据哈希。排除/恢复类更正保存目标 outcome、原因、审计员、时间和证据哈希。
- 默认查询只返回有效修订。审计页面可以查看完整的更正链和逐条审核历史。

### 1.4 训练数据集快照

- 新表 `training_dataset_snapshots`，字段包括：
  - snapshot id、创建时间、scope
  - 纳入数与排除数
  - 各排除原因的计数
  - 资格策略版本（`outcome-eligibility-v1`）
  - dataset hash
  - 来源与创建者
  - 触发任务
- 新表 `training_dataset_snapshot_items`：记录每条 outcome 是否纳入、更正头、快照时的审核状态和排除原因。
- 快照与条目**不可修改、不可删除**。dataset hash 是对规范化清单（纳入 id + 更正头、排除 id + 原因）计算的 SHA-256，测试会复算。eligible 状态相同时复用同一个快照，不会悄悄产生新的数据集。
- `calibration_runs.dataset_snapshot_id` 与 `risk_model_versions.dataset_snapshot_id` 只能设置一次，之后不能改动（数据库强制）。**模型版本都关联训练它的快照。**

### 1.5 校准流水线接入治理

流程为：结果 → 资格判断 → 数据快照 → 训练（只读快照中纳入的行）→ 评估 → 候选模型版本。训练失败或数据不足时，run 会返回明确的 `failure_reason`，快照页面也会显示：

| 原因 | 来源 |
|---|---|
| `no_eligible_outcomes` | 没有可训练的结果（任务 failure_code） |
| `insufficient_samples` | 训练/验证时间分区样本不足 |
| `no_risk_variance` | 风险分数缺少差异（不同取值太少或跨度太小） |
| `insufficient_class_support` | 违约或未违约样本不足 |
| `scope_mismatch` | 数据集 scope 与部署 scope 不一致 |
| `no_holdout_improvement` | 留出集没有改善（Brier / log loss 退化或没有提升），不会报告为改善 |
| `validation_not_independent`、`not_newer_than_active`、`dataset_changed_before_deployment` 等 | 其他门槛 |

数据库中已存储的原始代码保持不变，`failure_reason` 是面向用户的稳定词表。

### 1.6 模型注册表联动与决策追溯

- 模型版本详情新增：训练数据快照 id、快照哈希、训练 outcome 数、排除数和排除原因。
- 风险决策记录新增模型版本标签与快照 id。新接口 `GET /api/v1/risk-decisions/{id}/lineage` 串起完整链路：**决策 → 模型版本 → 数据快照 → 训练用 outcome**。

## 2. 数据库变化（迁移 `20260927_0018`）

- `outcome_review_events` 新增 `from_status`、`actor_role`、`comment`。
  - 已有行这三列为 NULL，没有改写历史行。
  - 新增触发器 `audit_outcome_review_event`，负责填写 from/角色，并校验转换是否合法。
  - 拒绝原因新增 `QUALITY_ANOMALY`。
- `review_outcome()` 支持人工审核模式，并且不会重复写 REVIEWING。
- 新表 `training_dataset_snapshots`、`training_dataset_snapshot_items`，带不可变触发器。
- `calibration_runs`、`risk_model_versions` 新增 `dataset_snapshot_id`，只能设置一次。
- `calibration_jobs.trigger_type` 新增 `outcome_reviewed`；账本事件新增 `ACTUAL_OUTCOME_REVIEWED`。
- 回填只追加：每个已有训练成员关系的 run 会得到一份 `migration_backfill` 快照，内容与它的训练成员完全一致。迁移前没有记录排除信息，所以回填快照不声称任何排除。
- 降级：没有新治理历史时允许降级，否则拒绝。已实测 升级 → 降级 → 再升级。

## 3. API 变化

| 方法 | 路径 | 角色 |
|---|---|---|
| GET | `/api/v1/outcome-governance/overview`（总数、审核中、可训练、已拒绝、更正数、快照数） | 审计员、风险经理 |
| GET | `/api/v1/outcome-governance/review-queue?status=` | 审计员、风险经理 |
| POST | `/api/v1/outcomes/{id}/review`（APPROVE / REJECT） | 审计员、风险经理 |
| GET | `/api/v1/outcomes/{id}/review-history` | 审计员、风险经理 |
| GET | `/api/v1/outcome-governance/eligibility?scope=`（当前会进入训练的数据及排除原因） | 审计员、风险经理 |
| GET | `/api/v1/dataset-snapshots?scope=`、`/api/v1/dataset-snapshots/{id}` | 审计员、风险经理、融资方 |
| GET | `/api/v1/risk-decisions/{id}/lineage` | 审计员、风险经理、融资方 |

其他响应的新增字段：

- 校准 run：`failure_reason`、`dataset_snapshot_id`
- 模型版本：`dataset_snapshot_id`、`dataset_snapshot_hash`、`training_outcome_count`、`excluded_outcome_count`、`exclusion_reasons`
- 风险决策：`model_version_label`、`dataset_snapshot_id`

## 4. 页面变化

- **结果治理**（原“数据反馈”页）：
  - 总数、审核中、可训练、已拒绝、更正数卡片
  - 待审核队列，每行可填写意见后通过或拒绝（选择原因）
  - “进入训练的数据”：按 scope 显示纳入/排除、排除原因分布和数据集哈希
  - 结果列表，点击查看更正链与逐条审核历史（从 → 到、操作人、角色、意见、时间）
- **数据快照**（新页面，审计员、风险经理、融资方可见）：
  - 快照列表：id、创建时间、scope、纳入/排除数、hash、使用该快照的模型版本、训练结果
  - 快照详情：每条 outcome 是否纳入以及原因
- **模型中心**：版本详情显示训练数据快照（可点击打开）、训练结果数、排除数与原因。
- **风险决策详情**：显示所用模型版本与训练数据快照（可点击打开）。

实机截图：Docker 栈，真实 PostgreSQL；数据库从 Phase 2.1 的数据卷迁移而来；页面上的审核操作通过 Playwright 在 UI 中完成。

- [结果治理](docs/product/phase2_2/outcome-governance.png)
- [数据快照](docs/product/phase2_2/dataset-snapshots.png)
- [模型版本与快照](docs/product/phase2_2/model-version-snapshot.png)

## 5. 测试结果

新增 `tests/test_outcome_data_governance.py`（15 个测试函数、16 个用例），覆盖：

| 范围 | 测试 |
|---|---|
| 状态机一致 | `test_review_transitions_equal_the_frozen_database_list` |
| 每次变化都有 from/to/操作人/角色/时间/原因 | `test_every_review_event_records_from_to_operator_role_time_and_reason` |
| 非法转换、改写或删除历史被拒绝 | `test_database_rejects_illegal_review_transitions_and_history_edits` |
| 人工审核模式、审核通过后触发训练、角色权限 | `test_manual_review_mode_holds_outcomes_until_a_reviewer_approves` |
| 审核拒绝后不进入训练；已训练的结果需走更正 | `test_reviewer_rejection_keeps_the_outcome_out_of_training` |
| 资格规则与排除原因 | `test_exclusion_reason_order_covers_every_condition`、`test_eligibility_service_judges_each_outcome_with_a_reason` |
| 训练只能经过资格服务 | `test_training_reads_only_through_the_eligibility_service` |
| 快照：哈希、不可变、去重、run/版本关联 | `test_training_run_and_model_version_reference_an_immutable_hashed_snapshot` |
| 数据不足给出明确原因、不产生模型 | `test_insufficient_data_fails_with_an_explicit_reason`（样本不足、无风险差异两种情形）、`test_failure_reasons_never_report_an_improvement` |
| 决策追溯到训练数据 | `test_decision_lineage_reaches_the_training_outcomes` |
| 更正链与默认只返回有效修订 | `test_correction_chain_keeps_originals_and_defaults_to_effective` |
| 迁移回填 | `test_migration_backfills_a_snapshot_for_existing_training_runs` |
| API 与角色 | `test_outcome_governance_api` |

编写实现时发现并修复了一个缺陷：概览统计的查询没有显式的 FROM 表，相关子查询因此退化成单行，统计结果偏少。已改为显式 `select_from`，并由 API 测试覆盖。

改动了 5 处既有测试，均不削弱原有意图：

- 运行响应的 schema 与列清单加入新字段。
- UI 契约测试改为断言新的概览接口，并加入新页面。
- Phase 2.1 的一个迁移测试改用 SQL 插入，因为在旧版本数据库上，ORM 已经包含后续版本新增的列。
- 空数据集的任务 failure_code 从 `calibration_data_rejected` 细化为 `no_eligible_outcomes`，既有测试没有依赖旧值。

| 检查 | 结果 |
|---|---|
| `ruff check .` | 通过 |
| `mypy` | 73 个源文件无问题 |
| 应用全量测试（真实 PostgreSQL 17） | **856 passed、12 skipped、0 failed** |
| Docker 重建与重启验证 | 通过，见下 |
| CI | 见 PR |

Docker 验证（本地 8010）的步骤：

1. 用新镜像在 Phase 2.1 的数据卷上启动，迁移在容器内升级到 `20260927_0018`，为已有模型回填快照。
2. 追加 12 条自动审核的结果和 3 条人工审核模式的结果。容器内的 worker 用快照训练出 v2（纳入 52，排除 4：REVIEW_PENDING 3、BUSINESS_INCONSISTENT 1）。
3. 风险经理在 UI 中通过 1 条、拒绝 1 条（QUALITY_ANOMALY）。审核通过自动触发训练，得到 v3（纳入 53，排除 3）。
4. `docker compose down`（保留卷）后再次 `up`。

重启前后，以下内容逐项一致：快照、快照条目、模型版本及其快照关联、审核事件（含 from/角色/意见）。

## 6. 当前限制

- 默认仍为自动审核（`OUTCOME_MANUAL_REVIEW=false`），以保持既有行为；需要人工把关的环境要显式开启。开关以事务级设置传给数据库，只对经过 OutcomeService 写入的结果生效。
- 迁移前的审核事件没有 from/角色/意见字段（保持原样，不回写）。回填的快照只有纳入成员，没有排除信息。
- 规则复核仍是确定性的业务一致性检查，不核验外部证据的真实性；“质量异常”依赖人工审核判断。
- 已用于训练的结果不能通过审核拒绝，只能用更正（排除或替代）。更正会使包含它的激活模型失效并触发重训。
- 快照的排除清单覆盖该 scope 的全部结果，数据量很大时快照条目会同比增长（当前 MVP 规模可接受）。
- `failure_reason` 是对已存储原始代码的映射，未知代码原样返回。

## 7. Commit

- 实现提交：见 PR
