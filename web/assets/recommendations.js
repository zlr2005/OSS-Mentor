"use strict";

(function () {
  const ui = window.OMUI;
  const page = document.body.dataset.page || "recommendations";
  const elements = {
    list: document.getElementById("issue-list"),
    message: document.getElementById("recommendation-message"),
    runContext: document.getElementById("run-context"),
    toolbar: document.getElementById("home-toolbar") || document.getElementById("list-toolbar"),
    resultNote: document.getElementById("home-result-note") || document.getElementById("list-note"),
    resultCount: document.getElementById("result-count"),
    publicSection: document.getElementById("public-candidates"),
    publicGrid: document.getElementById("public-grid"),
    projectSection: document.getElementById("project-opportunities"),
    projectGrid: document.getElementById("proj-grid"),
    projectNote: document.getElementById("project-note"),
    sessionStatus: document.getElementById("session-status"),
    sessionPill: document.getElementById("session-pill"),
    sessionActions: document.getElementById("session-actions"),
    sessionCard: document.getElementById("session-card"),
    primaryAction: document.getElementById("primary-action"),
    toggleReasons: document.getElementById("toggle-reasons"),
    search: document.getElementById("recommendation-search"),
  };
  const state = {
    session: null,
    batch: null,
    items: [],
    track: "all",
    query: "",
    preview: new URLSearchParams(window.location.search).get("state"),
  };

  function currentReturnTo() {
    const returnTo = `${window.location.pathname}${window.location.search}`;
    return returnTo.startsWith("/") && !returnTo.startsWith("//") ? returnTo : "/";
  }

  function loginUrl(returnTo = currentReturnTo()) {
    return `/login?return_to=${encodeURIComponent(returnTo)}`;
  }

  function setSessionActions({ authenticated, available }) {
    if (!elements.sessionActions) return;
    if (!available) {
      elements.sessionActions.innerHTML =
        `<a class="session-action session-action--primary" href="${ui.escapeHtml(loginUrl())}">登录</a>`;
      return;
    }
    if (!authenticated) {
      elements.sessionActions.innerHTML =
        `<a class="session-action session-action--primary" href="${ui.escapeHtml(loginUrl())}">登录</a>`;
      return;
    }
    elements.sessionActions.innerHTML = `
      <button class="session-action" type="button" data-session-action="relogin" title="退出当前会话后重新进行 GitHub 授权">重新登录</button>
      <button class="session-action session-action--danger" type="button" data-session-action="logout" title="结束当前本地登录会话">退出登录</button>`;
  }

  function setSessionError(message) {
    const small = elements.sessionStatus?.querySelector(".who small");
    if (!small) return;
    small.textContent = message;
    small.classList.add("session-error");
  }

  function setSessionChip({ authenticated, user, available }) {
    if (!elements.sessionStatus) return;
    const avatar = elements.sessionStatus.querySelector(".avatar-mono");
    const who = elements.sessionStatus.querySelector(".who");
    setSessionActions({ authenticated, available });
    if (!available) {
      if (avatar) avatar.textContent = "!";
      if (who) who.innerHTML = "登录服务未配置<small>当前仅能浏览公共候选</small>";
      if (elements.sessionPill) {
        elements.sessionPill.textContent = "demo";
        elements.sessionPill.className = "pill pill--warn";
      }
      return;
    }
    if (authenticated) {
      const name = user?.display_name || user?.github_login || "已登录用户";
      if (avatar) avatar.textContent = String(name).slice(0, 1).toUpperCase();
      if (who) who.innerHTML = `${ui.escapeHtml(name)}<small>会话已建立</small>`;
      if (elements.sessionPill) {
        elements.sessionPill.textContent = "已登录";
        elements.sessionPill.className = "pill pill--ok";
      }
      return;
    }
    if (avatar) avatar.textContent = "访";
    if (who) who.innerHTML = "未登录<small>可浏览公共候选</small>";
    if (elements.sessionPill) {
      elements.sessionPill.textContent = "public";
      elements.sessionPill.className = "pill pill--neutral";
    }
  }

  elements.sessionActions?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-session-action]");
    if (!button) return;
    const action = button.dataset.sessionAction;
    if (action !== "logout" && action !== "relogin") return;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = action === "relogin" ? "正在重新登录…" : "正在退出…";
    elements.sessionActions.setAttribute("aria-busy", "true");
    try {
      await ui.requestJson("/api/v1/auth/logout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      window.location.assign(loginUrl());
    } catch (error) {
      button.disabled = false;
      button.textContent = original;
      setSessionError(`操作失败：${error.message}`);
    } finally {
      elements.sessionActions.removeAttribute("aria-busy");
    }
  });

  function renderSessionCard(batch) {
    if (!elements.sessionCard) return;
    const profile = batch?.profile || {};
    if (!batch) {
      elements.sessionCard.innerHTML = `
        <div class="pc-head">
          <span class="avatar-mono" aria-hidden="true">?</span>
          <span class="who"><b>还没有可用画像</b><small>建立画像后才能生成个性化推荐</small></span>
          <span class="pill pill--warn">未完成</span>
        </div>
        <div class="pc-grid">
          <div><dt>推荐轨道</dt><dd>—</dd></div>
          <div><dt>画像版本</dt><dd class="mono">—</dd></div>
          <div><dt>本次推荐</dt><dd>—</dd></div>
        <div><dt>本次批次</dt><dd>—</dd></div>
        </div>
        <p class="model-line">完成个人画像后，这里会显示推荐数量、画像版本与推荐说明入口。</p>`;
      return;
    }
    const count = batch.count ?? (batch.items || []).length;
    elements.sessionCard.innerHTML = `
      <div class="pc-head">
        <span class="avatar-mono" aria-hidden="true">${ui.escapeHtml(String(profile.display_name || profile.profile_key || "U").slice(0, 1))}</span>
        <span class="who"><b>${ui.escapeHtml(profile.display_name || profile.profile_key || "当前画像")}</b><small>${ui.escapeHtml(profile.profile_source || "profile")} · ${ui.escapeHtml(profile.profile_version || "版本未标注")}</small></span>
        <span class="pill ${profile.service_track === "growth" ? "pill--adv" : "pill--new"}">${ui.escapeHtml(ui.trackLabel(profile.service_track || batch.service_track))}</span>
      </div>
      <div class="pc-grid">
        <div><dt>推荐轨道</dt><dd>${ui.escapeHtml(ui.trackLabel(profile.service_track || batch.service_track))}</dd></div>
        <div><dt>画像版本</dt><dd class="mono">${ui.escapeHtml(profile.profile_version || "—")}</dd></div>
        <div><dt>本次推荐</dt><dd class="mono">${count} 条</dd></div>
        <div><dt>本次批次</dt><dd class="mono">${ui.escapeHtml(batch.run_id ? batch.run_id.slice(0, 8) : "—")}</dd></div>
      </div>
      <p class="model-line">模型与数据版本见 <a href="/recommendation-run${batch.run_id ? `?run_id=${encodeURIComponent(batch.run_id)}` : ""}">推荐说明</a>。</p>`;
  }

  function showMessage(code, title, body, actions = "") {
    if (!elements.message) return;
    elements.message.hidden = false;
    elements.message.innerHTML = `<p><span class="mono">${ui.escapeHtml(code)}</span> <b>${ui.escapeHtml(title)}</b> ${ui.escapeHtml(body)}</p>${actions ? `<div class="panel-actions">${actions}</div>` : ""}`;
  }

  function hideMessage() {
    if (elements.message) elements.message.hidden = true;
  }

  function renderPreviewPanel(code, title, body, actions) {
    elements.list.innerHTML = ui.panel(code, title, body, actions);
    elements.resultNote.textContent = title;
    if (elements.resultCount) elements.resultCount.textContent = "0";
    if (elements.toolbar) elements.toolbar.hidden = true;
  }

  function counts() {
    return {
      all: state.items.length,
      newcomer: state.items.filter((item) => item.service_track === "newcomer").length,
      growth: state.items.filter((item) => item.service_track === "growth").length,
    };
  }

  function updateCounts() {
    const current = counts();
    for (const [key, value] of Object.entries(current)) {
      const target = document.getElementById(`track-count-${key}`);
      if (target) target.textContent = String(value);
    }
  }

  function filteredItems() {
    let items = state.items;
    if (state.track !== "all") {
      items = items.filter((item) => item.service_track === state.track);
    }
    if (state.query) {
      const needle = state.query.toLowerCase();
      items = items.filter((item) =>
        `${item.title} ${item.repository_full_name}`.toLowerCase().includes(needle)
      );
    }
    return items;
  }

  function renderList() {
    if (!state.items.length) {
      renderPreviewPanel(
        "ok: 0 results",
        "没有符合条件的推荐结果",
        "当前画像与仍可领取的 Issue 没有交集。可以放宽语言、任务类型或难度上限，或先浏览公共候选。",
        `<a class="btn btn--sm btn--ghost" href="/profile">调整个人画像</a><a class="link-ext" href="#public-candidates">浏览公共候选</a>`
      );
      return;
    }
    let items = filteredItems();
    if (page === "home") items = items.slice(0, 6);
    if (!items.length) {
      renderPreviewPanel(
        "filter: 0 results",
        "当前筛选没有结果",
        "换一个轨道或清空搜索词即可恢复。",
        `<button class="btn btn--sm btn--ghost" type="button" id="reset-filter">恢复全部筛选</button>`
      );
      document.getElementById("reset-filter")?.addEventListener("click", () => {
        state.track = "all";
        state.query = "";
        if (elements.search) elements.search.value = "";
        document.querySelectorAll(".seg-btn").forEach((button) => {
          button.setAttribute("aria-pressed", String(button.dataset.track === "all"));
        });
        renderList();
      });
      return;
    }
    const context = state.batch?.feedback_context || "";
    const model = state.batch?.model || null;
    elements.list.innerHTML = items.map((item, index) => {
      const globalIndex = state.items.indexOf(item) + 1;
      return ui.issueCard(item, globalIndex, { feedbackContext: context, model });
    }).join("");
    if (elements.toggleReasons?.getAttribute("aria-pressed") === "true") {
      elements.list.querySelectorAll("details").forEach((details) => { details.open = true; });
    }
    const suffix = page === "home" && state.items.length > 6 ? " · 首页展示前 6 条" : "";
    elements.resultNote.textContent = `${state.items.length} 条可领取任务${suffix}`;
    if (elements.resultCount) elements.resultCount.textContent = String(state.items.length);
    updateCounts();
  }

  async function loadPublicCandidates(limit = 6) {
    if (!elements.publicSection || !elements.publicGrid) return;
    try {
      const data = await ui.requestJson(`/api/v1/public-candidates?limit=${limit}`);
      elements.publicSection.hidden = false;
      elements.publicGrid.innerHTML = (data.items || []).map(ui.publicCard).join("") ||
        ui.panel("public: 0", "暂无公共候选", "候选池当前没有可展示的公开任务。", `<a class="link-ext" href="/status">查看系统状态</a>`);
    } catch (error) {
      elements.publicSection.hidden = false;
      elements.publicGrid.innerHTML = ui.panel(
        error.code || `http: ${error.status}`,
        "公共候选暂不可用",
        error.message,
        `<a class="link-ext" href="/status">查看系统状态</a>`
      );
    }
  }

  async function loadProjects(runId) {
    if (!elements.projectSection || !elements.projectGrid || !runId) return;
    try {
      const data = await ui.requestJson(
        `/api/v1/me/project-recommendations?run_id=${encodeURIComponent(runId)}`
      );
      if (!(data.items || []).length) {
        elements.projectSection.hidden = true;
        return;
      }
      elements.projectSection.hidden = false;
      elements.projectNote.textContent = `项目排序来自本次 Issue 推荐的聚合（${data.ranking_source}）。项目模型尚未接入，因此不把它标成 CODER 项目推荐。`;
      elements.projectGrid.innerHTML = data.items.map(ui.projectCard).join("");
    } catch (error) {
      elements.projectSection.hidden = false;
      elements.projectGrid.innerHTML = ui.panel(
        error.code || `http: ${error.status}`,
        "项目推荐暂不可用",
        error.message,
        `<a class="link-ext" href="/status">查看系统状态</a>`
      );
    }
  }

  function renderAnonymous(session) {
    setSessionChip({ authenticated: false, user: null, available: session?.available !== false });
    renderSessionCard(null);
    if (elements.toolbar) elements.toolbar.hidden = true;
    if (elements.runContext) elements.runContext.innerHTML = "";
    elements.list.innerHTML = ui.panel(
      "401 authentication_required",
      "登录后生成个性化推荐",
      "未登录时不会读取画像，因此这里没有匹配分、轨道判定或推荐依据。你仍然可以浏览下方明确标记的公共候选。",
      `<a class="btn btn--sm btn--ghost" href="/login?return_to=%2Frecommendations">登录 GitHub</a><a class="link-ext" href="#public-candidates">浏览公共候选</a>`
    );
    elements.resultNote.textContent = "未登录 · 仅公共候选";
    if (elements.primaryAction) {
      elements.primaryAction.textContent = "登录并建立画像";
      elements.primaryAction.href = "/login?return_to=%2Fprofile";
    }
    loadPublicCandidates();
  }

  function renderNoProfile(session) {
    setSessionChip({ authenticated: true, user: session.user, available: true });
    renderSessionCard(null);
    if (elements.toolbar) elements.toolbar.hidden = true;
    if (elements.runContext) elements.runContext.innerHTML = "";
    elements.list.innerHTML = ui.panel(
      "profile: null",
      "画像未完成或不存在",
      "已登录，但还没有可用于推荐的完整画像。先完成技能自评、偏好和难度设置，再回来查看个性化推荐。",
      `<a class="btn btn--sm btn--ghost" href="/profile">建立个人画像</a><a class="link-ext" href="#public-candidates">浏览公共候选</a>`
    );
    elements.resultNote.textContent = "未完成画像 · 仅公共候选";
    if (elements.primaryAction) {
      elements.primaryAction.textContent = "建立个人画像";
      elements.primaryAction.href = "/profile";
    }
    loadPublicCandidates();
  }

  async function loadAuthenticated(session) {
    setSessionChip({ authenticated: true, user: session.user, available: true });
    try {
      const batch = await ui.requestJson("/api/v1/me/recommendations?limit=10");
      state.batch = batch;
      state.items = batch.items || [];
      ui.storeBatch(batch);
      renderSessionCard(batch);
      if (elements.runContext) elements.runContext.innerHTML = ui.runContext(batch);
      if (elements.toolbar) elements.toolbar.hidden = false;
      if (elements.primaryAction) {
        elements.primaryAction.textContent = "调整个人画像";
        elements.primaryAction.href = "/profile";
      }
      hideMessage();
      if (state.preview === "stale") {
        showMessage(
          "dataset: stale",
          "数据版本可能过期",
          "推荐来自本地候选快照。提交前请打开 GitHub 原文确认 Issue 是否仍可领取。",
          `<a class="btn btn--sm btn--ghost" href="/status">查看系统状态</a>`
        );
      }
      updateCounts();
      renderList();
      loadProjects(batch.run_id);
    } catch (error) {
      if (error.status === 401) {
        renderAnonymous(session);
        return;
      }
      if (error.status === 404 && error.code === "profile_required") {
        renderNoProfile(session);
        return;
      }
      renderSessionCard(null);
      if (elements.toolbar) elements.toolbar.hidden = true;
      elements.list.innerHTML = ui.panel(
        error.code || `http: ${error.status}`,
        "推荐暂时不可用",
        error.message,
        `<a class="btn btn--sm btn--ghost" href="/recommendations">重新加载</a><a class="link-ext" href="/status">查看系统状态</a>`
      );
      elements.resultNote.textContent = "推荐服务异常";
      loadPublicCandidates();
    }
  }

  function renderAuthUnavailable(session) {
    setSessionChip({ authenticated: false, user: null, available: false });
    renderSessionCard(null);
    if (elements.toolbar) elements.toolbar.hidden = true;
    elements.list.innerHTML = ui.panel(
      "503 service_not_ready",
      "登录或数据库服务尚未就绪",
      `当前无法读取个人画像，只能浏览公共候选。${session?.error?.message ? `服务返回：${session.error.message}` : "请检查 OAuth、数据库初始化和服务状态。"}`,
      `<a class="link-ext" href="#public-candidates">浏览公共候选</a><a class="link-ext" href="/status">查看系统状态</a>`
    );
    elements.resultNote.textContent = "登录服务未配置 · 仅公共候选";
    if (elements.primaryAction) {
      elements.primaryAction.textContent = "浏览公共候选";
      elements.primaryAction.href = "#public-candidates";
    }
    loadPublicCandidates();
  }

  function renderStaticPreview() {
    const preview = state.preview;
    if (preview === "loading") {
      elements.list.innerHTML = ui.skeleton(3);
      elements.resultNote.textContent = "加载中";
      if (elements.toolbar) elements.toolbar.hidden = true;
      return true;
    }
    if (preview === "empty") {
      renderPreviewPanel(
        "ok: 0 results",
        "没有符合条件的推荐结果",
        "当前画像与仍可领取的 Issue 没有交集。这是空结果，不是错误，也不会影响公共候选的浏览。",
        `<a class="btn btn--sm btn--ghost" href="/profile">调整个人画像</a><a class="link-ext" href="#public-candidates">浏览公共候选</a>`
      );
      return true;
    }
    if (preview === "no-profile") {
      renderPreviewPanel(
        "profile: null",
        "画像未完成",
        "推荐需要技能、环境和成长目标作为输入。先完成个人画像，再看个性化推荐。",
        `<a class="btn btn--sm btn--ghost" href="/profile">建立个人画像</a>`
      );
      return true;
    }
    if (preview === "stale-preview") {
      renderPreviewPanel(
        "409 preview_stale",
        "画像预览已过期",
        "确认预览时画像或问卷版本已经变化。请重新生成预览，避免把旧回答写入新画像。",
        `<a class="btn btn--sm btn--ghost" href="/profile">重新生成预览</a><a class="link-ext" href="/status">查看系统状态</a>`
      );
      return true;
    }
    if (preview === "model-503") {
      renderPreviewPanel(
        "503 model unavailable",
        "模型服务暂时不可用",
        "服务不会自动用规则结果冒充 CODER 推荐。请稍后重试，或在系统状态页查看模型与数据版本。",
        `<a class="btn btn--sm btn--ghost" href="/status">查看系统状态</a><a class="link-ext" href="/recommendations">重新加载</a>`
      );
      return true;
    }
    if (preview === "upstream") {
      renderPreviewPanel(
        "upstream: error",
        "上游 GitHub 暂时不可用",
        "当前无法核验 Issue 的最新可用性，因此不返回未经核验的推荐。",
        `<a class="btn btn--sm btn--ghost" href="/status">查看系统状态</a><a class="link-ext" href="/recommendations">稍后重试</a>`
      );
      return true;
    }
    if (preview === "version-mismatch") {
      renderPreviewPanel(
        "409 model_version_mismatch",
        "模型版本不匹配",
        "模型权重、配置或输入模式与登记版本不一致。服务不会静默改用规则结果；请由 D 重新登记模型或回退到已登记版本。",
        `<a class="btn btn--sm btn--ghost" href="/status">查看模型状态</a><a class="link-ext" href="/recommendations">稍后重试</a>`
      );
      return true;
    }
    if (preview === "migration-failure") {
      renderPreviewPanel(
        "503 database_migration_failed",
        "数据库迁移失败",
        "数据库 schema 版本与服务不匹配，推荐不会返回半成品结果。请检查迁移日志并恢复数据库。",
        `<a class="btn btn--sm btn--ghost" href="/status">查看系统状态</a>`
      );
      return true;
    }
    return false;
  }

  function bindToolbar() {
    document.querySelectorAll(".seg-btn").forEach((button) => {
      button.addEventListener("click", () => {
        state.track = button.dataset.track || "all";
        document.querySelectorAll(".seg-btn").forEach((item) => {
          item.setAttribute("aria-pressed", String(item === button));
        });
        renderList();
      });
    });
    elements.toggleReasons?.addEventListener("click", () => {
      const open = elements.toggleReasons.getAttribute("aria-pressed") !== "true";
      elements.toggleReasons.setAttribute("aria-pressed", String(open));
      elements.toggleReasons.textContent = open ? "收起全部推荐依据" : "展开全部推荐依据";
      elements.list.querySelectorAll("details").forEach((details) => { details.open = open; });
    });
    elements.search?.addEventListener("input", () => {
      state.query = elements.search.value.trim();
      renderList();
    });
  }

  async function init() {
    bindToolbar();
    if (renderStaticPreview()) {
      if (state.preview === "empty" || state.preview === "no-profile") {
        loadPublicCandidates();
      }
      return;
    }
    if (state.preview === "unauth") {
      renderAnonymous({ available: true, authenticated: false, user: null });
      return;
    }
    try {
      const session = await ui.getSession();
      state.session = session;
      if (!session.available) {
        renderAuthUnavailable(session);
        return;
      }
      if (!session.authenticated) {
        renderAnonymous(session);
        return;
      }
      await loadAuthenticated(session);
    } catch (error) {
      renderAuthUnavailable({ available: false, authenticated: false, user: null, error });
    }
  }

  init();
})();
