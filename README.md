# DAIBM-SCF Demo MVP

Integrated application `v0.6` · Research Core `v0.4` / 集成应用 `v0.6` · 科研核心 `v0.4`

Двуязычный демонстрационный прототип магистерской диссертации: русский интерфейс используется по умолчанию, китайский включается одной кнопкой. Система показывает полный контур одобрения, риск-контроля и аудита: «заявка → оценка риска → решение → контрольное действие → проверяемая запись».

基于硕士学位论文构建的俄中双语演示原型。系统默认使用俄语，可一键切换中文，完整展示“融资申请 → 风险评估 → 融资决策 → 控制反馈 → 可信审计”的审批、风控与审计闭环。

## Что можно показать / 可以演示什么

- обзор, оценку риска, аудиторский реестр и методику / 业务总览、风险审查、审计账本和模型说明；
- исследовательскую трассу «данные → граф → TGNN → политика → аудит» / “数据 → 时序图 → TGNN → 策略 → 审计”科研证据链；
- реальную CPU-инференцию ONNX и сравнение риска до/после синтетического сценария / 真实 ONNX CPU 推理及合成风险注入前后对比；
- три синтетических кейса «одобрено / ручная проверка / отказ» / 三组“通过 / 人工复核 / 拒绝”合成案例；
- объяснение вклада факторов риска / 风险因素贡献解释；
- отпечаток торговых реквизитов и блокировку повторного использования счёта-фактуры / 交易凭证字段指纹与重复发票拦截；
- версию бизнес-модели, идентификатор оценки и хеш входа / 业务评分模型版本、评估标识与输入哈希；
- проверенные показатели повторной реализации TGNN/XGBoost с явной маркировкой происхождения / 经校验且明确标注来源的 TGNN/XGBoost 重实现指标；
- четыре связанных события на заявку и проверку хеш-цепочки / 每笔申请四类事件及哈希链验证；
- создание собственного кейса / 现场创建融资案例；
- контролируемый маршрут «выдача → два погашения → закрытие» / 受控的“放款→两期还款→结清”流程；
- имитацию подмены и восстановление реестра / 模拟篡改并恢复账本；
- полноэкранный режим и печать решения / 全屏演示和打印决策报告。

### Ролевой вход / 角色登录

После запуска открывается настоящая страница входа. Все роли используют пароль `Demo123!` / 启动后首先进入真实登录页，全部角色使用密码 `Demo123!`：

| Роль / 角色 | Учётная запись / 账户 | Основное действие / 主要操作 |
|---|---|---|
| Поставщик / 供应商 | `supplier.demo` | создать, изменить и подать заявку / 创建、修改并提交申请 |
| Якорная компания / 核心企业 | `core.demo` | подтвердить предел задолженности и сделку или вернуть её / 确认应付上限与交易，或退回 |
| Финансист / 融资方 | `financier.demo` | оценить риск и принять решение / 风险评估与融资决策 |
| Риск-менеджер / 风险经理 | `risk.demo` | назначить контрольное действие / 设置控制措施 |
| Аудитор / 审计员 | `auditor.demo` | проверить и закрыть аудиторский след / 核验并完成审计 |

Основной статусный маршрут / 主状态流：

```text
draft → submitted → trade_confirmed → risk_assessed
      → approved | manual_review | rejected → controlled → audited
```

Это пять реальных сессий и разграничение полномочий на сервере, а не переключатель роли в браузере. / 五个角色使用独立会话，权限由服务端验证，不是前端角色切换器。

Готовый сценарий выступления: [docs/demo-script.md](docs/demo-script.md).

完整演示讲稿：[docs/demo-script.md](docs/demo-script.md)。

Одностраничные резервные материалы / 单页答辩备用材料：

- [docs/defense-one-page.pdf](docs/defense-one-page.pdf) - русско-китайский лист защиты / 俄中双语答辩速查；
- [docs/research-brief-en.pdf](docs/research-brief-en.pdf) - English research brief;
- editable sources / 可编辑源文件: [docs/defense-one-page.md](docs/defense-one-page.md), [docs/research-brief-en.md](docs/research-brief-en.md).

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

