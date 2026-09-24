(() => {
  "use strict";

  const COPY = {
    ru: {
      navDashboard: "Риск-панель", navAlerts: "Центр оповещений", navTasks: "Задачи", navDetail: "Риск-профиль", navRules: "Правила риска",
      dashEyebrow: "РИСК-ОПЕРАЦИИ", dashTitle: "Панель риска портфеля", dashSubtitle: "Все показатели вычислены из записанных бизнес-данных.",
      alertEyebrow: "ОПОВЕЩЕНИЯ", alertTitle: "Центр оповещений о риске", alertSubtitle: "Правило → оповещение → назначение → обработка → решение → закрытие.",
      taskEyebrow: "ЗАДАЧИ", taskTitle: "Центр задач", taskSubtitle: "Ответственный, срок, заметки, результат и журнал аудита.",
      detailEyebrow: "РИСК-ПРОФИЛЬ", detailTitle: "Риск-профиль финансирования", detailSubtitle: "Предприятие, риск, модель, жизненный цикл и аудит в одном окне.",
      ruleEyebrow: "ПРАВИЛА", ruleTitle: "Центр правил риска", ruleSubtitle: "Каждое изменение — новая версия со временем вступления в силу.",
      refresh: "Обновить", loading: "Загрузка…", empty: "Записей нет.", forbidden: "Текущая роль не имеет доступа.", confirmed: "Операция выполнена",
      assets: "Активы", totalFinanced: "Выдано всего", balance: "Текущий остаток", enterprises: "Предприятий", financings: "Финансирований",
      distribution: "Распределение риска", NORMAL: "Норма", WATCH: "Наблюдение", HIGH_RISK: "Высокий риск", DEFAULTED: "Дефолт",
      lifecycle: "Жизненный цикл", normal_repaid: "Погашено в срок", overdue: "Просрочка", disposal: "Урегулирование", restructured: "Реструктуризация", recovery: "Взыскание", written_off: "Списание",
      losses: "Потери", recovered: "Взыскано всего", writtenOff: "Списано", netLoss: "Чистый убыток", recoveryRate: "Уровень взыскания",
      modelStatus: "Состояние модели", activeModel: "Активная модель", lastUpdate: "Последнее обновление", latestSnapshot: "Последний снимок", latestFailure: "Последняя причина неудачи", none: "нет",
      alertSummary: "Открытые оповещения", myTasks: "Мои задачи", overdueTasks: "Просрочено", facilities: "Финансирования",
      scan: "Запустить проверку правил", status: "Статус", severity: "Серьёзность", type: "Тип", reason: "Причина срабатывания", owner: "Ответственный", created: "Создано", all: "Все", resolution: "Решение",
      assign: "Назначить", start: "Начать обработку", resolve: "Решить", close: "Закрыть после проверки", reopen: "Вернуть на доработку", comment: "Комментарий", addComment: "Добавить комментарий", history: "История", evidence: "Доказательства", createTask: "Создать задачу",
      mine: "Мои задачи", pending: "В работе", completed: "Завершённые", title: "Название", assignee: "Исполнитель", due: "Срок", note: "Заметка", addNote: "Добавить заметку", uploadResult: "Загрузить результат", complete: "Завершить", cancel: "Отменить", reassign: "Переназначить", changeDue: "Изменить срок", summary: "Итог", attachments: "Файлы результата", description: "Описание", taskType: "Тип задачи",
      enterprise: "Предприятие", financing: "Финансирование", risk: "Риск", model: "Модель", timeline: "Жизненный цикл", audit: "Аудит", riskClass: "Класс риска", band: "Уровень", score: "Оценка", trend: "Тренд", scoreHistory: "История оценок", artifactHash: "Хеш артефакта", snapshot: "Снимок данных", actor: "Исполнитель", time: "Время", selectFacility: "Выберите финансирование",
      rule: "Правило", version: "Версия", threshold: "Порог", enabled: "Включено", effectiveFrom: "Вступает в силу", changeReason: "Причина изменения", saveVersion: "Сохранить новую версию", pendingRules: "Запланированные версии", ruleHistory: "История версий"
    },
    zh: {
      navDashboard: "风险驾驶舱", navAlerts: "风险预警中心", navTasks: "风险任务中心", navDetail: "风险详情", navRules: "风险规则中心",
      dashEyebrow: "风险运营", dashTitle: "风险驾驶舱", dashSubtitle: "所有指标均由已记录的业务数据实时计算，不含模拟统计。",
      alertEyebrow: "风险预警", alertTitle: "风险预警中心", alertSubtitle: "风险识别 → 预警创建 → 指派 → 处理 → 解决 → 审核关闭。",
      taskEyebrow: "风险任务", taskTitle: "风险任务中心", taskSubtitle: "负责人、截止时间、处理备注、处理结果与完整审计。",
      detailEyebrow: "风险详情", detailTitle: "融资风险详情", detailSubtitle: "企业、融资、风险、模型、生命周期与审计的完整视图。",
      ruleEyebrow: "风险规则", ruleTitle: "风险规则中心", ruleSubtitle: "规则每次变化都生成新版本，并记录生效时间与原因。",
      refresh: "刷新", loading: "加载中…", empty: "暂无记录。", forbidden: "当前角色无权访问此页面。", confirmed: "操作已完成",
      assets: "资产情况", totalFinanced: "融资总金额", balance: "当前余额", enterprises: "企业数量", financings: "融资笔数",
      distribution: "风险分布", NORMAL: "正常", WATCH: "关注", HIGH_RISK: "高风险", DEFAULTED: "违约",
      lifecycle: "生命周期统计", normal_repaid: "正常还款", overdue: "逾期", disposal: "风险处置", restructured: "重组", recovery: "追偿", written_off: "核销",
      losses: "损失情况", recovered: "总回收金额", writtenOff: "核销金额", netLoss: "净损失", recoveryRate: "回收率",
      modelStatus: "模型状态", activeModel: "当前 ACTIVE 模型", lastUpdate: "最近模型更新时间", latestSnapshot: "最近训练数据快照", latestFailure: "最近失败原因", none: "无",
      alertSummary: "未关闭预警", myTasks: "我的任务", overdueTasks: "已逾期", facilities: "融资列表",
      scan: "运行规则检测", status: "状态", severity: "严重程度", type: "风险类型", reason: "触发原因", owner: "负责人", created: "创建时间", all: "全部", resolution: "处理结论",
      assign: "指派", start: "开始处理", resolve: "解决", close: "审核关闭", reopen: "退回重新处理", comment: "备注", addComment: "添加备注", history: "处理历史", evidence: "触发证据", createTask: "创建任务",
      mine: "我的任务", pending: "待处理", completed: "已完成", title: "标题", assignee: "负责人", due: "截止时间", note: "处理备注", addNote: "添加备注", uploadResult: "上传处理结果", complete: "完成关闭", cancel: "取消", reassign: "重新指派", changeDue: "调整截止时间", summary: "处理结论", attachments: "处理结果文件", description: "说明", taskType: "任务类型",
      enterprise: "企业信息", financing: "融资信息", risk: "风险信息", model: "模型信息", timeline: "生命周期", audit: "审计", riskClass: "风险分类", band: "风险等级", score: "风险评分", trend: "风险变化趋势", scoreHistory: "历史风险评分", artifactHash: "工件哈希", snapshot: "数据快照", actor: "操作人", time: "时间", selectFacility: "选择一笔融资",
      rule: "规则", version: "版本", threshold: "阈值", enabled: "启用", effectiveFrom: "生效时间", changeReason: "变更原因", saveVersion: "保存为新版本", pendingRules: "待生效版本", ruleHistory: "版本历史"
    }
  };
  const lang = () => (document.documentElement.lang === "ru" ? "ru" : "zh");
  const tr = (key) => COPY[lang()][key] || key;
  const role = () => document.body.dataset.workflowRole || "";
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const short = (value) => (value ? `${String(value).slice(0, 8)}…` : "—");
  const pct = (value) => (typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—");
  const notify = (message, isError = false) => { if (typeof window.toast === "function") window.toast(message, isError); };
  const chip = (value) => `<span class="status-chip risk-chip" data-risk-value="${esc(value)}">${esc(tr(value))}</span>`;
  const panel = (tag, title, body, extra = "") => `<section class="workflow-panel"><div class="workflow-panel-head"><div><span>${esc(tag)}</span><h2>${esc(title)}</h2></div>${extra}</div>${body}</section>`;
  const cards = (items) => `<div class="gov-cards">${items.map(([key, label, value]) => `<article data-metric="${esc(key)}"><small>${esc(tr(label))}</small><b>${esc(value)}</b></article>`).join("")}</div>`;
  const has = (...roles) => roles.includes(role());
  const state = { alertFilter: "", alert: null, taskView: "mine", task: null, detail: null, facilities: [], assignees: null };

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...options });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) {
      const detail = payload?.detail;
      const error = new Error(detail?.message || (Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : `HTTP ${response.status}`));
      error.status = response.status;
      throw error;
    }
    return payload;
  }
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });
  const guard = async (container, roles, loader) => {
    if (!container) return;
    if (roles && !roles.includes(role())) { container.innerHTML = `<p class="facility-empty-copy">${esc(tr("forbidden"))}</p>`; return; }
    container.innerHTML = `<p class="facility-empty-copy">${esc(tr("loading"))}</p>`;
    try { await loader(); } catch (error) { container.innerHTML = `<p class="facility-inline-error">${esc(error.message)}</p>`; }
  };
  async function assignees() {
    if (!state.assignees) state.assignees = await api("/api/v1/risk/assignees");
    return state.assignees;
  }

  // --- Dashboard -----------------------------------------------------------------

  async function refreshDashboard() {
    const container = document.querySelector("#riskDashContent");
    await guard(container, ["admin", "risk_manager", "auditor", "financier"], async () => {
      const [board, facilities] = await Promise.all([api("/api/v1/risk/dashboard"), api("/api/v1/risk/facilities")]);
      const a = board.assets, l = board.lifecycle, loss = board.losses, m = board.model;
      const total = Object.values(board.risk_distribution).reduce((x, y) => x + y, 0) || 1;
      const bars = Object.entries(board.risk_distribution).map(([key, count]) => `<div class="risk-bar" data-risk-class="${esc(key)}"><span>${esc(tr(key))}</span><i style="width:${(100 * count / total).toFixed(1)}%"></i><b>${count}</b></div>`).join("");
      const active = m.active.map((item) => `<li><b>${esc(item.label)}</b><small>${esc(item.scope)} · ${esc(item.activated_at)} · ${esc(short(item.artifact_hash))}</small></li>`).join("") || `<li>${esc(tr("none"))}</li>`;
      const severity = Object.entries(board.alerts.open_by_severity).map(([key, count]) => `<li>${chip(key)} <b>${count}</b></li>`).join("") || `<li>${esc(tr("none"))}</li>`;
      const rows = facilities.map((item) => `<tr data-risk-facility="${esc(item.facility_id)}"><td>${esc(short(item.facility_id))}</td><td>${esc(item.enterprise?.name || "—")}</td><td>${esc(item.principal)}</td><td>${esc(item.outstanding_amount)}</td><td>${esc(item.status)}</td><td>${chip(item.risk_class)}</td><td>${esc(item.latest_score ?? "—")}</td><td>${item.open_alerts}</td></tr>`).join("");
      container.innerHTML = `${panel("ASSETS", tr("assets"), `<div id="dashAssets">${cards([["total_financed", "totalFinanced", a.total_financed], ["current_balance", "balance", a.current_balance], ["enterprise_count", "enterprises", a.enterprise_count], ["financing_count", "financings", a.financing_count]])}</div>`, `<span class="task-badge">${esc(board.scope)}</span>`)}
        <div class="gov-grid">
          ${panel("RISK", tr("distribution"), `<div id="dashDistribution" class="risk-bars">${bars}</div>`)}
          ${panel("LOSS", tr("losses"), `<div id="dashLosses">${cards([["total_recovered", "recovered", loss.total_recovered], ["written_off", "writtenOff", loss.written_off], ["net_loss", "netLoss", loss.net_loss], ["recovery_rate", "recoveryRate", pct(loss.recovery_rate)]])}</div>`)}
        </div>
        ${panel("LIFECYCLE", tr("lifecycle"), `<div id="dashLifecycle">${cards(Object.keys(l).map((key) => [key, key, l[key]]))}</div>`)}
        <div class="gov-grid">
          ${panel("MODEL", tr("modelStatus"), `<div id="dashModel"><h3>${esc(tr("activeModel"))}</h3><ul class="gov-list">${active}</ul><dl class="gov-detail"><div><dt>${esc(tr("lastUpdate"))}</dt><dd>${esc(m.last_model_update || tr("none"))}</dd></div><div><dt>${esc(tr("latestSnapshot"))}</dt><dd>${m.latest_snapshot ? `${esc(short(m.latest_snapshot.snapshot_id))} · ${m.latest_snapshot.included_count}/${m.latest_snapshot.excluded_count}` : esc(tr("none"))}</dd></div><div><dt>${esc(tr("latestFailure"))}</dt><dd>${m.latest_failure ? `${esc(m.latest_failure.failure_reason)} · ${esc(m.latest_failure.completed_at)}` : esc(tr("none"))}</dd></div></dl></div>`)}
          ${panel("OPERATIONS", tr("alertSummary"), `<ul id="dashAlerts" class="gov-list">${severity}</ul>${cards([["my_open", "myTasks", board.tasks.my_open], ["my_overdue", "overdueTasks", board.tasks.my_overdue]])}`)}
        </div>
        ${panel("FACILITIES", tr("facilities"), `<div class="gov-table-wrap"><table id="riskFacilityTable" class="gov-table"><thead><tr><th>ID</th><th>${esc(tr("enterprise"))}</th><th>${esc(tr("totalFinanced"))}</th><th>${esc(tr("balance"))}</th><th>${esc(tr("status"))}</th><th>${esc(tr("riskClass"))}</th><th>${esc(tr("score"))}</th><th>⚠</th></tr></thead><tbody>${rows || `<tr><td colspan="8">${esc(tr("empty"))}</td></tr>`}</tbody></table></div>`)}`;
      container.querySelectorAll("[data-risk-facility]").forEach((row) => row.addEventListener("click", () => openDetail(row.dataset.riskFacility)));
    });
  }

  // --- Alerts --------------------------------------------------------------------

  async function refreshAlerts() {
    const container = document.querySelector("#alertContent");
    await guard(container, ["admin", "risk_manager", "auditor"], async () => {
      const query = state.alertFilter ? `?status=${state.alertFilter}` : "";
      const alerts = await api(`/api/v1/risk/alerts${query}`);
      if (state.alert) state.alert = await api(`/api/v1/risk/alerts/${state.alert.alert_id}`);
      const people = await assignees();
      const filter = `<select id="alertStatusFilter"><option value="">${esc(tr("all"))}</option>${["OPEN", "ASSIGNED", "PROCESSING", "RESOLVED", "CLOSED"].map((s) => `<option value="${s}" ${s === state.alertFilter ? "selected" : ""}>${s}</option>`).join("")}</select>`;
      const scan = has("admin", "risk_manager") ? `<button id="runRiskScan" class="btn btn-primary" type="button">${esc(tr("scan"))}</button>` : "";
      const rows = alerts.map((item) => `<tr data-alert-id="${esc(item.alert_id)}" class="${state.alert?.alert_id === item.alert_id ? "selected" : ""}"><td>${chip(item.severity)}</td><td>${esc(item.risk_type)}</td><td>${esc(item.trigger_reason)}</td><td>${esc(short(item.facility_id || item.request_id))}</td><td>${chip(item.status)}</td><td>${esc(item.owner || "—")}</td><td>${esc(item.created_at)}</td></tr>`).join("");
      container.innerHTML = `${panel("ALERTS", tr("alertTitle"), `<div class="gov-inline-form">${filter}${scan}</div><div class="gov-table-wrap"><table id="alertTable" class="gov-table"><thead><tr><th>${esc(tr("severity"))}</th><th>${esc(tr("type"))}</th><th>${esc(tr("reason"))}</th><th>ID</th><th>${esc(tr("status"))}</th><th>${esc(tr("owner"))}</th><th>${esc(tr("created"))}</th></tr></thead><tbody>${rows || `<tr><td colspan="7">${esc(tr("empty"))}</td></tr>`}</tbody></table></div>`, `<span class="task-badge">${alerts.length}</span>`)}
        <section id="alertDetail" class="workflow-panel">${alertDetail(state.alert, people)}</section>`;
      container.querySelector("#alertStatusFilter")?.addEventListener("change", (event) => { state.alertFilter = event.currentTarget.value; refreshAlerts(); });
      container.querySelector("#runRiskScan")?.addEventListener("click", async () => {
        try { const result = await post("/api/v1/risk/alerts/scan", {}); notify(`${tr("confirmed")} · +${result.created}`); await refreshAlerts(); } catch (error) { notify(error.message, true); }
      });
      container.querySelectorAll("[data-alert-id]").forEach((row) => row.addEventListener("click", async () => {
        try { state.alert = await api(`/api/v1/risk/alerts/${row.dataset.alertId}`); await refreshAlerts(); } catch (error) { notify(error.message, true); }
      }));
      container.querySelectorAll("[data-alert-action]").forEach((form) => form.addEventListener("submit", alertAction));
      container.querySelector("#alertTaskForm")?.addEventListener("submit", (event) => createTask(event, { alert_id: state.alert.alert_id }));
      container.querySelector("[data-open-alert-facility]")?.addEventListener("click", (event) => openDetail(event.currentTarget.dataset.openAlertFacility));
    });
  }

  function alertDetail(alert, people) {
    if (!alert) return `<div class="workflow-empty"><span>⚠</span><b>${esc(tr("alertTitle"))}</b></div>`;
    const me = document.body.dataset.workflowUserId;
    const owners = people.filter((p) => p.role === "risk_manager" || p.role === "admin").map((p) => `<option value="${esc(p.user_id)}" ${p.user_id === me ? "selected" : ""}>${esc(p.username)}</option>`).join("");
    const forms = [];
    const form = (action, inner, label, cls = "btn-primary") => `<form class="gov-inline-form" data-alert-action="${action}">${inner}<button class="btn ${cls}" type="submit">${esc(tr(label))}</button></form>`;
    const text = (name, placeholder) => `<input name="${name}" minlength="2" maxlength="500" required placeholder="${esc(tr(placeholder))}">`;
    if (has("admin", "risk_manager") && ["OPEN", "ASSIGNED", "PROCESSING"].includes(alert.status)) forms.push(form("assign", `<select name="owner_user_id">${owners}</select>`, "assign"));
    const isOwner = has("admin") || alert.owner_user_id === me;
    if (alert.status === "ASSIGNED" && isOwner) forms.push(form("start", "", "start"));
    if (alert.status === "PROCESSING" && isOwner) forms.push(form("resolve", text("resolution", "resolution"), "resolve"));
    if (alert.status === "RESOLVED" && has("admin", "auditor")) {
      forms.push(form("close", text("comment", "comment"), "close"));
      forms.push(form("reopen", text("comment", "comment"), "reopen", "btn-danger"));
    }
    if (alert.status !== "CLOSED") forms.push(form("comments", text("comment", "comment"), "addComment", "btn-secondary"));
    const events = alert.events.map((e) => `<li><b>${esc(e.action)} · ${esc(e.from_status || "—")} → ${esc(e.to_status)}</b><small>${esc(e.actor)} (${esc(e.actor_role)}) · ${esc(e.comment || "—")}</small><em>${esc(e.recorded_at)}</em></li>`).join("");
    const tasks = alert.tasks.map((t) => `<li>${chip(t.status)} <b>${esc(t.title)}</b> <small>${esc(t.assignee)} · ${esc(t.due_at)}</small></li>`).join("");
    const taskForm = has("admin", "risk_manager", "auditor") && alert.status !== "CLOSED" ? taskFormHtml("alertTaskForm", people) : "";
    return `<div class="workflow-panel-head"><div><span>${esc(alert.rule_key)} v${alert.rule_version}</span><h2>${esc(alert.trigger_reason)}</h2><p>${esc(alert.alert_id)} · v${alert.version}</p></div>${chip(alert.status)}</div>
      <dl class="gov-detail"><div><dt>${esc(tr("severity"))}</dt><dd>${chip(alert.severity)}</dd></div><div><dt>${esc(tr("type"))}</dt><dd>${esc(alert.risk_type)}</dd></div><div><dt>${esc(tr("owner"))}</dt><dd>${esc(alert.owner || "—")}</dd></div><div><dt>${esc(tr("resolution"))}</dt><dd>${esc(alert.resolution || "—")}</dd></div><div><dt>${esc(tr("financing"))}</dt><dd>${alert.facility_id ? `<button class="btn" type="button" data-open-alert-facility="${esc(alert.facility_id)}">${esc(short(alert.facility_id))}</button>` : esc(short(alert.request_id))}</dd></div><div><dt>${esc(tr("evidence"))}</dt><dd><code>${esc(JSON.stringify(alert.evidence))}</code></dd></div></dl>
      <div id="alertActions">${forms.join("")}</div>
      <h3>${esc(tr("history"))}</h3><ol id="alertEvents" class="facility-history-rail">${events}</ol>
      <h3>${esc(tr("navTasks"))}</h3><ul class="gov-list">${tasks || `<li>${esc(tr("empty"))}</li>`}</ul>${taskForm}`;
  }

  async function alertAction(event) {
    event.preventDefault();
    const action = event.currentTarget.dataset.alertAction;
    const data = Object.fromEntries(new FormData(event.currentTarget).entries());
    try {
      state.alert = await post(`/api/v1/risk/alerts/${state.alert.alert_id}/${action}`, { version: state.alert.version, ...data });
      notify(tr("confirmed"));
      await refreshAlerts();
    } catch (error) { notify(error.message, true); }
  }

  // --- Tasks ---------------------------------------------------------------------

  function taskFormHtml(id, people) {
    const options = people.map((p) => `<option value="${esc(p.user_id)}">${esc(p.username)} (${esc(p.role)})</option>`).join("");
    const types = ["INVESTIGATION", "COLLECTION", "DISPOSAL_REVIEW", "DATA_FIX", "OTHER"].map((t) => `<option value="${t}">${t}</option>`).join("");
    const due = new Date(Date.now() + 3 * 86400000).toISOString().slice(0, 16);
    return `<form id="${id}" class="gov-inline-form risk-task-form"><input name="title" minlength="2" maxlength="200" required placeholder="${esc(tr("title"))}"><select name="task_type">${types}</select><select name="assignee_user_id">${options}</select><input name="due_at" type="datetime-local" value="${due}" required><input name="description" minlength="2" maxlength="500" required placeholder="${esc(tr("description"))}"><button class="btn btn-primary" type="submit">${esc(tr("createTask"))}</button></form>`;
  }

  async function createTask(event, extra) {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget).entries());
    data.due_at = new Date(data.due_at).toISOString();
    try {
      state.task = await post("/api/v1/risk/tasks", { ...data, ...extra });
      notify(tr("confirmed"));
      if (document.querySelector("#view-alerts.active")) await refreshAlerts(); else await refreshTasks();
    } catch (error) { notify(error.message, true); }
  }

  async function refreshTasks() {
    const container = document.querySelector("#taskContent");
    await guard(container, ["admin", "risk_manager", "auditor"], async () => {
      const [tasks, people] = await Promise.all([api(`/api/v1/risk/tasks?view=${state.taskView}`), assignees()]);
      if (state.task) state.task = await api(`/api/v1/risk/tasks/${state.task.task_id}`);
      const tabs = ["mine", "pending", "completed"].map((view) => `<button class="btn ${view === state.taskView ? "btn-primary" : "btn-secondary"}" type="button" data-task-view="${view}">${esc(tr(view))}</button>`).join("");
      const rows = tasks.map((t) => `<tr data-task-id="${esc(t.task_id)}" class="${state.task?.task_id === t.task_id ? "selected" : ""}"><td>${esc(t.title)}</td><td>${esc(t.task_type)}</td><td>${chip(t.status)}</td><td>${esc(t.assignee)}</td><td${t.overdue ? ' class="risk-overdue"' : ""}>${esc(t.due_at)}</td><td>${esc(t.created_by)}</td></tr>`).join("");
      container.innerHTML = `${panel("TASKS", tr("taskTitle"), `<div id="taskTabs" class="gov-inline-form">${tabs}</div><div class="gov-table-wrap"><table id="taskTable" class="gov-table"><thead><tr><th>${esc(tr("title"))}</th><th>${esc(tr("taskType"))}</th><th>${esc(tr("status"))}</th><th>${esc(tr("assignee"))}</th><th>${esc(tr("due"))}</th><th>${esc(tr("created"))}</th></tr></thead><tbody>${rows || `<tr><td colspan="6">${esc(tr("empty"))}</td></tr>`}</tbody></table></div><h3>${esc(tr("createTask"))}</h3>${taskFormHtml("newTaskForm", people)}`, `<span class="task-badge">${tasks.length}</span>`)}
        <section id="taskDetail" class="workflow-panel">${taskDetail(state.task, people)}</section>`;
      container.querySelectorAll("[data-task-view]").forEach((button) => button.addEventListener("click", () => { state.taskView = button.dataset.taskView; refreshTasks(); }));
      container.querySelectorAll("[data-task-id]").forEach((row) => row.addEventListener("click", async () => {
        try { state.task = await api(`/api/v1/risk/tasks/${row.dataset.taskId}`); await refreshTasks(); } catch (error) { notify(error.message, true); }
      }));
      container.querySelector("#newTaskForm")?.addEventListener("submit", (event) => createTask(event, {}));
      container.querySelectorAll("[data-task-action]").forEach((form) => form.addEventListener("submit", taskAction));
    });
  }

  function taskDetail(task, people) {
    if (!task) return `<div class="workflow-empty"><span>☑</span><b>${esc(tr("taskTitle"))}</b></div>`;
    const me = document.body.dataset.workflowUserId;
    const assignee = task.assignee_user_id === me;
    const manager = has("admin") || task.created_by === document.body.dataset.workflowUsername;
    const open = ["OPEN", "IN_PROGRESS"].includes(task.status);
    const form = (action, inner, label, cls = "btn-primary") => `<form class="gov-inline-form" data-task-action="${action}">${inner}<button class="btn ${cls}" type="submit">${esc(tr(label))}</button></form>`;
    const text = (name, label) => `<input name="${name}" minlength="2" maxlength="500" required placeholder="${esc(tr(label))}">`;
    const forms = [];
    if (assignee && task.status === "OPEN") forms.push(form("start", "", "start"));
    if (open && (assignee || manager)) forms.push(form("notes", text("note", "note"), "addNote", "btn-secondary"));
    if (assignee && task.status === "IN_PROGRESS") {
      forms.push(form("result", `<input name="file" type="file" required>`, "uploadResult", "btn-secondary"));
      forms.push(form("complete", text("result_summary", "summary"), "complete"));
    }
    if (open && manager) {
      forms.push(form("reassign", `<select name="assignee_user_id">${people.map((p) => `<option value="${esc(p.user_id)}">${esc(p.username)}</option>`).join("")}</select>`, "reassign", "btn-secondary"));
      forms.push(form("due", `<input name="due_at" type="datetime-local" required>`, "changeDue", "btn-secondary"));
      forms.push(form("cancel", text("comment", "comment"), "cancel", "btn-danger"));
    }
    const events = task.events.map((e) => `<li><b>${esc(e.action)} · ${esc(e.from_status || "—")} → ${esc(e.to_status)}</b><small>${esc(e.actor)} (${esc(e.actor_role)}) · ${esc(e.comment || "—")}</small><em>${esc(e.recorded_at)}</em></li>`).join("");
    const files = task.attachments.map((f) => `<li><a href="/api/v1/risk/tasks/${esc(task.task_id)}/attachments/${esc(f.attachment_id)}">${esc(f.filename)}</a> <small>${f.size_bytes} B · ${esc(short(f.sha256))}</small></li>`).join("");
    return `<div class="workflow-panel-head"><div><span>${esc(task.task_type)}</span><h2>${esc(task.title)}</h2><p>${esc(task.task_id)} · v${task.version}</p></div>${chip(task.status)}</div>
      <dl class="gov-detail"><div><dt>${esc(tr("assignee"))}</dt><dd>${esc(task.assignee)}</dd></div><div><dt>${esc(tr("due"))}</dt><dd>${esc(task.due_at)}</dd></div><div><dt>${esc(tr("description"))}</dt><dd>${esc(task.description)}</dd></div><div><dt>${esc(tr("summary"))}</dt><dd>${esc(task.result_summary || "—")}</dd></div></dl>
      <div id="taskActions">${forms.join("")}</div>
      <h3>${esc(tr("attachments"))}</h3><ul id="taskAttachments" class="gov-list">${files || `<li>${esc(tr("empty"))}</li>`}</ul>
      <h3>${esc(tr("history"))}</h3><ol id="taskEvents" class="facility-history-rail">${events}</ol>`;
  }

  async function taskAction(event) {
    event.preventDefault();
    const action = event.currentTarget.dataset.taskAction;
    const data = Object.fromEntries(new FormData(event.currentTarget).entries());
    try {
      let body = { version: state.task.version, ...data };
      if (action === "result") {
        const file = data.file;
        const bytes = new Uint8Array(await file.arrayBuffer());
        let binary = "";
        bytes.forEach((b) => { binary += String.fromCharCode(b); });
        body = { version: state.task.version, filename: file.name, content_type: file.type || "application/octet-stream", content_base64: btoa(binary) };
      }
      if (action === "due") body.due_at = new Date(data.due_at).toISOString();
      state.task = await post(`/api/v1/risk/tasks/${state.task.task_id}/${action}`, body);
      notify(tr("confirmed"));
      await refreshTasks();
    } catch (error) { notify(error.message, true); }
  }

  // --- Risk detail ---------------------------------------------------------------------

  function openDetail(facilityId) {
    state.detail = { facility_id: facilityId };
    window.switchView("riskdetail");
  }

  function sparkline(history) {
    if (history.length < 2) return "";
    const points = history.map((h, i) => `${(i * 200) / (history.length - 1)},${(40 - h.final_score * 40).toFixed(1)}`).join(" ");
    return `<svg id="riskTrend" viewBox="0 0 200 40" width="200" height="40" role="img" aria-label="${esc(tr("trend"))}"><polyline fill="none" stroke="#d24b50" stroke-width="2" points="${points}"/></svg>`;
  }

  async function refreshDetail() {
    const container = document.querySelector("#riskDetailContent");
    await guard(container, null, async () => {
      state.facilities = await api("/api/v1/risk/facilities");
      const picker = `<select id="riskDetailPicker"><option value="">${esc(tr("selectFacility"))}</option>${state.facilities.map((f) => `<option value="${esc(f.facility_id)}" ${state.detail?.facility_id === f.facility_id ? "selected" : ""}>${esc(short(f.facility_id))} · ${esc(f.enterprise?.name || "")} · ${esc(tr(f.risk_class))}</option>`).join("")}</select>`;
      let body = "";
      if (state.detail?.facility_id) {
        const d = await api(`/api/v1/risk/facilities/${state.detail.facility_id}`);
        state.detail = { facility_id: state.detail.facility_id, data: d };
        const history = d.risk.history.map((h) => `<tr><td>${esc(h.recorded_at)}</td><td>${esc(h.final_score)}</td><td>${esc(h.band)}</td><td>${esc(h.model_version || "baseline")}</td></tr>`).join("");
        const timeline = d.timeline.map((e) => `<li data-stage="${esc(e.stage)}"><b>${esc(e.stage_label)} · ${esc(e.event)}</b><small>${esc(e.actor || "—")}${e.role ? ` (${esc(e.role)})` : ""} · ${esc(e.reason || "—")}</small><em>${esc(e.time)}</em></li>`).join("");
        const audit = d.audit.map((e) => `<tr><td>${esc(e.source)}</td><td>${esc(e.action)}</td><td>${esc(e.actor || "—")}</td><td>${esc(e.role || "—")}</td><td>${esc(e.time || "—")}</td><td>${esc(e.reason || "—")}</td></tr>`).join("");
        const alerts = (d.alerts || []).map((a) => `<li>${chip(a.severity)} ${chip(a.status)} <b>${esc(a.trigger_reason)}</b></li>`).join("");
        const tasks = (d.tasks || []).map((t) => `<li>${chip(t.status)} <b>${esc(t.title)}</b> <small>${esc(t.assignee)} · ${esc(t.due_at)}</small></li>`).join("");
        body = `<div class="gov-grid">
            ${panel("ENTERPRISE", tr("enterprise"), `<dl id="detailEnterprise" class="gov-detail"><div><dt>Supplier</dt><dd>${esc(d.enterprise.supplier?.name || "—")} (${esc(d.enterprise.supplier?.code || "")})</dd></div><div><dt>Core</dt><dd>${esc(d.enterprise.core_enterprise?.name || "—")}</dd></div><div><dt>Contract</dt><dd>${esc(d.enterprise.contract_number || "—")}</dd></div><div><dt>Invoice</dt><dd>${esc(d.enterprise.invoice_number || "—")}</dd></div></dl>`)}
            ${panel("FINANCING", tr("financing"), `<dl id="detailFinancing" class="gov-detail"><div><dt>${esc(tr("totalFinanced"))}</dt><dd>${esc(d.financing.principal)} ${esc(d.financing.currency)}</dd></div><div><dt>${esc(tr("balance"))}</dt><dd>${esc(d.financing.outstanding_amount)}</dd></div><div><dt>${esc(tr("status"))}</dt><dd>${esc(d.financing.status)}</dd></div><div><dt>Term</dt><dd>${esc(d.financing.term_days)}d</dd></div></dl>`)}
          </div>
          <div class="gov-grid">
            ${panel("RISK", tr("risk"), `<dl id="detailRisk" class="gov-detail"><div><dt>${esc(tr("riskClass"))}</dt><dd>${chip(d.risk.risk_class)}</dd></div><div><dt>${esc(tr("band"))}</dt><dd>${esc(d.risk.current_band || "—")}</dd></div><div><dt>${esc(tr("score"))}</dt><dd>${esc(d.risk.current_score ?? "—")}</dd></div><div><dt>${esc(tr("trend"))}</dt><dd>${d.risk.trend ? `${esc(d.risk.trend.direction)} ${esc(d.risk.trend.delta)}` : "—"}</dd></div></dl>${sparkline(d.risk.history)}<h3>${esc(tr("scoreHistory"))}</h3><div class="gov-table-wrap"><table class="gov-table"><tbody>${history || `<tr><td>${esc(tr("empty"))}</td></tr>`}</tbody></table></div>`)}
            ${d.model ? panel("MODEL", tr("model"), `<dl id="detailModel" class="gov-detail"><div><dt>${esc(tr("model"))}</dt><dd>${esc(d.model.model_version || "baseline")}</dd></div><div><dt>${esc(tr("artifactHash"))}</dt><dd title="${esc(d.model.artifact_hash)}">${esc(short(d.model.artifact_hash))}</dd></div><div><dt>${esc(tr("snapshot"))}</dt><dd>${esc(short(d.model.dataset_snapshot_id))}</dd></div><div><dt>Engine</dt><dd>${esc(d.model.engine_version || "—")}</dd></div></dl>`) : ""}
          </div>
          ${panel("LIFECYCLE", tr("timeline"), `<ol id="detailTimeline" class="facility-history-rail">${timeline || `<li>${esc(tr("empty"))}</li>`}</ol>`)}
          ${d.alerts ? `<div class="gov-grid">${panel("ALERTS", tr("navAlerts"), `<ul class="gov-list">${alerts || `<li>${esc(tr("empty"))}</li>`}</ul>`)}${panel("TASKS", tr("navTasks"), `<ul class="gov-list">${tasks || `<li>${esc(tr("empty"))}</li>`}</ul>`)}</div>` : ""}
          ${panel("AUDIT", tr("audit"), `<div class="gov-table-wrap"><table id="detailAudit" class="gov-table"><thead><tr><th>Source</th><th>Action</th><th>${esc(tr("actor"))}</th><th>Role</th><th>${esc(tr("time"))}</th><th>${esc(tr("reason"))}</th></tr></thead><tbody>${audit}</tbody></table></div>`)}`;
      }
      container.innerHTML = `${panel("FACILITY", tr("selectFacility"), `<div class="gov-inline-form">${picker}</div>`)}${body}`;
      container.querySelector("#riskDetailPicker")?.addEventListener("change", (event) => { state.detail = event.currentTarget.value ? { facility_id: event.currentTarget.value } : null; refreshDetail(); });
    });
  }

  // --- Rules ---------------------------------------------------------------------------

  async function refreshRules() {
    const container = document.querySelector("#ruleContent");
    await guard(container, ["admin", "risk_manager", "auditor"], async () => {
      const data = await api("/api/v1/risk/rules");
      const admin = has("admin");
      const row = (r) => `<tr data-rule="${esc(r.rule_key)}"><td><b>${esc(r.rule_key)}</b><br><small>${esc(r.description)}</small></td><td>v${r.version}</td><td>${esc(r.threshold ?? "—")}</td><td>${chip(r.severity)}</td><td>${r.enabled ? "✓" : "✗"}</td><td>${esc(r.effective_from)}</td><td>${esc(r.created_by)} · ${esc(r.change_reason)}</td>
        ${admin ? `<td><form class="gov-inline-form" data-rule-form="${esc(r.rule_key)}" data-rule-version="${r.version}">${r.threshold !== null ? `<input name="threshold" type="number" step="0.0001" min="0" value="${esc(r.threshold)}" required>` : ""}<select name="severity">${["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((s) => `<option ${s === r.severity ? "selected" : ""}>${s}</option>`).join("")}</select><label><input name="enabled" type="checkbox" ${r.enabled ? "checked" : ""}> ${esc(tr("enabled"))}</label><input name="effective_from" type="datetime-local" title="${esc(tr("effectiveFrom"))}"><input name="change_reason" minlength="2" maxlength="500" required placeholder="${esc(tr("changeReason"))}"><button class="btn btn-primary" type="submit">${esc(tr("saveVersion"))}</button></form></td>` : ""}</tr>`;
      const head = `<thead><tr><th>${esc(tr("rule"))}</th><th>${esc(tr("version"))}</th><th>${esc(tr("threshold"))}</th><th>${esc(tr("severity"))}</th><th>${esc(tr("enabled"))}</th><th>${esc(tr("effectiveFrom"))}</th><th>${esc(tr("changeReason"))}</th>${admin ? "<th></th>" : ""}</tr></thead>`;
      const plain = (rows) => rows.map((r) => `<tr><td>${esc(r.rule_key)}</td><td>v${r.version}</td><td>${esc(r.threshold ?? "—")}</td><td>${esc(r.severity)}</td><td>${r.enabled ? "✓" : "✗"}</td><td>${esc(r.effective_from)}</td><td>${esc(r.created_by)} · ${esc(r.change_reason)}</td></tr>`).join("");
      container.innerHTML = `${panel("RULES", tr("ruleTitle"), `<div class="gov-table-wrap"><table id="ruleTable" class="gov-table">${head}<tbody>${data.rules.map(row).join("")}</tbody></table></div>`)}
        ${data.pending.length ? panel("PENDING", tr("pendingRules"), `<div class="gov-table-wrap"><table class="gov-table"><tbody>${plain(data.pending)}</tbody></table></div>`) : ""}
        ${panel("HISTORY", tr("ruleHistory"), `<div class="gov-table-wrap"><table id="ruleHistory" class="gov-table"><tbody>${plain(data.history)}</tbody></table></div>`)}`;
      container.querySelectorAll("[data-rule-form]").forEach((form) => form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const values = new FormData(form);
        const body = {
          expected_version: Number(form.dataset.ruleVersion),
          threshold: values.has("threshold") ? String(values.get("threshold")) : null,
          severity: values.get("severity"),
          enabled: values.get("enabled") === "on",
          change_reason: String(values.get("change_reason") || "").trim(),
        };
        if (values.get("effective_from")) body.effective_from = new Date(String(values.get("effective_from"))).toISOString();
        try { await post(`/api/v1/risk/rules/${form.dataset.ruleForm}/versions`, body); notify(tr("confirmed")); await refreshRules(); } catch (error) { notify(error.message, true); }
      }));
    });
  }

  // --- Boot ----------------------------------------------------------------------------

  const REFRESH = { riskdash: refreshDashboard, alerts: refreshAlerts, tasks: refreshTasks, riskdetail: refreshDetail, rules: refreshRules };

  function applyLanguage() {
    document.querySelectorAll("[data-risk-i18n]").forEach((element) => { element.textContent = tr(element.dataset.riskI18n); });
  }

  function boot() {
    const originalSwitch = window.switchView;
    window.switchView = function (view) {
      originalSwitch(view);
      if (REFRESH[view]) REFRESH[view]();
    };
    const originalLanguage = window.setLanguage;
    window.setLanguage = function (next) {
      originalLanguage(next);
      applyLanguage();
      const active = document.querySelector(".view.active");
      const view = active?.id?.replace("view-", "");
      if (REFRESH[view]) REFRESH[view]();
    };
    document.querySelectorAll("[data-risk-refresh]").forEach((button) => button.addEventListener("click", () => REFRESH[button.dataset.riskRefresh]?.()));
    applyLanguage();
  }

  window.riskOpsOpenDetail = openDetail;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
