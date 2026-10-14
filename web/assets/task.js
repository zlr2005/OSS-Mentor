"use strict";

const params = new URLSearchParams(window.location.search);
const rawTaskId = (params.get("task_candidate_id") || "").trim();
const feedbackContext = params.get("feedback_context") || null;

const title = document.querySelector("#task-title");
const repository = document.querySelector("#task-repository");
const repositoryFact = document.querySelector("#task-repository-fact");
const issueNumber = document.querySelector("#task-issue-number");
const issueFact = document.querySelector("#task-issue-fact");
const availability = document.querySelector("#task-availability");
const verifiedAt = document.querySelector("#task-verified-at");
const verifiedFact = document.querySelector("#task-verified-fact");
const reasons = document.querySelector("#task-availability-reasons");
const body = document.querySelector("#task-body");
const githubLink = document.querySelector("#task-github-link");
const status = document.querySelector("#task-status");
const refresh = document.querySelector("#refresh-task");
const message = document.querySelector("#task-message");
const messageTitle = document.querySelector("#task-message-title");
const messageCopy = document.querySelector("#task-message-copy");
const messageAction = document.querySelector("#task-message-action");
const feedbackActions = document.querySelector("#feedback-actions");
const feedbackNote = document.querySelector("#feedback-note");

const availabilityLabels = {
  available: "可领取",
  closed: "已关闭",
  assigned: "已有负责人",
  linked_open_pr: "已有开放 PR",
  locked: "讨论已锁定",
  repository_inactive: "仓库不活跃",
  temporarily_unverified: "待复核",
};

const reasonLabels = {
  issue_closed: "Issue 已关闭",
  issue_assigned: "Issue 已有负责人",
  issue_locked: "讨论已锁定",
  linked_open_pr: "已有关联的开放 PR",
  repository_inactive: "仓库长期没有推送",
  verification_older_than_limit: "上次 GitHub 复核已超过 24 小时",
  github_unavailable: "GitHub 上已无法访问该 Issue",
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
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error?.message || `请求失败（${response.status}）`);
    error.status = response.status;
    error.code = payload.error?.code || "unknown_error";
    throw error;
  }
  return payload;
}

function showMessage(headline, copy, action = null, isError = false) {
  messageTitle.textContent = headline;
  messageCopy.textContent = copy;
  message.setAttribute("role", isError ? "alert" : "status");
  messageAction.hidden = !action;
  if (action) {
    messageAction.href = action.href;
    messageAction.textContent = action.label;
  }
  message.hidden = false;
}

function renderTask(task) {
  const repositoryName = task.repository_full_name || "未知仓库";
  const available = task.availability === "available";
  const label = availabilityLabels[task.availability] || task.availability;
  title.textContent = task.title || "（该 Issue 没有标题）";
  repository.textContent = `${repositoryName} · #${task.issue_number}`;
  repositoryFact.textContent = repositoryName;
  issueNumber.textContent = `Issue #${task.issue_number}`;
  issueFact.textContent = `#${task.issue_number}`;
  availability.textContent = label;
  availability.dataset.state = available ? "available" : "unavailable";
  verifiedAt.textContent = task.verified_at
    ? `最近复核 ${task.verified_at}`
    : "尚无 GitHub 复核记录";
  verifiedFact.textContent = task.verified_at || "尚无记录";
  reasons.innerHTML = (task.availability_reasons || [])
    .map(reason => `<li>${escapeHtml(reasonLabels[reason] || reason)}</li>`)
    .join("");
  const text = String(task.body_text || "").trim();
  body.innerHTML = text
    ? text.split(/\n{2,}/).map(block => `<p>${escapeHtml(block)}</p>`).join("")
    : '<p class="task-placeholder">该 Issue 没有正文，请到 GitHub 查看完整讨论与验收要求。</p>';
  const url = safeUrl(task.html_url);
  githubLink.href = url;
  githubLink.hidden = url === "#";
  if (task.refreshed) {
    status.textContent = "刚完成复核";
  } else {
    status.textContent = task.verified_at ? "使用本地记录" : "待复核";
  }
}