Проверка готовности к защите выполняет только чтение: проверяет health, пять независимых ролевых сессий с обязательным выходом, русско-китайскую страницу входа и четыре файла раздаточных материалов. / 答辩预检只执行读取操作：检查健康状态、五个独立角色会话并逐一退出、俄中双语登录页，以及四个答辩材料文件：

```powershell
.\.venv\Scripts\python.exe scripts\defense_preflight.py --base-url http://127.0.0.1:8010
```

Для намеренно полного сброса только текущего Compose-проекта используйте исключительно `reset-defense-demo.cmd`: он требует точного ввода `RESET DEMO`, фиксирует локальный контекст Docker Desktop и Compose-проект, удаляет том PostgreSQL, запускает `start-demo.cmd`, а затем выполняет предполётную проверку. Не выполняйте удаление тома вручную. / 如确需仅对当前 Compose 项目执行全新重置，请只运行 `reset-defense-demo.cmd`：脚本要求准确输入 `RESET DEMO`，固定本机 Docker Desktop 上下文和 Compose 项目，随后删除 PostgreSQL 卷、调用 `start-demo.cmd` 并执行答辩预检。请勿手动执行卷删除操作。

Опциональный расширенный режим запускается единственным безопасным лаунчером `start-fabric-demo.cmd`. Он сначала поднимает базовый проект `daibm-scf-mvp`, затем изолированный проект `daibm-fabric-demo`, разворачивает chaincode и проверяет внутренний Gateway. Gateway не публикует порт на хост. Обычный запуск `start-demo.cmd` остаётся полностью рабочим: при недоступном Fabric бизнес-транзакция фиксируется в PostgreSQL, а хеш остаётся в outbox со статусом `pending`/retry. Кнопка аудитора показывает только реальные ответы API; повтор вручную разрешён только для `permanent_failed`. По умолчанию данные PostgreSQL и состояние Fabric сохраняются. Для намеренной очистки только контейнеров и локального состояния Fabric выполните `start-fabric-demo.cmd /clean` и введите точно `RESET FABRIC`; том PostgreSQL не затрагивается.

可选高级模式只需运行安全启动器 `start-fabric-demo.cmd`。它先启动基础项目 `daibm-scf-mvp`，再启动隔离项目 `daibm-fabric-demo`、部署链码并检查内部 Gateway；Gateway 不暴露宿主机端口。普通 `start-demo.cmd` 仍可独立运行：Fabric 不可用时，业务事务照常写入 PostgreSQL，哈希以 `pending`/待重试状态保留在 outbox。审计员页面只显示真实 API 结果，且只有 `permanent_failed` 可手动重新入队。默认保留 PostgreSQL 与 Fabric 状态；若明确要只清理 Fabric 容器和本地状态，运行 `start-fabric-demo.cmd /clean` 并准确输入 `RESET FABRIC`，不会删除 PostgreSQL 卷。

Основной сценарий начинается с **«Создать заявку на финансирование»** / 主流程从 **“创建供应链融资申请”** 开始，并按“申请—评估—决策—控制—审计”完成审批、风控与审计闭环。Кнопка **«Загрузить 3 готовых кейса»** / **“载入 3 组预置案例”** 保留用于快速对比三类决策。

Вкладка «Финансирование» реализует точный по копейкам версионный маршрут выдачи, двух погашений и закрытия. This is a **controlled financing lifecycle simulation** and **does not execute a real bank transfer**. / “融资”页面以精确到分、带版本和审计事件的方式模拟放款、两期还款与结清；它不会发起真实银行转账。

