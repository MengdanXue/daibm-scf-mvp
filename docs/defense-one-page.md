# DAIBM-SCF DEFENSE SHEET

Лист защиты магистерской диссертации

硕士论文答辩速查

## Architecture / Архитектура

- Browser UI (Russian default, Chinese switch) -> FastAPI role API -> SQLAlchemy -> PostgreSQL 17.
- Offline synthetic data -> temporal graph -> minimal GCN-BiLSTM TGNN -> ONNX Runtime -> policy -> hash-linked audit events.
- Локальный контур: два контейнера на `127.0.0.1:8010`; PostgreSQL является проверяемым журналом, а не блокчейном.
- 本地双容器架构：俄中双语页面、FastAPI、PostgreSQL 17，以及离线训练后发布的 ONNX 模型。

## Five-role order / Пять ролей / 五角色顺序

1. Поставщик `supplier.demo`: create and submit / 供应商创建并提交申请.
2. Якорная компания `core.demo`: confirm trade / 核心企业确认交易.
3. Финансист `financier.demo`: assess and decide / 融资方评估并决策.
4. Риск-менеджер `risk.demo`: record control / 风险经理设置控制措施.
5. Аудитор `auditor.demo`: verify and close audit / 审计员核验并完成审计.

Each account receives an independent server session. API authorization and version checks enforce every handoff.

## Financing lifecycle / Финансовый цикл

- After approval and audit: initiate disbursement -> confirm disbursement -> submit two repayments -> confirm each repayment -> close at zero balance.
- This is a controlled financing lifecycle simulation. It does not execute a real bank transfer.
- A closed facility may add controlled/simulated outcome lineage and a Platt calibration candidate, with gated automatic activation for the business baseline only.
- Это имитация: нет процентов, комиссий, FX, бухгалтерских проводок, внешнего расчёта или сверки.
- 这是受控融资模拟：不执行真实资金划转，也不包含利息、费用、汇兑、会计或外部结算。

<!-- column-break -->

## Research evidence / Исследовательские данные

- Provenance: `2026_EXPLORATORY_SENSITIVITY`; synthetic five-seed rerun only.
- TGNN ROC-AUC mean ± sample SD: `0.5469 ± 0.0855`; PR-AUC mean `0.1211`; high seed variability and near chance.
- XGBoost ROC-AUC mean ± sample SD: `0.5889 ± 0.0202`; PR-AUC mean `0.1467`; slightly steadier but still weak.
- At threshold `0.50`, XGBoost positive recall is `0.022` (FN `716`, TP `16`). Fixed-bin calibration gaps: TGNN `0.387`, XGBoost `0.265`.
- Values are mean ± sample SD across n=5 seeds. They are descriptive, not significance tests; the fixed threshold grid does not select an optimum.

## Exact non-claims / Точные ограничения / 精确边界

- The package does not reproduce the original thesis and does not generalize to real enterprises.
- The default demo is not a production bank, a production Fabric network, PoA+, or an automatic TGNN retraining system.
- Advanced evidence is bounded: single-organization Fabric 2.5.16 anchors hashes; optional Circom/Groth16 evidence is wired into trade confirmation. Python checks structure, not cryptographic validity; Fabric stores hashes, not verification results. These are demonstration modules, not production Fabric consensus or a production ZKP service.
- Outcome feedback does not retrain the TGNN, does not trigger on drift, and does not prove real-enterprise effects. Calibration metrics use the fitting samples, not held-out outcomes.
- PostgreSQL provides a tamper-evident hash chain, not distributed consensus. The minimal TGNN is smaller than the thesis architecture.
- Данные синтетические; исходные данные, код и метрики диссертации недоступны.
- 数据为合成数据；不声称复现论文指标、真实企业效果、显著性或生产级安全性。

## Preflight, reset, fallback / Подготовка / 备用

- Start: `start-demo.cmd`; read-only check: `.\.venv\Scripts\python.exe scripts/defense_preflight.py`.
- Clean reset only when intended: `reset-defense-demo.cmd`, then type exactly `RESET DEMO`.
- If the browser fails, open `docs/defense-one-page.pdf` and `docs/research-brief-en.pdf`.
- Visual evidence: `output/five-role-acceptance.png`; optional video: `output/defense-video/*.webm`.
- Research evidence: `output/research/sensitivity-pack/`; verify with `python -m research.cli verify-multiseed --path output/research/sensitivity-pack`.
- 无网络时系统仍可本地运行；页面不可用时使用上述 PDF、截图和已验证科研证据包。
