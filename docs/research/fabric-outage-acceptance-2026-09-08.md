# Fabric gateway 断连恢复实机验收（2026-09-08）

## 结果

2026-09-08 02:12:44 UTC，真实停止本地 gateway 容器后，业务应用将唯一待发送 anchor 保留为 pending，记录 FABRIC_UNAVAILABLE；恢复 gateway 后，按正常重试时间再次 dispatch，同一 anchor 变为 anchored。gateway 与 Fabric peer CLI 读回一致。相同内容重复提交返回 200，未新增区块。此结果属于 2026_REIMPLEMENTATION 开发网络工程验收。

| 检查 | 实测 |
| --- | --- |
| gateway 停止 | Docker inspect State.Running = false |
| 故障时 dispatch | claimed=1, anchored=0, retryable=1, permanent_failed=0 |
| 故障时 outbox | pending；FABRIC_UNAVAILABLE；attempt_count=1；event_hash 不变 |
| 故障时基础服务 | /api/health ledger.valid=true；Fabric 链高仍为 35 |
| 恢复后 dispatch | claimed=1, anchored=1, retryable=0, permanent_failed=0 |
| 恢复后 outbox | anchored；last_error_code=null；attempt_count=2 |
| 上链与重复请求 | 链高 35 → 36；相同 envelope 重提返回 200；链高仍为 36 |
| 队列再扫描 | claimed=0 |
| 收尾 | gateway ready；应用及 PostgreSQL healthy；五角色只读 defense_preflight 通过 |

唯一新增的合成草稿保留为验收证据：

- subject_id：`d1d7d8b3-49be-40ac-a41a-ba5752c65ae9`
- anchor_id：`40588df8-ff95-588c-9aa0-3fac09d8bbb5`
- event_hash：`81c2e6c2c17217a651efaa597c5f7f19a06cc0d7416a3ee008d9ca6357414f43`
- contract_number：`SIM-OUTAGE-89e46183d184`

首次脚本调用在创建草稿后因误用响应键 id（实际为 request_id）停止，尚未停止 gateway 或 dispatch。修正后使用 --resume-anchor 复用同一未发送记录，完成上述实测；没有删除或重复创建草稿。

## 工件与运行方式

`scripts/fabric_outage_acceptance.py` 是显式 opt-in 的本地实机脚本，使用 8010 的业务 API、内部 gateway HTTP 和真实 peer CLI。它会创建合成草稿、暂时停止 daibm-fabric-anchor-gateway，并在 finally 中启动 gateway、检查 readiness；没有数据库写回或账本重置代码。异常时若恢复失败，脚本会报错，不应将报错解释为通过。

```sh
python scripts/fabric_outage_acceptance.py --allow-local-gateway-outage
```

先运行基础 Demo 和 Fabric；确保无其他待发送记录、没有并发写入者。脚本拒绝不干净的 outbox（以及达到 200 条检查上限的环境）。`--resume-anchor` 只接受 attempt_count=0 的 pending anchor，且所属草稿的合同号必须以 SIM-OUTAGE- 开头。脚本故意不加入普通 CI，避免停止其他工作负载；本轮 Ruff 检查和真实脚本执行通过。

本次恢复并使用原有部署容器，未部署 PR6 的版本字段修复。运行镜像：

- application：`sha256:49817ead0e01d10ed8158b6e50961b5fabd1149766dbab38fa9c19b0d397d1d0`
- gateway：`sha256:22e507e54610cfccc3d18c34774b6526d7250a4a6ccf2bb96fcc4e95a2dda43a`
- Docker Server 29.7.2；Fabric 2.5.16、scfchannel、audit-anchor 原部署。

因此该测试验证既有 outbox 重试路径；不能替代 PR6 新版本字段的升级后实机验收，也不涉及重新生成 ZKP 证明。

## Docker 启动故障及恢复

本次起点为 Docker Desktop 未启动。日志显示 run/sailor-ingest.sock 和 docker-secrets-engine/engine.sock 两处旧 AF_UNIX reparse points 阻止 backend 启动。单文件移动失败；检查目录仅含零字节运行时 socket 且 Docker 进程已退出后，保留目录并重新生成运行时目录，Linux engine 恢复。

保留目录位于本机 LocalAppData：Docker/run-preserved-20260908、Docker/run-preserved-20260908-retry2，以及 docker-secrets-engine-preserved-20260908、docker-secrets-engine-preserved-20260908-retry2。其中包含旧 socket 或中途启动遗留目录；未删除。镜像、卷和项目数据保持原样。相关上游故障报告：[Docker desktop-feedback #460](https://github.com/docker/desktop-feedback/issues/460)。这是现场恢复措施，不能保证以后不再发生。

## 证据范围

本阶段关闭“真实 gateway 停止后 outbox 重试并恢复上链”这一项待验收。仍不代表 peer/orderer 故障、网络分区、多个并发故障、生产容错或 PoA+ 验收。历史报告保留原日期事实，并链接本报告作为后续进展。