async function loadTask(shouldRefresh) {
  refresh.disabled = true;
  body.setAttribute("aria-busy", "true");
  message.hidden = true;
  status.textContent = shouldRefresh ? "复核中" : "读取中";
  if (shouldRefresh) refresh.textContent = "正在复核…";
  try {
    const suffix = shouldRefresh ? "?refresh=true" : "";
    const task = await requestJson(`/api/v1/tasks/${rawTaskId}${suffix}`);
    renderTask(task);
  } catch (error) {
    body.innerHTML = '<p class="task-placeholder">暂时无法读取任务描述。</p>';
    status.textContent = "不可用";
    if (error.status === 404) {
      showMessage("找不到这个任务", "它可能已从候选池移除，或链接里的编号不正确。",
        {href: "/recommendations", label: "返回推荐"}, false);
    } else if (error.status === 429) {
      showMessage("GitHub 限流", "短时间内复核请求过多，请稍后再试。",
        {href: "#", label: "重试"}, true);
    } else if (error.status === 502) {
      showMessage("GitHub 暂时不可用", "上游返回了不可恢复的错误，稍后再试。",
        {href: "#", label: "重试"}, true);
    } else if (error.status === 503) {
      showMessage("服务尚未就绪", "本地服务没有启用任务复核能力，请先查看本地任务记录。",
        {href: "/recommendations", label: "返回推荐"}, true);
    } else {
      showMessage("任务详情暂时不可用", `${error.message}，请稍后重试。`,
        {href: "#", label: "重试"}, true);
    }
    if (!messageAction.hidden) {
      messageAction.addEventListener("click", event => {
        if (messageAction.getAttribute("href") === "#") {
          event.preventDefault();
          loadTask(false);
        }
      }, {once: true});
    }
  } finally {
    body.setAttribute("aria-busy", "false");
    refresh.disabled = false;
    refresh.textContent = "重新复核可用性";
  }
}

feedbackActions.addEventListener("click", async event => {
  const button = event.target.closest("[data-feedback]");
  if (!button) return;
  if (!feedbackContext) {
    feedbackNote.dataset.error = "true";
    feedbackNote.textContent = "缺少画像上下文，请从推荐页进入任务详情后再记录反馈。";
    return;
  }
  const buttons = [...feedbackActions.querySelectorAll("[data-feedback]")];
  buttons.forEach(item => { item.disabled = true; });
  feedbackNote.dataset.error = "false";
  feedbackNote.textContent = "正在保存…";
  try {
    await requestJson("/api/v1/feedback", {
      method: "POST",
      body: JSON.stringify({
        task_candidate_id: Number(rawTaskId),
        feedback_context: feedbackContext,
        feedback_state: button.dataset.feedback,
      }),
    });
    buttons.forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    feedbackNote.textContent = "反馈已保存，后续推荐会参考它。";
  } catch (error) {
    let hint = `保存失败：${error.message}`;
    if (error.status === 401) hint = "登录状态已失效，请重新登录后再记录反馈。";
    if (error.status === 403) hint = "当前画像不属于登录用户，请从推荐页重新进入。";
    if (error.status === 404) hint = "该任务已不在候选池中，无法记录反馈。";
    feedbackNote.dataset.error = "true";
    feedbackNote.textContent = hint;
  } finally {
    buttons.forEach(item => { item.disabled = false; });
  }
});

refresh.addEventListener("click", () => loadTask(true));

if (!/^[1-9][0-9]{0,18}$/.test(rawTaskId)) {
  title.textContent = "缺少任务编号";
  repository.textContent = "任务详情";
  body.innerHTML = '<p class="task-placeholder">链接里没有可用的 task_candidate_id。</p>';
  status.textContent = "参数错误";
  refresh.disabled = true;
  feedbackActions.querySelectorAll("[data-feedback]").forEach(item => { item.disabled = true; });
  showMessage("链接不完整", "请从推荐页打开任务，或补上 task_candidate_id 参数。",
    {href: "/recommendations", label: "返回推荐"}, true);
} else {
  loadTask(false);
}
