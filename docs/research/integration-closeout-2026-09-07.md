# 生命周期、纠正链与校准范围收口验收

状态：进行中；本文只将已执行的检查标为通过。最终提交、完整回归、浏览器验收、原卷升级重建与最终 Actions 尚待补齐。

工作树：`D:/毕业论文/daibm-scf-mvp/.worktrees/lifecycle-corrections-scope`；分支：`integration/lifecycle-scope-20260907`；私有草稿 PR：[PR 5](https://github.com/MengdanXue/daibm-scf-mvp/pull/5)。本轮起点 `61ea85019f470f38f0ec0cf53fa8fbe7b20cd5bb`。保留既有工作树、历史提交及未提交实现，未改主检出目录、未直接合并 main。

## 已批准的生命周期语义

- DEFAULTED 可以保留未偿本金，允许追偿、重组和核销。
- 重组只替换当前未偿本金的未来计划。旧计划、付款、违约和重组记录永久保留；重复重组和新计划再次违约各有独立血缘。
- `default_event` 保留第一次违约，`default_history` 展示完整的计划版本及违约历史。
- 正常还清后关闭显示 `NORMAL_SETTLED`；核销关闭显示 `WRITTEN_OFF`。成功完成重组计划不会抹去此前违约。
- `principal = outstanding_balance + recovered_amount + written_off_amount`，`realized_loss = written_off_amount`。`recovered_amount` 包含违约前后全部已确认本金现金；待审、拒绝付款和被替代计划不重复计入。

## 数据、纠正与训练资格

原 outcome 不允许 UPDATE/DELETE。通过认证审计员追加 EXCLUDE/REINSTATE，记录 actor、时间、原因、证据摘要及不可变纠正链。训练只消费各 outcome 当前有效的训练资格；EXCLUDE 使包含该样本的 active 校准失效。REINSTATE 必须重新验证原事实，不能用来重标有矛盾的结果。本轮不宣称支持任意替代事实的 superseding result。

## 独立验证与晋升边界

按完整时间组选择最接近 70/30 的训练/验证分界；仅依时间和数量选择，同距时取较早分界。训练时间严格早于验证时间，时间组不跨区。最低 20 条训练、10 条验证；训练至少 5 个不同原始分数，跨度至少 0.05；两区正负类各至少 2 条。

仅训练区拟合，验证区独立计算 Brier/log-loss。两指标都不得超过 `1e-12` 容差退化，至少一项改善超过该容差才允许晋升。不得晋升后再用全量重拟合。旧 v2/v3 artifact 保留可读，但不能作为新的晋升捷径；训练集拟合值不构成独立提升证据。

| 数据/评估范围 | 使用限制 |
|---|---|
| `controlled_demo` | 仅同范围演示推理及部署 |
| `external_verified` | 仅同范围；仍为人工声明，不是外部或密码学认证 |
| `mixed` | 可记录混合候选信息，禁止自动晋升真实用途 |
| 跨范围 | 推理回退或部署/回滚拒绝，并保留审计血缘 |

## 原持久卷现场与备份

本轮实测 Docker Desktop 4.88.1 / Engine 29.7.2 已可用，旧 engine pipe 阻塞已解除。两个原容器均为早前停止状态；先只读备份，再正常启动原 PostgreSQL。原应用尚未启动。未 reset、重装、清卷、注销 WSL 或删除 VHD。

原卷：`daibm-scf-mvp_postgres-data`、`daibm-scf-mvp_calibration-artifacts`。备份位置为工作树内 `.superpowers/sdd/2026-09-07-integration-closeout/volume-backups-20260907/`，不提交含数据的备份到 Git。保留该目录。

| 备份 | SHA-256 |
|---|---|
| PostgreSQL 冷备 `postgres-original-cold.tar.gz` | `02134597e413c18219a3a116d4ed8868b258de9dcafbb4de53ed8f28d36b1457` |
| calibration 原卷 `calibration-original.tar.gz` | `1817f0df39ba1051421cafe4d7c4d87809d82764421f9e74612e1bb99b49d319` |

另保存原库 custom-format logical dump、schema-only dump、逐表原字段摘要及 15 个 artifact 文件摘要。冷备已恢复到独立克隆卷，PostgreSQL 17.11 完成普通 WAL 恢复。克隆与原库升级前 22 张表逐行摘要全部一致，证明备份可恢复。

实际原库为 `20260824_0009`：23 笔融资（21 已关闭、2 已放款）、42 条已确认付款、20 条 outcome、20 次校准、602 条 ledger、483 条 anchor outbox。23 笔本金守恒检查通过。所有 20 个历史 outcome 原始风险分数均为 `0.5595`，只有一个不同值，不能将旧拟合数字视为风险区分能力提升。

## 升级副本及原样本预演

独立克隆正常执行 `0009→0010→0011→0012→0013`。投影原有字段比较，22 张表中除预期 Alembic revision 外，全部原行摘要不变。未通过 stamp 或修改金融历史绕过迁移检查。

| 原矛盾 outcome | 对应融资 |
|---|---|
| `ff3d5fb5-ca97-4fe4-a265-97273dd943fb` | `8e690430-76a2-454f-88cf-c93c612a574b` |
| `0097e031-0f4d-46e0-9814-776364d8bf97` | `880db957-9f77-4d81-96d5-9117d47a09fe` |
| `3be2151d-8aa4-4e00-bb9f-ea2cb8ebcb08` | `d6ddf9ce-cd20-4ed6-8f92-e09d18e6b78c` |
| `ccc33d32-0720-4b94-b183-aca08c289871` | `137f4927-8ca7-459a-a5d1-eac9522d1527` |
| `6bc35f9e-914f-4e4b-884f-a23ec5c7fbb5` | `8ce2be1d-b8a2-4c55-a468-7734337f225d` |

每条原 outcome 声称违约、60 天逾期、损失 120000.00；对应融资本金 1200000.00 已全部现金收回，余额为零，没有 governed default/write-off 事件。遗留 `closure_reason=NULL` 未被擅自回填。

在 **原数据副本** 上通过 `auditor.demo` 的公开 API 追加 5 个 `HISTORICAL_FACTS_CONFLICT / EXCLUDE`。20 条 outcome 原行全部不变；原 ledger、anchor、付款、计划及动作行全部保留。训练资格降为 15 条，旧 controlled_demo active 校准因 `outcome_excluded` 失效。5 个异步 job 均 completed；去重后的候选因 `temporal_insufficient_partition_sizes` 被拒绝，15 条全为负类。15 个 artifact 文件摘要全部保持原值。

这些是实际原数据克隆的预演，不是新造 demo 样本。**正式原卷迁移、5 条纠正、容器重建及最终摘要对照仍待执行。**

## 本轮新增验证

Task 3 提交 `433de4b`：已知失败复现 14/14、生命周期/服务/API/outcome/job 定向回归 149/149、迁移/治理/集成 50/50、补充回归 6/6；Ruff 和 mypy（63 个源文件）通过。Task 3 独立复审代理两次因账户额度限制失败，未形成独立 verdict。

应用 reset 退役和 outcome UI/job 轮询提交为 `32e8735`、`6b3b830`。相关 API、认证、服务、schema、UI/release 合约测试 73 项通过；Ruff 通过。`scripts/outcome_browser_acceptance.py --help` 现在只显示帮助，不连接服务；浏览器流程已使用隔离的 8017 运行并完成登录、建案、支付、关闭和 actual-outcome POST，但旧验收脚本仍按即时 calibration run 断言，最终在候选状态断言失败。该失败已记录，不能宣称浏览器全流程通过。

最新分支已推送到私有 PR 5，Actions run `34129903036` 和 `34129898901` 在记录时仍为 in_progress，不能代替本地或持久卷验收。

## 最终验证待补记录

Task 3 实施/复审、Task 4 双语浏览器、Task 5 reset 退役/策略共享/真实性能门禁审计、完整回归与最终 Actions 的准确命令、数量、结果和最终提交将在执行后补入；当前不作全绿或全部完成声明。
