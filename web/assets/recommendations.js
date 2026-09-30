"use strict";

const list = document.querySelector("#ranked-list");
const status = document.querySelector("#result-status");
const refresh = document.querySelector("#refresh-recommendations");
const message = document.querySelector("#recommendation-message");
const messageTitle = document.querySelector("#message-title");
const messageCopy = document.querySelector("#message-copy");
const messageAction = document.querySelector("#message-action");
const profileSummary = document.querySelector("#profile-summary");
const contextState = document.querySelector("#context-state");
let feedbackContext = null;

const warningLabels = {
  diversity_repository_cap_relaxed: "候选仓库不足，已放宽单仓库数量限制。",
  diversity_task_type_relaxed: "候选类型不足，结果可能集中在同一任务类型。",
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;",
  })[character]);
}

function safeUrl(value) {
  try {
    const url = new URL(String(value || ""), window.location.origin);
    return url.protocol === "https:" && url.hostname === "github.com" ? url.href : "#";
  } catch {
    return "#";
  }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error?.message || `请求失败（${response.status}）`);
    error.status = response.status;
    error.code = body.error?.code || "unknown_error";
    throw error;
  }
  return body;
}

function showMessage(title, copy, action = null, isError = false) {
  messageTitle.textContent = title;
  messageCopy.textContent = copy;
  message.setAttribute("role", isError ? "alert" : "status");
  messageAction.hidden = !action;
  if (action) {
    messageAction.href = action.href;
    messageAction.textContent = action.label;
  }
  message.hidden = false;
}

function renderProfile(body) {
  const profile = body.profile || {};
  const values = [
    ["画像", profile.display_name || profile.profile_key || "当前用户"],
    ["轨道", profile.service_track === "growth" ? "进阶成长" : "新人破冰"],
    ["推荐数", String(body.count ?? body.items?.length ?? 0)],
    ["批次", body.run_id ? body.run_id.slice(0, 8) : "即时结果"],
  ];
  profileSummary.innerHTML = values.map(([key, value]) =>
    `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`
  ).join("");
}

function reasonRow(reason) {
  const delta = Number(reason.score_delta || 0);
  const points = Math.round(delta * 100);
  return `<div class="reason-row">
    <strong>${escapeHtml(reason.label)}</strong>
    <span>${escapeHtml(reason.evidence)}</span>
    <output data-negative="${delta < 0}">${points >= 0 ? "+" : ""}${points} 点</output>
    <small>${escapeHtml(reason.code)} · ${escapeHtml(reason.feature_version)}</small>
  </div>`;
}

function recommendationRow(item, index) {
  const taskUrl = escapeHtml(safeUrl(item.html_url));
  const matched = item.matched_skills?.join("、") || "无明确要求";
  const missing = item.missing_skills?.join("、") || "无关键缺口";
  const warnings = item.warnings?.length
    ? `<p class="warning-copy">注意：${escapeHtml(item.warnings.map(
      warning => warningLabels[warning] || warning
    ).join(" "))}</p>` : "";
  const feedbackRecorded = item.feedback_state === "not_suitable";
  return `<li class="ranked-item" data-candidate-id="${item.task_candidate_id}">
    <div class="rank-position">${String(index + 1).padStart(2, "0")}<span>${Math.round(item.score * 100)} 分</span></div>
    <article>
      <div class="task-meta"><strong>${escapeHtml(item.repository_full_name)}</strong><span>#${item.issue_number}</span><span>代码 ${item.difficulty.code} · 环境 ${item.difficulty.setup}</span></div>
      <h3><a href="${taskUrl}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a></h3>
      <div class="reason-ledger">${(item.reasons || []).map(reasonRow).join("")}</div>
      <div class="skill-summary"><span><strong>已覆盖</strong> ${escapeHtml(matched)}</span><span><strong>成长空间</strong> ${escapeHtml(missing)}</span></div>
      ${warnings}
      <div class="item-actions">
        <a class="task-link" href="${taskUrl}" target="_blank" rel="noopener noreferrer">查看任务</a>
        <button class="feedback-button" type="button" data-feedback="not_suitable" ${feedbackRecorded ? "disabled" : ""}>${feedbackRecorded ? "已记录不适合" : "不适合我"}</button>
        <span class="feedback-status" role="status"></span>
      </div>
    </article>
  </li>`;
}

async function loadRecommendations() {
  refresh.disabled = true;
  contextState.textContent = "计算中";
  status.textContent = "正在生成推荐…";
  message.hidden = true;
  list.setAttribute("aria-busy", "true");
  try {
    const body = await requestJson("/api/v1/me/recommendations?limit=10");
    feedbackContext = body.feedback_context || null;
    renderProfile(body);
    const items = body.items || [];
    list.innerHTML = items.map(recommendationRow).join("");
    if (!items.length) showMessage("暂时没有符合条件的任务", "可以放宽画像中的语言、任务类型或难度设置。", {href: "/profile", label: "调整画像"});
    status.textContent = `${items.length} 个可领取任务`;
    contextState.textContent = "已同步";
  } catch (error) {
    list.innerHTML = "";
    profileSummary.innerHTML = "<div><dt>状态</dt><dd>暂不可用</dd></div>";
    if (error.status === 401 || error.code === "authentication_required") {
      showMessage("需要先登录", "登录后我们才能读取属于你的当前画像。", {href: "/login?return_to=%2Frecommendations", label: "前往登录"}, true);
    } else if (error.code === "profile_not_found" || error.code === "profile_required") {
      showMessage("还没有开发者画像", "先完成画像，再回来查看个性化推荐。", {href: "/profile", label: "创建画像"}, false);
    } else {
      showMessage("推荐暂时不可用", `${error.message}，请稍后重试。`, {href: "#", label: "重新加载"}, true);
      messageAction.addEventListener("click", event => { event.preventDefault(); loadRecommendations(); }, {once: true});
    }
    status.textContent = "未生成结果";
    contextState.textContent = "需要处理";
  } finally {
    list.setAttribute("aria-busy", "false");
    refresh.disabled = false;
  }
}

list.addEventListener("click", async event => {
  const button = event.target.closest("[data-feedback]");
  if (!button) return;
  const row = button.closest("[data-candidate-id]");
  const feedbackStatus = row.querySelector(".feedback-status");
  button.disabled = true;
  button.textContent = "正在记录…";
  feedbackStatus.textContent = "";
  try {
    await requestJson("/api/v1/feedback", {
      method: "POST",
      body: JSON.stringify({
        task_candidate_id: Number(row.dataset.candidateId),
        feedback_context: feedbackContext,
        feedback_state: button.dataset.feedback,
      }),
    });
    button.textContent = "已记录不适合";
    feedbackStatus.textContent = "反馈已保存";
  } catch (error) {
    button.textContent = "重试反馈";
    feedbackStatus.textContent = `保存失败：${error.message}`;
    feedbackStatus.dataset.error = "true";
    button.disabled = false;
  } finally {
    if (button.textContent !== "已记录不适合") button.disabled = false;
  }
});

refresh.addEventListener("click", loadRecommendations);
loadRecommendations();
