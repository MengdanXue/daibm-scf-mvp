# DAIBM-SCF Demo MVP

Integrated application `v0.5` · Research Core `v0.4` / 集成应用 `v0.5` · 科研核心 `v0.4`

Двуязычный демонстрационный прототип магистерской диссертации: русский интерфейс используется по умолчанию, китайский включается одной кнопкой. Система показывает полный сценарий «заявка → оценка риска → решение → контрольное действие → проверяемая запись».

基于硕士学位论文构建的俄中双语演示原型。系统默认使用俄语，可一键切换中文，完整展示“融资申请 → 风险评估 → 融资决策 → 控制反馈 → 可信审计”的业务闭环。

## Что можно показать / 可以演示什么

- обзор, оценку риска, аудиторский реестр и методику / 业务总览、风险审查、审计账本和模型说明；
- исследовательскую трассу «данные → граф → TGNN → политика → аудит» / “数据 → 时序图 → TGNN → 策略 → 审计”科研证据链；
- реальную CPU-инференцию ONNX и сравнение риска до/после синтетического сценария / 真实 ONNX CPU 推理及合成风险注入前后对比；
- три синтетических кейса «одобрено / ручная проверка / отказ» / 三组“通过 / 人工复核 / 拒绝”合成案例；
- объяснение вклада факторов риска / 风险因素贡献解释；
- четыре связанных события на заявку и проверку хеш-цепочки / 每笔申请四类事件及哈希链验证；
- создание собственного кейса / 现场创建融资案例；
- имитацию подмены и восстановление реестра / 模拟篡改并恢复账本；
- полноэкранный режим и печать решения / 全屏演示和打印决策报告。

Готовый сценарий выступления: [docs/demo-script.md](docs/demo-script.md).

完整演示讲稿：[docs/demo-script.md](docs/demo-script.md)。

## Быстрый запуск / 快速启动

Требуется запущенный Docker Desktop. В Windows дважды щёлкните `start-demo.cmd`. Скрипт соберёт два контейнера, дождётся реального health check и откроет браузер.

需要先启动 Docker Desktop。Windows 下双击 `start-demo.cmd`，脚本会构建 PostgreSQL 与应用容器，等待健康检查通过后自动打开浏览器。

Ручной запуск / 手动启动：

```powershell
docker compose up --build -d
```

Откройте / 打开：<http://127.0.0.1:8010>

Хост-порт `8010` выбран, чтобы не конфликтовать с другими локальными сервисами на `8000`; внутри контейнера API по-прежнему работает на `8000`. При необходимости перед запуском Compose задайте `$env:MVP_PORT="8000"` в PowerShell или `set MVP_PORT=8000` в `cmd.exe`. / 默认主机端口使用 `8010`，以避开本机常见的 `8000` 端口冲突；容器内 API 仍为 `8000`。如需兼容原端口，可在 PowerShell 中设置 `$env:MVP_PORT="8000"`，或在 `cmd.exe` 中执行 `set MVP_PORT=8000`。

Обычная остановка сохраняет синтетические данные / 普通停止会保留合成演示数据：

```powershell
docker compose down
```

Полный сброс локального демонстрационного тома / 完全清空本地演示数据库卷：

```powershell
docker compose down -v
```

Нажмите **«Запустить демонстрацию»** / 点击 **“开始答辩演示”**，系统会重置并装载 3 个演示案例和 12 条账本事件。

## Архитектура / 架构

```text
FastAPI API
    -> FinancingService + ResearchDecisionService
       (единые транзакции / 原子事务)
    -> SQLAlchemy repositories
    -> PostgreSQL 17

Offline research pipeline
    -> deterministic synthetic data (500 × 24)
    -> temporal graph (12-month windows)
    -> XGBoost comparison + minimal GCN–BiLSTM TGNN
    -> promoted ONNX artifact used by the API
```

- PostgreSQL — единственная база данных / PostgreSQL 是唯一数据库；
- SQLAlchemy 2.x + Psycopg 3 — единый слой доступа / 统一数据访问层；
- Alembic — единственный способ создавать и изменять схему / Alembic 是唯一结构迁移入口；
- заявка и четыре события фиксируются атомарно / 申请与四条审计事件原子提交；
- transaction-level advisory lock предотвращает разветвление хеш-цепочки / 事务级 advisory lock 防止并发哈希链分叉。
- модель обучается только офлайн; при запуске API проверяет хеш и загружает зафиксированный ONNX-артефакт / 模型仅离线训练；API 启动时校验哈希并加载已冻结的 ONNX 模型；
- после инференции оценка, политика и три события аудита фиксируются атомарно / 推理后，评估、策略与三条审计事件原子写入。