После закрытия объекта аудитор может записать контролируемый/симулированный фактический результат. Ссылка на свидетельство хешируется SHA-256 только в браузере; сервер получает лишь хеш. Каждый неизменяемый результат запускает детерминированное обучение слоя Platt. Версия активируется автоматически только при `n >= 20`, не менее 5 положительных и 5 отрицательных наблюдений, проверенном SHA-256/schema артефакта и отсутствии регрессии Brier/log loss. PostgreSQL гарантирует единственную активную версию; аудитор может вернуть только её непосредственного предшественника. Новые оценки сохраняют исходный и итоговый баллы, ID версии или код безопасного возврата к baseline. / 融资结清后，审计员可录入受控/模拟实际结果；证据引用只在浏览器中计算 SHA-256，服务端仅接收哈希。每个不可变结果都会触发确定性的 Platt 层训练。只有样本数不少于 20、正负样本各不少于 5、工件 SHA-256/schema 校验通过且 Brier/log loss 不退化时才自动激活。PostgreSQL 保证仅有一个激活版本；审计员只能回滚到其直接前序版本。新评估同时保存原始分、最终分、校准版本 ID 或安全回退代码。

PostgreSQL data and published calibration artifacts use separate Docker named volumes (`postgres-data` and `calibration-artifacts`), so rebuilding the application container preserves both outcome lineage and the active calibration file. / PostgreSQL 数据与已发布校准工件分别使用 Docker 命名卷（`postgres-data` 与 `calibration-artifacts`），重建应用容器不会丢失结果血缘或当前激活校准文件。

Подтверждая сделку, якорная компания указывает предел задолженности, который она признаёт за поставщиком. Приложение проверяет «счёт не превышает предел» само, а дополнительно запрашивает у изолированного сервиса доказательство с нулевым разглашением этого же утверждения (схема Circom `invoice_limit`, Groth16 над BN128). Доказательство, его SHA-256 и обязательство Poseidon к сумме попадают в хеш-цепочку аудита, откуда outbox забирает `proof_sha256` и `circuit_version` для якорения. По умолчанию сервис необязателен: при сбое записывается fallback. При ZKP_PROOF_REQUIRED=true подтверждение блокируется (HTTP 503) без частичной записи. Python проверяет структуру ответа, но доверяет prover и не проверяет Groth16 криптографически; Fabric закрепляет хеши, а не валидность доказательства. Соль не сохраняется, поэтому обязательство связывает сумму, но не раскрывается повторно; сервер видит сумму счёта в любом случае, поэтому это демонстрация механизма, а не сокрытие суммы от оператора. / 核心企业确认交易时录入它对该供应商认可的应付上限。应用自己校验“发票金额不超过上限”，同时向隔离的证明服务请求同一命题的零知识证明（Circom `invoice_limit` 电路，BN128 上的 Groth16）。证明、其 SHA-256 与对金额的 Poseidon 承诺一并写入审计哈希链，outbox 从中取出 `proof_sha256` 与 `circuit_version` 用于锚定。默认允许证明服务不可用时显式降级；ZKP_PROOF_REQUIRED=true 时，缺失或不可用的证明会阻止确认并返回 HTTP 503，事务整体回滚。Python 校验响应结构但仍信任内部 prover，不独立执行 Groth16 密码学验证；Fabric 只锚定哈希，不证明 ZKP 有效。salt 不保存，因此承诺只作绑定、不再打开；服务端本来就能看到发票金额，所以这是机制演示，而非对运营方隐藏金额。

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
- `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me` — вход, выход и текущая роль / 登录、退出与当前角色；
- `GET /api/v1/dashboard`, `GET /api/v1/tasks` — ролевые показатели и очередь задач / 角色指标与待办；
- `GET /api/v1/organizations/core-enterprises` — доступный справочник якорных компаний из PostgreSQL / PostgreSQL 中可选核心企业目录；
- `POST /api/v1/applications`, `GET /api/v1/applications` — создание и доступный реестр заявок / 创建申请与角色可见列表；
- новая заявка получает SHA-256 отпечаток реквизитов; повторное использование того же счёта-фактуры тем же поставщиком и якорной компанией возвращает `409 duplicate_invoice_claim` / 新申请生成交易字段 SHA-256 指纹，同一供应商与核心企业重复使用同一发票时返回 `409 duplicate_invoice_claim`；
- `POST /api/v1/applications/{id}/submit` — подача поставщиком / 供应商提交；
- `POST /api/v1/applications/{id}/trade-confirmation` — подтверждение якорной компанией / 核心企业确认；
- `POST /api/v1/applications/{id}/risk-assessment` — оценка финансистом / 融资方风险评估；
- `POST /api/v1/applications/{id}/decision` — финансовое решение / 融资决策；
- `POST /api/v1/applications/{id}/control-action` — контроль риск-менеджера / 风险控制；
- `POST /api/v1/applications/{id}/audit-review` — итоговая проверка аудитора / 审计核验；
- `GET|POST /api/v1/facilities` и команды `initiate-disbursement`, `confirm-disbursement`, `payments`, `mark-overdue`, `close` — ролевой имитатор жизненного цикла / 按角色授权的融资生命周期模拟；
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

