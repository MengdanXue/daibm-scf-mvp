(function () {
  "use strict";

  const COPY = {
    ru: {
      loginTitle: "Войдите в рабочий контур",
      loginSubtitle: "Пять участников проводят одну заявку от поставщика до проверяемого аудиторского следа.",
      researchBoundary: "Демонстрационный прототип на синтетических данных · PostgreSQL · проверяемый журнал событий",
      demoAccess: "Демонстрационный доступ", chooseRole: "Выберите роль", orCredentials: "или введите учётные данные",
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
      roleFinancier: "Финансист", roleRiskManager: "Риск-менеджер", roleAuditor: "Аудитор",
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
      duplicate_invoice_claim: "Этот счёт-фактура уже используется в другой заявке"
    },
    zh: {
      loginTitle: "进入业务工作台", loginSubtitle: "五类参与者共同将一笔申请从供应商推进到可验证的审计轨迹。",
      researchBoundary: "基于合成数据的演示原型 · PostgreSQL · 可验证事件日志", demoAccess: "演示访问", chooseRole: "选择角色",
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
      roleSupplier: "供应商", roleCoreEnterprise: "核心企业", roleFinancier: "融资方", roleRiskManager: "风险经理", roleAuditor: "审计员",
      stageCreate: "创建与提交", stageConfirm: "交易确认", stageDecide: "评估与决策", stageControl: "风险控制", stageAudit: "审计核验",
      supplierMission: "创建申请、核对交易信息，并提交给核心企业。", coreMission: "确认合同与发票真实性，或将申请退回供应商。",
      financierMission: "执行风险评估、查看模型结果并作出融资决策。", riskMission: "根据决策设置控制措施并写入日志。",
      auditorMission: "核验完整业务流程与日志完整性后关闭申请。", queueAll: "显示当前角色可查看的申请；有可执行操作的待办优先排列。",
      noApplications: "当前角色暂无可查看的申请。", version: "版本", supplier: "供应商", core: "核心企业", contract: "合同",
      invoice: "发票", term: "期限", risk: "风险评分", decision: "决策", actionStation: "当前可执行操作",
      noAction: "当前阶段没有该角色可执行的操作。", comment: "交接备注", commentPlaceholder: "简要记录操作依据……",
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
      duplicate_invoice_claim: "该发票已被另一笔融资申请使用"
    }
  };

  const ROLE_META = {
    supplier: { key: "roleSupplier", mission: "supplierMission", seal: "S", color: "#2768ee", order: 0 },
    core_enterprise: { key: "roleCoreEnterprise", mission: "coreMission", seal: "C", color: "#7a5ce0", order: 1 },
    financier: { key: "roleFinancier", mission: "financierMission", seal: "F", color: "#15976c", order: 2 },
    risk_manager: { key: "roleRiskManager", mission: "riskMission", seal: "R", color: "#d48a16", order: 3 },
    auditor: { key: "roleAuditor", mission: "auditorMission", seal: "A", color: "#b94650", order: 4 }
  };

  const STATUS_STAGE = {
    draft: 0, trade_returned: 0, submitted: 1, trade_confirmed: 2, risk_assessed: 2,
    approved: 3, manual_review: 3, rejected: 3, controlled: 4, audited: 5
  };

  const state = {
    lang: localStorage.getItem("daibm-lang") || "ru",
    user: null, accounts: [], dashboard: null, tasks: [], applications: [], selected: null, editing: null, busy: false
  };

  const tr = (key) => COPY[state.lang][key] || key;
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[character]);
  const money = (value) => new Intl.NumberFormat(state.lang === "ru" ? "ru-RU" : "zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }).format(Number(value || 0));

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
    renderAccounts();
    if (state.user) renderWorkbench();
  }

  function renderAccounts() {
    const container = document.querySelector("#demoAccounts");
    if (!container) return;
    if (!state.accounts.length) {
      container.innerHTML = `<p>${escapeHtml(tr("loading"))}</p>`;
      return;
    }
    container.innerHTML = state.accounts.map((account) => {
      const role = ROLE_META[account.role];
      return `<button class="account-card" type="button" data-demo-username="${escapeHtml(account.username)}">
        <span class="account-icon" style="color:${role.color}">${role.seal}</span>
        <span><b>${escapeHtml(tr(role.key))}</b><small>${escapeHtml(account.display_name)} · ${escapeHtml(account.organization_code)}</small></span>
        <span class="account-arrow">→</span>
      </button>`;
    }).join("");
    container.querySelectorAll("[data-demo-username]").forEach((button) => {
      button.addEventListener("click", () => login(button.dataset.demoUsername, "Demo123!"));
    });
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
      errorElement.textContent = tr("loginFailed");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    try { await wfApi("/api/v1/auth/logout", { method: "POST" }); } catch (_) { /* local logout still applies */ }
    state.user = null;
    state.applications = [];
    state.selected = null;
    state.editing = null;
    document.body.classList.remove("authenticated");
    document.querySelector("#loginForm").reset();
    document.querySelector('#loginForm input[name="username"]').value = "supplier.demo";
    document.querySelector('#loginForm input[name="password"]').value = "Demo123!";
    renderAccounts();
  }

  function configureRoleNavigation() {
    const role = state.user.role;
    const rules = {
      workflow: true,
      overview: role === "financier" || role === "auditor",
      review: role === "financier" || role === "auditor",
      research: role === "financier" || role === "auditor",
      ledger: role === "auditor",
      model: true
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

  async function enterWorkbench() {
    const role = ROLE_META[state.user.role];
    renderCurrentUser();
    document.querySelector("#view-workflow").style.setProperty("--role-color", role.color);
    configureRoleNavigation();
    window.switchView("workflow");
    await refreshWorkflow();
    await refreshLegacyForRole();
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
    const evidenceMarkup = `<div class="workflow-evidence">
      <article><span>${escapeHtml(tr("tradeEvidence"))}</span><b title="${escapeHtml(tradeEvidence?.fingerprint_sha256)}">${escapeHtml(compactHash(tradeEvidence?.fingerprint_sha256))}</b><small>${escapeHtml(tradeEvidence ? tr("duplicateCheckPassed") : "—")}</small></article>
      <article><span>${escapeHtml(tr("businessRiskEvidence"))}</span><b title="${escapeHtml(riskEvidence?.input_sha256)}">${escapeHtml(riskEvidence?.engine_version || "—")}</b><small>${escapeHtml(compactHash(riskEvidence?.input_sha256))}</small></article>
      <p>${escapeHtml(tr("researchComparisonBoundary"))}</p>
    </div>`;
    container.innerHTML = `<div class="detail-top"><div><span class="status-chip" data-status="${escapeHtml(application.status)}">${escapeHtml(tr(application.status))}</span><h2>${escapeHtml(application.contract_number)}</h2><p>${escapeHtml(application.request_id)}</p></div><div class="detail-version"><span>${escapeHtml(tr("version"))}</span><b>v${application.version}</b></div></div>
      <div class="detail-fields">
        ${detailField(tr("supplier"), application.applicant_id)}${detailField(tr("core"), "CORE-001")}${detailField(tr("amount"), money(application.amount))}
        ${detailField(tr("invoice"), application.invoice_number)}${detailField(tr("term"), `${application.term_days} ${tr("days")}`)}${detailField(tr("paymentDelay"), `${application.features.payment_delay_days} ${tr("days")}`)}
      </div>
      <div class="risk-result"><span>${escapeHtml(tr("risk"))}<strong>${escapeHtml(risk)}</strong></span><span>${escapeHtml(tr("decision"))}<strong>${escapeHtml(application.decision ? tr(application.decision) : "—")}</strong></span></div>
      ${evidenceMarkup}
      ${renderActionStation(application)}`;
  }

  function renderActionStation(application) {
    const actions = application.allowed_actions;
    if (!actions.length) return `<div class="action-note">${escapeHtml(tr("noAction"))}</div>`;
    const comment = actions.some((action) => ["confirm_trade","return_trade","decide","apply_control","audit"].includes(action))
      ? `<label><span>${escapeHtml(tr("comment"))}</span><textarea id="workflowComment" class="action-input" placeholder="${escapeHtml(tr("commentPlaceholder"))}"></textarea></label>` : "";
    const buttons = [];
    if (actions.includes("update")) buttons.push(`<button class="btn btn-soft" type="button" data-workflow-action="edit">${escapeHtml(tr("edit"))}</button>`);
    if (actions.includes("submit")) buttons.push(`<button class="btn btn-primary" type="button" data-workflow-action="submit">${escapeHtml(tr("submit"))}</button>`);
    if (actions.includes("confirm_trade")) buttons.push(`<button class="btn btn-primary" type="button" data-workflow-action="confirm">${escapeHtml(tr("confirmTrade"))}</button>`);
    if (actions.includes("return_trade")) buttons.push(`<button class="btn btn-danger" type="button" data-workflow-action="return">${escapeHtml(tr("returnTrade"))}</button>`);
    if (actions.includes("assess_risk")) buttons.push(`<button class="btn btn-primary" type="button" data-workflow-action="assess">${escapeHtml(tr("assessRisk"))}</button>`);
    if (actions.includes("decide")) buttons.push(`<button class="btn btn-primary" type="button" data-workflow-action="decision" data-decision="approved">${escapeHtml(tr("approve"))}</button><button class="btn btn-soft" type="button" data-workflow-action="decision" data-decision="manual_review">${escapeHtml(tr("manualReview"))}</button><button class="btn btn-danger" type="button" data-workflow-action="decision" data-decision="rejected">${escapeHtml(tr("reject"))}</button>`);
    if (actions.includes("apply_control")) buttons.push(`<button class="btn btn-primary" type="button" data-workflow-action="control">${escapeHtml(tr("applyControl"))}</button>`);
    if (actions.includes("audit")) buttons.push(`<button class="btn btn-primary" type="button" data-workflow-action="audit">${escapeHtml(tr("auditReview"))}</button>`);
    setTimeout(() => document.querySelectorAll("[data-workflow-action]").forEach((button) => button.addEventListener("click", () => executeAction(button))), 0);
    return `<div class="action-station"><h3>${escapeHtml(tr("actionStation"))}</h3><p>${escapeHtml(tr(ROLE_META[state.user.role].mission))}</p>${comment}<div class="action-buttons">${buttons.join("")}</div></div>`;
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
    const values = { ...application.features, core_enterprise_organization_code: "CORE-001", contract_number: application.contract_number, invoice_number: application.invoice_number, amount: application.amount, term_days: application.term_days };
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
      confirm: ["/trade-confirmation", { version: application.version, confirmed: true, comment }],
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
    document.querySelector("#applicationForm").addEventListener("submit", saveApplication);
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