Приложение строит PostgreSQL URL только из `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`. Переключателя типа базы данных нет.

应用仅从上述五个 `POSTGRES_*` 配置构造 PostgreSQL 连接，不提供数据库类型切换。

## API

- `GET /api/health` — PostgreSQL и целостность реестра / PostgreSQL 与账本健康状态；
- `GET /api/dashboard` — показатели / 演示指标；
- `POST /api/requests` — новая оценка / 创建并评估申请；
- `GET /api/requests` — реестр заявок / 申请列表；
- `GET /api/ledger` — журнал событий / 账本事件；
- `GET /api/ledger/verify` — проверка цепочки / 验证哈希链；
- `POST /api/demo/reset` — три исходных кейса / 重置三组案例；
- `POST /api/demo/tamper` — безопасная подмена синтетического события / 模拟篡改；
- `POST /api/demo/recover` — восстановление с сохранением доказательств инцидента / 保留异常证据的恢复；
- `GET /api/research/status` — версии данных, графа, модели и политики / 数据、图、模型与策略版本；
- `POST /api/research/inference` — реальная инференция продвинутой TGNN / 已发布 TGNN 的真实推理；
- `POST /api/research/scenarios/{enterprise_id}/inject-risk` — версионный синтетический сценарий и повторная инференция / 版本化合成风险场景并重新推理；
- `POST /api/research/scenarios/reset` — возврат к исходному сценарию без изменения эталонных данных / 返回基准场景且不修改参考数据；
- `GET /docs` — OpenAPI。

## Миграции и тесты / 迁移与测试

Ручное применение миграции / 手动执行迁移：

```powershell
python -m alembic upgrade head
```

Тесты используют отдельный реальный PostgreSQL 17 через Testcontainers; Docker должен быть запущен.

测试通过 Testcontainers 启动独立的真实 PostgreSQL 17，因此需要 Docker 正常运行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

## Воспроизводимость Research Core / 科研核心复现

Команды выполняются из корня репозитория. Для генерации и обучения установите отдельные исследовательские зависимости: / 以下命令均在仓库根目录执行；生成与训练需安装独立科研依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-research.txt
python -m research.cli generate --output output/research/data
python -m research.cli train-xgboost --output output/research/xgboost
python -m research.cli train-tgnn --output output/research/tgnn
python -m research.cli promote --run-dir output/research/tgnn --destination artifacts/reference
python -m research.cli verify --reference artifacts/reference
```

Полная эталонная сборка доступна как `python -m research.cli build-reference`; она обучает обе модели, экспортирует ONNX, проверяет паритет и продвигает артефакт. Обычный запуск Docker эту операцию не выполняет. / 完整参考构建可使用 `python -m research.cli build-reference`：它会训练两类模型、导出 ONNX、校验一致性并发布模型。普通 Docker 启动不会训练模型。

Зафиксированный набор имеет SHA-256 `f784faa8bdef23625888d64de75c2f29a80c0a51e266e0822652569507648353`; продвинутый ONNX — `158d273db310c3f1abf4be7cb06aee78568e475ebb7564ebbeaa16d3efeeb0e5`. Каноническая матрица утверждений и доказательств: [docs/thesis-traceability.md](docs/thesis-traceability.md). / 冻结数据集与 ONNX 哈希见上述值；论文声明与工程证据的唯一边界文档为该追溯矩阵。

## Границы MVP / 科研边界

Это компьютерная имитационная проверка сценария, а не промышленная банковская платформа. Реестр — связанная хеш-цепочка, сохранённая в PostgreSQL. Реализована минимальная TGNN (GCN–BiLSTM–MLP) с ONNX-инференцией; прозрачная модель правил остаётся отдельным инженерным базовым контуром. MVP не заявляет полную архитектуру TGNN и показатели исходной диссертации, Hyperledger Fabric, PoA+, ZKP или интеграцию с реальным банком.

本项目属于计算机模拟场景验证，不是生产级银行系统。账本是存储于 PostgreSQL 的关联哈希链。系统已实现最小 GCN–BiLSTM–MLP TGNN 与 ONNX 推理，透明规则模型仍作为独立工程基线。MVP 不声称复现完整 TGNN 架构或原论文指标，也不声称实现 Hyperledger Fabric、PoA+、ZKP 或真实银行系统集成。

Синтетический том базы данных, исходный текст диссертации и реальные данные предприятий не включаются в Git-репозиторий.

合成数据库卷、论文原文件和真实企业数据不会提交至 Git 仓库。
