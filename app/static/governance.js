(() => {
  "use strict";

  const COPY = {
    ru: {
      navGovernance: "Центр моделей", navFeedback: "Обратная связь данных",
      govEyebrow: "УПРАВЛЕНИЕ МОДЕЛЯМИ", govTitle: "Центр моделей", govSubtitle: "Версии, статусы, метрики независимой проверки, история активаций и откат — из PostgreSQL.",
      fbEyebrow: "УПРАВЛЕНИЕ ДАННЫМИ", fbTitle: "Обратная связь по результатам", fbSubtitle: "Каждый результат проходит проверку пригодности до обучения; исправления добавляются новой ревизией.",
      refresh: "Обновить", forbidden: "Текущая роль не имеет доступа к этому разделу.", loading: "Загрузка…", empty: "Записей пока нет.",
      activeModels: "Активные модели", noActive: "нет активной модели", models: "Реестр моделей", matrix: "Матрица совместимости контуров",
      version: "Версия", type: "Тип", scope: "Контур", status: "Статус", dataset: "Снимок данных", samples: "n", metrics: "Brier (holdout) до → после", created: "Создана", creator: "Автор",
      detail: "Карточка модели", events: "История реестра", artifact: "Проверка артефакта", consistent: "хеш совпадает", inconsistent: "хеш НЕ совпадает", rollback: "Откатить к предыдущей", retire: "Вывести из эксплуатации",
      candidates: "Кандидаты на активацию", noCandidates: "Нет кандидатов, ожидающих решения.", activationHistory: "История активаций", activatedAt: "Активирована", promotionReason: "Основание активации", activate: "Активировать", evaluation: "Оценка", unregistered: "Незарегистрированные артефакты",
      reviewingCount: "На проверке", rejectedCount: "Отклонено", correctionCount: "Исправления", reviewQueue: "Очередь проверки", noReviewing: "Нет результатов, ожидающих проверки.", reviewComment: "Комментарий проверяющего", approve: "Одобрить", reject: "Отклонить", trainingData: "Данные для обучения", included: "Включено", excluded: "Исключено", observed: "Наблюдение", reviewHistory: "История проверки", snapshots: "Снимки обучающих данных", usedByModels: "Версии моделей", trainingResult: "Результат обучения", datasetSnapshot: "Снимок данных обучения", trainingOutcomes: "Результатов в обучении",
      navSnapshots: "Снимки данных", snEyebrow: "ДАННЫЕ ОБУЧЕНИЯ", snTitle: "Снимки обучающих данных", snSubtitle: "Каждое обучение читает только неизменяемый снимок проверенных результатов.",
      reasonCode: "Код основания", confirmed: "Операция выполнена", modelScope: "Модель", requestScope: "Запрос", result: "Результат", reason: "Причина",
      totals: "Всего записей", effective: "Действующих", eligible: "Пригодны для обучения", superseded: "Заменено исправлением", byStatus: "По статусу проверки", rejected: "Причины отклонения",
      outcomes: "Результаты", revision: "Ревизия", review: "Проверка", lineage: "Цепочка исправлений", showAll: "Показать и заменённые ревизии",
      decisionTitle: "Почему такой уровень риска", decisionNone: "Оценка риска ещё не выполнена.", assessedBy: "Оценил", input: "Входные данные", model: "Модель калибровки", artifactHash: "Хеш артефакта", scopeCheck: "Проверка контура", fallback: "Возврат к базовой модели", baseline: "базовая модель без калибровки", band: "Уровень",
      DRAFT: "Черновик", EVALUATING: "Оценивается", CANDIDATE: "Кандидат", ACTIVE: "Активна", ROLLED_BACK: "Откачена", RETIRED: "Выведена", REJECTED: "Отклонена",
      CREATED: "Создан", REVIEWING: "На проверке", ELIGIBLE: "Пригоден", TRAINING_USED: "Использован в обучении"
    },
    zh: {
      navGovernance: "模型中心", navFeedback: "结果治理",
      govEyebrow: "模型治理", govTitle: "模型中心", govSubtitle: "版本、状态、独立验证指标、激活历史与回滚，全部来自 PostgreSQL。",
      fbEyebrow: "数据治理", fbTitle: "结果数据治理", fbSubtitle: "每条业务结果经审核（CREATED → REVIEWING → ELIGIBLE → TRAINING_USED 或 REJECTED）后才可进入训练；更正以新修订追加，原记录永不修改。",
      refresh: "刷新", forbidden: "当前角色无权访问此页面。", loading: "加载中…", empty: "暂无记录。",
      activeModels: "当前激活模型", noActive: "无激活模型", models: "模型注册表", matrix: "Scope 兼容矩阵",
      version: "版本", type: "类型", scope: "Scope", status: "状态", dataset: "数据快照", samples: "n", metrics: "Brier（留出集）前 → 后", created: "创建时间", creator: "创建者",
      detail: "模型详情", events: "注册表历史", artifact: "工件校验", consistent: "哈希一致", inconsistent: "哈希不一致", rollback: "回滚到上一版本", retire: "下线模型",
      candidates: "待激活候选模型", noCandidates: "暂无等待决策的候选模型。", activationHistory: "激活历史", activatedAt: "激活时间", promotionReason: "晋升原因", activate: "激活", evaluation: "评估", unregistered: "未注册的工件",
      reviewingCount: "审核中", rejectedCount: "已拒绝", correctionCount: "更正数", reviewQueue: "待审核结果", noReviewing: "暂无等待审核的结果。", reviewComment: "审核意见", approve: "通过", reject: "拒绝", trainingData: "进入训练的数据", included: "纳入", excluded: "排除", observed: "观测时间", reviewHistory: "审核历史", snapshots: "训练数据快照", usedByModels: "使用该快照的模型版本", trainingResult: "训练结果", datasetSnapshot: "训练数据快照", trainingOutcomes: "训练结果数",
      navSnapshots: "数据快照", snEyebrow: "训练数据", snTitle: "训练数据快照", snSubtitle: "每次训练只读取经审核、不可变的结果数据快照。",
      reasonCode: "原因代码", confirmed: "操作已完成", modelScope: "模型", requestScope: "请求", result: "结果", reason: "原因",
      totals: "记录总数", effective: "有效结果", eligible: "可训练", superseded: "已被更正替代", byStatus: "审查状态分布", rejected: "拒绝原因",
      outcomes: "业务结果", revision: "修订", review: "审查", lineage: "更正链", showAll: "包含已被替代的修订",
      decisionTitle: "为什么得到这个风险等级", decisionNone: "尚未进行风险评估。", assessedBy: "评估人", input: "输入数据", model: "校准模型", artifactHash: "工件哈希", scopeCheck: "Scope 检查", fallback: "回退到基线", baseline: "未校准的基线模型", band: "等级",
      DRAFT: "草稿", EVALUATING: "评估中", CANDIDATE: "候选", ACTIVE: "激活", ROLLED_BACK: "已回滚", RETIRED: "已下线", REJECTED: "已拒绝",
      CREATED: "已创建", REVIEWING: "审查中", ELIGIBLE: "可训练", TRAINING_USED: "已用于训练"
    }
  };
  const READ_ROLES = new Set(["auditor", "risk_manager", "financier"]);
  const state = { registry: null, selected: null, summary: null, outcomes: [], lineage: null, includeSuperseded: false, reviewHistory: null, eligibilityScope: "controlled_demo", snapshots: null, snapshot: null };

  const lang = () => (document.documentElement.lang === "ru" ? "ru" : "zh");
  const tr = (key) => COPY[lang()][key] || key;
  const role = () => document.body.dataset.workflowRole || "";
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const short = (hash) => (hash ? `${String(hash).slice(0, 10)}…${String(hash).slice(-6)}` : "—");
  const num = (value) => (typeof value === "number" ? value.toFixed(4) : "—");
  const chip = (status) => `<span class="status-chip gov-status" data-registry-status="${esc(status)}">${esc(tr(status))}</span>`;

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...options });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) {
      const error = new Error(payload?.detail?.message || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return payload;
  }
  const notify = (message, isError = false) => { if (typeof window.toast === "function") window.toast(message, isError); };

  // --- Model center (model versions are the registry of record) -------------

  async function refreshRegistry() {
    const container = document.querySelector("#governanceContent");
    if (!container) return;
    if (!READ_ROLES.has(role())) { container.innerHTML = `<p class="facility-empty-copy">${esc(tr("forbidden"))}</p>`; return; }
    container.innerHTML = `<p class="facility-empty-copy">${esc(tr("loading"))}</p>`;
    try {
      const [versions, history, matrix] = await Promise.all([
        api("/api/v1/model-versions"),
        api("/api/v1/model-versions/activation-history"),
        api("/api/v1/model-registry/scope-compatibility"),
      ]);
      state.registry = { ...versions, history, matrix };
      if (state.selected) state.selected = await api(`/api/v1/model-versions/${state.selected.id}`);
      renderRegistry();
    } catch (error) { container.innerHTML = `<p class="facility-inline-error">${esc(error.message)}</p>`; }
  }

  const brier = (item, key) => num(item.metrics?.[key]?.brier_score);
  const panel = (tag, title, body, badge = "") => `<section class="workflow-panel"><div class="workflow-panel-head"><div><span>${esc(tag)}</span><h2>${esc(title)}</h2></div>${badge}</div>${body}</section>`;

  function renderRegistry() {
    const container = document.querySelector("#governanceContent");
    const registry = state.registry;
    if (!container || !registry) return;
    const auditor = role() === "auditor";
    const active = ["controlled_demo", "external_verified"].map((scope) => {
      const model = registry.active_by_scope[scope];
      return `<article data-active-scope="${esc(scope)}"><span>${esc(scope)}</span><b>${esc(model ? model.label : tr("noActive"))}</b>
        <small>${esc(tr("activatedAt"))}: ${esc(model?.activated_at || "—")}</small><small title="${esc(model?.artifact_hash || "")}">${esc(tr("artifactHash"))}: ${esc(short(model?.artifact_hash))}</small>
        <small>${esc(tr("promotionReason"))}: ${esc(model?.promotion_reason || "—")}</small></article>`;
    }).join("");
    const candidates = registry.candidates.map((item) => `<li data-candidate-id="${esc(item.id)}"><b>${esc(item.label)}</b><small>${esc(item.scope)} · n=${esc(item.metrics?.sample_count ?? "—")} · Brier ${esc(brier(item, "holdout_before"))} → ${esc(brier(item, "holdout_after"))} · ${esc(short(item.artifact_hash))}</small></li>`).join("");
    const rows = registry.versions.map((item) => `<tr data-version-id="${esc(item.id)}" class="${state.selected?.id === item.id ? "selected" : ""}">
      <td><b>${esc(item.label)}</b></td><td>${esc(item.model_type)}</td><td>${esc(item.scope)}</td><td>${chip(item.status)}</td>
      <td title="${esc(item.training_dataset_version)}">${esc(short(item.training_dataset_version))}</td><td>${esc(item.metrics?.sample_count ?? "—")}</td>
      <td>${esc(brier(item, "holdout_before"))} → ${esc(brier(item, "holdout_after"))}</td>
      <td title="${esc(item.artifact_hash)}">${esc(short(item.artifact_hash))}</td>
      <td>${esc(item.created_at)}</td><td>${esc(item.created_by)}</td><td>${esc(item.activated_at || "—")}</td></tr>`).join("");
    const history = registry.history.map((item) => `<li><b>${esc(item.version_label)} · ${esc(tr(item.from_status || "—"))} → ${esc(tr(item.to_status))}</b><small>${esc(item.reason)} · ${esc(item.actor)} · ${esc(short(item.artifact_hash))}</small><em>${esc(item.recorded_at)}</em></li>`).join("");
    const matrix = registry.matrix.map((row) => `<tr><td>${esc(row.model_scope)}</td><td>${esc(row.request_scope)}</td><td><span class="gov-scope" data-scope-result="${esc(row.result)}">${esc(row.result)}</span></td><td>${esc(row.reason)}</td></tr>`).join("");
    const unregistered = auditor && registry.unregistered_artifacts.length
      ? `<p class="gov-note">${esc(tr("unregistered"))}: ${registry.unregistered_artifacts.map((id) => `<button class="btn" type="button" data-register-run="${esc(id)}">${esc(short(id))}</button>`).join(" ")}</p>` : "";
    container.innerHTML = `${panel("ACTIVE", tr("activeModels"), `<div id="governanceActive" class="gov-cards">${active}</div>`)}
      <div class="gov-grid">
        ${panel("CANDIDATE", tr("candidates"), `<ul id="modelCandidates" class="gov-list">${candidates || `<li>${esc(tr("noCandidates"))}</li>`}</ul>`, `<span class="task-badge">${registry.candidates.length}</span>`)}
        ${panel("HISTORY", tr("activationHistory"), `<ol id="activationHistory" class="facility-history-rail">${history || `<li>${esc(tr("empty"))}</li>`}</ol>`)}
      </div>
      <div class="gov-grid">
        ${panel("REGISTRY", tr("models"), `${unregistered}<div class="gov-table-wrap"><table id="modelRegistryTable" class="gov-table"><thead><tr><th>${esc(tr("version"))}</th><th>${esc(tr("type"))}</th><th>${esc(tr("scope"))}</th><th>${esc(tr("status"))}</th><th>${esc(tr("dataset"))}</th><th>${esc(tr("samples"))}</th><th>${esc(tr("metrics"))}</th><th>${esc(tr("artifactHash"))}</th><th>${esc(tr("created"))}</th><th>${esc(tr("creator"))}</th><th>${esc(tr("activatedAt"))}</th></tr></thead><tbody>${rows || `<tr><td colspan="11">${esc(tr("empty"))}</td></tr>`}</tbody></table></div>`, `<span class="task-badge">${registry.versions.length}</span>`)}
        <section id="modelDetail" class="workflow-panel">${renderModelDetail()}</section>
      </div>
      ${panel("SCOPE", tr("matrix"), `<div class="gov-table-wrap"><table id="scopeMatrix" class="gov-table"><thead><tr><th>${esc(tr("modelScope"))}</th><th>${esc(tr("requestScope"))}</th><th>${esc(tr("result"))}</th><th>${esc(tr("reason"))}</th></tr></thead><tbody>${matrix}</tbody></table></div>`)}`;
    const select = async (id) => {
      try { state.selected = await api(`/api/v1/model-versions/${id}`); renderRegistry(); }
      catch (error) { notify(error.message, true); }
    };
    container.querySelectorAll("[data-version-id]").forEach((row) => row.addEventListener("click", () => select(row.dataset.versionId)));
    container.querySelectorAll("[data-candidate-id]").forEach((row) => row.addEventListener("click", () => select(row.dataset.candidateId)));
    container.querySelectorAll("[data-register-run]").forEach((button) => button.addEventListener("click", () => command("/api/v1/model-versions", { calibration_run_id: button.dataset.registerRun })));
    container.querySelectorAll("[data-open-snapshot]").forEach((button) => button.addEventListener("click", () => showSnapshot(button.dataset.openSnapshot)));
    container.querySelector("#activateVersionForm")?.addEventListener("submit", (event) => submitForm(event, "activate"));
    container.querySelector("#rollbackVersionForm")?.addEventListener("submit", (event) => submitForm(event, "rollback"));
    container.querySelector("#retireModelForm")?.addEventListener("submit", (event) => submitForm(event, "retire"));
  }

  function renderModelDetail() {
    const model = state.selected;
    if (!model) return `<div class="workflow-empty"><span>≋</span><b>${esc(tr("detail"))}</b></div>`;
    const check = model.artifact_check;
    const transitions = (model.transitions || []).map((item) => `<li><b>#${esc(item.status_sequence)} · ${esc(tr(item.from_status || "—"))} → ${esc(tr(item.to_status))}</b><small>${esc(item.reason)} · ${esc(item.actor)}${item.evaluation_metrics ? ` · ${esc(tr("evaluation"))} ✓` : ""}</small><em>${esc(item.recorded_at)}</em></li>`).join("");
    const auditor = role() === "auditor";
    const evaluation = model.evaluation ? `${model.evaluation_passed ? "✓" : "✗"} ${esc(model.evaluation.gate_reason || model.evaluation.source || "")}` : "—";
    const retirable = !["ACTIVE", "RETIRED"].includes(model.status);
    return `<div class="workflow-panel-head"><div><span>${esc(model.model_id)}</span><h2>${esc(model.label)}</h2><p>${esc(model.id)}</p></div>${chip(model.status)}</div>
      <dl class="gov-detail">
        <div><dt>${esc(tr("type"))}</dt><dd>${esc(model.model_type)}</dd></div><div><dt>${esc(tr("scope"))}</dt><dd>${esc(model.scope)}</dd></div>
        <div><dt>${esc(tr("dataset"))}</dt><dd title="${esc(model.training_dataset_version)}">${esc(short(model.training_dataset_version))}</dd></div>
        <div><dt>${esc(tr("artifactHash"))}</dt><dd title="${esc(model.artifact_hash)}">${esc(short(model.artifact_hash))}</dd></div>
        <div><dt>${esc(tr("artifact"))}</dt><dd id="artifactCheck" data-consistent="${esc(check?.consistent)}">${esc(check ? tr(check.consistent ? "consistent" : "inconsistent") : "—")}</dd></div>
        <div><dt>${esc(tr("evaluation"))}</dt><dd>${evaluation}</dd></div>
        <div><dt>${esc(tr("metrics"))}</dt><dd>${esc(brier(model, "holdout_before"))} → ${esc(brier(model, "holdout_after"))}</dd></div>
        <div><dt>${esc(tr("creator"))}</dt><dd>${esc(model.created_by)}</dd></div>
        <div><dt>${esc(tr("activatedAt"))}</dt><dd>${esc(model.activated_at || "—")}</dd></div>
        <div><dt>${esc(tr("promotionReason"))}</dt><dd>${esc(model.promotion_reason || "—")}</dd></div>
        <div><dt>${esc(tr("datasetSnapshot"))}</dt><dd id="modelSnapshot">${model.dataset_snapshot_id ? `<button class="btn" type="button" data-open-snapshot="${esc(model.dataset_snapshot_id)}">${esc(short(model.dataset_snapshot_id))}</button>` : "—"}</dd></div>
        <div><dt>${esc(tr("trainingOutcomes"))}</dt><dd>${esc(model.training_outcome_count ?? "—")} / ${esc(tr("excluded"))} ${esc(model.excluded_outcome_count ?? "—")}</dd></div>
        <div><dt>${esc(tr("rejected"))}</dt><dd>${model.exclusion_reasons ? Object.entries(model.exclusion_reasons).map(([reason, count]) => `${esc(reason)}: ${count}`).join(", ") || "—" : "—"}</dd></div>
      </dl>
      ${auditor && model.can_activate ? `<form id="activateVersionForm" class="gov-inline-form"><label><span>${esc(tr("promotionReason"))}</span><input name="reason" minlength="8" maxlength="500" required></label><button class="btn btn-primary" type="submit">${esc(tr("activate"))}</button></form>` : ""}
      ${auditor && model.can_rollback ? `<form id="rollbackVersionForm" class="gov-inline-form"><label><span>${esc(tr("reasonCode"))}</span><input name="reason_code" value="MODEL_DEGRADED" pattern="[A-Z][A-Z0-9_]{2,63}" required></label><button class="btn btn-danger" type="submit">${esc(tr("rollback"))}</button></form>` : ""}
      ${auditor && retirable ? `<form id="retireModelForm" class="gov-inline-form"><label><span>${esc(tr("reasonCode"))}</span><input name="reason_code" value="OBSOLETE_MODEL" pattern="[A-Z][A-Z0-9_]{2,63}" required></label><button class="btn btn-danger" type="submit">${esc(tr("retire"))}</button></form>` : ""}
      <h3>${esc(tr("events"))}</h3><ol id="modelEvents" class="facility-history-rail">${transitions || `<li>${esc(tr("empty"))}</li>`}</ol>`;
  }

  async function command(path, body) {
    try {
      const result = await api(path, { method: "POST", body: JSON.stringify(body) });
      if (result && result.id) state.selected = result;
      notify(tr("confirmed"));
      await refreshRegistry();
    } catch (error) { notify(error.message, true); }
  }

  async function submitForm(event, action) {
    event.preventDefault();
    const model = state.selected;
    if (!model) return;
    const data = Object.fromEntries(new FormData(event.currentTarget).entries());
    if (action === "retire") {
      try {
        await api(`/api/v1/model-registry/calibration/${model.calibration_run_id}/retire`, { method: "POST", body: JSON.stringify({ reason_code: String(data.reason_code || "").trim() }) });
        notify(tr("confirmed"));
        await refreshRegistry();
      } catch (error) { notify(error.message, true); }
      return;
    }
    const body = action === "activate" ? { reason: String(data.reason || "").trim() } : { reason_code: String(data.reason_code || "").trim() };
    await command(`/api/v1/model-versions/${model.id}/${action}`, body);
  }

  // --- Outcome governance ----------------------------------------------------------

  const REVIEWERS = ["auditor", "risk_manager"];
  const REJECT_REASONS = ["QUALITY_ANOMALY", "DATA_QUALITY_INSUFFICIENT", "BUSINESS_INCONSISTENT", "BUSINESS_EXCEPTION", "SCOPE_MISMATCH"];

  async function refreshFeedback() {
    const container = document.querySelector("#feedbackContent");
    if (!container) return;
    if (!REVIEWERS.includes(role())) { container.innerHTML = `<p class="facility-empty-copy">${esc(tr("forbidden"))}</p>`; return; }
    container.innerHTML = `<p class="facility-empty-copy">${esc(tr("loading"))}</p>`;
    try {
      const [overview, queue, eligibility] = await Promise.all([
        api("/api/v1/outcome-governance/overview"),
        api("/api/v1/outcome-governance/review-queue?status=REVIEWING"),
        api(`/api/v1/outcome-governance/eligibility?scope=${state.eligibilityScope}`),
      ]);
      state.summary = { overview, queue, eligibility };
      state.outcomes = role() === "auditor" ? await api(`/api/v1/outcomes?limit=200&include_superseded=${state.includeSuperseded}`) : [];
      renderFeedback();
    } catch (error) { container.innerHTML = `<p class="facility-inline-error">${esc(error.message)}</p>`; }
  }

  function renderFeedback() {
    const container = document.querySelector("#feedbackContent");
    if (!container || !state.summary || !state.summary.overview) return;
    const { overview, queue, eligibility } = state.summary;
    const card = (key, label, value) => `<article data-outcome-count="${esc(key)}"><small>${esc(tr(label))}</small><b>${esc(value)}</b></article>`;
    const cards = `<div id="outcomeGovernanceCounts" class="gov-cards">${card("total", "totals", overview.total_records)}${card("reviewing", "reviewingCount", overview.reviewing)}${card("eligible", "eligible", overview.eligible + overview.training_used)}${card("rejected", "rejectedCount", overview.rejected)}${card("corrections", "correctionCount", overview.corrections)}</div>`;
    const reasonOptions = REJECT_REASONS.map((code) => `<option value="${code}">${esc(code)}</option>`).join("");
    const queueRows = queue.map((item) => `<tr data-review-outcome="${esc(item.outcome_id)}"><td title="${esc(item.outcome_id)}">${esc(short(item.outcome_id))}</td><td>r${item.revision}</td><td>${esc(item.provenance)}</td><td>${item.defaulted ? "default" : "—"}</td><td>${esc(item.loss_amount)}</td><td>${esc(num(item.original_risk_score))}</td><td>${esc(item.observed_at)}</td>
      <td><form class="gov-inline-form" data-review-form="${esc(item.outcome_id)}"><input name="comment" minlength="4" maxlength="500" required placeholder="${esc(tr("reviewComment"))}"><select name="reason_code">${reasonOptions}</select><button class="btn btn-primary" type="submit" name="decision" value="APPROVE">${esc(tr("approve"))}</button><button class="btn btn-danger" type="submit" name="decision" value="REJECT">${esc(tr("reject"))}</button></form></td></tr>`).join("");
    const exclusions = Object.entries(eligibility.exclusion_summary).map(([reason, count]) => `<li><span title="${esc(overview.exclusion_reason_labels[reason] || "")}">${esc(reason)}</span> <b>${count}</b></li>`).join("");
    const trainingRows = eligibility.outcomes.map((item) => `<tr data-eligibility-included="${item.included}"><td title="${esc(item.outcome_id)}">${esc(short(item.outcome_id))}</td><td>r${item.revision}</td><td>${chip(item.review_status || "CREATED")}</td><td>${item.included ? "✓" : "✗"}</td><td>${esc(item.exclusion_reason || "—")}</td><td>${esc(num(item.original_risk_score))}</td><td>${item.defaulted ? "default" : "—"}</td></tr>`).join("");
    const outcomes = state.outcomes.map((item) => `<tr data-outcome-id="${esc(item.outcome_id)}"><td>${esc(short(item.outcome_id))}</td><td>r${item.revision}</td><td>${chip(item.review_status || "CREATED")}</td><td>${esc(item.review_reason || "—")}</td><td>${item.defaulted ? "default" : "—"}</td><td>${esc(item.loss_amount)}</td><td>${esc(item.provenance)}</td></tr>`).join("");
    const lineage = state.lineage ? state.lineage.revisions.map((entry) => `<li><b>r${entry.outcome.revision} · ${esc(short(entry.outcome.outcome_id))}${entry.is_effective ? " ✓" : ""}</b><small>${esc(entry.outcome.correction_reason_code || "original")} · ${entry.review_history.map((row) => esc(row.status + (row.reason_code ? `(${row.reason_code})` : ""))).join(" → ")}</small><em>${entry.corrections.map((row) => esc(`${row.action}:${row.reason_code}`)).join(", ") || "—"}</em></li>`).join("") : "";
    const history = state.reviewHistory ? state.reviewHistory.history.map((row) => `<li><b>${esc(tr(row.from_status || "—"))} → ${esc(tr(row.to_status))}${row.reason_code ? ` · ${esc(row.reason_code)}` : ""}</b><small>${esc(row.operator)} (${esc(row.role || "—")}) · ${esc(row.comment || "—")}</small><em>${esc(row.recorded_at)}</em></li>`).join("") : "";
    const scopeSelect = `<select id="eligibilityScope">${["controlled_demo", "external_verified"].map((scope) => `<option value="${scope}" ${scope === state.eligibilityScope ? "selected" : ""}>${scope}</option>`).join("")}</select>`;
    container.innerHTML = `${panel("OUTCOMES", tr("fbTitle"), cards)}
      ${panel("REVIEW", tr("reviewQueue"), `<div class="gov-table-wrap"><table id="outcomeReviewQueue" class="gov-table"><thead><tr><th>ID</th><th>${esc(tr("revision"))}</th><th>${esc(tr("scope"))}</th><th>default</th><th>loss</th><th>score</th><th>${esc(tr("observed"))}</th><th>${esc(tr("review"))}</th></tr></thead><tbody>${queueRows || `<tr><td colspan="8">${esc(tr("noReviewing"))}</td></tr>`}</tbody></table></div>`, `<span class="task-badge">${queue.length}</span>`)}
      ${panel("TRAINING DATA", tr("trainingData"), `<div class="gov-inline-form">${scopeSelect}<span>${esc(tr("included"))}: <b id="eligibleIncluded">${eligibility.included_count}</b> · ${esc(tr("excluded"))}: <b>${eligibility.excluded_count}</b> · hash <span title="${esc(eligibility.dataset_hash)}">${esc(short(eligibility.dataset_hash))}</span></span></div><ul id="exclusionReasons" class="gov-list">${exclusions || `<li>${esc(tr("empty"))}</li>`}</ul><div class="gov-table-wrap"><table id="trainingDataTable" class="gov-table"><thead><tr><th>ID</th><th>${esc(tr("revision"))}</th><th>${esc(tr("review"))}</th><th>${esc(tr("included"))}</th><th>${esc(tr("reason"))}</th><th>score</th><th>default</th></tr></thead><tbody>${trainingRows || `<tr><td colspan="7">${esc(tr("empty"))}</td></tr>`}</tbody></table></div>`)}
      ${role() === "auditor" ? `<section class="workflow-panel"><div class="workflow-panel-head"><div><span>CORRECTIONS</span><h2>${esc(tr("outcomes"))}</h2></div><label class="gov-toggle"><input id="includeSuperseded" type="checkbox" ${state.includeSuperseded ? "checked" : ""}> ${esc(tr("showAll"))}</label></div>
        <div class="gov-table-wrap"><table id="outcomeGovernanceTable" class="gov-table"><thead><tr><th>ID</th><th>${esc(tr("revision"))}</th><th>${esc(tr("review"))}</th><th>${esc(tr("reason"))}</th><th>default</th><th>loss</th><th>${esc(tr("scope"))}</th></tr></thead><tbody>${outcomes || `<tr><td colspan="7">${esc(tr("empty"))}</td></tr>`}</tbody></table></div>
        ${state.lineage ? `<h3>${esc(tr("lineage"))}</h3><ol id="outcomeRevisionChain" class="facility-history-rail">${lineage}</ol>` : ""}
        ${state.reviewHistory ? `<h3>${esc(tr("reviewHistory"))}</h3><ol id="outcomeReviewHistory" class="facility-history-rail">${history}</ol>` : ""}</section>` : ""}`;
    container.querySelector("#includeSuperseded")?.addEventListener("change", (event) => { state.includeSuperseded = event.currentTarget.checked; refreshFeedback(); });
    container.querySelector("#eligibilityScope")?.addEventListener("change", (event) => { state.eligibilityScope = event.currentTarget.value; refreshFeedback(); });
    container.querySelectorAll("[data-outcome-id]").forEach((row) => row.addEventListener("click", async () => {
      try {
        [state.lineage, state.reviewHistory] = await Promise.all([
          api(`/api/v1/outcomes/${row.dataset.outcomeId}/lineage`),
          api(`/api/v1/outcomes/${row.dataset.outcomeId}/review-history`),
        ]);
        renderFeedback();
      } catch (error) { notify(error.message, true); }
    }));
    container.querySelectorAll("[data-review-form]").forEach((form) => form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const decision = event.submitter?.value || "APPROVE";
      const data = new FormData(form);
      const body = { decision, comment: String(data.get("comment") || "").trim() };
      if (decision === "REJECT") body.reason_code = data.get("reason_code");
      try {
        await api(`/api/v1/outcomes/${form.dataset.reviewForm}/review`, { method: "POST", body: JSON.stringify(body) });
        notify(tr("confirmed"));
        await refreshFeedback();
      } catch (error) { notify(error.message, true); }
    }));
  }

  // --- Dataset snapshots ---------------------------------------------------------------

  async function refreshSnapshots() {
    const container = document.querySelector("#snapshotContent");
    if (!container) return;
    if (!READ_ROLES.has(role())) { container.innerHTML = `<p class="facility-empty-copy">${esc(tr("forbidden"))}</p>`; return; }
    container.innerHTML = `<p class="facility-empty-copy">${esc(tr("loading"))}</p>`;
    try {
      state.snapshots = await api("/api/v1/dataset-snapshots");
      if (state.snapshot) state.snapshot = await api(`/api/v1/dataset-snapshots/${state.snapshot.snapshot_id}`);
      renderSnapshots();
    } catch (error) { container.innerHTML = `<p class="facility-inline-error">${esc(error.message)}</p>`; }
  }

  async function openSnapshot(snapshotId) {
    try { state.snapshot = await api(`/api/v1/dataset-snapshots/${snapshotId}`); renderSnapshots(); }
    catch (error) { notify(error.message, true); }
  }

  function renderSnapshots() {
    const container = document.querySelector("#snapshotContent");
    if (!container || !state.snapshots) return;
    const versions = (item) => item.model_versions.map((version) => `${esc(version.label)} ${chip(version.status)}`).join("<br>") || "—";
    const runs = (item) => item.training_runs.map((run) => esc(run.failure_reason || run.deployment_status)).join(", ") || "—";
    const rows = state.snapshots.map((item) => `<tr data-snapshot-id="${esc(item.snapshot_id)}" class="${state.snapshot?.snapshot_id === item.snapshot_id ? "selected" : ""}"><td title="${esc(item.snapshot_id)}">${esc(short(item.snapshot_id))}</td><td>${esc(item.created_at)}</td><td>${esc(item.scope)}</td><td>${item.included_count}</td><td>${item.excluded_count}</td><td title="${esc(item.dataset_hash)}">${esc(short(item.dataset_hash))}</td><td>${versions(item)}</td><td>${runs(item)}</td></tr>`).join("");
    const detail = state.snapshot;
    const items = detail ? detail.outcomes.map((row) => `<tr data-snapshot-included="${row.included}"><td title="${esc(row.outcome_id)}">${esc(short(row.outcome_id))}</td><td>r${row.revision}</td><td>${row.included ? "✓" : "✗"}</td><td>${esc(row.exclusion_reason || "—")}</td><td>${esc(row.review_status || "—")}</td><td>${esc(num(row.original_risk_score))}</td><td>${row.defaulted ? "default" : "—"}</td></tr>`).join("") : "";
    const reasons = detail ? Object.entries(detail.exclusion_summary).map(([reason, count]) => `<li><span>${esc(reason)}</span> <b>${count}</b></li>`).join("") : "";
    container.innerHTML = `${panel("SNAPSHOTS", tr("snapshots"), `<div class="gov-table-wrap"><table id="datasetSnapshotTable" class="gov-table"><thead><tr><th>ID</th><th>${esc(tr("created"))}</th><th>${esc(tr("scope"))}</th><th>${esc(tr("included"))}</th><th>${esc(tr("excluded"))}</th><th>hash</th><th>${esc(tr("usedByModels"))}</th><th>${esc(tr("trainingResult"))}</th></tr></thead><tbody>${rows || `<tr><td colspan="8">${esc(tr("empty"))}</td></tr>`}</tbody></table></div>`, `<span class="task-badge">${state.snapshots.length}</span>`)}
      ${detail ? `<section id="snapshotDetail" class="workflow-panel"><div class="workflow-panel-head"><div><span>${esc(detail.source)}</span><h2>${esc(short(detail.snapshot_id))}</h2><p title="${esc(detail.dataset_hash)}">${esc(detail.scope)} · ${esc(detail.eligibility_policy)} · ${esc(detail.dataset_hash)}</p></div></div>
        <dl class="gov-detail"><div><dt>${esc(tr("included"))}</dt><dd>${detail.included_count}</dd></div><div><dt>${esc(tr("excluded"))}</dt><dd>${detail.excluded_count}</dd></div><div><dt>${esc(tr("usedByModels"))}</dt><dd>${versions(detail)}</dd></div><div><dt>${esc(tr("trainingResult"))}</dt><dd>${runs(detail)}</dd></div><div><dt>${esc(tr("creator"))}</dt><dd>${esc(detail.created_by)}</dd></div></dl>
        <ul class="gov-list">${reasons}</ul>
        <div class="gov-table-wrap"><table id="snapshotItems" class="gov-table"><thead><tr><th>ID</th><th>${esc(tr("revision"))}</th><th>${esc(tr("included"))}</th><th>${esc(tr("reason"))}</th><th>${esc(tr("review"))}</th><th>score</th><th>default</th></tr></thead><tbody>${items}</tbody></table></div></section>` : ""}`;
    container.querySelectorAll("[data-snapshot-id]").forEach((row) => row.addEventListener("click", () => openSnapshot(row.dataset.snapshotId)));
  }

  // --- Risk decision detail -----------------------------------------------------

  async function renderRiskDecision(requestId) {
    const container = document.querySelector("#riskDecisionDetail");
    if (!container || container.dataset.requestId !== requestId) return;
    try {
      const records = await api(`/api/v1/applications/${requestId}/risk-decisions`);
      if (container.dataset.requestId !== requestId) return;
      const latest = records[records.length - 1];
      if (!latest) { container.innerHTML = `<h3>${esc(tr("decisionTitle"))}</h3><p>${esc(tr("decisionNone"))}</p>`; return; }
      container.innerHTML = `<h3>${esc(tr("decisionTitle"))}</h3><dl class="gov-detail">
        <div><dt>${esc(tr("band"))}</dt><dd>${esc(latest.band)} · ${esc(num(latest.raw_score))} → ${esc(num(latest.final_score))}</dd></div>
        <div><dt>${esc(tr("assessedBy"))}</dt><dd>${esc(latest.assessed_by)} (${esc(latest.actor_role)}) · ${esc(latest.recorded_at)}</dd></div>
        <div><dt>${esc(tr("input"))}</dt><dd title="${esc(latest.input_sha256)}">${esc(latest.engine_version)} · ${esc(short(latest.input_sha256))}</dd></div>
        <div><dt>${esc(tr("model"))}</dt><dd>${esc(latest.model_version_label || (latest.calibration_run_id ? short(latest.calibration_run_id) : tr("baseline")))}${latest.calibration_run_id ? "" : latest.model_version_label ? ` (${esc(tr("fallback"))})` : ""}</dd></div>
        <div><dt>${esc(tr("datasetSnapshot"))}</dt><dd id="decisionSnapshot">${latest.dataset_snapshot_id ? `<button class="btn" type="button" data-open-snapshot="${esc(latest.dataset_snapshot_id)}">${esc(short(latest.dataset_snapshot_id))}</button>` : "—"}</dd></div>
        <div><dt>${esc(tr("artifactHash"))}</dt><dd title="${esc(latest.calibration_artifact_sha256)}">${esc(short(latest.calibration_artifact_sha256))}</dd></div>
        <div><dt>${esc(tr("scopeCheck"))}</dt><dd><span class="gov-scope" data-scope-result="${esc(latest.scope_result)}">${esc(latest.scope_result)}</span> · ${esc(latest.request_scope)} · ${esc(latest.scope_reason)}</dd></div>
        ${latest.fallback_code ? `<div><dt>${esc(tr("fallback"))}</dt><dd>${esc(latest.fallback_code)}</dd></div>` : ""}
      </dl>`;
      container.querySelectorAll("[data-open-snapshot]").forEach((button) => button.addEventListener("click", () => showSnapshot(button.dataset.openSnapshot)));
    } catch (error) { container.innerHTML = `<p class="facility-inline-error">${esc(error.message)}</p>`; }
  }

  function showSnapshot(snapshotId) {
    window.switchView("snapshots");
    openSnapshot(snapshotId);
  }

  function applyLanguage() {
    document.querySelectorAll("[data-gov-i18n]").forEach((element) => { element.textContent = tr(element.dataset.govI18n); });
    renderRegistry();
    renderFeedback();
    renderSnapshots();
  }

  function boot() {
    const originalSwitch = window.switchView;
    window.switchView = function (view) {
      originalSwitch(view);
      if (view === "governance") refreshRegistry();
      if (view === "feedback") refreshFeedback();
      if (view === "snapshots") refreshSnapshots();
    };
    const originalLanguage = window.setLanguage;
    window.setLanguage = function (next) { originalLanguage(next); applyLanguage(); };
    document.querySelector("#refreshGovernance")?.addEventListener("click", refreshRegistry);
    document.querySelector("#refreshFeedback")?.addEventListener("click", refreshFeedback);
    document.querySelector("#refreshSnapshots")?.addEventListener("click", refreshSnapshots);
    applyLanguage();
  }

  window.governanceRiskDecision = renderRiskDecision;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
