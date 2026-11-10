"use strict";

/* Shared presentation helpers for the midterm pages.
 *
 * The API contract is RecommendationItemV3 for personalized lists and the
 * PublicCandidateItem shape for anonymous lists.  This module only formats
 * server values; it does not compute match scores, tracks or reasons.
 */
(function () {
  const BATCH_STORAGE_KEY = "oss-mentor.last-batch.v1";
  const TRACK_LABELS = { newcomer: "新手轨道", growth: "进阶轨道" };
  const TRACK_CLASSES = { newcomer: "pill--new", growth: "pill--adv" };
  const REASON_LABELS = {
    language_match: "语言匹配",
    task_type_match: "任务类型匹配",
    skill_match: "技能覆盖",
    skill_stretch: "成长跨度",
    newcomer_signal: "新人友好",
    active_repository: "仓库活跃",
    fresh_issue: "任务新鲜度",
    contributing_guide_available: "有贡献指南",
    issue_clarity: "Issue 清晰度",
    growth_value: "成长价值",
    negative_feedback_penalty: "负反馈惩罚",
    diversity_rerank: "多样性重排",
  };
  const AVAILABILITY_LABELS = {
    available: ["仍可领取", "pill--ok"],
    closed: ["已关闭", "pill--danger"],
    assigned: ["已分配", "pill--danger"],
    linked_open_pr: ["已有开放 PR", "pill--warn"],
    locked: ["已锁定", "pill--warn"],
    repository_inactive: ["仓库不活跃", "pill--danger"],
    temporarily_unverified: ["待复核", "pill--warn"],
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#039;",
    })[character]);
  }

  function safeGithubUrl(value) {
    try {
      const url = new URL(String(value || ""), window.location.origin);
      return url.protocol === "https:" && url.hostname === "github.com"
        ? url.href
        : "#";
    } catch {
      return "#";
    }
  }

  function repoMark(repository) {
    const name = String(repository || "").split("/").pop() || "?";
    return escapeHtml(name.slice(0, 2).toUpperCase());
  }

  function trackLabel(track) {
    return TRACK_LABELS[track] || "未标注轨道";
  }

  function trackPill(track) {
    return `<span class="pill ${TRACK_CLASSES[track] || "pill--neutral"}">${escapeHtml(trackLabel(track))}</span>`;
  }

  function relativeDate(value) {
    if (!value) return "未记录";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(date);
  }

  function isStale(verifiedAt) {
    if (!verifiedAt) return true;
    const verified = new Date(verifiedAt);
    if (Number.isNaN(verified.getTime())) return true;
    return Date.now() - verified.getTime() > 24 * 60 * 60 * 1000;
  }

  function availabilityInfo(item) {
    const [baseText, baseClass] = AVAILABILITY_LABELS[item.availability] || [
      "状态未知",
      "pill--neutral",
    ];
    if (item.availability !== "available") {
      return { text: baseText, className: baseClass, title: "以上游 Issue 当前状态为准" };
    }
    if (isStale(item.verified_at)) {
      return {
        text: "仍可领取 · 待复核",
        className: "pill--warn",
        title: item.verified_at
          ? `上次 GitHub 复核：${item.verified_at}；提交前请以 GitHub 原文为准`
          : "尚无 GitHub 复核记录；提交前请以 GitHub 原文为准",
      };
    }
    return {
      text: "仍可领取",
      className: "pill--ok",
      title: `最近复核：${item.verified_at}`,
    };
  }

  function reasonList(reasons) {
    return (reasons || []).map((reason) => {
      const points = Math.round(Number(reason.score_delta || 0) * 100);
      const label = reason.label || REASON_LABELS[reason.code] || reason.code || "推荐依据";
      const pointsLabel = points === 0 ? "匹配依据" : `${points >= 0 ? "+" : ""}${points} 点`;
      return `<li class="reason">
        <div class="r-head">
          <span class="r-label">${escapeHtml(label)}</span>
          <span class="r-ver">${escapeHtml(reason.feature_version || "")}</span>
        </div>
        <p class="r-text">${escapeHtml(reason.evidence || "无额外证据")}</p>
        <code class="r-ev">${pointsLabel}</code>
      </li>`;
    }).join("");
  }

  function skillRows(item) {
    const covered = (item.matched_skills || []).length
      ? item.matched_skills.map((skill) => `<li class="chip chip--cover">${escapeHtml(skill)}</li>`).join("")
      : "<li class=\"chip\">无明确已覆盖技能</li>";
    const gaps = (item.missing_skills || []).length
      ? item.missing_skills.map((skill) => `<li class="chip chip--gap">${escapeHtml(skill)}</li>`).join("")
      : "<li class=\"chip\">无关键缺口</li>";
    return `<div class="skill-row"><span class="skill-key">已覆盖</span><ul class="chips">${covered}</ul></div>
      <div class="skill-row"><span class="skill-key">待补齐</span><ul class="chips">${gaps}</ul></div>`;
  }

  function modelLabel(model) {
    if (!model) return "model unavailable";
    if (model.variant === "rule_legacy") {
      return "rule_legacy · 规则兼容模式";
    }
    return `${model.variant} · ${model.version}`;
  }

  function detailUrl(item, feedbackContext, runId) {
    const query = new URLSearchParams({ task_candidate_id: String(item.task_candidate_id) });
    if (feedbackContext) query.set("feedback_context", feedbackContext);
    if (runId) query.set("run_id", runId);
    return `/task?${query.toString()}`;
  }

  function warningList(warnings) {
    const labels = {
      rule_reasons_are_context_only: "匹配依据不是模型分数的加分明细。",
      gated_scores_are_not_success_probabilities: "排序分不代表完成任务的概率。",
      inventory_availability_assumed: "本次试用未重新核验任务状态。",
      profile_bootstrap_recommendations: "当前根据填写的画像推荐，个性化模型正在准备。",
      model_features_preparing: "系统正在自动准备推荐资料。",
      coder_no_public_code_history: "暂未取得可用公开代码历史，使用新用户推荐模式。",
    };
    return (warnings || []).map(value =>
      `<p class="r-text">${escapeHtml(labels[value] || value)}</p>`
    ).join("");
  }

  function issueCard(item, index, options) {
    const availability = availabilityInfo(item);
    const feedbackContext = options.feedbackContext || "";
    const runId = options.runId || "";
    const feedbackState = item.feedback_state || "";
    const reasons = item.reasons || [];
    const first = index === 1;
    const score = Math.max(0, Math.min(100, Math.round(Number(item.score || 0) * 100)));
    const feedbackActions = feedbackContext
      ? `<button class="btn btn--sm btn--ghost" type="button" data-feedback="interested" ${feedbackState ? "disabled" : ""}>${feedbackState === "interested" ? "已记录感兴趣" : "感兴趣"}</button>
         <button class="btn btn--sm btn--ghost" type="button" data-feedback="not_suitable" ${feedbackState ? "disabled" : ""}>${feedbackState === "not_suitable" ? "已记录不适合" : "不适合我"}</button>
         <span class="feedback-status" role="status"></span>`
      : "";
    return `<article class="issue ${first ? "is-top" : ""}" data-candidate-id="${item.task_candidate_id}" data-track="${escapeHtml(item.service_track || "")}" data-feedback-context="${escapeHtml(feedbackContext)}" data-run-id="${escapeHtml(runId)}">
      <div class="ic-body">
        <header class="ic-top">
          <span class="repo">
            <span class="repo-mark" aria-hidden="true">${repoMark(item.repository_full_name)}</span>
            <span class="full mono">${escapeHtml(item.repository_full_name)}</span>
            <span class="num mono">#${item.issue_number}</span>
          </span>
          ${first ? '<span class="pill pill--top">最佳匹配</span>' : ""}
          ${trackPill(item.service_track)}
          <span class="pill ${availability.className}" title="${escapeHtml(availability.title)}">${escapeHtml(availability.text)}</span>
        </header>
        <h3 class="ic-title"><a href="${escapeHtml(detailUrl(item, feedbackContext, runId))}">${escapeHtml(item.title)}</a></h3>
        <div class="ic-meta">
          <span class="meta-item mono">代码难度 ${item.difficulty?.code ?? "—"} · 环境 ${item.difficulty?.setup ?? "—"}</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="meta-item">${escapeHtml(relativeDate(item.verified_at))} 复核</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="meta-item">${(item.warnings || []).length ? `${item.warnings.length} 条提醒` : "无额外提醒"}</span>
        </div>
      </div>
      <div class="ic-score">
        <span class="score-num">${score}</span>
        <span class="score-label">匹配分</span>
        <span class="score-bar" role="progressbar" aria-valuenow="${score}" aria-valuemin="0" aria-valuemax="100" aria-label="匹配分 ${score} 分">
          <i style="width:${score}%"></i>
        </span>
      </div>
      <details class="ic-reasons reasons">
        <summary><span class="chev" aria-hidden="true"></span>推荐依据 <span class="count mono">${reasons.length} 条</span></summary>
        <ul class="reason-list">${reasonList(reasons)}</ul>
        ${warningList(item.warnings)}
      </details>
      <div class="ic-skills">${skillRows(item)}</div>
      <footer class="ic-foot">
        <span class="model-badge" style="background:transparent;border:0;padding:0" title="模型与版本来自服务端">${escapeHtml(modelLabel(options.model))}</span>
        <div class="ic-actions">
          ${feedbackActions}
          <a class="btn btn--sm btn--ghost" href="${escapeHtml(detailUrl(item, feedbackContext, runId))}">查看详情</a>
          <a class="link-ext" href="${escapeHtml(safeGithubUrl(item.html_url))}" target="_blank" rel="noopener noreferrer">GitHub 原文</a>
        </div>
      </footer>
    </article>`;
  }

  function publicCard(item) {
    const availability = availabilityInfo(item);
    return `<article class="pub-card">
      <header class="ic-top">
        <span class="repo">
          <span class="repo-mark" aria-hidden="true">${repoMark(item.repository_full_name)}</span>
          <span class="full mono">${escapeHtml(item.repository_full_name)}</span>
          <span class="num mono">#${item.issue_number}</span>
        </span>
        <span class="pill pill--neutral">公共候选</span>
        <span class="pill ${availability.className}" title="${escapeHtml(availability.title)}">${escapeHtml(availability.text)}</span>
      </header>
      <h3>${escapeHtml(item.title)}</h3>
      <ul class="chips">${(item.labels || []).slice(0, 4).map((label) => `<li class="chip">${escapeHtml(label)}</li>`).join("") || "<li class=\"chip\">无标签</li>"}</ul>
      <footer class="pub-foot">
        <div class="pub-foot-top">
          <span class="pill pill--neutral">无匹配分 · 非个性化</span>
          <a class="link-ext" href="${escapeHtml(safeGithubUrl(item.html_url))}" target="_blank" rel="noopener noreferrer">原文</a>
        </div>
        <div class="pub-foot-meta">
          <span>${escapeHtml(item.primary_language || "语言未知")}</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span>上游更新 ${escapeHtml(relativeDate(item.last_activity_at))}</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span>${item.comment_count || 0} 条讨论</span>
        </div>
      </footer>
    </article>`;
  }

  function projectCard(project, index = 0) {
    const issues = project.related_issues || [];
    const related = issues.map((issue) =>
      `<li class="chip"><a href="/task?task_candidate_id=${issue.task_candidate_id}">#${issue.issue_number} · ${Math.round(Number(issue.score || 0) * 100)} 分 →</a></li>`
    ).join("") || "<li class=\"chip\">无关联 Issue</li>";
    const basis = (project.basis || []).map((line, basisIndex) =>
      `<li class="reason"><div class="r-head"><span class="r-label">依据 ${String(basisIndex + 1).padStart(2, "0")}</span></div><p class="r-text">${escapeHtml(line)}</p></li>`
    ).join("") || "<li class=\"reason\"><p class=\"r-text\">暂无额外依据</p></li>";
    const first = index === 0;
    const score = Math.max(0, Math.min(100, Math.round(Number(project.score || 0) * 100)));
    const source = project.ranking_source || "issue aggregation";
    return `<article class="issue ${first ? "is-top" : ""}">
      <div class="ic-body">
        <header class="ic-top">
          <span class="repo">
            <span class="repo-mark" aria-hidden="true">${repoMark(project.repository_full_name)}</span>
            <span class="full mono">仓库推荐</span>
          </span>
          ${first ? '<span class="pill pill--top">最佳匹配</span>' : ""}
          <span class="pill pill--neutral">聚合推荐</span>
        </header>
        <h3 class="ic-title">${escapeHtml(project.repository_full_name)}</h3>
        <div class="ic-meta">
          <span class="meta-item mono">${issues.length} 个关联 Issue</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="meta-item mono">${escapeHtml(source)}</span>
        </div>
      </div>
      <div class="ic-score">
        <span class="score-num">${score}</span>
        <span class="score-label">聚合分</span>
        <span class="score-bar" role="progressbar" aria-valuenow="${score}" aria-valuemin="0" aria-valuemax="100" aria-label="聚合分 ${score} 分">
          <i style="width:${score}%"></i>
        </span>
      </div>
      <details class="ic-reasons reasons">
        <summary><span class="chev" aria-hidden="true"></span>推荐依据 <span class="count mono">${(project.basis || []).length} 条</span></summary>
        <ul class="reason-list">${basis}</ul>
      </details>
      <div class="ic-skills">
        <div class="skill-row"><span class="skill-key">关联 Issue</span><ul class="chips">${related}</ul></div>
      </div>
      <footer class="ic-foot">
        <span class="model-badge" style="background:transparent;border:0;padding:0" title="仓库由本次 Issue 推荐聚合">${escapeHtml(source)}</span>
        <div class="ic-actions">
          <a class="btn btn--sm btn--ghost" href="${escapeHtml(safeGithubUrl(project.repository_url))}" target="_blank" rel="noopener noreferrer">仓库与贡献入口</a>
        </div>
      </footer>
    </article>`;
  }

  function runContext(batch, options = {}) {
    const profile = batch.profile || {};
    const runId = batch.run_id || "";
    const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
    const count = batch.count ?? (batch.items || []).length;
    return `<div class="run-context">
      <div class="run-context-main">
        <p class="run-context-label">当前画像</p>
        <p class="run-context-name">${escapeHtml(profile.display_name || profile.profile_key || "当前画像")}</p>
        <dl class="run-context-facts">
          <div><dt>推荐轨道</dt><dd>${escapeHtml(trackLabel(profile.service_track || batch.service_track))}</dd></div>
          <div><dt>画像版本</dt><dd class="mono">${escapeHtml(profile.profile_version || "未标注")}</dd></div>
          <div><dt>本次推荐</dt><dd>${count} 条</dd></div>
        </dl>
        <p class="run-context-note mono" title="${escapeHtml(runId || "即时结果")}">${escapeHtml(runId ? runId.slice(0, 12) : "即时结果")}${runId ? "…" : ""} · ${escapeHtml(batch.match_version || "匹配版本未登记")}</p>
      </div>
      <div class="run-context-actions">
        ${runId ? `<a class="btn btn--sm btn--ghost" href="/recommendation-run${query}">推荐说明</a>` : ""}
        <a class="btn btn--sm btn--quiet" href="/status">系统状态</a>
      </div>
    </div>`;
  }

  function panel(code, title, body, actions = "") {
    return `<div class="panel" role="status">
      <span class="panel-code">${escapeHtml(code)}</span>
      <h3>${escapeHtml(title)}</h3>
      <p>${escapeHtml(body)}</p>
      <div class="panel-actions">${actions}</div>
    </div>`;
  }

  function skeleton(count = 3) {
    return `<div class="skel" role="status" aria-live="polite">
      <span class="sr-only">正在加载推荐结果</span>
      ${Array.from({ length: count }, () => `<div class="skel-card" aria-hidden="true">
        <div class="sk sk--s"></div><div class="sk sk--t"></div><div class="sk"></div>
        <div class="sk sk--m"></div><div class="sk sk--s"></div>
      </div>`).join("")}
    </div>`;
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(body.error?.message || `请求失败（${response.status}）`);
      error.status = response.status;
      error.code = body.error?.code || "unknown_error";
      error.requestId = body.request_id || body.error?.request_id || "";
      throw error;
    }
    return body;
  }

  async function getSession() {
    try {
      const body = await requestJson("/api/v1/me");
      return { available: true, authenticated: true, user: body.user || null };
    } catch (error) {
      if (error.status === 401) {
        return { available: true, authenticated: false, user: null };
      }
      if (error.status === 503) {
        return { available: false, authenticated: false, user: null, error };
      }
      throw error;
    }
  }

  function storeBatch(batch) {
    try {
      sessionStorage.setItem(BATCH_STORAGE_KEY, JSON.stringify({
        savedAt: new Date().toISOString(),
        batch,
      }));
    } catch {
      /* sessionStorage is optional; the page still works without it. */
    }
  }

  function readBatch() {
    try {
      const raw = sessionStorage.getItem(BATCH_STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return parsed && parsed.batch ? parsed.batch : null;
    } catch {
      return null;
    }
  }

  async function submitFeedback(taskCandidateId, state, feedbackContext, runId, button, status) {
    if (!feedbackContext) return;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "保存中…";
    if (status) status.textContent = "";
    try {
      const body = {
        task_candidate_id: Number(taskCandidateId),
        feedback_context: feedbackContext,
        feedback_state: state,
      };
      if (runId) body.run_id = runId;
      await requestJson("/api/v1/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      button.textContent = state === "not_suitable" ? "已记录不适合" : "已记录";
      if (status) status.textContent = "反馈已保存";
    } catch (error) {
      button.disabled = false;
      button.textContent = original;
      if (status) status.textContent = `保存失败：${error.message}`;
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-feedback]");
    if (!button) return;
    const card = button.closest("[data-candidate-id]");
    if (!card) return;
    const batch = readBatch();
    const context = card.dataset.feedbackContext || batch?.feedback_context || "";
    const runId = card.dataset.runId || batch?.run_id || "";
    const status = card.querySelector(".feedback-status");
    submitFeedback(card.dataset.candidateId, button.dataset.feedback, context, runId, button, status);
  });

  window.OMUI = {
    escapeHtml,
    safeGithubUrl,
    requestJson,
    getSession,
    trackLabel,
    trackPill,
    relativeDate,
    availabilityInfo,
    reasonList,
    issueCard,
    publicCard,
    projectCard,
    runContext,
    panel,
    skeleton,
    storeBatch,
    readBatch,
    modelLabel,
  };
})();
