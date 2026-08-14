# DAIBM-SCF Minimal MVP

这是一个基于硕士论文 DAIBM-SCF 架构提炼出的最小、可运行、可解释原型。它验证的是业务闭环，不宣称复现论文中的全部算法或生产级基础设施。

## MVP 展示什么

1. 企业提交供应链融资请求。
2. 可解释风险基线计算风险分数并给出主要风险贡献。
3. 决策引擎输出 `approved`、`manual_review` 或 `rejected`。
4. 风险结果触发控制动作，并回写到防篡改哈希链账本。
5. 系统可以重新计算整条哈希链，验证审计记录是否被修改。
6. 单页界面展示申请、风险、决策和账本状态。

## 有意不实现的内容

- 当前账本是 SQLite 上的哈希链审计日志，不是 Hyperledger Fabric 网络。
- 当前风险模型是透明的加权逻辑基线，不是 TGNN、LSTM 或 XGBoost。
- 当前没有 PoA+、零知识证明、真实银行接口或生产部署。
- 演示数据均为合成场景，不代表真实融资审批效果。

这些边界是刻意设计的：第一版只验证“数据进入 → 风险评估 → 融资决策 → 控制动作回写”的双向闭环。论文组件与 MVP 的对应关系见 [docs/thesis-to-mvp.md](docs/thesis-to-mvp.md)。

## 快速启动

要求 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

打开 <http://127.0.0.1:8000>，点击“载入演示场景”。

也可以使用 Docker：

```bash
docker compose up --build
```

## API

- `GET /api/health`：健康状态与账本完整性。
- `GET /api/dashboard`：MVP 汇总指标。
- `POST /api/requests`：创建融资请求并执行闭环。
- `GET /api/requests`：查看最近申请。
- `GET /api/ledger`：查看审计事件。
- `GET /api/ledger/verify`：验证哈希链。
- `POST /api/demo/seed`：写入三个合成演示场景。

交互式 API 文档位于 <http://127.0.0.1:8000/docs>。

## 测试

```powershell
pytest -q
```

测试覆盖风险排序、账本篡改检测和 API 闭环。

## 数据与隐私

运行数据默认写入 `data/daibm_scf.db`，该文件已被 Git 忽略。仓库不包含硕士论文原文、真实企业数据或个人凭据，便于未来在决定公开代码时进行隐私审查。

