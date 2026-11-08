"use strict";

(function () {
  const ui = window.OMUI;
  const params = new URLSearchParams(window.location.search);
  const rawTaskId = params.get("task_candidate_id") || "";
  const queryContext = params.get("feedback_context") || "";
  const queryRunId = params.get("run_id") || "";
  const storedBatch = ui.readBatch();
  const storedItem = (storedBatch?.items || []).find(
    (item) => String(item.task_candidate_id) === String(rawTaskId)
  );
  let feedbackContext = queryContext || storedBatch?.feedback_context || "";
  const feedbackRunId = queryRunId || storedBatch?.run_id || "";
  let currentTask = null;

  const elements = {
    body: document.getElementById("task-body"),
    message: document.getElementById("task-message"),
    context: document.getElementById("task-context"),
    actions: document.getElementById("task-actions"),
    feedback: document.getElementById("feedback-actions"),
    feedbackNote: document.getElementById("feedback-note"),
    github: document.getElementById("task-github"),
    refresh: document.getElementById("refresh-task"),
    idLabel: document.getElementById("task-id-label"),
  };

  function showMessage(code, title, body, actions = "") {
    elements.message.hidden = false;
    elements.message.innerHTML = `<p><span class="mono">${ui.escapeHtml(code)}</span> <b>${ui.escapeHtml(title)}</b> ${ui.escapeHtml(body)}</p>${actions ? `<div class="panel-actions">${actions}</div>` : ""}`;
  }

  function hideMessage() {
    elements.message.hidden = true;
  }

  function recommendationBlock(item, batch) {
    if (!item) {
      return `<div class="d-block">
        <h4>本次匹配依据</h4>
        <p class="unavailable">没有找到该任务对应的推荐批次。请从推荐页进入，才能看到轨道、匹配分和推荐理由。</p>
      </div>`;
    }
    const score = Math.round(Number(item.score || 0) * 100);
    return `<div class="d-block">
      <h4>本次匹配依据<span class="source-tag">RecommendationItemV3</span></h4>
      <div class="run-context" style="margin:0 0 12px;box-shadow:none">
        <div class="run-context-main">
          <p>${ui.escapeHtml(ui.trackLabel(item.service_track))} · 匹配分 <b class="mono">${score}</b> · 代码难度 ${item.difficulty?.code ?? "—"} / 环境 ${item.difficulty?.setup ?? "—"}</p>
          <p class="note mono">${ui.escapeHtml(batch?.run_id || "未标注批次")} · ${ui.escapeHtml(batch?.match_version || "")}</p>
        </div>
        <div class="run-context-actions"><a class="btn btn--sm btn--ghost" href="/recommendation-run${batch?.run_id ? `?run_id=${encodeURIComponent(batch.run_id)}` : ""}">推荐说明</a></div>
      </div>
      <div class="ic-skills" style="margin-top:0">
        <div class="skill-row"><span class="skill-key">已覆盖</span><ul class="chips">${(item.matched_skills || []).map((skill) => `<li class="chip chip--cover">${ui.escapeHtml(skill)}</li>`).join("") || "<li class=\"chip\">无明确已覆盖技能</li>"}</ul></div>
        <div class="skill-row"><span class="skill-key">待补齐</span><ul class="chips">${(item.missing_skills || []).map((skill) => `<li class="chip chip--gap">${ui.escapeHtml(skill)}</li>`).join("") || "<li class=\"chip\">无关键缺口</li>"}</ul></div>
      </div>
      <ul class="reason-list">${ui.reasonList(item.reasons || [])}</ul>
    </div>`;
  }

  function detailHTML(task) {
    const availability = ui.availabilityInfo(task);
    const guidance = task.guidance || {};
    const requirements = task.skill_requirements || [];
    const paths = guidance.impacted_paths || [];
    const steps = guidance.steps || [];
    const labels = task.labels || [];
    const types = task.task_types || [];
    const requirementsHtml = requirements.length
      ? `<ul class="d-list">${requirements.map((item) => `<li><span class="mono">L${item.minimum_level}</span><span>${ui.escapeHtml(item.skill_name)} · 重要度 ${Number(item.importance || 0).toFixed(2)}</span></li>`).join("")}</ul>`
      : `<p class="unavailable">本地快照没有该任务的技能要求；请在上游 Issue 中确认。</p>`;
    const pathsHtml = paths.length
      ? `<ul class="chips">${paths.map((path) => `<li class="chip mono">${ui.escapeHtml(path)}</li>`).join("")}</ul>`
      : `<p class="unavailable">没有从 Issue 正文中识别到明确路径。请阅读正文或上游讨论确认改动位置。</p>`;
    const labelsHtml = labels.length
      ? `<ul class="chips">${labels.map((label) => `<li class="chip">${ui.escapeHtml(label)}</li>`).join("")}</ul>`
      : `<p class="unavailable">上游没有提供标签。</p>`;
    const guideHtml = guidance.contributing_url
      ? `<p><a class="link-ext" href="${ui.escapeHtml(ui.safeGithubUrl(guidance.contributing_url))}" target="_blank" rel="noopener noreferrer">查看仓库 CONTRIBUTING 指南</a></p>`
      : `<p class="unavailable">仓库未标记 CONTRIBUTING；请到 GitHub 仓库主页确认贡献入口。</p>`;
    return `<div class="detail-shell">
      <header class="detail-head">
        <span class="repo-mark" aria-hidden="true" style="width:36px;height:36px;font-size:12px">${ui.escapeHtml(String(task.repository_full_name || "?").split("/").pop().slice(0, 2).toUpperCase())}</span>
        <div class="dh-copy">
          <div class="dh-sub">
            <span class="full">${ui.escapeHtml(task.repository_full_name)}#${task.issue_number}</span>
            <span class="dot-sep" aria-hidden="true"></span>
            <span class="pill ${availability.className}" title="${ui.escapeHtml(availability.title)}">${ui.escapeHtml(availability.text)}</span>
            ${task.primary_language ? `<span class="dot-sep" aria-hidden="true"></span><span class="mono">${ui.escapeHtml(task.primary_language)}</span>` : ""}
          </div>
          <h3>${ui.escapeHtml(task.title)}</h3>
        </div>
        <a class="btn btn--sm btn--ghost" href="${ui.escapeHtml(ui.safeGithubUrl(task.html_url))}" target="_blank" rel="noopener noreferrer">GitHub 原文</a>
      </header>
      <div class="detail-body task-guidance">
        <div class="d-block">
          <h4>任务目标<span class="source-tag">${ui.escapeHtml(guidance.goal_source || "unavailable")}</span></h4>
          ${guidance.goal ? `<p>${ui.escapeHtml(guidance.goal)}</p>` : `<p class="unavailable">上游没有提供可读正文，请打开 GitHub 原文查看。</p>`}
        </div>
        <div class="d-cols">
          <div class="d-block"><h4>技能要求</h4>${requirementsHtml}</div>
          <div class="d-block"><h4>关联代码 / 模块<span class="source-tag">${ui.escapeHtml(guidance.impacted_paths_source || "unavailable")}</span></h4>${pathsHtml}</div>
        </div>
        <div class="d-block">
          <h4>开始步骤<span class="source-tag">${ui.escapeHtml(guidance.steps_source || "generic_guidance")}</span></h4>
          <ol class="steps">${steps.map((step) => `<li><span>${ui.escapeHtml(step)}</span></li>`).join("")}</ol>
        </div>
        <div class="d-block"><h4>贡献指南</h4>${guideHtml}</div>
        <div class="d-cols">
          <div class="d-block">
            <h4>当前可用性</h4>
            <p><span class="pill ${availability.className}">${ui.escapeHtml(availability.text)}</span></p>
            <p class="note">最后复核：<span class="mono">${ui.escapeHtml(task.verified_at || "尚无记录")}</span></p>
            <p class="note">上游最近活动：<span class="mono">${ui.escapeHtml(task.last_activity_at || "未记录")}</span></p>
            <p class="note">讨论 <span class="mono">${task.comment_count || 0}</span> 条</p>
            ${(task.availability_reasons || []).length ? `<ul class="d-list">${task.availability_reasons.map((reason) => `<li><span class="mono">!</span><span>${ui.escapeHtml(reason)}</span></li>`).join("")}</ul>` : ""}
          </div>
          <div class="d-block"><h4>上游标签</h4>${labelsHtml}${types.length ? `<p class="note" style="margin-top:9px">任务类型：${types.map((type) => ui.escapeHtml(type)).join(" · ")}</p>` : ""}</div>
        </div>
        ${recommendationBlock(storedItem, storedBatch)}
        <div class="d-block"><h4>上游 Issue 描述</h4><div class="task-body-text">${ui.escapeHtml(task.body_text || "该 Issue 没有正文，请到 GitHub 查看完整讨论与验收要求。")}</div></div>
      </div>
      <footer class="detail-foot">
        <p class="note">任务目标来自上游正文摘要；关联路径由正文启发式识别；开始步骤是页面提供的通用建议，不是上游结构化字段。</p>
      </footer>
    </div>`;
  }

  async function loadTask(refresh = false) {
    elements.refresh.disabled = true;
    elements.refresh.textContent = refresh ? "正在复核…" : "重新复核可用性";
    hideMessage();
    if (refresh) elements.body.setAttribute("aria-busy", "true");
    try {
      const suffix = refresh ? "?refresh=true" : "";
      const task = await ui.requestJson(`/api/v1/tasks/${rawTaskId}${suffix}`);
      currentTask = task;
      elements.body.innerHTML = detailHTML(task);
      elements.actions.hidden = false;
      elements.idLabel.textContent = String(task.task_candidate_id);
      const githubUrl = ui.safeGithubUrl(task.html_url);
      elements.github.href = githubUrl;
      elements.github.hidden = githubUrl === "#";
      if (refresh) showMessage("refresh: ok", "已完成复核", "可用性与上游字段已刷新；提交前仍请以 GitHub 原文为准。");
      updateFeedbackAvailability(task);
    } catch (error) {
      elements.body.innerHTML = ui.panel(
        error.code || `http: ${error.status}`,
        error.status === 404 ? "任务不存在或已失效" : "任务详情暂时不可用",
        error.status === 404
          ? "这个 task_candidate_id 不在当前候选池中。请从推荐页重新选择任务。"
          : error.message,
        `<a class="btn btn--sm btn--ghost" href="/recommendations">返回推荐</a><a class="link-ext" href="/status">查看系统状态</a>`
      );
      elements.actions.hidden = true;
      if (error.status === 503) showMessage("503 service_not_ready", "任务复核未启用", "服务端没有启用单任务 GitHub 复核；本地快照仍可阅读。", `<a class="link-ext" href="/status">查看系统状态</a>`);
      if (error.status === 429) showMessage("429 rate_limited", "GitHub 限流", "短时间内复核请求过多，请稍后再试。", `<a class="link-ext" href="/status">查看系统状态</a>`);
      if (error.status === 502) showMessage("502 upstream_error", "GitHub 上游失败", "上游返回了不可恢复的错误，请稍后再试。", `<a class="link-ext" href="/status">查看系统状态</a>`);
    } finally {
      elements.refresh.disabled = false;
      elements.refresh.textContent = "重新复核可用性";
      elements.body.setAttribute("aria-busy", "false");
    }
  }

  function updateFeedbackAvailability(task) {
    const buttons = elements.feedback.querySelectorAll("[data-feedback]");
    if (!feedbackContext) {
      buttons.forEach((button) => { button.disabled = true; });
      elements.feedbackNote.textContent = "从推荐页进入后可记录反馈；当前链接缺少画像上下文。";
      return;
    }
    const currentState = storedItem?.feedback_state || "";
    buttons.forEach((button) => {
      button.disabled = false;
      button.setAttribute("aria-pressed", String(button.dataset.feedback === currentState));
    });
    elements.feedbackNote.textContent = currentState ? `当前记录：${currentState}` : "反馈只作用于当前画像。";
  }

  elements.feedback.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-feedback]");
    if (!button) return;
    const buttons = elements.feedback.querySelectorAll("[data-feedback]");
    buttons.forEach((item) => { item.disabled = true; });
    elements.feedbackNote.textContent = "正在保存…";
    try {
      const feedbackBody = {
        task_candidate_id: Number(rawTaskId),
        feedback_context: feedbackContext,
        feedback_state: button.dataset.feedback,
      };
      if (feedbackRunId) feedbackBody.run_id = feedbackRunId;
      await ui.requestJson("/api/v1/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(feedbackBody),
      });
      buttons.forEach((item) => {
        item.disabled = false;
        item.setAttribute("aria-pressed", String(item === button));
      });
      elements.feedbackNote.textContent = "反馈已保存，后续推荐会参考它。";
    } catch (error) {
      buttons.forEach((item) => { item.disabled = false; });
      elements.feedbackNote.textContent = `保存失败：${error.message}`;
    }
  });

  elements.refresh.addEventListener("click", () => loadTask(true));

  if (!/^[1-9][0-9]{0,18}$/.test(rawTaskId)) {
    elements.body.innerHTML = ui.panel(
      "400 invalid_task_id",
      "链接缺少任务编号",
      "请从推荐页打开任务，或补上 task_candidate_id 参数。",
      `<a class="btn btn--sm btn--ghost" href="/recommendations">返回推荐</a>`
    );
    elements.actions.hidden = true;
  } else {
    if (storedBatch) {
      elements.context.hidden = false;
      elements.context.outerHTML = ui.runContext(storedBatch);
    }
    loadTask(false);
  }
})();