Прикладные тесты используют отдельный реальный PostgreSQL 17 через Testcontainers; Docker должен быть запущен. Они не требуют установки тяжёлого стека обучения моделей.

应用测试通过 Testcontainers 启动独立的真实 PostgreSQL 17，因此需要 Docker 正常运行；这一组测试不需要安装模型训练依赖。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests --ignore=tests/research -q
```

Полный набор, включая обучение TGNN/XGBoost / 包含 TGNN/XGBoost 训练的完整科研测试：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-research.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Полный браузерный маршрут пяти ролей / 五角色完整浏览器验收（сначала запустите приложение / 请先启动应用）：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe scripts\browser_acceptance.py
```

Сценарий создаёт уникальную заявку, проводит её через поставщика, якорную компанию, финансиста, риск-менеджера и аудитора, проверяет русско-китайское отображение доказательств и сохраняет снимок в `output/five-role-acceptance.png`. / 脚本会创建唯一申请，依次经过供应商、核心企业、融资方、风险经理和审计员，校验俄中双语证据，并将截图保存至 `output/five-role-acceptance.png`。

Следующий сценарий берёт свежую одобренную и прошедшую аудит заявку, проводит её через выдачу, два погашения и закрытие в четырёх независимых браузерных контекстах / 随后使用刚刚通过审计的申请，在四个独立会话中完成放款、两笔还款与结清：

```powershell
.\.venv\Scripts\python.exe scripts\facility_browser_acceptance.py
```

Итоговый русско-китайский снимок: `output/facility-lifecycle-clone-acceptance.png`. / 最终中文界面截图保存为 `output/facility-lifecycle-clone-acceptance.png`。

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

Исследовательский пакет чувствительности по пяти синтетическим seed запускается отдельно; по умолчанию он обучает каждую модель до 100 эпох с early stopping. Команда сохраняет проверяемые labels/probabilities/hashes, пять PNG, две исходные CSV-таблицы и доступное приложение `research-appendix.md`. / 五个合成 seed 的敏感性证据包需单独运行；默认每个模型最多训练 100 轮并采用 early stopping。命令保留可验证的标签、概率和哈希，同时输出 5 张 PNG、2 份底层 CSV 与含无障碍文字说明的 `research-appendix.md`：

```powershell
python -m research.cli evaluate-multiseed --output output/research/sensitivity-runs --destination output/research/sensitivity-pack
python -m research.cli verify-multiseed --path output/research/sensitivity-pack
```

Все интервалы в этом пакете являются описательными 95% t-интервалами вариабельности пяти seed (`n=5`), а не проверками значимости. Пороговая сетка задана заранее; система не выбирает «лучший» порог. Результаты относятся только к синтетической реализации 2026 года и не воспроизводят исходные результаты диссертации. / 包内区间均为五个 seed（`n=5`）的描述性 95% t 区间，不是显著性检验。阈值网格预先固定，系统不选择“最佳阈值”。这些结果仅属于 2026 年合成重实现，不是原论文结果的复现。

