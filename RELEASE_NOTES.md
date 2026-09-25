# DAIBM-SCF v1.0.0 发布说明 / Release notes

**供应链金融 AI 风控平台原型 v1.0.0**：从融资申请、风险评估、模型决策、放款、还款、逾期、预警、任务处理、
结果反馈到模型治理的完整闭环，所有关键操作都可审计、可追溯，并按组织隔离数据。

## 亮点

- **完整的租户隔离**：申请阶段起即绑定放款机构；融资、预警、任务、业务结果、数据快照与模型版本都按组织归属。
  每个机构只用自己的数据训练模型、只用自己的 ACTIVE 模型做决策。数据库层拒绝任何跨组织的数据血缘。
- **开箱即用的演示**：设置 `DAIBM_DEMO_PASSWORD` 与 `DAIBM_DEMO_DATASET=true` 启动后，系统通过真实业务服务生成
  三家演示企业（正常、风险、违约）的完整生命周期，一个 ACTIVE 模型、一个待审批 CANDIDATE 模型、数据快照和决策血缘。
- **企业级运维**：健康检查、指标、哈希校验的备份/恢复、配置中心、登录锁定与会话超时、安全事件审计（Phase 4）。
- **性能基线**：1000 笔融资、5000 条业务结果、10000 条审计事件规模下，关键页面约 200 ms 以内，所有操作在 1 秒以内。

## 升级说明

- 数据库迁移到 `20260930_0021`：为申请回填放款机构，为模型流水线回填组织归属。
  如果历史训练数据混合了多个组织的结果，迁移会拒绝执行并给出说明，不会猜测归属。
- 当存在多个放款机构时，供应商创建申请必须选择放款机构（API 字段 `lender_organization_code`）。
- 升级前请先执行 `scripts/ops/backup.sh before-v1`。

## 兼容性

- 未改动的部分：核心风险算法、TGNN 研究核心、Fabric 架构、审计账本与历史审计数据。
- API 新增：`GET /api/v1/organizations/lenders`；`GET /api/v1/calibration-deployments/active` 可选参数 `organization_id`。
- 模型相关响应增加 `organization_id` / `organization_code` 字段。

详细变更见 [CHANGELOG.md](CHANGELOG.md)，部署见 [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)，
操作见 [USER_GUIDE.md](USER_GUIDE.md)，验收见 [V1_RELEASE_REPORT.md](V1_RELEASE_REPORT.md)，
产品总结见 [FINAL_PRODUCT_REPORT.md](FINAL_PRODUCT_REPORT.md)，演示见 [docs/demo/DEMO_SCRIPT.md](docs/demo/DEMO_SCRIPT.md)。
