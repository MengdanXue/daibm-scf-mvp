(() => {
  "use strict";

  const COPY = {
    ru: {
      navAdmin: "Администрирование", adminEyebrow: "КОРПОРАТИВНАЯ ГОТОВНОСТЬ", adminTitle: "Администрирование платформы",
      adminSubtitle: "Организации, пользователи, аудиторский доступ, события безопасности, конфигурация и эксплуатация.",
      tabOrganizations: "Организации и пользователи", tabSecurity: "События безопасности", tabConfig: "Центр конфигурации", tabOps: "Эксплуатация",
      refresh: "Обновить", loading: "Загрузка…", empty: "Записей нет.", forbidden: "Текущая роль не имеет доступа.", confirmed: "Операция выполнена",
      organizations: "Организации", users: "Пользователи", grants: "Аудиторский доступ", code: "Код", name: "Название", type: "Тип", status: "Статус",
      userCount: "Пользователей", created: "Создано", username: "Учётная запись", displayName: "Имя", role: "Роль", organization: "Организация",
      active: "Активен", lockedUntil: "Заблокирован до", reason: "Причина", suspend: "Приостановить", activate: "Активировать", disable: "Отключить", enable: "Включить",
      createOrganization: "Создать организацию", createUser: "Создать пользователя", password: "Начальный пароль", grant: "Выдать доступ", revoke: "Отозвать",
      auditor: "Аудитор", grantedAt: "Выдан", eventType: "Тип события", action: "Действие", resource: "Ресурс", time: "Время", clientIp: "IP", all: "Все",
      key: "Параметр", value: "Значение", version: "Версия", source: "Источник", updatedBy: "Изменил", save: "Сохранить версию", rollback: "Откатить к этой версии",
      history: "История версий", rollbackOf: "Откат версии", health: "Состояние", database: "База данных", workers: "Фоновые процессы", business: "Бизнес-метрики",
      system: "Системные метрики", requests: "Запросов", errors: "Ошибок 5xx", uptime: "Время работы, с", facilities: "Финансирования", alerts: "Оповещения", tasks: "Задачи",
      alive: "работает", stale: "нет сигнала", disabled: "выключен", revision: "Ревизия схемы", latency: "Задержка, мс", permissionMatrix: "Матрица прав"
    },
    zh: {
      navAdmin: "平台管理", adminEyebrow: "企业级就绪", adminTitle: "平台管理",
      adminSubtitle: "组织、用户、审计授权、安全事件、配置中心与运维状态。",
      tabOrganizations: "组织与用户", tabSecurity: "安全事件", tabConfig: "配置中心", tabOps: "运维监控",
      refresh: "刷新", loading: "加载中…", empty: "暂无记录。", forbidden: "当前角色无权访问此页面。", confirmed: "操作已完成",
      organizations: "组织", users: "用户", grants: "审计授权", code: "编码", name: "名称", type: "类型", status: "状态",
      userCount: "用户数", created: "创建时间", username: "账号", displayName: "姓名", role: "角色", organization: "所属组织",
      active: "启用", lockedUntil: "锁定至", reason: "原因", suspend: "停用组织", activate: "启用组织", disable: "停用", enable: "启用",
      createOrganization: "新建组织", createUser: "新建用户", password: "初始密码", grant: "授予审计权限", revoke: "撤销",
      auditor: "审计员", grantedAt: "授予时间", eventType: "事件类型", action: "操作", resource: "资源", time: "时间", clientIp: "IP", all: "全部",
      key: "参数", value: "取值", version: "版本", source: "来源", updatedBy: "修改人", save: "保存为新版本", rollback: "回滚到此版本",
      history: "版本历史", rollbackOf: "回滚自版本", health: "健康状态", database: "数据库", workers: "后台任务", business: "业务指标",
      system: "系统指标", requests: "请求数", errors: "5xx 错误数", uptime: "运行时长（秒）", facilities: "融资", alerts: "预警", tasks: "任务",
      alive: "运行中", stale: "无心跳", disabled: "未启用", revision: "数据库版本", latency: "延迟（毫秒）", permissionMatrix: "权限矩阵"
    }
  };
  const lang = () => (document.documentElement.lang === "ru" ? "ru" : "zh");
  const tr = (key) => COPY[lang()][key] || key;
  const role = () => document.body.dataset.workflowRole || "";
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const notify = (message, isError = false) => { if (typeof window.toast === "function") window.toast(message, isError); };
  const panel = (tag, title, body) => `<section class="workflow-panel"><div class="workflow-panel-head"><div><span>${esc(tag)}</span><h2>${esc(title)}</h2></div></div>${body}</section>`;
  const table = (id, headers, rows) => `<div class="gov-table-wrap"><table id="${id}" class="gov-table"><thead><tr>${headers.map((h) => `<th>${esc(tr(h))}</th>`).join("")}</tr></thead><tbody>${rows.join("") || `<tr><td colspan="${headers.length}">${esc(tr("empty"))}</td></tr>`}</tbody></table></div>`;
  const state = { tab: "organizations", eventType: "" };
  const TABS = { organizations: ["admin"], security: ["admin", "auditor"], config: ["admin", "auditor"], ops: ["admin"] };

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
  const ask = (label) => { const value = window.prompt(label); return value && value.trim().length >= 2 ? value.trim() : null; };
  const act = async (operation) => { try { await operation(); notify(tr("confirmed")); await refresh(); } catch (error) { notify(error.message, true); } };

  async function renderOrganizations(container) {
    const [organizations, users, grants] = await Promise.all([
      api("/api/v1/admin/organizations"), api("/api/v1/admin/users"), api("/api/v1/admin/audit-grants")
    ]);
    const orgOptions = organizations.map((o) => `<option value="${esc(o.organization_id)}">${esc(o.code)} · ${esc(o.name)}</option>`).join("");
    const auditors = users.filter((u) => u.role === "auditor").map((u) => `<option value="${esc(u.user_id)}">${esc(u.username)}</option>`).join("");
    container.innerHTML = [
      panel("ORGANIZATIONS", tr("organizations"), table("adminOrganizations", ["code", "name", "type", "status", "userCount", ""],
        organizations.map((o) => `<tr data-organization="${esc(o.code)}"><td><b>${esc(o.code)}</b></td><td>${esc(o.name)}</td><td>${esc(o.type)}</td><td>${esc(o.status)}</td><td>${o.user_count}</td>
          <td><button class="btn btn-soft" type="button" data-org-status="${esc(o.organization_id)}" data-next="${o.status === "active" ? "suspended" : "active"}">${esc(tr(o.status === "active" ? "suspend" : "activate"))}</button></td></tr>`))
        + `<form class="gov-inline-form" id="createOrganizationForm"><input name="code" required pattern="[A-Z][A-Z0-9-]{2,31}" placeholder="${esc(tr("code"))}"><input name="name" required minlength="2" placeholder="${esc(tr("name"))}"><select name="organization_type"><option>supplier</option><option>core_enterprise</option><option>financier</option><option>auditor</option></select><button class="btn btn-primary" type="submit">${esc(tr("createOrganization"))}</button></form>`),
      panel("USERS", tr("users"), table("adminUsers", ["username", "displayName", "role", "organization", "active", "lockedUntil", ""],
        users.map((u) => `<tr data-user="${esc(u.username)}"><td>${esc(u.username)}</td><td>${esc(u.display_name)}</td><td>${esc(u.role)}</td><td>${esc(u.organization_code)}</td><td>${u.is_active ? "✓" : "✗"}</td><td>${esc(u.locked_until || "—")}</td>
          <td><button class="btn btn-soft" type="button" data-user-active="${esc(u.user_id)}" data-next="${u.is_active ? "false" : "true"}">${esc(tr(u.is_active ? "disable" : "enable"))}</button></td></tr>`))
        + `<form class="gov-inline-form" id="createUserForm"><input name="username" required pattern="[a-z][a-z0-9._\\-]{2,63}" placeholder="${esc(tr("username"))}"><input name="display_name" required minlength="2" placeholder="${esc(tr("displayName"))}"><select name="role">${["supplier", "core_enterprise", "financier", "risk_manager", "auditor", "admin"].map((r) => `<option>${r}</option>`).join("")}</select><select name="organization_id">${orgOptions}</select><input name="password" type="password" required autocomplete="new-password" placeholder="${esc(tr("password"))}"><button class="btn btn-primary" type="submit">${esc(tr("createUser"))}</button></form>`),
      panel("AUDIT SCOPE", tr("grants"), table("adminGrants", ["auditor", "organization", "reason", "grantedAt", "active", ""],
        grants.map((g) => `<tr><td>${esc(g.auditor)}</td><td>${esc(g.organization_code)}</td><td>${esc(g.reason)}</td><td>${esc(g.granted_at)}</td><td>${g.active ? "✓" : "✗"}</td><td>${g.active ? `<button class="btn btn-soft" type="button" data-revoke="${esc(g.grant_id)}">${esc(tr("revoke"))}</button>` : ""}</td></tr>`))
        + `<form class="gov-inline-form" id="grantForm"><select name="auditor_user_id">${auditors}</select><select name="organization_id">${orgOptions}</select><input name="reason" required minlength="2" placeholder="${esc(tr("reason"))}"><button class="btn btn-primary" type="submit">${esc(tr("grant"))}</button></form>`)
    ].join("");
    container.querySelectorAll("[data-org-status]").forEach((button) => button.addEventListener("click", () => {
      const reason = ask(tr("reason")); if (!reason) return;
      act(() => post(`/api/v1/admin/organizations/${button.dataset.orgStatus}/status`, { status: button.dataset.next, reason }));
    }));
    container.querySelectorAll("[data-user-active]").forEach((button) => button.addEventListener("click", () => {
      const reason = ask(tr("reason")); if (!reason) return;
      act(() => post(`/api/v1/admin/users/${button.dataset.userActive}/active`, { active: button.dataset.next === "true", reason }));
    }));
    container.querySelectorAll("[data-revoke]").forEach((button) => button.addEventListener("click", () => {
      const reason = ask(tr("reason")); if (!reason) return;
      act(() => post(`/api/v1/admin/audit-grants/${button.dataset.revoke}/revoke`, { reason }));
    }));
    const formBody = (form) => Object.fromEntries(new FormData(form).entries());
    container.querySelector("#createOrganizationForm").addEventListener("submit", (event) => { event.preventDefault(); act(() => post("/api/v1/admin/organizations", formBody(event.target))); });
    container.querySelector("#createUserForm").addEventListener("submit", (event) => { event.preventDefault(); act(() => post("/api/v1/admin/users", formBody(event.target))); });
    container.querySelector("#grantForm").addEventListener("submit", (event) => { event.preventDefault(); act(() => post("/api/v1/admin/audit-grants", formBody(event.target))); });
  }

  async function renderSecurity(container) {
    const query = state.eventType ? `?event_type=${encodeURIComponent(state.eventType)}` : "";
    const events = await api(`/api/v1/admin/security-events${query}`);
    const types = ["", "LOGIN_SUCCESS", "LOGIN_FAILURE", "LOGIN_LOCKED", "LOGOUT", "SESSION_EXPIRED", "PERMISSION_DENIED", "ADMIN_ACTION"];
    container.innerHTML = panel("SECURITY", tr("tabSecurity"),
      `<form class="gov-inline-form"><select id="securityEventType">${types.map((t) => `<option value="${t}" ${t === state.eventType ? "selected" : ""}>${esc(t || tr("all"))}</option>`).join("")}</select></form>`
      + table("securityEvents", ["time", "eventType", "username", "action", "resource", "clientIp"],
        events.map((e) => `<tr data-event-type="${esc(e.event_type)}"><td>${esc(e.recorded_at)}</td><td><b>${esc(e.event_type)}</b></td><td>${esc(e.username || "—")}</td><td>${esc(e.action)}</td><td>${esc(e.resource_type || "")} ${esc(e.resource_id || "")}</td><td>${esc(e.client_ip || "—")}</td></tr>`)));
    container.querySelector("#securityEventType").addEventListener("change", (event) => { state.eventType = event.target.value; refresh(); });
  }

  async function renderConfig(container) {
    const data = await api("/api/v1/admin/config");
    const admin = role() === "admin";
    const input = (item) => item.kind === "bool"
      ? `<select name="value"><option value="true" ${item.value ? "selected" : ""}>true</option><option value="false" ${item.value ? "" : "selected"}>false</option></select>`
      : `<input name="value" type="number" required min="${item.minimum ?? ""}" max="${item.maximum ?? ""}" value="${esc(item.value)}">`;
    container.innerHTML = panel("CONFIG", tr("tabConfig"), table("configTable", ["key", "value", "version", "source", "updatedBy", ""],
      data.items.map((item) => `<tr data-config="${esc(item.key)}"><td><b>${esc(item.key)}</b><br><small>${esc(item.description)}</small></td><td>${esc(item.value)}</td><td>v${item.version}</td><td>${esc(item.source)}</td><td>${esc(item.updated_by || "—")}</td>
        <td>${admin ? `<form class="gov-inline-form" data-config-form="${esc(item.key)}" data-kind="${item.kind}" data-version="${item.version}">${input(item)}<input name="reason" required minlength="2" placeholder="${esc(tr("reason"))}"><button class="btn btn-primary" type="submit">${esc(tr("save"))}</button></form>` : ""}</td></tr>`)))
      + panel("HISTORY", tr("history"), table("configHistory", ["key", "version", "value", "reason", "updatedBy", "rollbackOf", ""],
        data.history.map((h) => {
          const current = data.items.find((item) => item.key === h.key);
          const canRollback = admin && current && current.version !== h.version;
          return `<tr><td>${esc(h.key)}</td><td>v${h.version}</td><td>${esc(h.value)}</td><td>${esc(h.change_reason)}</td><td>${esc(h.created_by)} · ${esc(h.created_at)}</td><td>${h.rollback_of_version ? `v${h.rollback_of_version}` : "—"}</td>
            <td>${canRollback ? `<button class="btn btn-soft" type="button" data-config-rollback="${esc(h.key)}" data-to="${h.version}" data-current="${current.version}">${esc(tr("rollback"))}</button>` : ""}</td></tr>`;
        })));
    container.querySelectorAll("[data-config-form]").forEach((form) => form.addEventListener("submit", (event) => {
      event.preventDefault();
      const values = new FormData(form);
      const raw = String(values.get("value"));
      const value = form.dataset.kind === "bool" ? raw === "true" : Number(raw);
      act(() => post(`/api/v1/admin/config/${form.dataset.configForm}`, { value, reason: String(values.get("reason")).trim(), expected_version: Number(form.dataset.version) }));
    }));
    container.querySelectorAll("[data-config-rollback]").forEach((button) => button.addEventListener("click", () => {
      const reason = ask(tr("reason")); if (!reason) return;
      act(() => post(`/api/v1/admin/config/${button.dataset.configRollback}/rollback`, { to_version: Number(button.dataset.to), reason, expected_version: Number(button.dataset.current) }));
    }));
  }

  async function renderOps(container) {
    const [health, metrics, matrix] = await Promise.all([
      fetch("/api/v1/ops/health", { credentials: "same-origin" }).then((r) => r.json()),
      api("/api/v1/ops/metrics"),
      api("/api/v1/admin/permissions")
    ]);
    const worker = (name, item) => `<tr><td>${esc(name)}</td><td>${esc(tr(!item.enabled ? "disabled" : item.alive ? "alive" : "stale"))}</td><td>${esc(item.last_heartbeat_age_seconds ?? "—")}</td></tr>`;
    const counts = (obj) => Object.entries(obj).map(([k, v]) => `${esc(k)}: <b>${v}</b>`).join(" · ") || "—";
    container.innerHTML = panel("HEALTH", `${tr("health")}: ${health.status}`,
      `<div class="gov-cards"><article data-metric="database"><small>${esc(tr("database"))}</small><b>${health.database.ok ? "OK" : "FAIL"}</b><small>${esc(tr("revision"))} ${esc(health.database.revision || "—")} · ${esc(tr("latency"))} ${esc(health.database.latency_ms ?? "—")}</small></article>
        <article data-metric="requests"><small>${esc(tr("requests"))}</small><b>${metrics.system.request_count}</b></article>
        <article data-metric="errors"><small>${esc(tr("errors"))}</small><b>${metrics.system.error_count}</b></article>
        <article data-metric="uptime"><small>${esc(tr("uptime"))}</small><b>${metrics.system.uptime_seconds}</b></article></div>`
      + table("workerHealth", ["workers", "status", "time"], Object.entries(health.workers).map(([name, item]) => worker(name, item))))
      + panel("BUSINESS", tr("business"), `<p>${esc(tr("facilities"))}: ${counts(metrics.business.facilities_by_status)}</p><p>${esc(tr("alerts"))}: ${counts(metrics.business.alerts_by_status)}</p><p>${esc(tr("tasks"))}: ${counts(metrics.business.tasks_by_status)}</p>`)
      + panel("RBAC", tr("permissionMatrix"), table("permissionMatrix", ["action", "role"], Object.entries(matrix).map(([action, roles]) => `<tr><td>${esc(action)}</td><td>${esc(roles.join(", "))}</td></tr>`)));
  }

  const RENDER = { organizations: renderOrganizations, security: renderSecurity, config: renderConfig, ops: renderOps };

  async function refresh() {
    const container = document.querySelector("#adminContent");
    if (!container) return;
    const allowedTabs = Object.keys(TABS).filter((tab) => TABS[tab].includes(role()));
    document.querySelectorAll("[data-admin-tab]").forEach((button) => {
      button.hidden = !allowedTabs.includes(button.dataset.adminTab);
      button.setAttribute("aria-selected", String(button.dataset.adminTab === state.tab));
    });
    if (!allowedTabs.length) { container.innerHTML = `<p class="facility-empty-copy">${esc(tr("forbidden"))}</p>`; return; }
    if (!allowedTabs.includes(state.tab)) state.tab = allowedTabs[0];
    document.querySelectorAll("[data-admin-tab]").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.adminTab === state.tab)));
    container.innerHTML = `<p class="facility-empty-copy">${esc(tr("loading"))}</p>`;
    try { await RENDER[state.tab](container); } catch (error) { container.innerHTML = `<p class="facility-inline-error">${esc(error.message)}</p>`; }
  }

  function applyLanguage() {
    document.querySelectorAll("[data-admin-i18n]").forEach((element) => { element.textContent = tr(element.dataset.adminI18n); });
  }

  function boot() {
    const originalSwitch = window.switchView;
    window.switchView = function (view) { originalSwitch(view); if (view === "admin") refresh(); };
    const originalLanguage = window.setLanguage;
    window.setLanguage = function (next) {
      originalLanguage(next);
      applyLanguage();
      if (document.querySelector("#view-admin.active")) refresh();
    };
    document.querySelectorAll("[data-admin-tab]").forEach((button) => button.addEventListener("click", () => { state.tab = button.dataset.adminTab; refresh(); }));
    document.querySelector("[data-admin-refresh]")?.addEventListener("click", refresh);
    applyLanguage();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