Зафиксированный набор имеет SHA-256 `f784faa8bdef23625888d64de75c2f29a80c0a51e266e0822652569507648353`; продвинутый ONNX — `158d273db310c3f1abf4be7cb06aee78568e475ebb7564ebbeaa16d3efeeb0e5`. Каноническая матрица утверждений и доказательств: [docs/thesis-traceability.md](docs/thesis-traceability.md). / 冻结数据集与 ONNX 哈希见上述值；论文声明与工程证据的唯一边界文档为该追溯矩阵。

## Границы MVP / 科研边界

Это компьютерная имитационная проверка сценария, а не промышленная банковская платформа. Канонический реестр — связанная хеш-цепочка в PostgreSQL; опциональный локальный Hyperledger Fabric закрепляет только хеши через Gateway/outbox. Это не production blockchain и не доказательство промышленного консенсуса, производительности или отказоустойчивости. Реализована минимальная TGNN (GCN–BiLSTM–MLP) с ONNX-инференцией; прозрачная модель правил остаётся отдельным инженерным базовым контуром. Обратная связь адаптирует только Platt-калибровку baseline и не переобучает TGNN. Версия на данных `CONTROLLED_DEMO` явно остаётся в контуре `controlled_demo`; её активация демонстрирует механизм управления версиями, а не эффект на реальных предприятиях. Отпечаток торговых реквизитов подтверждает неизменность введённых полей, но не заменяет проверку исходного файла или внешней налоговой платформы. Имитатор финансирования не моделирует interest, fees, FX или accounting и не интегрирован с реальным банком. MVP не заявляет PoA+ или полный ZKP-комплекс диссертации; реализовано только демонстрационное доказательство invoice <= limit.

本项目属于计算机模拟场景验证，不是生产级银行系统。规范账本仍是 PostgreSQL 关联哈希链；可选的本地 Hyperledger Fabric 仅通过 Gateway/outbox 锚定哈希。这不是生产级区块链，也不证明生产级共识、性能或容错能力。系统已实现最小 GCN–BiLSTM–MLP TGNN 与 ONNX 推理，透明规则模型仍作为独立工程基线。结果回流只自适应训练 baseline 的 Platt 校准层，不重训 TGNN。基于 `CONTROLLED_DEMO` 数据激活的版本会明确标记为 `controlled_demo`，它证明的是版本治理和安全回退机制，而不是真实企业效果。交易字段指纹只能证明已录入字段的一致性，不替代原始文件或外部税票平台核验。MVP 不声称复现完整 TGNN 架构或原论文指标，也不声称实现 PoA+、完整论文 ZKP 系统或银行系统集成；已有单一 invoice <= limit 证明演示。

Синтетический том базы данных, исходный текст диссертации и реальные данные предприятий не включаются в Git-репозиторий.

合成数据库卷、论文原文件和真实企业数据不会提交至 Git 仓库。

## Локальная граница безопасности / 本地安全边界

Демонстрация привязана к `127.0.0.1` и использует синтетические восстанавливаемые данные. Пароли хешируются через scrypt, сессии хранятся на сервере, cookie имеет `HttpOnly` и `SameSite=Strict`, а полномочия проверяются API. Однако фиксированные демонстрационные учётные данные, HTTP без `Secure` cookie и отсутствие промышленного управления секретами, ограничения частоты входа, резервного копирования и политики хранения означают, что эту сборку нельзя публиковать напрямую в интернете.

演示仅绑定 `127.0.0.1`，使用可重建的合成数据。密码采用 scrypt 哈希，会话保存在服务端，Cookie 设置 `HttpOnly` 与 `SameSite=Strict`，权限由 API 校验。但固定演示账户、未启用 HTTPS/`Secure` Cookie，以及未配置生产级密钥管理、登录限流、备份和数据保留策略，意味着该版本不能直接部署到公网。
