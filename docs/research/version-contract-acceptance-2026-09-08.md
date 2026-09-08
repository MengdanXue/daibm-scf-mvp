# 版本字段契约阶段验收（2026-09-08）

## 结论

本阶段修复 `invoice_limit@1` 在应用、PostgreSQL、Fabric gateway 与 chaincode 之间不一致的问题。版本 token 现在允许一个明确的 `@` 修订分隔符，并保持总长度不超过 64；旧 anchor 不被回写。该阶段是 `2026_REIMPLEMENTATION` 的小步修复，不改变硕士论文历史结果。

## RED → 修复 → GREEN

RED 测试先用同一组 JSON vectors 验证：`invoice_limit@1`、`tgnn@2.1` 等应保留；空串、重复 `@`、非法后缀、空白、路径分隔符、非字符串和超过 64 字符应拒绝。修复覆盖四个边界：

- `app/repositories/anchors.py::_safe_version`；
- `AnchorOutboxModel` 的三个 PostgreSQL check constraint；
- `advanced/gateway/src/anchor.js`；
- `advanced/fabric/chaincode/lib/anchor-contract.js`。

新增 Alembic `20260907_0011`，从旧约束增量升级。降级前逐列检查是否已有 `@` 修订值；若有则拒绝降级，避免产生不可验证的数据。迁移只改变约束，不更新任何既有 row。

## 测试证据

- Python vectors：31 个非数据库用例通过；本机随后运行数据库用例时 Docker Desktop 引擎在当前执行进程不可访问，因此 5 个 PostgreSQL 用例未能在本机启动，不将其写成通过。
- Node gateway version-contract：3 passed。
- Python/JavaScript syntax checks：通过。
- Fabric chaincode 和完整 PostgreSQL 迁移测试由 CI 执行；CI 结果以远端 workflow 为准。

远端 workflow `application-ci` run `34142767620` 已全部成功：application-tests（含真实 PostgreSQL）、research-tests、advanced-tests、static-analysis 均为 `success`。

## 追溯边界

之前 Fabric 验收报告中记录的 `circuitVersion` 缺失是**历史现场快照**，不回写原 anchor。修复后，新建事件可把 `invoice_limit@1` 保留在 outbox envelope 和链上 anchor；历史 anchor 仍保持原字节内容。原证明 hash、event hash 和 Fabric 幂等语义不变。

此修复不声称重新运行 ZKP 证明、Fabric 故障恢复、20 validators / 5 data centers、PoA+ 或生产级安全。实机 gateway 断连恢复仍按前一阶段报告保持待验收。
