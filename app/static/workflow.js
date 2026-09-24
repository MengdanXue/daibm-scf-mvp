(function () {
  "use strict";

  const COPY = {
    ru: {
      loginTitle: "Войдите в рабочий контур",
      loginSubtitle: "Пять участников проводят одну заявку от поставщика до проверяемого аудиторского следа.",
      researchBoundary: "Демонстрационный прототип на синтетических данных · PostgreSQL · проверяемый журнал событий",
      demoAccess: "Демонстрационный доступ", chooseRole: "Выберите роль", demoRoleGuide: "Порядок демонстрационных ролей", demoRoleSelected: "Выбрана роль: {role}. Проверьте данные и нажмите кнопку входа.", orCredentials: "или введите учётные данные",
      username: "Имя пользователя", password: "Пароль", signIn: "Войти в систему", logout: "Выйти",
      commonPassword: "Общий пароль демо-ролей:", navWorkflow: "Рабочий контур",
      workflowEyebrow: "РОЛЕВОЙ БИЗНЕС-ПРОЦЕСС", workflowTitle: "Финансирование цепи поставок", refresh: "Обновить данные",
      currentStation: "Текущая рабочая станция", allApplications: "Доступные заявки", myTasks: "Мои задачи",
      activeStatus: "Активных статусов", database: "Хранилище", newApplication: "Новая заявка на финансирование",
      newApplicationHint: "Заполните реквизиты сделки. Заявка сохранится как черновик и будет доступна для подачи.",
      coreEnterpriseCode: "Код якорной компании", contractNumber: "Номер договора", invoiceNumber: "Номер счёта-фактуры",
      amount: "Сумма, CNY", termDays: "Срок, дней", paymentDelay: "Просрочка, дней", counterpartyRisk: "Риск контрагента, 0–1",
      relationshipMonths: "Отношения, месяцев", transactions30d: "Сделок за 30 дней", invoiceMismatch: "Есть расхождение документов",
      saveDraft: "Сохранить черновик", updateDraft: "Сохранить изменения", applicationQueue: "Очередь заявок",
      selectApplication: "Выберите заявку", selectApplicationHint: "Здесь появятся реквизиты, допустимые действия и полная история передачи между ролями.",
      workflowTimeline: "История передачи ответственности", timelineHint: "Каждый переход выполняется атомарно и фиксируется вместе с ролью исполнителя.",
      timelineEmpty: "Выберите заявку, чтобы увидеть историю.", roleSupplier: "Поставщик", roleCoreEnterprise: "Якорная компания",
      roleFinancier: "Финансист", roleRiskManager: "Риск-менеджер", roleAuditor: "Аудитор", roleAdmin: "Администратор", openRiskDetail: "Риск-профиль", adminMission: "Видит весь портфель, настраивает правила риска и распределяет работу.",
      stageCreate: "Создание и подача", stageConfirm: "Подтверждение сделки", stageDecide: "Оценка и решение", stageControl: "Контроль", stageAudit: "Проверка следа",
      supplierMission: "Создайте заявку, проверьте реквизиты и передайте её якорной компании.",
      coreMission: "Подтвердите реальность договора и счёта-фактуры либо верните заявку поставщику.",
      financierMission: "Выполните оценку риска, изучите результат модели и примите решение о финансировании.",
      riskMission: "Назначьте контрольное действие по принятому решению и сохраните его в журнале.",
      auditorMission: "Проверьте всю цепочку действий и целостность журнала перед закрытием заявки.",
      queueAll: "Показаны все доступные вашей роли заявки; задачи с допустимыми действиями отмечены первыми.",
      noApplications: "Для этой роли пока нет доступных заявок.", version: "Версия", supplier: "Поставщик", core: "Якорная компания",
      contract: "Договор", invoice: "Счёт-фактура", term: "Срок", risk: "Оценка риска", decision: "Решение",
      actionStation: "Доступное действие", noAction: "На текущем этапе действий для вашей роли нет.", comment: "Комментарий к передаче",
      commentPlaceholder: "Кратко зафиксируйте основание действия…", edit: "Изменить черновик", submit: "Подать заявку",
      payableCeiling: "Подтверждаемый предел задолженности", payableCeilingHint: "Не ниже суммы счёта. Публичная граница доказательства.",
      payableConfirmed: "Подтверждённый предел", proofEvidence: "Доказательство лимита счёта", proofCircuit: "Схема", proofDigest: "Хеш доказательства",
      proofCommitment: "Обязательство к сумме", proofAbsent: "Доказательство не приложено", proofFallbackPrefix: "Причина",
      confirmTrade: "Подтвердить сделку", returnTrade: "Вернуть поставщику", assessRisk: "Выполнить оценку риска",
      approve: "Одобрить", manualReview: "Ручная проверка", reject: "Отклонить", applyControl: "Зафиксировать контроль",
      auditReview: "Завершить аудит", created: "Черновик сохранён", updated: "Изменения сохранены", actionComplete: "Действие выполнено",
      sessionExpired: "Сессия завершена. Войдите снова.", loginFailed: "Не удалось войти. Проверьте имя пользователя и пароль.",
      requestFailed: "Операция не выполнена", loading: "Загрузка…", signedIn: "Вход выполнен", taskReady: "требует действия",
      timelineCreate: "Создание заявки", days: "дн.", cancelEdit: "Новый черновик",
      draft: "Черновик", submitted: "Подана", trade_returned: "Возвращена", trade_confirmed: "Сделка подтверждена",
      risk_assessed: "Риск оценён", approved: "Одобрена", manual_review: "Ручная проверка", rejected: "Отклонена",
      controlled: "Контроль назначен", audited: "Аудит завершён",
      create: "Создание", create_draft: "Создание черновика", update: "Изменение", confirm_trade: "Подтверждение сделки", return_trade: "Возврат сделки",
      assess_risk: "Оценка риска", decide: "Финансовое решение", apply_control: "Контрольное действие", audit: "Аудиторская проверка",
      tradeEvidence: "Отпечаток торговых реквизитов", businessRiskEvidence: "Доказательство бизнес-оценки", duplicateCheckPassed: "Проверка дублирования пройдена",
      researchComparisonBoundary: "Бизнес-оценка использует прозрачную базовую модель; TGNN показана отдельно как исследовательское сравнение.",
      duplicate_invoice_claim: "Этот счёт-фактура уже используется в другой заявке",
      navFacilities: "Финансирование", facilityEyebrow: "КОНТРОЛИРУЕМЫЙ ЖИЗНЕННЫЙ ЦИКЛ", facilityTitle: "От выдачи до закрытия",
      facilitySubtitle: "Точная сумма, график и допустимое действие текущей роли в одном досье.", facilityCreate: "Создать финансирование",
      facilityCreateHint: "Укажите одобренную и завершившую аудит заявку; сумма графика должна точно совпадать с основной суммой.",
      facilityRequestId: "ID одобренной заявки", facilityPrincipal: "Основная сумма", facilityCurrency: "Валюта",
      facilityDueOne: "Срок транша 1", facilityAmountOne: "Сумма транша 1", facilityDueTwo: "Срок транша 2", facilityAmountTwo: "Сумма транша 2",
      facilityCreateAction: "Создать график", facilityListTitle: "Финансовые досье", facilityListHint: "Доступ определяется организацией и текущей ролью.",
      facilitySelect: "Выберите финансовое досье", facilitySelectHint: "Здесь появятся точный баланс, график, платежи и действие вашей роли.",
      facilityPaid: "Уже погашено", facilityOutstanding: "Остаток", facilityInstallments: "График погашения", facilityPayments: "Платежи",
      facilityBoundaryTitle: "Граница демонстрации", facilityBoundary: "Это контролируемая имитация жизненного цикла; система не выполняет реальный банковский перевод.",
      facilityNoItems: "Финансовых досье пока нет. Финансист может создать одно из одобренной заявки.", facilityNoPayments: "Платежи ещё не представлены.",
      facilityEvidence: "След выдачи", facilityActionStation: "Действие текущей роли", facilityNoAction: "На этой стадии у текущей роли нет допустимых действий.",
      facilityVersion: "Версия команды", facilityInstallment: "Транш", facilityDueDate: "Срок", facilityAmount: "Сумма", facilityPaidAmount: "Погашено",
      facilityPaymentReference: "Референс платежа", facilityDecisionComment: "Комментарий к решению", facilitySubmitPayment: "Представить платёж",
      facilityConfirmPayment: "Подтвердить платёж", facilityRejectPayment: "Отклонить платёж", facilityMarkOverdue: "Отметить просрочку",
      facilityInitiate: "Инициировать выдачу", facilityConfirmDisbursement: "Подтвердить выдачу", facilityClose: "Закрыть досье",
      facilityCreated: "График финансирования создан", facilityActionComplete: "Жизненный цикл обновлён", facilityInvalidMoney: "Введите положительные суммы с точностью до копейки.",
      facilityScheduleMismatch: "Суммы двух траншей должны точно совпадать с основной суммой.", facilityRequiredFields: "Заполните обязательные поля действия.",
      ready_for_disbursement: "Готово к выдаче", disbursed: "Выдача инициирована", active: "Активно", overdue: "Просрочено", repaid: "Погашено", closed: "Закрыто",
      installment_scheduled: "По графику", installment_partially_paid: "Частично погашено", installment_paid: "Погашено", installment_overdue: "Просрочено",
      payment_submitted: "На проверке", payment_confirmed: "Подтверждён", payment_rejected: "Отклонён",
      restructured: "Реструктурировано", defaulted: "Дефолт", in_disposal: "Работа с проблемным активом", in_recovery: "Взыскание", recovered: "Взыскано после дефолта", written_off: "Списано", installment_superseded: "Заменено новой версией",
      facilityOpenDisposal: "Открыть работу с проблемным активом", facilityCloseDisposal: "Закрыть работу с проблемным активом", facilityRestructure: "Реструктурировать", facilityDeclareDefault: "Объявить дефолт",
      facilityStartRecovery: "Начать взыскание", facilityRecordRecovery: "Записать поступление от взыскания", facilityWriteOff: "Списать остаток",
      facilityReasonCode: "Код основания", facilityComment: "Комментарий", facilityEvidenceReference: "Ссылка на доказательство (хешируется в браузере)", facilityDaysPastDue: "Дней просрочки",
      facilityDefaultedAt: "Дата дефолта", facilityRecoverySource: "Источник взыскания", facilityRecoveryReference: "Референс поступления", facilityNewDueDate: "Новый срок",
      facilityHistory: "История статусов", facilityContracts: "Версии договора", facilityRecoveries: "Поступления от взыскания", facilityNoHistory: "Записей пока нет.",
      facilityArrears: "Просроченная задолженность", facilityWrittenOff: "Списано", facilityNetLoss: "Чистый убыток", facilityRecoveryCollected: "Взыскано",
      facilityContractVersion: "Версия", facilityDecisions: "Решения по риску",
      facility_not_found: "Финансовое досье не найдено или недоступно вашей роли.", forbidden_role: "Текущая роль не может выполнить это действие.",
      facility_precondition_failed: "Для создания нужна одобренная заявка с завершённым аудитом и совпадающей суммой.", facility_conflict: "Данные изменились или действие больше недоступно. Обновите досье и повторите с новой командой.",
      fabricAnchorEyebrow: "ОПЦИОНАЛЬНЫЙ ВНЕШНИЙ ЯКОРЬ", fabricAnchorTitle: "Якорение в Fabric", fabricAnchorSubtitle: "Хеши аудиторских событий передаются через транзакционный outbox; бизнес-записи остаются в PostgreSQL.",
      fabricAnchorOptional: "Опциональный расширенный режим: бизнес-процесс продолжается, а хеши остаются в очереди, если Fabric недоступен.", fabricAnchorConnected: "Эта отправка получила реальные подтверждения Fabric. Обновление списка само по себе не проверяет доступность Gateway.",
      anchorPending: "Ожидают", anchorRetry: "Повтор", anchorAnchored: "Закреплены", anchorPermanentFailed: "Постоянная ошибка", anchorRefresh: "Обновить", anchorDispatch: "Отправить одну партию", anchorRetryFailed: "Повторить", anchorRecent: "Последние записи", anchorEmpty: "Записей для якорения пока нет.", anchorAttempt: "попыток", anchorError: "ошибка", anchorNoError: "без ошибки", anchorCountsLabel: "Счётчики статусов якорения Fabric",
      outcomeEyebrow: "УПРАВЛЯЕМАЯ ОБРАТНАЯ СВЯЗЬ", outcomeTitle: "Фактический результат и адаптивная калибровка", outcomeSubtitle: "Неизменяемый результат запускает обучение; внедрение разрешается только фиксированными воротами качества и целостности.",
      outcomeDefaulted: "Зафиксирован дефолт", outcomeDaysPastDue: "Просрочка, дней", outcomeLossAmount: "Сумма потерь", outcomeObservedAt: "Наблюдалось", outcomeEvidenceReference: "Ссылка на доказательство (хешируется только в браузере)", outcomeProvenance: "Происхождение", outcomeControlledDemo: "Контролируемая симуляция", outcomeExternalVerified: "Внешне проверено", outcomeSubmit: "Зафиксировать результат", outcomeSubmitting: "Хеширование и запись…",
      outcomeExploratory: "EXPLORATORY · исследовательский", outcomeEligible: "ELIGIBLE · ворота обучения пройдены", outcomeFailed: "FAILED · обучение не завершено", outcomeRunMissing: "NO RUN · запуск отсутствует", outcomeLineageTitle: "Линия происхождения результата", outcomeMetrics: "Brier / log loss до → после", outcomeArtifactIntegrity: "Целостность артефакта", outcomeSample: "выборка", outcomePositive: "положительных", outcomeNegative: "отрицательных", outcomeRun: "Запуск калибровки", outcomeTrainingStatus: "Статус обучения", outcomeDeploymentStatus: "Статус внедрения", outcomeActiveVersion: "Текущая активная версия", outcomeNoActiveVersion: "Активной версии пока нет", outcomeDeploymentScope: "Контур данных", outcomeActivationReason: "Решение ворот", outcomeActivatedAt: "Активировано", outcomeRollback: "Вернуть предыдущую версию", outcomeRollbackDone: "Предыдущая версия калибровки восстановлена", outcomeRecorded: "Результат зафиксирован идемпотентно", outcomeRetryIdentity: "При сетевом повторе сохраняются те же данные и idempotency key.", outcomeBoundary: "Адаптируется только слой Platt; TGNN не переобучается. Активная версия действует лишь на новые оценки и сохраняет исходный и итоговый баллы.", outcomeRequired: "Заполните ссылку на доказательство и корректные значения.", outcomeNoMetrics: "метрики недоступны", outcomeDerivedFacts: "Производные факты закрытия", outcomeDerivedDefault: "дефолт подтвержден историей просрочки", outcomeDerivedSettled: "погашение подтверждено платежами"
    },
    zh: {
      loginTitle: "进入业务工作台", loginSubtitle: "五类参与者共同将一笔申请从供应商推进到可验证的审计轨迹。",
      researchBoundary: "基于合成数据的演示原型 · PostgreSQL · 可验证事件日志", demoAccess: "演示访问", chooseRole: "选择角色", demoRoleGuide: "演示角色顺序", demoRoleSelected: "已选择角色：{role}。请检查账户信息后点击登录。",
      orCredentials: "或输入账户信息", username: "用户名", password: "密码", signIn: "登录系统", logout: "退出",
      commonPassword: "演示角色通用密码：", navWorkflow: "业务工作台", workflowEyebrow: "基于角色的业务流程",
      workflowTitle: "供应链融资", refresh: "刷新数据", currentStation: "当前工作站", allApplications: "可查看申请",
      myTasks: "我的待办", activeStatus: "活跃状态", database: "数据存储", newApplication: "新建融资申请",
      newApplicationHint: "填写交易信息。申请将先保存为草稿，确认后可提交。", coreEnterpriseCode: "核心企业代码",
      contractNumber: "合同编号", invoiceNumber: "发票编号", amount: "融资金额，CNY", termDays: "期限，天",
      paymentDelay: "付款延迟，天", counterpartyRisk: "交易对手风险，0–1", relationshipMonths: "合作关系，月",
      transactions30d: "近30天交易数", invoiceMismatch: "存在单据不一致", saveDraft: "保存草稿", updateDraft: "保存修改",
      applicationQueue: "申请队列", selectApplication: "请选择申请", selectApplicationHint: "这里将显示交易信息、当前角色允许的操作和完整交接历史。",
      workflowTimeline: "责任交接历史", timelineHint: "每次状态变更均以原子事务执行，并记录操作角色。", timelineEmpty: "选择申请后查看历史。",
      roleSupplier: "供应商", roleCoreEnterprise: "核心企业", roleFinancier: "融资方", roleRiskManager: "风险经理", roleAuditor: "审计员", roleAdmin: "管理员", openRiskDetail: "风险详情", adminMission: "查看全部风险数据、配置风险规则并分派处理任务。",
      stageCreate: "创建与提交", stageConfirm: "交易确认", stageDecide: "评估与决策", stageControl: "风险控制", stageAudit: "审计核验",
      supplierMission: "创建申请、核对交易信息，并提交给核心企业。", coreMission: "确认合同与发票真实性，或将申请退回供应商。",
      financierMission: "执行风险评估、查看模型结果并作出融资决策。", riskMission: "根据决策设置控制措施并写入日志。",
      auditorMission: "核验完整业务流程与日志完整性后关闭申请。", queueAll: "显示当前角色可查看的申请；有可执行操作的待办优先排列。",
      noApplications: "当前角色暂无可查看的申请。", version: "版本", supplier: "供应商", core: "核心企业", contract: "合同",
      invoice: "发票", term: "期限", risk: "风险评分", decision: "决策", actionStation: "当前可执行操作",
      noAction: "当前阶段没有该角色可执行的操作。", comment: "交接备注", commentPlaceholder: "简要记录操作依据……",
      payableCeiling: "确认的应付上限", payableCeilingHint: "不得低于发票金额；它是证明的公开上界。",
      payableConfirmed: "已确认应付上限", proofEvidence: "发票额度证明", proofCircuit: "电路", proofDigest: "证明哈希",
      proofCommitment: "金额承诺", proofAbsent: "未附带证明", proofFallbackPrefix: "原因",
      edit: "修改草稿", submit: "提交申请", confirmTrade: "确认交易", returnTrade: "退回供应商", assessRisk: "执行风险评估",
      approve: "批准", manualReview: "人工复核", reject: "拒绝", applyControl: "记录控制措施", auditReview: "完成审计",
      created: "草稿已保存", updated: "修改已保存", actionComplete: "操作已完成", sessionExpired: "会话已结束，请重新登录。",
      loginFailed: "登录失败，请检查用户名和密码。", requestFailed: "操作失败", loading: "加载中……", signedIn: "登录成功",
      taskReady: "需要处理", timelineCreate: "创建申请", days: "天", cancelEdit: "新建草稿",
      draft: "草稿", submitted: "已提交", trade_returned: "已退回", trade_confirmed: "交易已确认", risk_assessed: "风险已评估",
      approved: "已批准", manual_review: "人工复核", rejected: "已拒绝", controlled: "已设置控制", audited: "审计已完成",
      create: "创建", create_draft: "创建草稿", update: "修改", confirm_trade: "确认交易", return_trade: "退回交易", assess_risk: "风险评估",
      decide: "融资决策", apply_control: "控制措施", audit: "审计核验",
      tradeEvidence: "交易凭证字段指纹", businessRiskEvidence: "业务评分证据", duplicateCheckPassed: "重复融资校验已通过",
      researchComparisonBoundary: "业务评分使用透明基线模型；TGNN 仅作为独立科研对照展示。",
      duplicate_invoice_claim: "该发票已被另一笔融资申请使用",
      navFacilities: "融资生命周期", facilityEyebrow: "受控融资生命周期", facilityTitle: "从放款到结清",
      facilitySubtitle: "在同一份卷宗中查看精确金额、分期计划和当前角色允许执行的操作。", facilityCreate: "创建融资设施",
      facilityCreateHint: "填写已批准且完成审计的申请；分期金额之和必须与本金完全一致。", facilityRequestId: "已批准申请 ID",
      facilityPrincipal: "本金", facilityCurrency: "币种", facilityDueOne: "第 1 期到期日", facilityAmountOne: "第 1 期金额",
      facilityDueTwo: "第 2 期到期日", facilityAmountTwo: "第 2 期金额", facilityCreateAction: "创建还款计划",
      facilityListTitle: "融资卷宗", facilityListHint: "可见范围由组织和当前角色共同决定。", facilitySelect: "请选择融资卷宗",
      facilitySelectHint: "这里将显示精确余额、分期、付款和当前角色操作。", facilityPaid: "已确认偿还", facilityOutstanding: "剩余余额",
      facilityInstallments: "还款计划", facilityPayments: "付款记录", facilityBoundaryTitle: "演示边界",
      facilityBoundary: "这是受控融资生命周期模拟；系统不会执行真实银行转账。", facilityNoItems: "暂无融资卷宗。融资方可从已批准申请创建。",
      facilityNoPayments: "尚未提交付款。", facilityEvidence: "放款证据", facilityActionStation: "当前角色操作",
      facilityNoAction: "当前阶段没有该角色可执行的操作。", facilityVersion: "命令版本", facilityInstallment: "分期", facilityDueDate: "到期日",
      facilityAmount: "金额", facilityPaidAmount: "已还", facilityPaymentReference: "付款参考号", facilityDecisionComment: "决策备注",
      facilitySubmitPayment: "提交付款", facilityConfirmPayment: "确认付款", facilityRejectPayment: "拒绝付款", facilityMarkOverdue: "标记逾期",
      facilityInitiate: "发起放款", facilityConfirmDisbursement: "确认放款", facilityClose: "关闭卷宗", facilityCreated: "融资还款计划已创建",
      facilityActionComplete: "融资生命周期已更新", facilityInvalidMoney: "请输入精确到分的正数金额。", facilityScheduleMismatch: "两期金额之和必须与本金完全一致。",
      facilityRequiredFields: "请填写该操作的必填字段。", ready_for_disbursement: "待放款", disbursed: "已发起放款", active: "进行中",
      overdue: "已逾期", repaid: "已还清", closed: "已关闭", installment_scheduled: "按计划", installment_partially_paid: "部分已还",
      installment_paid: "已还清", installment_overdue: "已逾期", payment_submitted: "待审核", payment_confirmed: "已确认", payment_rejected: "已拒绝",
      restructured: "重组履约", defaulted: "违约", in_disposal: "风险处置", in_recovery: "追偿", recovered: "追偿完毕", written_off: "已核销", installment_superseded: "已被新版本替代",
      facilityOpenDisposal: "发起风险处置", facilityCloseDisposal: "结束风险处置", facilityRestructure: "重组", facilityDeclareDefault: "认定违约",
      facilityStartRecovery: "启动追偿", facilityRecordRecovery: "登记追偿回款", facilityWriteOff: "核销余额",
      facilityReasonCode: "原因代码", facilityComment: "说明", facilityEvidenceReference: "证据引用（在浏览器中计算哈希）", facilityDaysPastDue: "逾期天数",
      facilityDefaultedAt: "违约时间", facilityRecoverySource: "追偿来源", facilityRecoveryReference: "回款参考号", facilityNewDueDate: "新到期日",
      facilityHistory: "状态历史", facilityContracts: "合同版本", facilityRecoveries: "追偿回款", facilityNoHistory: "暂无记录。",
      facilityArrears: "逾期欠款", facilityWrittenOff: "已核销", facilityNetLoss: "净损失", facilityRecoveryCollected: "已追回",
      facilityContractVersion: "版本", facilityDecisions: "风险决策",
      facility_not_found: "融资卷宗不存在或当前角色无权查看。", forbidden_role: "当前角色不能执行此操作。",
      facility_precondition_failed: "创建融资要求申请已批准、审计完成且本金一致。", facility_conflict: "数据已变化或操作不再可用。请刷新卷宗后使用新命令重试。",
      fabricAnchorEyebrow: "可选外部锚定", fabricAnchorTitle: "Fabric 锚定", fabricAnchorSubtitle: "审计事件哈希经事务型 outbox 发送；业务记录仍保存在 PostgreSQL。",
      fabricAnchorOptional: "可选高级模式：Fabric 不可用时业务流程仍会继续，哈希保留在队列中。", fabricAnchorConnected: "本次派发已收到真实 Fabric 确认；仅刷新列表并不探测 Gateway 当前状态。",
      anchorPending: "待处理", anchorRetry: "待重试", anchorAnchored: "已锚定", anchorPermanentFailed: "永久失败", anchorRefresh: "刷新", anchorDispatch: "派发一批", anchorRetryFailed: "重新入队", anchorRecent: "最近记录", anchorEmpty: "暂时没有待锚定记录。", anchorAttempt: "尝试次数", anchorError: "错误", anchorNoError: "无错误", anchorCountsLabel: "Fabric 锚定状态计数",
      outcomeEyebrow: "受控结果回流", outcomeTitle: "实际结果与自适应校准", outcomeSubtitle: "不可变结果触发训练；只有满足固定样本、质量和工件完整性门槛才允许激活。",
      outcomeDefaulted: "是否违约", outcomeDaysPastDue: "逾期天数", outcomeLossAmount: "损失金额", outcomeObservedAt: "观测时间", outcomeEvidenceReference: "证据引用（仅在浏览器内哈希）", outcomeProvenance: "来源", outcomeControlledDemo: "受控模拟", outcomeExternalVerified: "外部已核验", outcomeSubmit: "记录结果", outcomeSubmitting: "正在哈希并记录……",
      outcomeExploratory: "EXPLORATORY · 探索性", outcomeEligible: "ELIGIBLE · 训练门槛已满足", outcomeFailed: "FAILED · 训练未完成", outcomeRunMissing: "NO RUN · 缺少校准运行", outcomeLineageTitle: "结果血缘", outcomeMetrics: "Brier / log loss 校准前 → 校准后", outcomeArtifactIntegrity: "候选工件完整性", outcomeSample: "样本", outcomePositive: "正样本", outcomeNegative: "负样本", outcomeRun: "校准运行", outcomeTrainingStatus: "训练状态", outcomeDeploymentStatus: "部署状态", outcomeActiveVersion: "当前激活版本", outcomeNoActiveVersion: "暂无激活版本", outcomeDeploymentScope: "数据范围", outcomeActivationReason: "门控结论", outcomeActivatedAt: "激活时间", outcomeRollback: "回滚到上一版本", outcomeRollbackDone: "已恢复上一校准版本", outcomeRecorded: "结果已按幂等语义记录", outcomeRetryIdentity: "网络重试会保留相同数据和 idempotency key。", outcomeBoundary: "仅自适应训练 Platt 层，不重训 TGNN。激活版本只作用于新评估，并同时保留原始分和最终分。", outcomeRequired: "请填写证据引用和有效数值。", outcomeNoMetrics: "暂无指标", outcomeDerivedFacts: "由结清事实推导", outcomeDerivedDefault: "逾期历史确认违约", outcomeDerivedSettled: "付款记录确认已结清"
    }
  };

  const ROLE_META = {
    supplier: { key: "roleSupplier", mission: "supplierMission", seal: "S", color: "#2768ee", order: 0 },
    core_enterprise: { key: "roleCoreEnterprise", mission: "coreMission", seal: "C", color: "#7a5ce0", order: 1 },
    financier: { key: "roleFinancier", mission: "financierMission", seal: "F", color: "#15976c", order: 2 },
    risk_manager: { key: "roleRiskManager", mission: "riskMission", seal: "R", color: "#d48a16", order: 3 },
    auditor: { key: "roleAuditor", mission: "auditorMission", seal: "A", color: "#b94650", order: 4 },
    admin: { key: "roleAdmin", mission: "adminMission", seal: "M", color: "#334155", order: 5 }
  };

  const STATUS_STAGE = {
    draft: 0, trade_returned: 0, submitted: 1, trade_confirmed: 2, risk_assessed: 2,
    approved: 3, manual_review: 3, rejected: 3, controlled: 4, audited: 5
  };

  const state = {
    lang: localStorage.getItem("daibm-lang") || "ru", user: null,
    accounts: [], coreEnterprises: [], dashboard: null, tasks: [],
    applications: [], selected: null, editing: null, busy: false,
    facilities: [], selectedFacility: null, facilityPending: false,
    anchors: [], anchorsBusy: false, anchorLastSummary: null,
    outcomes: [], calibrationRuns: [], activeCalibration: null, outcomePending: false,
    outcomeIdempotencyKeys: Object.create(null)
  };

  const tr = (key) => COPY[state.lang][key] || key;
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[character]);
  const money = (value) => new Intl.NumberFormat(state.lang === "ru" ? "ru-RU" : "zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }).format(Number(value || 0));
  const normalizeMoneyInput = (value) => String(value ?? "").trim().replace(",", ".");

  function moneyToCents(value, allowZero = false) {
    const normalized = normalizeMoneyInput(value);
    if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)) throw new Error(tr("facilityInvalidMoney"));
    const [whole, fraction = ""] = normalized.split(".");
    const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
    if (cents < 0n || (!allowZero && cents === 0n)) throw new Error(tr("facilityInvalidMoney"));
    return cents;
  }

  function centsToMoney(cents) {
    const whole = cents / 100n;
    const fraction = String(cents % 100n).padStart(2, "0");
    return `${whole}.${fraction}`;
  }

  function exactFacilityMoney(value, currency) {
    const normalized = centsToMoney(moneyToCents(value, true));
    const [whole, fraction] = normalized.split(".");
    const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, state.lang === "ru" ? " " : ",");
    return `${grouped}.${fraction} ${escapeHtml(currency)}`;
  }

  function sumFacilityMoney(values) {
    return centsToMoney(values.reduce((total, value) => total + moneyToCents(value, true), 0n));
  }

  async function wfApi(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options
    });
    if (response.status === 204) return null;
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) {
      if (response.status === 401 && state.user) {
        state.user = null;
        document.body.classList.remove("authenticated");
        renderAccounts();
      }
      const code = payload?.detail?.code;
      const message = (code && COPY[state.lang][code]) || payload?.detail?.message || code || tr("requestFailed");
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function setBusy(value) {
    state.busy = value;
    document.querySelector("#view-workflow")?.classList.toggle("workflow-busy", value);
    document.querySelector("#loginButton").disabled = value;
  }

  function setFacilityPending(value) {
    state.facilityPending = value;
    const view = document.querySelector("#view-facilities");
    view?.classList.toggle("facility-pending", value);
    view?.setAttribute("aria-busy", String(value));
    view?.querySelectorAll("button, input, select").forEach((control) => { control.disabled = value; });
  }

  function notify(message, isError = false) {
    if (typeof window.toast === "function") {
      window.toast(message);
      document.querySelector("#toast")?.classList.toggle("workflow-toast-error", isError);
    }
  }

  function applyWorkflowLanguage() {
    document.querySelectorAll("[data-wf-i18n]").forEach((element) => {
      const value = tr(element.dataset.wfI18n);
      if (value) element.textContent = value;
    });
    document.querySelectorAll("[data-wf-i18n-aria-label]").forEach((element) => {
      element.setAttribute("aria-label", tr(element.dataset.wfI18nAriaLabel));
    });
    document.querySelector("#demoAccounts")?.setAttribute("aria-label", tr("demoRoleGuide"));
    renderAccounts();
    if (state.user) {
      renderWorkbench();
      renderFacilityWorkbench();
      renderAnchors();
    }
  }

  function renderAccounts() {
    const container = document.querySelector("#demoAccounts");
    if (!container) return;
    container.setAttribute("aria-label", tr("demoRoleGuide"));
    container.querySelectorAll("[data-demo-username]").forEach((button) => {
      const roleName = button.dataset.demoRole;
      const role = ROLE_META[roleName];
      const account = state.accounts.find((candidate) => candidate.username === button.dataset.demoUsername);
      const label = button.querySelector("[data-demo-role-label]");
      const detail = button.querySelector("[data-demo-account-detail]");
      if (label && role) label.textContent = tr(role.key);
      if (detail) detail.textContent = account
        ? `${account.username} · ${account.organization_code}`
        : button.dataset.demoUsername;
      button.style.setProperty("--guide-color", role?.color || "#2768ee");
      if (button.dataset.guideBound === "true") return;
      button.dataset.guideBound = "true";
      button.addEventListener("click", () => selectDemoRole(button));
    });
  }

  function selectDemoRole(button) {
    const username = button.dataset.demoUsername;
    const role = ROLE_META[button.dataset.demoRole];
    document.querySelector('#loginForm input[name="username"]').value = username;
    document.querySelector('#loginForm input[name="password"]').value = "Demo123!";
    const announcement = document.querySelector("#demoRoleAnnouncement");
    if (announcement) {
      announcement.textContent = tr("demoRoleSelected").replace("{role}", tr(role?.key || button.dataset.demoRole));
    }
    document.querySelector("#loginButton").focus();
  }

  async function login(username, password) {
    const errorElement = document.querySelector("#loginError");
    errorElement.textContent = "";
    setBusy(true);
    try {
      const result = await wfApi("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      state.user = result.user;
      document.body.classList.add("authenticated");
      await enterWorkbench();
      notify(tr("signedIn"));
    } catch (_) {
      if (state.user) {
        try { await wfApi("/api/v1/auth/logout", { method: "POST" }); }
        catch (_) { /* the local rollback still applies */ }
        state.user = null;
        state.coreEnterprises = [];
        state.applications = [];
        state.selected = null;
        state.editing = null;
        state.facilities = [];
        state.selectedFacility = null;
        document.body.classList.remove("authenticated");
        renderAccounts();
      }
      errorElement.textContent = tr("loginFailed");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    try { await wfApi("/api/v1/auth/logout", { method: "POST" }); } catch (_) { /* local logout still applies */ }
    state.user = null;
    state.applications = [];
    state.coreEnterprises = [];
    state.selected = null;
    state.editing = null;
    state.facilities = [];
    state.selectedFacility = null;
    state.anchors = [];
    state.anchorLastSummary = null;
    state.outcomes = [];
    state.calibrationRuns = [];
    state.activeCalibration = null;
    state.outcomeIdempotencyKeys = Object.create(null);
    document.body.classList.remove("authenticated");
    delete document.body.dataset.workflowRole;
    document.querySelector("#loginForm").reset();
    document.querySelector('#loginForm input[name="username"]').value = "supplier.demo";
    document.querySelector('#loginForm input[name="password"]').value = "Demo123!";
    renderAccounts();
  }

  function configureRoleNavigation() {
    const role = state.user.role;
    const rules = {
      workflow: true,
      facilities: true,
      overview: role === "financier" || role === "auditor",
      review: role === "financier" || role === "auditor",
      research: role === "financier" || role === "auditor",
      ledger: role === "auditor",
      governance: ["auditor", "risk_manager", "financier", "admin"].includes(role),
      feedback: role === "auditor" || role === "risk_manager",
      snapshots: ["auditor", "risk_manager", "financier"].includes(role),
      model: true,
      riskdash: ["admin", "risk_manager", "auditor", "financier"].includes(role),
      alerts: ["admin", "risk_manager", "auditor"].includes(role),
      tasks: ["admin", "risk_manager", "auditor"].includes(role),
      rules: ["admin", "risk_manager", "auditor"].includes(role),
      riskdetail: true
    };
    document.querySelectorAll("[data-view-button]").forEach((button) => {
      button.hidden = !rules[button.dataset.viewButton];
    });
  }

  async function refreshLegacyForRole() {
    if (state.user.role === "auditor" && typeof window.refreshAll === "function") {
      await window.refreshAll();
      return;
    }
    if (state.user.role === "financier" && typeof window.renderAll === "function") {
      try {
        [dashboardState, requestsState] = await Promise.all([api("/api/dashboard"), api("/api/requests")]);
        ledgerState = [];
        verificationState = { valid: true };
        await refreshResearchStatus(false);
        renderAll();
      } catch (_) { /* workflow remains independently usable */ }
    }
  }

  window.refreshLegacyForRole = refreshLegacyForRole;

  async function enterWorkbench() {
    const role = ROLE_META[state.user.role];
    document.body.dataset.workflowRole = state.user.role;
    document.body.dataset.workflowUserId = state.user.user_id;
    document.body.dataset.workflowUsername = state.user.username;
    renderCurrentUser();
    document.querySelector("#view-workflow").style.setProperty("--role-color", role.color);
    document.querySelector("#view-facilities").style.setProperty("--role-color", role.color);
    configureRoleNavigation();
    window.switchView(state.user.role === "admin" ? "riskdash" : "workflow");
    state.coreEnterprises = state.user.role === "supplier"
      ? await wfApi("/api/v1/organizations/core-enterprises")
      : [];
    await Promise.all([refreshWorkflow(), refreshFacilities(), state.user.role === "auditor" ? refreshAnchors() : Promise.resolve(), state.user.role === "auditor" ? refreshOutcomes() : Promise.resolve()]);
    await refreshLegacyForRole();
  }

  function anchorDisplayStatus(anchor) {
    return anchor.status === "pending" && Number(anchor.attempt_count) > 0 ? "retry" : anchor.status;
  }

  function setAnchorPending(value) {
    state.anchorsBusy = value;
    const panel = document.querySelector("#fabricAnchorPanel");
    panel?.setAttribute("aria-busy", String(value));
    panel?.querySelectorAll("button").forEach((button) => { button.disabled = value; });
  }

  function renderAnchors() {
    const panel = document.querySelector("#fabricAnchorPanel");
    if (!panel) return;
    const isAuditor = state.user?.role === "auditor";
    panel.hidden = !isAuditor;
    if (!isAuditor) return;
    const counts = { pending: 0, retry: 0, anchored: 0, permanent_failed: 0 };
    state.anchors.forEach((anchor) => { counts[anchorDisplayStatus(anchor)] += 1; });
    document.querySelector("#anchorPendingCount").textContent = counts.pending;
    document.querySelector("#anchorRetryCount").textContent = counts.retry;
    document.querySelector("#anchorAnchoredCount").textContent = counts.anchored;
    document.querySelector("#anchorFailedCount").textContent = counts.permanent_failed;
    document.querySelector("#fabricAnchorMode").textContent = state.anchorLastSummary?.anchored > 0 ? tr("fabricAnchorConnected") : tr("fabricAnchorOptional");
    const summary = state.anchorLastSummary;
    document.querySelector("#anchorDispatchSummary").textContent = summary
      ? `${tr("anchorAnchored")}: ${summary.anchored} · ${tr("anchorRetry")}: ${summary.retryable} · ${tr("anchorPermanentFailed")}: ${summary.permanent_failed}`
      : "";
    const list = document.querySelector("#anchorList");
    if (!state.anchors.length) {
      list.innerHTML = `<p class="anchor-empty">${escapeHtml(tr("anchorEmpty"))}</p>`;
      return;
    }
    list.innerHTML = state.anchors.slice(0, 12).map((anchor) => {
      const displayStatus = anchorDisplayStatus(anchor);
      const retry = anchor.status === "permanent_failed"
        ? `<button type="button" class="anchor-retry" data-anchor-retry="${escapeHtml(anchor.anchor_id)}">${escapeHtml(tr("anchorRetryFailed"))}</button>`
        : "";
      return `<article class="anchor-record" data-anchor-status="${escapeHtml(displayStatus)}" data-anchor-id="${escapeHtml(anchor.anchor_id)}"><div class="anchor-hash"><span>${escapeHtml(String(anchor.event_hash).slice(0, 12))}…</span><small>${escapeHtml(String(anchor.anchor_id).slice(0, 8))}</small></div><b>${escapeHtml(tr(displayStatus === "permanent_failed" ? "anchorPermanentFailed" : `anchor${displayStatus[0].toUpperCase()}${displayStatus.slice(1)}`))}</b><p>${escapeHtml(tr("anchorAttempt"))}: ${Number(anchor.attempt_count) || 0}<br>${escapeHtml(tr("anchorError"))}: ${escapeHtml(anchor.last_error_code || tr("anchorNoError"))}</p>${retry}</article>`;
    }).join("");
  }

  async function refreshAnchors() {
    if (state.user?.role !== "auditor") return;
    setAnchorPending(true);
    try {
      state.anchors = await wfApi("/api/v1/anchors?limit=50");
      renderAnchors();
    } catch (error) { notify(error.message, true); }
    finally { setAnchorPending(false); }
  }

  async function dispatchAnchors() {
    if (state.user?.role !== "auditor") return;
    setAnchorPending(true);
    try {
      const summary = await wfApi("/api/v1/anchor-dispatches", { method: "POST", body: JSON.stringify({ limit: 20 }) });
      state.anchorLastSummary = summary;
      state.anchors = await wfApi("/api/v1/anchors?limit=50");
      renderAnchors();
    } catch (error) { notify(error.message, true); }
    finally { setAnchorPending(false); }
  }

  async function retryAnchor(anchorId) {
    const anchor = state.anchors.find((candidate) => candidate.anchor_id === anchorId);
    if (!anchor || anchor.status !== "permanent_failed" || state.user?.role !== "auditor") return;
    setAnchorPending(true);
    try {
      await wfApi(`/api/v1/anchors/${anchorId}/retry`, { method: "POST" });
      state.anchors = await wfApi("/api/v1/anchors?limit=50");
      renderAnchors();
    } catch (error) { notify(error.message, true); }
    finally { setAnchorPending(false); }
  }

  async function refreshWorkflow(preferredId = null) {
    if (!state.user) return;
    setBusy(true);
    try {
      const [dashboard, tasks, applications] = await Promise.all([
        wfApi("/api/v1/dashboard"), wfApi("/api/v1/tasks"), wfApi("/api/v1/applications?limit=200")
      ]);
      state.dashboard = dashboard;
      state.tasks = tasks.items;
      const taskIds = new Set(state.tasks.map((item) => item.request_id));
      state.applications = [...applications].sort((left, right) => Number(taskIds.has(right.request_id)) - Number(taskIds.has(left.request_id)) || String(right.updated_at).localeCompare(String(left.updated_at)));
      const selectedId = preferredId || state.selected?.request_id;
      state.selected = state.applications.find((item) => item.request_id === selectedId) || state.applications[0] || null;
      renderWorkbench();
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
  }

  function renderWorkbench() {
    if (!state.user) return;
    const role = ROLE_META[state.user.role];
    renderCurrentUser();
    document.querySelector("#roleMission").textContent = tr(role.mission);
    const banner = document.querySelector("#roleBanner");
    banner.querySelector(".role-seal").textContent = role.seal;
    banner.querySelector("b").textContent = `${tr(role.key)} · ${state.user.organization_name}`;
    banner.querySelector("p").textContent = tr(role.mission);
    document.querySelector("#supplierCreate").hidden = state.user.role !== "supplier";
    renderCoreEnterpriseOptions();
    document.querySelector("#workflowApplicationCount").textContent = state.dashboard?.application_count ?? 0;
    document.querySelector("#workflowTaskCount").textContent = state.dashboard?.task_count ?? 0;
    document.querySelector("#workflowStatusCount").textContent = Object.keys(state.dashboard?.status_counts || {}).length;
    document.querySelector("#taskBadge").textContent = state.tasks.length;
    document.querySelector("#queueHint").textContent = tr("queueAll");
    renderCustodyRail();
    renderApplications();
    renderDetail();
    renderTimeline();
  }

  function renderCurrentUser() {
    const role = ROLE_META[state.user.role];
    const container = document.querySelector("#currentUser");
    container.hidden = false;
    container.innerHTML = `<b>${escapeHtml(state.user.display_name)}</b><small>${escapeHtml(tr(role.key))} · ${escapeHtml(state.user.organization_code)}</small>`;
  }

  function renderCustodyRail() {
    const currentRoleIndex = ROLE_META[state.user.role].order;
    const workflowIndex = state.selected ? STATUS_STAGE[state.selected.status] : currentRoleIndex;
    document.querySelectorAll("#custodyRail [data-role-stage]").forEach((item, index) => {
      item.classList.toggle("complete", workflowIndex === 5 || index < workflowIndex);
      item.classList.toggle("current", workflowIndex !== 5 && index === workflowIndex);
    });
  }

  function renderApplications() {
    const container = document.querySelector("#workflowApplications");
    if (!state.applications.length) {
      container.innerHTML = `<div class="queue-empty"><p>${escapeHtml(tr("noApplications"))}</p></div>`;
      return;
    }
    container.innerHTML = state.applications.map((application) => {
      const task = application.allowed_actions.length ? `<span class="status-chip" data-status="${escapeHtml(application.status)}">${escapeHtml(tr("taskReady"))}</span>` : `<span class="status-chip" data-status="${escapeHtml(application.status)}">${escapeHtml(tr(application.status))}</span>`;
      return `<button class="application-item ${state.selected?.request_id === application.request_id ? "selected" : ""}" type="button" data-application-id="${escapeHtml(application.request_id)}">
        <span><b>${escapeHtml(application.contract_number)}</b><small>${escapeHtml(application.applicant_id)} · ${escapeHtml(application.invoice_number)}</small>${task}</span>
        <span class="application-amount"><b>${escapeHtml(money(application.amount))}</b><small>v${application.version}</small></span>
      </button>`;
    }).join("");
    container.querySelectorAll("[data-application-id]").forEach((button) => button.addEventListener("click", () => {
      state.selected = state.applications.find((item) => item.request_id === button.dataset.applicationId);
      renderWorkbench();
    }));
  }

  function detailField(label, value) {
    return `<div class="detail-field"><span>${escapeHtml(label)}</span><b>${escapeHtml(value ?? "—")}</b></div>`;
  }

  function renderCoreEnterpriseOptions() {
    const select = document.querySelector(
      '#applicationForm select[name="core_enterprise_organization_code"]'
    );
    if (!select) return;
    const selectedCode = state.editing?.core_enterprise_organization_code
      || select.value;
    select.innerHTML = state.coreEnterprises.map((organization) =>
      `<option value="${escapeHtml(organization.organization_code)}">${escapeHtml(organization.name)} · ${escapeHtml(organization.organization_code)}</option>`
    ).join("");
    if (state.coreEnterprises.some(
      (organization) => organization.organization_code === selectedCode
    )) select.value = selectedCode;
  }

  const compactHash = (value) => value ? `${String(value).slice(0, 10)}…${String(value).slice(-8)}` : "—";

  function renderDetail() {
    const container = document.querySelector("#workflowDetail");
    const application = state.selected;
    if (!application) {
      container.innerHTML = `<div class="workflow-empty"><span>↳</span><b>${escapeHtml(tr("selectApplication"))}</b><p>${escapeHtml(tr("selectApplicationHint"))}</p></div>`;
      return;
    }
    const risk = application.risk_score == null ? "—" : Number(application.risk_score).toFixed(4);
    const tradeEvidence = application.trade_evidence;
    const riskEvidence = application.risk_evidence;
    const coreEnterprise = [
      application.core_enterprise_organization_name,
      application.core_enterprise_organization_code
    ].filter(Boolean).join(" · ");
    const scoreLineage = (
      typeof riskEvidence?.raw_score === "number"
      && typeof riskEvidence?.final_score === "number"
    ) ? `${riskEvidence.raw_score.toFixed(4)} → ${riskEvidence.final_score.toFixed(4)}` : "—";
    const calibrationLineage = riskEvidence?.calibration_fallback_code
      || compactHash(riskEvidence?.calibration_run_id || riskEvidence?.input_sha256);
    const proof = application.invoice_limit_evidence;
    const proofMarkup = proof
      ? `<div class="workflow-evidence"><h3>${escapeHtml(tr("proofEvidence"))}</h3>
      <div class="detail-fields">
        ${detailField(tr("payableConfirmed"), money(proof.confirmed_payable_amount))}
        ${detailField(tr("proofCircuit"), proof.circuit_version || "—")}
        ${detailField(tr("proofDigest"), proof.proof_sha256 ? compactHash(proof.proof_sha256) : `${tr("proofAbsent")}${proof.fallback_code ? ` (${tr("proofFallbackPrefix")}: ${proof.fallback_code})` : ""}`)}
        ${detailField(tr("proofCommitment"), proof.payable_commitment ? compactHash(proof.payable_commitment) : "—")}
      </div>
      <p>${escapeHtml(proof.statement)}</p>
    </div>` : "";
    const evidenceMarkup = `<div class="workflow-evidence">
      <article><span>${escapeHtml(tr("tradeEvidence"))}</span><b title="${escapeHtml(tradeEvidence?.fingerprint_sha256)}">${escapeHtml(compactHash(tradeEvidence?.fingerprint_sha256))}</b><small>${escapeHtml(tradeEvidence ? tr("duplicateCheckPassed") : "—")}</small></article>
      <article><span>${escapeHtml(tr("businessRiskEvidence"))}</span><b title="${escapeHtml(riskEvidence?.input_sha256)}">${escapeHtml(riskEvidence?.engine_version || "—")}</b><small>${escapeHtml(scoreLineage)} · ${escapeHtml(calibrationLineage)}</small></article>
      <p>${escapeHtml(tr("researchComparisonBoundary"))}</p>
    </div>`;
    container.innerHTML = `<div class="detail-top"><div><span class="status-chip" data-status="${escapeHtml(application.status)}">${escapeHtml(tr(application.status))}</span><h2>${escapeHtml(application.contract_number)}</h2><p>${escapeHtml(application.request_id)}</p></div><div class="detail-version"><span>${escapeHtml(tr("version"))}</span><b>v${application.version}</b></div></div>
      <div class="detail-fields">
        ${detailField(tr("supplier"), application.applicant_id)}${detailField(tr("core"), coreEnterprise)}${detailField(tr("amount"), money(application.amount))}
        ${detailField(tr("invoice"), application.invoice_number)}${detailField(tr("term"), `${application.term_days} ${tr("days")}`)}${detailField(tr("paymentDelay"), `${application.features.payment_delay_days} ${tr("days")}`)}
      </div>
      <div class="risk-result"><span>${escapeHtml(tr("risk"))}<strong>${escapeHtml(risk)}</strong></span><span>${escapeHtml(tr("decision"))}<strong>${escapeHtml(application.decision ? tr(application.decision) : "—")}</strong></span></div>
      ${evidenceMarkup}
      ${riskEvidence ? `<div id="riskDecisionDetail" class="workflow-evidence risk-decision-detail" data-request-id="${escapeHtml(application.request_id)}"></div>` : ""}
      ${proofMarkup}
      ${renderActionStation(application)}`;
    if (riskEvidence && typeof window.governanceRiskDecision === "function") {
      window.governanceRiskDecision(application.request_id);
    }
  }

  function renderActionStation(application) {
    const actions = application.allowed_actions;
    if (!actions.length) return `<div class="action-note">${escapeHtml(tr("noAction"))}</div>`;
    const comment = actions.some((action) => ["confirm_trade","return_trade","decide","apply_control","audit"].includes(action))
      ? `<label><span>${escapeHtml(tr("comment"))}</span><textarea id="workflowComment" class="action-input" placeholder="${escapeHtml(tr("commentPlaceholder"))}"></textarea></label>` : "";
    // The acknowledged ceiling is the public bound of the invoice-limit
    // proof, so it is captured with the confirmation it authorises.
    const ceiling = actions.includes("confirm_trade")
      ? `<label><span>${escapeHtml(tr("payableCeiling"))}</span><input id="workflowPayableCeiling" class="action-input" type="number" min="${escapeHtml(String(application.amount))}" step="0.01" value="${escapeHtml(Number(application.amount).toFixed(2))}"><small>${escapeHtml(tr("payableCeilingHint"))}</small></label>` : "";
    const buttons = [];
    if (actions.includes("update")) buttons.push(`<button class="btn btn-soft" type="button" data-workflow-action="edit">${escapeHtml(tr("edit"))}</button>`);
    if (actions.includes("submit")) buttons.push(`<button class="btn btn-primary" type="button" data-workflow-action="submit">${escapeHtml(tr("submit"))}</button>`);
    if (actions.includes("confirm_trade")) buttons.push(`<button class="btn btn-primary" type="button" data-workflow-action="confirm">${escapeHtml(tr("confirmTrade"))}</button>`);
    if (actions.includes("return_trade")) buttons.push(`<button class="btn btn-danger" type="button" data-workflow-action="return">${escapeHtml(tr("returnTrade"))}</button>`);
    if (actions.includes("assess_risk")) buttons.push(`<button class="btn btn-primary" type="button" data-workflow-action="assess">${escapeHtml(tr("assessRisk"))}</button>`);
    if (actions.includes("decide")) buttons.push(`<button class="btn btn-primary" type="button" data-workflow-action="decision" data-decision="approved">${escapeHtml(tr("approve"))}</button><button class="btn btn-soft" type="button" data-workflow-action="decision" data-decision="manual_review">${escapeHtml(tr("manualReview"))}</button><button class="btn btn-danger" type="button" data-workflow-action="decision" data-decision="rejected">${escapeHtml(tr("reject"))}</button>`);
    if (actions.includes("apply_control")) buttons.push(`<button class="btn btn-primary" type="button" data-workflow-action="control">${escapeHtml(tr("applyControl"))}</button>`);
    if (actions.includes("audit")) buttons.push(`<button class="btn btn-primary" type="button" data-workflow-action="audit">${escapeHtml(tr("auditReview"))}</button>`);
    return `<div class="action-station"><h3>${escapeHtml(tr("actionStation"))}</h3><p>${escapeHtml(tr(ROLE_META[state.user.role].mission))}</p>${ceiling}${comment}<div class="action-buttons">${buttons.join("")}</div></div>`;
  }

  function renderTimeline() {
    const container = document.querySelector("#workflowTimeline");
    const timeline = state.selected?.timeline || [];
    if (!timeline.length) {
      container.innerHTML = `<p class="timeline-empty">${escapeHtml(tr("timelineEmpty"))}</p>`;
      return;
    }
    container.innerHTML = timeline.map((event, index) => `<article class="timeline-event"><span>${String(index + 1).padStart(2,"0")} · ${escapeHtml(new Date(event.created_at).toLocaleString(state.lang === "ru" ? "ru-RU" : "zh-CN"))}</span><b>${escapeHtml(tr(event.action_type))}</b><small>${escapeHtml(event.actor_display_name)}<br>${escapeHtml(tr(ROLE_META[event.actor_role]?.key || event.actor_role))} · ${escapeHtml(event.organization_code)}</small><p>${escapeHtml(event.comment || "—")}</p></article>`).join("");
  }

  async function refreshFacilities(preferredId = null) {
    if (!state.user) return;
    try {
      const facilities = await wfApi("/api/v1/facilities?limit=200");
      state.facilities = facilities;
      const selectedId = preferredId || state.selectedFacility?.facility_id;
      state.selectedFacility = facilities.find((facility) => facility.facility_id === selectedId) || facilities[0] || null;
      renderFacilityWorkbench();
    } catch (error) {
      notify(error.message, true);
    }
  }

  async function refreshOutcomes() {
    if (state.user?.role !== "auditor") {
      state.outcomes = [];
      state.calibrationRuns = [];
      state.activeCalibration = null;
      return;
    }
    try {
      const activeDeploymentPath = "/api/v1/calibration-deployments/active";
      [state.outcomes, state.calibrationRuns, state.activeCalibration] = await Promise.all([
        wfApi("/api/v1/outcomes?limit=50"),
        wfApi("/api/v1/calibration-runs?limit=50"),
        wfApi(`${activeDeploymentPath}?scope=controlled_demo`).catch((error) => {
          if (error.status === 404) return null;
          throw error;
        })
      ]);
      renderFacilityWorkbench();
    } catch (error) { notify(error.message, true); }
  }

  function localObservedAt(closedAt) {
    const closure = Date.parse(closedAt || "");
    const instant = Math.max(Date.now(), Number.isFinite(closure) ? closure + 1000 : 0);
    const rounded = new Date(Math.ceil(instant / 1000) * 1000);
    const local = new Date(rounded.getTime() - rounded.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 19);
  }

  function outcomeMetric(run, name) {
    const before = run?.metrics_before?.[name];
    const after = run?.metrics_after?.[name];
    if (!Number.isFinite(before) || !Number.isFinite(after)) return tr("outcomeNoMetrics");
    return `${Number(before).toFixed(4)} → ${Number(after).toFixed(4)}`;
  }

  function renderActualOutcomePanel(facility) {
    if (!(state.user.role === "auditor" && facility.status === "closed")) return "";
    const outcome = state.outcomes.find((item) => item.facility_id === facility.facility_id);
    const run = outcome ? state.calibrationRuns.find((item) => item.trigger_outcome_id === outcome.outcome_id) : null;
    if (!outcome) {
      state.outcomeIdempotencyKeys[facility.facility_id] ||= crypto.randomUUID();
      return `<section id="actualOutcomePanel" class="actual-outcome-panel" aria-labelledby="actualOutcomeTitle">
        <div class="outcome-heading"><div><span>${escapeHtml(tr("outcomeEyebrow"))}</span><h3 id="actualOutcomeTitle">${escapeHtml(tr("outcomeTitle"))}</h3><p>${escapeHtml(tr("outcomeSubtitle"))}</p></div><b>PLATT · GOVERNED AUTO</b></div>
        <form id="actualOutcomeForm" class="actual-outcome-form" data-facility-id="${escapeHtml(facility.facility_id)}">
          <p class="outcome-derived-facts">${escapeHtml(tr("outcomeDerivedFacts"))}: ${escapeHtml(facility.default_history?.length ? tr("outcomeDerivedDefault") : tr("outcomeDerivedSettled"))}; ${escapeHtml(tr("outcomeLossAmount"))}: ${escapeHtml(facility.realized_loss || "0.00")}</p>
          <label><span>${escapeHtml(tr("outcomeObservedAt"))}</span><input name="observed_at" type="datetime-local" step="1" value="${localObservedAt(facility.closed_at)}" required></label>
          <label class="outcome-evidence-reference"><span>${escapeHtml(tr("outcomeEvidenceReference"))}</span><input name="evidence_reference" autocomplete="off" maxlength="500" required></label>
          <label><span>${escapeHtml(tr("outcomeProvenance"))}</span><select name="provenance"><option value="CONTROLLED_DEMO">${escapeHtml(tr("outcomeControlledDemo"))}</option><option value="EXTERNAL_VERIFIED">${escapeHtml(tr("outcomeExternalVerified"))}</option></select></label>
          <button type="submit" class="btn btn-primary">${escapeHtml(tr("outcomeSubmit"))}</button>
        </form>
        <p class="outcome-retry-note">${escapeHtml(tr("outcomeRetryIdentity"))}</p><p id="outcomeSubmitError" class="facility-inline-error" role="alert"></p>
        <p class="outcome-boundary">${escapeHtml(tr("outcomeBoundary"))}</p><div id="outcomeLineage" hidden></div><div id="calibrationCandidate" hidden></div>
      </section>`;
    }
    const candidatePresentations = {
      "eligible_candidate": { key: "outcomeEligible", className: "eligible" },
      "exploratory_candidate": { key: "outcomeExploratory", className: "exploratory" },
      "failed": { key: "outcomeFailed", className: "failed" },
      "missing": { key: "outcomeRunMissing", className: "failed" }
    };
    const runStatus = run?.status || "missing";
    const candidatePresentation = candidatePresentations[runStatus] || candidatePresentations.failed;
    const active = state.activeCalibration;
    const canRollback = Boolean(active?.previous_active_run_id);
    return `<section id="actualOutcomePanel" class="actual-outcome-panel outcome-recorded" aria-labelledby="actualOutcomeTitle">
      <div class="outcome-heading"><div><span>${escapeHtml(tr("outcomeEyebrow"))}</span><h3 id="actualOutcomeTitle">${escapeHtml(tr("outcomeTitle"))}</h3><p>${escapeHtml(tr("outcomeRecorded"))}</p></div><b class="candidate-status ${candidatePresentation.className}">${escapeHtml(tr(candidatePresentation.key))}</b></div>
      <div id="outcomeLineage" class="outcome-lineage"><b>${escapeHtml(tr("outcomeLineageTitle"))}</b><dl><div><dt>outcome</dt><dd>${escapeHtml(outcome.outcome_id)}</dd></div><div><dt>assessment</dt><dd>${escapeHtml(outcome.risk_assessment_id)}</dd></div><div><dt>risk engine</dt><dd>${escapeHtml(outcome.risk_engine_version)}</dd></div><div><dt>model</dt><dd>${escapeHtml(outcome.model_version_id || "—")}</dd></div><div><dt>risk input</dt><dd title="${escapeHtml(outcome.risk_input_sha256)}">${escapeHtml(compactHash(outcome.risk_input_sha256))}</dd></div><div><dt>evidence</dt><dd title="${escapeHtml(outcome.evidence_sha256)}">${escapeHtml(compactHash(outcome.evidence_sha256))}</dd></div><div><dt>${escapeHtml(tr("outcomeProvenance"))}</dt><dd>${escapeHtml(outcome.provenance)}</dd></div></dl></div>
      <div id="calibrationCandidate" class="calibration-candidate"><div class="deployment-banner" data-deployment-status="${escapeHtml(active?.deployment_status || "missing")}">${escapeHtml(tr(active ? "outcomeActiveVersion" : "outcomeNoActiveVersion"))} · ${escapeHtml(active?.calibration_run_id || "—")}</div><div class="candidate-kpis"><article><span>${escapeHtml(tr("outcomeTrainingStatus"))}</span><b>${escapeHtml(runStatus)}</b></article><article><span>${escapeHtml(tr("outcomeDeploymentStatus"))}</span><b>${escapeHtml(run?.deployment_status || "—")}</b></article><article><span>${escapeHtml(tr("outcomeSample"))}</span><b>n=${Number(run?.sample_count || 0)} · +${Number(run?.positive_count || 0)} / −${Number(run?.negative_count || 0)}</b></article><article><span>Brier</span><b>${escapeHtml(outcomeMetric(run, "brier_score"))}</b></article><article><span>log loss</span><b>${escapeHtml(outcomeMetric(run, "log_loss"))}</b></article><article><span>${escapeHtml(tr("outcomeArtifactIntegrity"))}</span><b>${escapeHtml(run?.artifact_integrity || "not_applicable")}</b></article><article><span>${escapeHtml(tr("outcomeDeploymentScope"))}</span><b>${escapeHtml(run?.deployment_scope || "—")}</b></article><article><span>${escapeHtml(tr("outcomeActivationReason"))}</span><b>${escapeHtml(run?.activation_reason || "—")}</b></article><article><span>${escapeHtml(tr("outcomeActivatedAt"))}</span><b>${escapeHtml(run?.activated_at || "—")}</b></article></div><div class="deployment-actions"><p>${escapeHtml(tr("outcomeMetrics"))}</p>${canRollback ? `<button class="btn btn-danger" type="button" data-calibration-rollback="${escapeHtml(active.calibration_run_id)}">${escapeHtml(tr("outcomeRollback"))}</button>` : ""}</div></div>
      <p id="outcomeSubmitError" class="facility-inline-error" role="alert"></p><p class="outcome-boundary">${escapeHtml(tr("outcomeBoundary"))}</p><form id="actualOutcomeForm" hidden></form>
    </section>`;
  }

  async function hashEvidenceReference(reference) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(reference));
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  function setOutcomePending(value) {
    state.outcomePending = value;
    const panel = document.querySelector("#actualOutcomePanel");
    panel?.setAttribute("aria-busy", String(value));
    panel?.querySelectorAll("button,input,select").forEach((control) => { control.disabled = value; });
  }

  async function submitActualOutcome(event) {
    event.preventDefault();
    const form = event.target.closest?.("#actualOutcomeForm");
    if (!form) return;
    const facility = state.selectedFacility;
    if (!facility || state.outcomePending || state.user?.role !== "auditor" || facility.status !== "closed") return;
    const data = new FormData(form);
    const reference = String(data.get("evidence_reference") || "").trim();
    const observed = new Date(String(data.get("observed_at")));
    const errorElement = document.querySelector("#outcomeSubmitError");
    try {
      if (!reference || !Number.isFinite(observed.getTime())) throw new Error(tr("outcomeRequired"));
      setOutcomePending(true);
      const payload = {
        idempotency_key: state.outcomeIdempotencyKeys[facility.facility_id] ||= crypto.randomUUID(),
        observed_at: observed.toISOString(),
        evidence_sha256: await hashEvidenceReference(reference),
        provenance: String(data.get("provenance"))
      };
      const result = await wfApi(`/api/v1/facilities/${facility.facility_id}/actual-outcome`, { method: "POST", body: JSON.stringify(payload) });
      state.outcomes = [result.outcome, ...state.outcomes.filter((item) => item.outcome_id !== result.outcome.outcome_id)];
      const jobId = result.calibration_job?.job_id;
      if (jobId) {
        for (let attempt = 0; attempt < 20; attempt += 1) {
          const job = await wfApi(`/api/v1/calibration-jobs/${jobId}`);
          if (["completed", "failed"].includes(job.status)) break;
          await new Promise((resolve) => setTimeout(resolve, 250));
        }
        await refreshOutcomes();
      }
      notify(tr("outcomeRecorded"));
      renderFacilityDetail();
    } catch (error) {
      if (errorElement) errorElement.textContent = error.message;
      notify(error.message, true);
    } finally { setOutcomePending(false); }
  }

  async function rollbackCalibration(expectedActiveRunId) {
    if (state.outcomePending || state.user?.role !== "auditor") return;
    try {
      setOutcomePending(true);
      await wfApi("/api/v1/calibration-deployments/rollback", {
        method: "POST",
        body: JSON.stringify({ expected_active_run_id: expectedActiveRunId, deployment_scope: state.activeCalibration?.deployment_scope || "controlled_demo" })
      });
      await refreshOutcomes();
      notify(tr("outcomeRollbackDone"));
    } catch (error) { notify(error.message, true); }
    finally { setOutcomePending(false); }
  }

  function renderFacilityWorkbench() {
    if (!state.user) return;
    document.querySelector("#facilityCreatePanel").hidden = state.user.role !== "financier";
    document.querySelector("#facilityCount").textContent = state.facilities.length;
    renderFacilityList();
    renderFacilityDetail();
    setFacilityPending(state.facilityPending);
  }

  function renderFacilityList() {
    const container = document.querySelector("#facilityList");
    if (!state.facilities.length) {
      container.innerHTML = `<div class="queue-empty"><p>${escapeHtml(tr("facilityNoItems"))}</p></div>`;
      return;
    }
    container.innerHTML = state.facilities.map((facility) => `<button class="facility-list-item ${state.selectedFacility?.facility_id === facility.facility_id ? "selected" : ""}" type="button" data-facility-id="${escapeHtml(facility.facility_id)}">
      <span><b>${escapeHtml(tr(facility.status))}</b><small>${escapeHtml(facility.request_id)}</small></span>
      <span class="facility-list-balance"><b>${exactFacilityMoney(facility.outstanding_amount, facility.currency)}</b><small>v${facility.version}</small></span>
    </button>`).join("");
    container.querySelectorAll("[data-facility-id]").forEach((button) => button.addEventListener("click", () => {
      state.selectedFacility = state.facilities.find((facility) => facility.facility_id === button.dataset.facilityId) || null;
      renderFacilityWorkbench();
    }));
  }

  function renderFacilityDetail() {
    const container = document.querySelector("#facilityDetail");
    const facility = state.selectedFacility;
    if (!facility) {
      container.innerHTML = `<div class="workflow-empty"><span>₽</span><b>${escapeHtml(tr("facilitySelect"))}</b><p>${escapeHtml(tr("facilitySelectHint"))}</p></div><div id="facilityMoneyRail" hidden></div><div id="facilityInstallments" hidden></div><div id="facilityPayments" hidden></div><div id="facilityActions" hidden></div>`;
      return;
    }
    const paidAmount = sumFacilityMoney(facility.installments.map((item) => item.paid_amount));
    const installments = facility.installments.map((item) => `<li data-installment-status="${escapeHtml(item.status)}">
      <span class="facility-stage-node">${String(item.sequence).padStart(2, "0")}</span>
      <b>${escapeHtml(tr("facilityInstallment"))} ${item.sequence}</b>
      <small>${escapeHtml(item.due_date)} · ${exactFacilityMoney(item.amount, facility.currency)}</small>
      <em>${escapeHtml(tr(`installment_${item.status}`))} · ${escapeHtml(tr("facilityPaidAmount"))} ${exactFacilityMoney(item.paid_amount, facility.currency)}</em>
    </li>`).join("");
    const payments = facility.payments.length ? facility.payments.map((payment) => `<article class="facility-payment-row">
      <span class="status-chip" data-status="${escapeHtml(payment.status)}">${escapeHtml(tr(`payment_${payment.status}`))}</span>
      <b>${exactFacilityMoney(payment.amount, facility.currency)}</b>
      <small>${escapeHtml(payment.payment_reference)} · ${escapeHtml(payment.submitted_at)}</small>
    </article>`).join("") : `<p class="facility-empty-copy">${escapeHtml(tr("facilityNoPayments"))}</p>`;
    const actions = facility.allowed_actions.length
      ? facility.allowed_actions.map((action) => renderFacilityAction(facility, action)).join("")
      : `<p class="facility-empty-copy">${escapeHtml(tr("facilityNoAction"))}</p>`;
    container.innerHTML = `<div class="facility-detail-head">
      <div><span class="status-chip" data-status="${escapeHtml(facility.status)}">${escapeHtml(tr(facility.status))}</span><h2>${exactFacilityMoney(facility.principal, facility.currency)}</h2><p>${escapeHtml(facility.facility_id)}</p></div>
      <div class="detail-version"><span>${escapeHtml(tr("facilityVersion"))}</span><b>v${facility.version}</b><button class="btn btn-secondary" type="button" data-open-risk-detail="${escapeHtml(facility.facility_id)}" onclick="window.riskOpsOpenDetail && window.riskOpsOpenDetail(this.dataset.openRiskDetail)">${escapeHtml(tr("openRiskDetail"))}</button></div>
    </div>
    <section id="facilityMoneyRail" class="facility-money-rail" aria-label="Exact facility balance rail">
      <div class="facility-money-flow">
        <article><span>01</span><small>${escapeHtml(tr("facilityPrincipal"))}</small><b>${exactFacilityMoney(facility.principal, facility.currency)}</b></article>
        <article><span>02</span><small>${escapeHtml(tr("facilityPaid"))}</small><b>${exactFacilityMoney(paidAmount, facility.currency)}</b></article>
        <article><span>03</span><small>${escapeHtml(tr("facilityOutstanding"))}</small><b>${exactFacilityMoney(facility.outstanding_amount, facility.currency)}</b></article>
        <article><span>04</span><small>${escapeHtml(tr("facilityArrears"))}</small><b>${exactFacilityMoney(facility.arrears_amount, facility.currency)}</b></article>
        <article><span>05</span><small>${escapeHtml(tr("facilityRecoveryCollected"))}</small><b>${exactFacilityMoney(facility.recovery_collected_amount, facility.currency)}</b></article>
        <article><span>06</span><small>${escapeHtml(tr("facilityNetLoss"))}</small><b>${exactFacilityMoney(facility.net_loss, facility.currency)}</b></article>
      </div>
    </section>
    <div class="facility-evidence"><span>${escapeHtml(tr("facilityEvidence"))}</span><b>${escapeHtml(facility.disbursement_reference || "—")}</b><small title="${escapeHtml(facility.disbursement_evidence_sha256 || "")}">${escapeHtml(compactHash(facility.disbursement_evidence_sha256))}</small></div>
    <section class="facility-detail-section"><div class="facility-section-head"><b>${escapeHtml(tr("facilityInstallments"))}</b><span>${facility.installments.length}</span></div><ol id="facilityInstallments" class="facility-stage-rail">${installments}</ol></section>
    <section class="facility-detail-section"><div class="facility-section-head"><b>${escapeHtml(tr("facilityPayments"))}</b><span>${facility.payments.length}</span></div><div id="facilityPayments" class="facility-payment-list">${payments}</div></section>
    ${renderFacilityGovernance(facility)}
    <section class="facility-detail-section facility-action-section"><div class="facility-section-head"><b>${escapeHtml(tr("facilityActionStation"))}</b><span>${facility.allowed_actions.length}</span></div><div id="facilityActions" class="facility-action-list">${actions}</div></section>
    ${renderActualOutcomePanel(facility)}`;
  }

  function renderFacilityGovernance(facility) {
    const history = facility.status_history.map((row) => `<li><b>${escapeHtml(row.from_status ? tr(row.from_status) : "—")} → ${escapeHtml(tr(row.to_status))}</b><small>${escapeHtml(row.trigger_action)} · ${escapeHtml(row.actor_role || "migration")} · v${row.resulting_version}${row.reason_code ? ` · ${escapeHtml(row.reason_code)}` : ""}</small><em>${escapeHtml(row.recorded_at)}</em></li>`).join("");
    const contracts = facility.contract_versions.map((row) => `<article class="facility-payment-row"><span class="status-chip">${escapeHtml(tr("facilityContractVersion"))} ${row.contract_version}</span><b>${escapeHtml(row.origin)}</b><small>${row.schedule.map((item) => `${escapeHtml(item.due_date)} · ${exactFacilityMoney(item.amount, facility.currency)}`).join(" / ")}</small><small title="${escapeHtml(row.terms_sha256)}">${escapeHtml(compactHash(row.terms_sha256))}</small></article>`).join("");
    const decisions = facility.lifecycle_decisions.map((row) => `<article class="facility-payment-row"><span class="status-chip">${escapeHtml(row.decision_type)}</span><b>${escapeHtml(row.reason_code)}</b><small>${escapeHtml(row.comment)} · ${escapeHtml(row.recorded_at)}</small></article>`).join("");
    const recoveries = facility.recoveries.map((row) => `<article class="facility-payment-row"><span class="status-chip">${escapeHtml(row.applied_to)}</span><b>${exactFacilityMoney(row.amount, facility.currency)}</b><small>${escapeHtml(row.source)} · ${escapeHtml(row.recovery_reference)} · ${escapeHtml(row.recorded_at)}</small></article>`).join("");
    const empty = `<p class="facility-empty-copy">${escapeHtml(tr("facilityNoHistory"))}</p>`;
    return `<section class="facility-detail-section"><div class="facility-section-head"><b>${escapeHtml(tr("facilityHistory"))}</b><span>${facility.status_history.length}</span></div><ol id="facilityStatusHistory" class="facility-history-rail">${history || empty}</ol></section>
    <section class="facility-detail-section"><div class="facility-section-head"><b>${escapeHtml(tr("facilityContracts"))}</b><span>${facility.contract_versions.length}</span></div><div id="facilityContracts" class="facility-payment-list">${contracts || empty}</div></section>
    ${facility.lifecycle_decisions.length ? `<section class="facility-detail-section"><div class="facility-section-head"><b>${escapeHtml(tr("facilityDecisions"))}</b><span>${facility.lifecycle_decisions.length}</span></div><div id="facilityDecisions" class="facility-payment-list">${decisions}</div></section>` : ""}
    ${facility.recoveries.length ? `<section class="facility-detail-section"><div class="facility-section-head"><b>${escapeHtml(tr("facilityRecoveries"))}</b><span>${facility.recoveries.length}</span></div><div id="facilityRecoveries" class="facility-payment-list">${recoveries}</div></section>` : ""}`;
  }

  function installmentOptions(facility, includePaid = false) {
    return facility.installments.filter((item) => item.status !== "superseded" && (includePaid || item.status !== "paid")).map((item) =>
      `<option value="${escapeHtml(item.installment_id)}">${escapeHtml(tr("facilityInstallment"))} ${item.sequence} · ${escapeHtml(item.due_date)} · ${exactFacilityMoney(item.amount, facility.currency)}</option>`
    ).join("");
  }

  function submittedPaymentOptions(facility) {
    return facility.payments.filter((payment) => payment.status === "submitted").map((payment) =>
      `<option value="${escapeHtml(payment.payment_id)}">${escapeHtml(payment.payment_reference)} · ${exactFacilityMoney(payment.amount, facility.currency)}</option>`
    ).join("");
  }

  function renderFacilityAction(facility, action) {
    const button = (label, className = "btn-primary") => `<button class="btn ${className}" type="submit" data-facility-action="${escapeHtml(action)}">${escapeHtml(label)}</button>`;
    if (action === "initiate_disbursement") return `<form class="facility-action-card" data-facility-action-form="${action}"><b>${escapeHtml(tr("facilityInitiate"))}</b>${button(tr("facilityInitiate"))}</form>`;
    if (action === "confirm_disbursement") return `<form class="facility-action-card" data-facility-action-form="${action}"><b>${escapeHtml(tr("facilityConfirmDisbursement"))}</b>${button(tr("facilityConfirmDisbursement"))}</form>`;
    if (action === "submit_payment") return `<form class="facility-action-card" data-facility-action-form="${action}"><b>${escapeHtml(tr("facilitySubmitPayment"))}</b><label><span>${escapeHtml(tr("facilityInstallment"))}</span><select name="installment_id" required>${installmentOptions(facility)}</select></label><label><span>${escapeHtml(tr("facilityAmount"))}</span><input name="amount" inputmode="decimal" required></label><label><span>${escapeHtml(tr("facilityPaymentReference"))}</span><input name="payment_reference" maxlength="120" required></label>${button(tr("facilitySubmitPayment"))}</form>`;
    if (action === "confirm_payment" || action === "reject_payment") {
      const isConfirm = action === "confirm_payment";
      return `<form class="facility-action-card" data-facility-action-form="${action}"><b>${escapeHtml(tr(isConfirm ? "facilityConfirmPayment" : "facilityRejectPayment"))}</b><label><span>${escapeHtml(tr("facilityPayments"))}</span><select name="payment_id" required>${submittedPaymentOptions(facility)}</select></label><label><span>${escapeHtml(tr("facilityDecisionComment"))}</span><input name="comment" maxlength="500" required></label>${button(tr(isConfirm ? "facilityConfirmPayment" : "facilityRejectPayment"), isConfirm ? "btn-primary" : "btn-danger")}</form>`;
    }
    const evidenceFields = (reason) => `<label><span>${escapeHtml(tr("facilityReasonCode"))}</span><input name="reason_code" value="${escapeHtml(reason)}" pattern="[A-Z][A-Z0-9_]{2,63}" required></label><label><span>${escapeHtml(tr("facilityComment"))}</span><input name="comment" maxlength="500" required></label><label><span>${escapeHtml(tr("facilityEvidenceReference"))}</span><input name="evidence_reference" maxlength="500" required></label>`;
    if (action === "mark_overdue") return `<form class="facility-action-card" data-facility-action-form="${action}"><b>${escapeHtml(tr("facilityMarkOverdue"))}</b><label><span>${escapeHtml(tr("facilityInstallment"))}</span><select name="installment_id" required>${installmentOptions(facility)}</select></label><label><span>${escapeHtml(tr("facilityDaysPastDue"))}</span><input name="days_past_due" type="number" min="1" max="36500" value="30" required></label><label><span>${escapeHtml(tr("facilityEvidenceReference"))}</span><input name="evidence_reference" maxlength="500" required></label>${button(tr("facilityMarkOverdue"), "btn-danger")}</form>`;
    const decisionLabels = { open_disposal: ["facilityOpenDisposal", "ARREARS_WORKOUT", "btn-danger"], close_disposal: ["facilityCloseDisposal", "ARREARS_CLEARED", "btn-primary"], start_recovery: ["facilityStartRecovery", "LEGAL_RECOVERY", "btn-danger"], write_off: ["facilityWriteOff", "UNCOLLECTIBLE_BALANCE", "btn-danger"] };
    if (decisionLabels[action]) {
      const [label, reason, style] = decisionLabels[action];
      return `<form class="facility-action-card" data-facility-action-form="${action}"><b>${escapeHtml(tr(label))}</b>${evidenceFields(reason)}${button(tr(label), style)}</form>`;
    }
    if (action === "declare_default") return `<form class="facility-action-card" data-facility-action-form="${action}"><b>${escapeHtml(tr("facilityDeclareDefault"))}</b>${evidenceFields("PAYMENT_DEFAULT")}<label><span>${escapeHtml(tr("facilityDaysPastDue"))}</span><input name="days_past_due" type="number" min="1" max="36500" value="90" required></label>${button(tr("facilityDeclareDefault"), "btn-danger")}</form>`;
    if (action === "restructure") return `<form class="facility-action-card" data-facility-action-form="${action}"><b>${escapeHtml(tr("facilityRestructure"))}</b>${evidenceFields("BORROWER_CASH_FLOW")}<label><span>${escapeHtml(tr("facilityNewDueDate"))}</span><input name="due_date" type="date" required></label><label><span>${escapeHtml(tr("facilityAmount"))}</span><input name="amount" inputmode="decimal" value="${escapeHtml(facility.outstanding_amount)}" readonly></label>${button(tr("facilityRestructure"))}</form>`;
    if (action === "record_recovery") return `<form class="facility-action-card" data-facility-action-form="${action}"><b>${escapeHtml(tr("facilityRecordRecovery"))}</b><label><span>${escapeHtml(tr("facilityRecoverySource"))}</span><select name="source">${["GUARANTOR", "COLLATERAL", "CORE_ENTERPRISE_BUYBACK", "LEGAL_ENFORCEMENT", "COLLECTION_AGENCY", "INSURANCE", "OTHER"].map((value) => `<option value="${value}">${value}</option>`).join("")}</select></label><label><span>${escapeHtml(tr("facilityAmount"))}</span><input name="amount" inputmode="decimal" required></label><label><span>${escapeHtml(tr("facilityRecoveryReference"))}</span><input name="recovery_reference" maxlength="120" required></label><label><span>${escapeHtml(tr("facilityEvidenceReference"))}</span><input name="evidence_reference" maxlength="500" required></label>${button(tr("facilityRecordRecovery"))}</form>`;
    if (action === "close") return `<form class="facility-action-card" data-facility-action-form="${action}"><b>${escapeHtml(tr("facilityClose"))}</b>${button(tr("facilityClose"))}</form>`;
    return "";
  }

  async function createFacility(event) {
    event.preventDefault();
    if (state.facilityPending) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const errorElement = document.querySelector("#facilityCreateError");
    errorElement.textContent = "";
    try {
      const principal = normalizeMoneyInput(data.get("principal"));
      const firstAmount = normalizeMoneyInput(data.get("amount_1"));
      const secondAmount = normalizeMoneyInput(data.get("amount_2"));
      if (moneyToCents(firstAmount) + moneyToCents(secondAmount) !== moneyToCents(principal)) throw new Error(tr("facilityScheduleMismatch"));
      const payload = {
        request_id: String(data.get("request_id")).trim(), principal, currency: String(data.get("currency")), version: 1,
        idempotency_key: crypto.randomUUID(),
        installments: [
          { sequence: 1, due_date: data.get("due_date_1"), amount: firstAmount },
          { sequence: 2, due_date: data.get("due_date_2"), amount: secondAmount }
        ]
      };
      setFacilityPending(true);
      const result = await wfApi("/api/v1/facilities", { method: "POST", body: JSON.stringify(payload) });
      notify(tr("facilityCreated"));
      await Promise.all([refreshFacilities(result.facility_id), refreshWorkflow()]);
    } catch (error) {
      errorElement.textContent = error.message;
      notify(error.message, true);
    } finally {
      setFacilityPending(false);
    }
  }

  async function executeFacilityAction(button) {
    const facility = state.selectedFacility;
    if (!facility || state.facilityPending) return;
    const action = button.dataset.facilityAction;
    const form = button.closest("[data-facility-action-form]");
    const data = new FormData(form);
    const command = { version: facility.version, idempotency_key: crypto.randomUUID() };
    let suffix = "";
    let payload = command;
    try {
      if (action === "initiate_disbursement") suffix = "/initiate-disbursement";
      else if (action === "confirm_disbursement") suffix = "/confirm-disbursement";
      else if (action === "submit_payment") {
        suffix = "/payments";
        payload = { ...command, installment_id: String(data.get("installment_id")), amount: normalizeMoneyInput(data.get("amount")), payment_reference: String(data.get("payment_reference")).trim() };
        moneyToCents(payload.amount);
        if (!payload.payment_reference) throw new Error(tr("facilityRequiredFields"));
      } else if (action === "confirm_payment" || action === "reject_payment") {
        const paymentId = String(data.get("payment_id"));
        const comment = String(data.get("comment")).trim();
        if (!paymentId || !comment) throw new Error(tr("facilityRequiredFields"));
        suffix = `/payments/${paymentId}/decision`;
        payload = { ...command, decision: action === "confirm_payment" ? "confirmed" : "rejected", comment };
      } else if (action === "mark_overdue") {
        suffix = "/mark-overdue";
        const reference = String(data.get("evidence_reference") || "").trim();
        if (!reference) throw new Error(tr("facilityRequiredFields"));
        payload = { ...command, installment_id: String(data.get("installment_id")), days_past_due: Number(data.get("days_past_due")), evidence_sha256: await hashEvidenceReference(reference) };
      } else if (["open_disposal", "close_disposal", "start_recovery", "write_off", "declare_default", "restructure"].includes(action)) {
        const reference = String(data.get("evidence_reference") || "").trim();
        const comment = String(data.get("comment") || "").trim();
        if (!reference || !comment) throw new Error(tr("facilityRequiredFields"));
        const evidence = { reason_code: String(data.get("reason_code")).trim(), comment, evidence_sha256: await hashEvidenceReference(reference) };
        suffix = `/${action.replace("_", "-")}`;
        payload = { ...command, ...evidence };
        if (action === "declare_default") payload = { ...payload, days_past_due: Number(data.get("days_past_due")), defaulted_at: new Date().toISOString() };
        if (action === "restructure") {
          if (!data.get("due_date")) throw new Error(tr("facilityRequiredFields"));
          payload = { ...payload, installments: [{ sequence: 1, due_date: data.get("due_date"), amount: facility.outstanding_amount }] };
        }
      } else if (action === "record_recovery") {
        const reference = String(data.get("evidence_reference") || "").trim();
        const recoveryReference = String(data.get("recovery_reference") || "").trim();
        if (!reference || !recoveryReference) throw new Error(tr("facilityRequiredFields"));
        suffix = "/recoveries";
        payload = { ...command, amount: normalizeMoneyInput(data.get("amount")), source: String(data.get("source")), recovery_reference: recoveryReference, evidence_sha256: await hashEvidenceReference(reference) };
        moneyToCents(payload.amount);
      } else if (action === "close") suffix = "/close";
      else return;
      setFacilityPending(true);
      const result = await wfApi(`/api/v1/facilities/${facility.facility_id}${suffix}`, { method: "POST", body: JSON.stringify(payload) });
      notify(tr("facilityActionComplete"));
      await Promise.all([refreshFacilities(result.facility_id), refreshWorkflow()]);
    } catch (error) {
      notify(error.message, true);
    } finally {
      setFacilityPending(false);
    }
  }

  function formPayload(form) {
    const data = new FormData(form);
    return {
      core_enterprise_organization_code: data.get("core_enterprise_organization_code"), contract_number: data.get("contract_number"),
      invoice_number: data.get("invoice_number"), amount: Number(data.get("amount")), term_days: Number(data.get("term_days")),
      payment_delay_days: Number(data.get("payment_delay_days")), counterparty_risk: Number(data.get("counterparty_risk")),
      invoice_mismatch: data.get("invoice_mismatch") === "on", relationship_months: Number(data.get("relationship_months")),
      transactions_last_30d: Number(data.get("transactions_last_30d"))
    };
  }

  async function saveApplication(event) {
    event.preventDefault();
    const payload = formPayload(event.currentTarget);
    setBusy(true);
    try {
      const result = state.editing
        ? await wfApi(`/api/v1/applications/${state.editing.request_id}`, { method: "PATCH", body: JSON.stringify({ ...payload, version: state.editing.version }) })
        : await wfApi("/api/v1/applications", { method: "POST", body: JSON.stringify(payload) });
      notify(tr(state.editing ? "updated" : "created"));
      state.editing = null;
      document.querySelector('#applicationForm button[type="submit"]').textContent = tr("saveDraft");
      await refreshWorkflow(result.request_id);
    } catch (error) { notify(error.message, true); }
    finally { setBusy(false); }
  }

  function editSelected() {
    const application = state.selected;
    state.editing = application;
    const form = document.querySelector("#applicationForm");
    const values = { ...application.features, core_enterprise_organization_code: application.core_enterprise_organization_code, contract_number: application.contract_number, invoice_number: application.invoice_number, amount: application.amount, term_days: application.term_days };
    Object.entries(values).forEach(([name, value]) => {
      const input = form.elements.namedItem(name);
      if (!input) return;
      if (input.type === "checkbox") input.checked = Boolean(value); else input.value = value;
    });
    form.querySelector('button[type="submit"]').textContent = tr("updateDraft");
    document.querySelector("#supplierCreate").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function executeAction(button) {
    const application = state.selected;
    if (!application) return;
    const action = button.dataset.workflowAction;
    if (action === "edit") { editSelected(); return; }
    const comment = document.querySelector("#workflowComment")?.value.trim() || (state.lang === "ru" ? "Подтверждено в демонстрационном процессе" : "已在演示流程中确认");
    const endpoints = {
      submit: ["/submit", { version: application.version }],
      confirm: ["/trade-confirmation", { version: application.version, confirmed: true, comment, confirmed_payable_amount: document.querySelector("#workflowPayableCeiling")?.value || undefined }],
      return: ["/trade-confirmation", { version: application.version, confirmed: false, comment }],
      assess: ["/risk-assessment", { version: application.version }],
      decision: ["/decision", { version: application.version, decision: button.dataset.decision, comment }],
      control: ["/control-action", { version: application.version, comment }],
      audit: ["/audit-review", { version: application.version, comment }]
    };
    const [suffix, payload] = endpoints[action];
    setBusy(true);
    try {
      const result = await wfApi(`/api/v1/applications/${application.request_id}${suffix}`, { method: "POST", body: JSON.stringify(payload) });
      notify(tr("actionComplete"));
      await refreshWorkflow(result.request_id);
      await refreshLegacyForRole();
    } catch (error) { notify(error.message, true); }
    finally { setBusy(false); }
  }

  async function boot() {
    const legacySetLanguage = window.setLanguage;
    window.setLanguage = function (next) {
      state.lang = next;
      legacySetLanguage(next);
      applyWorkflowLanguage();
    };
    document.querySelector("#loginForm").addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      login(String(data.get("username")), String(data.get("password")));
    });
    document.querySelector("#logoutButton").addEventListener("click", logout);
    document.querySelector("#refreshWorkflow").addEventListener("click", () => refreshWorkflow());
    document.querySelector("#refreshFacilities").addEventListener("click", () => Promise.all([refreshFacilities(), refreshOutcomes()]));
    document.querySelector("#refreshAnchors").addEventListener("click", refreshAnchors);
    document.querySelector("#dispatchAnchors").addEventListener("click", dispatchAnchors);
    document.querySelector("#applicationForm").addEventListener("submit", saveApplication);
    document.querySelector("#facilityCreateForm").addEventListener("submit", createFacility);
    document.addEventListener("submit", (event) => {
      if (event.target.matches?.("#actualOutcomeForm")) {
        submitActualOutcome(event);
        return;
      }
      const facilityForm = event.target.closest?.("[data-facility-action-form]");
      if (!facilityForm) return;
      event.preventDefault();
      const actionButton = event.submitter || facilityForm.querySelector("[data-facility-action]");
      if (actionButton) executeFacilityAction(actionButton);
    });
    document.addEventListener("click", (event) => {
      const button = event.target.closest?.("[data-workflow-action]");
      if (button) executeAction(button);
      const anchorRetry = event.target.closest?.("[data-anchor-retry]");
      if (anchorRetry) retryAnchor(anchorRetry.dataset.anchorRetry);
      const rollback = event.target.closest?.("[data-calibration-rollback]");
      if (rollback) rollbackCalibration(rollback.dataset.calibrationRollback);
    });
    applyWorkflowLanguage();
    try { state.accounts = await wfApi("/api/v1/auth/demo-accounts"); } catch (_) { state.accounts = []; }
    renderAccounts();
    try {
      const session = await wfApi("/api/v1/auth/session");
      if (!session.authenticated) return;
      state.user = session.user;
      document.body.classList.add("authenticated");
      await enterWorkbench();
    } catch (_) {
      document.body.classList.remove("authenticated");
    }
  }

  boot();
})();
