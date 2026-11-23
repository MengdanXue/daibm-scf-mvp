# ZKP 容器阶段验收（2026-09-07）

## 范围与结论

当前演示版 invoice_limit@1 的真实容器、HTTP 证明、数据库留痕及离线验证通过。本记录为 `2026_REIMPLEMENTATION` 工程验收，不是硕士论文历史实验复现，也不证明生产级隐私、安全性或 Fabric 已上链。

基础：main 202bbd8，加 PR #2 / 8d5d079 的两个已验证测试文件。PR #2 的 application-ci 34099235298 已全部成功。

## RED → 最小修复 → GREEN

1. 原镜像未复制 circuits/invoice_limit.circom；真实 preflight 返回 HTTP 500，镜像内 manifest 验证报 ENOENT。新增容器验收脚本在旧镜像中同样失败。
2. Windows checkout 将电路源文件与 package-lock.json 转为 CRLF，导致固定 manifest 的源文件 SHA-256 校验失败。
3. Dockerfile 仅增加 circuits 的 COPY；.gitattributes 对两类被哈希的源文件固定 LF。没有修改电路内容、manifest、任何证明工件、密钥或预期哈希。
4. 增加 scripts/container-acceptance.mjs，并在 CI advanced-tests 中构建真实镜像、以只读文件系统及 capability 限制运行该验收。不能用源码单元测试替代打包后镜像检查。

## 已执行与证据

- `node advanced/zkp/scripts/verify-artifacts.mjs`：原始 manifest 全部验证通过。
- 真实只读镜像：HTTP 证明通过、修改 public signal 被拒绝、修改 proof 坐标被拒绝、假声明 3 > 2 返回 422 / STATEMENT_FALSE。
- 运行中的 sidecar preflight：真实 Groth16 pairing 验证通过，不仅检查 /health。
- 镜像内既有 Node tests：18 passed、0 skipped。测试需临时 /tmp，用于生成并删除测试自有篡改副本；原始工件未改。
- 本机真实 PostgreSQL 回滚及策略 tests：11 passed、1 deselected。包括 required 模式无 prover/不可用时确认状态、版本和事件数不变，以及 optional 模式显式 fallback。
- 本轮未运行依赖 Windows 本地 Node 包安装的 opt-in `test_real_proof_survives_http_workflow_and_audit`；同等关键证据另外通过真实运行系统及独立离线验证取得，不将其写成该 pytest 用例通过。
- 同一五角色浏览器脚本，Edge 独立临时上下文，真实 sidecar 已接入：通过，申请 7c177ac5-52ec-4828-97f4-ca76ce94ea1e 已完成审计。
- 从 PostgreSQL 原样取出 TRADE_CONFIRMED 事件 21 的证明，在 Node/snarkjs 中离线验证成功；重算 proof SHA-256 与持久化值相同；更改公开上限后验证失败；事件无 proof_fallback_code。
- 事件 hash：`496c06cb6ab726726fe5f0466f2d1b87b287545ca0b305894d1e3b980986a116`。
- 证明 hash：`1acc659b22eee3c8d47b06cc615648f280371bc6347f350015e363a67f949d07`。
- 公开上限：150000000 minor units。数据为合成验收申请，不涉及真实企业。
- 运行后 defense_preflight 通过，原有数据库与账本未清空。

## 可复查命令

```sh
docker build -t daibm-zkp-ci:local advanced/zkp
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true daibm-zkp-ci:local node scripts/container-acceptance.mjs
docker exec daibm-invoice-limit-prover node src/preflight.mjs http://127.0.0.1:8091
```

镜像内 Node v24.20.0，snarkjs 0.7.6；基础镜像 node:24-bookworm-slim digest `sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e`。sidecar 使用 uid 1000，read_only、cap_drop ALL、no-new-privileges，未发布宿主机端口。

## 边界与下一阶段

- 固定演示 trusted setup；无生产 ceremony 证据。
- 应用内部信任 prover，只做结构和公开上限检查；本次独立密码学验证属于验收，不意味着应用新增独立 verifier。
- HTTP prover 会接收明文金额；ZKP 不等于 prover 看不到输入。
- required 回滚是在隔离测试数据库测试，不停止用户正在使用的业务应用来制造故障。
- Fabric 尚未启动/验收。本阶段先提交，再进入独立 Fabric 阶段。
- /health 的 ready 不应单独作为证明能力的验收标准；使用带真实证明验证的 preflight。
