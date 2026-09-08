# Fabric 开发网络阶段验收（2026-09-07）

后续状态（2026-09-08）：本文保留 9 月 7 日现场结果。实机 gateway 停止/恢复现已完成，见 [断连验收](fabric-outage-acceptance-2026-09-08.md)。版本字段修复与 CI 见 [版本契约验收](version-contract-acceptance-2026-09-08.md)；不代表旧 anchor 已回填。

## 范围与结论

`2026_REIMPLEMENTATION` 工程验收：真实 Fabric 开发网络的部署、五角色业务流程、outbox 上链、gateway / peer CLI 交叉读回、重复请求与冲突拒绝通过。**不是高级模式全量验收通过**：实机断连恢复未执行，电路版本显式上链存在缺口。

本阶段基于 ZKP PR #3（c9e4f3dedade68aac398698cb9fc60bb43e880ea）；其 application-ci 34100732766 已 completed / success。本报告不替代硕士论文历史实验材料，不修改论文、原始结果或历史账本。

## RED → 最小修复 → GREEN

首次直接执行完整 compose up 时，Compose 提前创建 gateway 的 credential bind-source 目录，包括本应为文件的 tls/ca.crt 路径；bootstrap 因身份生成目录不完整而正确 fail closed。

新增 `tests/test_release_contract.py::test_fabric_launcher_bootstraps_before_creating_credential_mounts`，修复前因缺少 bootstrap-first 命令而失败。`start-fabric-demo.cmd` 仅增加先执行 `run --rm --no-deps bootstrap` 及失败退出，再执行原有 `up --build -d`；未放宽身份完整性检查。

现场确认旧 output/fabric 只有空目录、没有文件或 reparse points 后，整体移至 output/fabric-empty-prebootstrap-20260907 留存，可恢复；未删除密钥或账本。随后按新顺序完成真实 bootstrap、up、chaincode deployment。未直接整段运行 Windows launcher，以免它为已经运行的基础 Demo 再创建另一 compose 项目并争用 8010 端口；因此本次是启动顺序实测，不是全新机器双击安装验收。

## 环境与链上证据

- 基础 Demo compose 项目 daibm-scf-defense；PostgreSQL 与应用持续 healthy，http://127.0.0.1:8010/ 的只读 defense_preflight 通过。
- Fabric 2.5.16，项目 daibm-fabric-demo，单 Org、单 peer、单 orderer 的 Raft 开发网络。
- channel：scfchannel；chaincode：audit-anchor，version 1.0，sequence 1。
- package ID：`audit-anchor_1:3ae06cfe4ccc431c9cf914051e8cdcd083c1d83060e71cadf4af23d2a8b855a5`。
- definition commit 交易：`95eff53796a910e40e166b6e9235a229ba9470ea1c8a3ab139515c1231687ed1`，部署命令收到 VALID。
- gateway /health 返回 200、fabric ready。实际 Node 服务进程 uid 1000；docker-init PID 1 为 root，不能把 docker exec 默认 uid 当成 Node 服务 uid。
- 验收时 32 条 outbox 均为 anchored，链高 35；这是本地现场快照，不是吞吐量、容错或业务收益指标。
- peer CLI 获取并解码 block 34，交易 ID：`eac045c06c489e591acddfac8d5d257cf1b2ffec3a20175a3c01b3253edf1a80`。原始 block 留在 ignored output/fabric/acceptance-block-34.pb；未将其单独推断为以下特定 anchor 的交易映射。

五角色浏览器验收通过；使用已安装 Edge 的独立临时上下文运行既有脚本，移动端面板宽度 390px 校验通过。新申请完成后，由 auditor dispatch outbox，目标 anchor 在 UI 中为 anchored，并由 gateway 和 peer CLI 完整对象交叉比较一致：

- anchor：`ae14db1d-052a-5a2c-a742-f11856b43b3a`。
- subject：`8fbd82a5-37a8-4238-be9c-9642eac5ed46`。
- eventHash：`73dba0bc6f892938ec62f4989e40b83c47ca985201162e9e8d8816d13a15e8b4`，与应用 outbox 一致。

对该实际 anchor 再提交相同内容，返回 200 且对象不变；同一 ID 改为不同 eventHash，返回 409；再次读回原值未变。测试前后链高均为 35。这验证的是该请求路径的幂等/冲突行为，不是分布式故障容忍。

## ZKP → 审计 → Fabric 交叉证据

前一阶段真实证明的 anchor `ee009eb0-38e1-5ebc-9e4a-ce330691b9c2`，gateway 与 peer CLI 读回完全一致，且以下值与上一阶段 PostgreSQL 持久化证据一致：

- eventHash：`496c06cb6ab726726fe5f0466f2d1b87b287545ca0b305894d1e3b980986a116`。
- proofSha256：`1acc659b22eee3c8d47b06cc615648f280371bc6347f350015e363a67f949d07`。
- subject：`7c177ac5-52ec-4828-97f4-ca76ce94ea1e`。

证明的密码学验证见 [ZKP 阶段报告](zkp-container-acceptance-2026-09-07.md)。这里验证的是摘要锚定及一致读回，不声称 Fabric chaincode 执行了 Groth16 verifier。

## 测试与复查入口

- `tests/test_anchor_dispatch.py tests/test_anchor_outbox.py tests/test_anchor_api.py tests/test_release_contract.py`：38 passed，真实 PostgreSQL 集成测试保留。
- `advanced/fabric/network/test/network-scripts.test.sh`：6 项通过，覆盖混合身份拒绝、hash-locked 身份、已有 channel fetch/join、package ID 精确匹配、升级 sequence、幂等部署。这些 shell 回归使用模拟 CLI，不能等同于实机故障验收。
- Ruff 检查通过；`scripts/defense_preflight.py` 的健康、UI、文档和五角色只读检查通过。
- 主要复查入口：`scripts/fabric_browser_acceptance.py`（会创建合成业务数据并 dispatch），`scripts/defense_preflight.py`（只读），gateway `GET /anchors/{id}`，peer `chaincode query -C scfchannel -n audit-anchor` / `ReadAnchor`。
- 浏览器截图 output/fabric-five-role-acceptance.png 来自五角色流程阶段，早于最终 outbox dispatch；不将它当成最终全量 anchored 的截图证据。

## 未完成与安全边界

1. **实机断连 / 恢复：待验收。** 暂停 gateway 后验证 outbox retry 的命令被当前执行策略在执行前拦截，未停止容器、未创建该次故障演练数据。没有换工具绕过。模拟异常测试不能替代此项；不声明真实故障恢复已通过。
2. **P1：显式 circuitVersion 缺失。** `app/repositories/anchors.py::_safe_version` 及 gateway / chaincode 版本格式不接受 `@`，导致 `invoice_limit@1` 在 outbox 为 null，链上对象无 circuitVersion。事件 hash 仍绑定原始事件内容，proofSha256 已正确锚定，但这不等于链上显式版本字段已完整。TODO：独立小步统一版本 contract、先写回归测试，明确仅新事件修复；不悄悄回写不可变历史 anchor。
3. 本地 peer 按现有开发配置挂载 Docker socket，具有宿主容器控制风险；未验收生产部署隔离。当前网络不代表 20 validators / 5 data centers，不代表 PoA+、跨组织容错或生产安全性。
4. PostgreSQL 仍是业务状态和 tamper-evident audit ledger；Fabric 为可选摘要锚定，不将两者混称。账本没有清空、篡改或重建，原硕士论文未改。

下一步应在本阶段提交后，独立处理版本 contract 缺口；真实断连演练需在执行机制允许的条件下另行安排，完成前保持待验收状态。
