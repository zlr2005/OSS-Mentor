"use strict";

const state = {
  profiles: [],
  selected: null,
  mode: "preset",
  feedbackContext: null,
  feedbackClientId: null,
};
const elements = {
  form: document.querySelector("#recommendation-form"),
  select: document.querySelector("#profile-select"),
  summary: document.querySelector("#profile-summary"),
  button: document.querySelector("#recommend-button"),
  buttonLabel: document.querySelector("#recommend-button-label"),
  status: document.querySelector("#api-status"),
  message: document.querySelector("#message"),
  list: document.querySelector("#recommendation-list"),
  count: document.querySelector("#result-count"),
  presetMode: document.querySelector("#preset-mode"),
  customMode: document.querySelector("#custom-mode"),
  presetFields: document.querySelector("#preset-fields"),
  customFields: document.querySelector("#custom-fields"),
  customTrack: document.querySelector("#custom-track"),
  profileError: document.querySelector("#profile-error"),
  privacyNote: document.querySelector("#privacy-note"),
};

const reasonLabels = {
  preferred_language: "符合你偏好的编程语言",
  preferred_task_type: "任务类型与你的偏好一致",
  newcomer_label_required: "项目明确标记为新人友好",
};

const skillLabels = {
  bug_fix: "Bug 修复",
  build_tooling: "构建工具",
  testing: "测试",
  feature: "功能开发",
  documentation: "文档",
  refactor: "重构",
  "platform:macos": "macOS 环境",
  "platform:windows": "Windows 环境",
  "platform:linux": "Linux 环境",
};

const feedbackLabels = {
  interested: "感兴趣",
  not_suitable: "不适合",
  started: "已开始",
  completed: "已完成",
};

async function getJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error?.message || `请求失败（${response.status}）`);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showMessage(title, body, kind = "info") {
  elements.message.hidden = false;
  elements.message.dataset.kind = kind;
  elements.message.querySelector("h3").textContent = title;
  elements.message.querySelector("p").textContent = body;
}

function renderProfile(profile) {
  if (!profile) return;
  const track = profile.service_track === "newcomer" ? "首次贡献通道" : "进阶成长通道";
  const tags = [
    ...profile.preferred_languages,
    ...profile.operating_systems.map(value => value === "macos" ? "macOS" : value),
  ];
  elements.summary.innerHTML = `
    <strong>${escapeHtml(track)}</strong><br>
    可接受代码难度 ${profile.max_code_difficulty}/3，环境难度 ${profile.max_setup_difficulty}/3
    <div class="profile-tags">${tags.map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>
  `;
}

function translateReason(reason) {
  if (reasonLabels[reason]) return reasonLabels[reason];
  if (reason.startsWith("skill_coverage=")) {
    const value = Math.round(Number(reason.split("=")[1]) * 100);
    return `已覆盖约 ${value}% 的任务技能要求`;
  }
  if (reason.startsWith("stretch_target=")) {
    return `技能跨度符合进阶目标（${escapeHtml(reason.split("=")[1])} 级）`;
  }
  return reason;
}

function renderSkills(gaps) {
  return gaps.map(item => {
    const hasGap = item.gap > 0;
    const label = skillLabels[item.skill] || item.skill;
    return `
      <div class="skill-item ${hasGap ? "gap" : ""}">
        <span>${escapeHtml(label)}</span>
        <span class="skill-levels">${item.developer_level} → ${item.required_level}
          ${hasGap ? `<span class="gap-badge">差 ${item.gap}</span>` : "✓"}
        </span>
      </div>`;
  }).join("");
}

function renderRecommendation(item, index) {
  const reasonText = item.reasons.map(translateReason).join("；");
  const feedbackStatus = item.feedback_state
    ? `当前状态：${feedbackLabels[item.feedback_state]}`
    : "选择后会保存在本地";
  return `
    <article class="recommendation-card" data-rank="${index + 1}">
      <div class="score-block" aria-label="匹配分 ${item.match_score}">
        <span class="score-value">${Math.round(item.match_score)}</span>
        <span class="score-label">匹配分 / 100</span>
      </div>
      <div>
        <p class="repo-name">${escapeHtml(item.repository)} · #${item.issue_number}</p>
        <h3 class="task-title"><a href="${escapeHtml(item.html_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a></h3>
        <div class="task-tags">
          <span class="tag">${item.track === "newcomer" ? "首次贡献" : "进阶成长"}</span>
          <span class="tag">技能覆盖 ${Math.round(item.skill_coverage * 100)}%</span>
          <span class="tag">最大差距 ${item.maximum_skill_gap} 级</span>
        </div>
        <div class="reason-box">
          <strong>为什么推荐</strong>
          <p>${reasonText}</p>
        </div>
        <h4 class="skill-title">技能要求与差距</h4>
        <div class="skill-grid">${renderSkills(item.skill_gaps)}</div>
        <div class="feedback-section" data-feedback-for="${item.task_candidate_id}">
          <div class="feedback-heading">
            <strong>这条推荐适合你吗？</strong>
            <span class="feedback-status" role="status">${escapeHtml(feedbackStatus)}</span>
          </div>
          <div class="feedback-actions" aria-label="推荐反馈">
            ${Object.entries(feedbackLabels).map(([value, label]) => `
              <button type="button" class="feedback-button ${item.feedback_state === value ? "active" : ""}"
                data-feedback-state="${value}" data-task-candidate-id="${item.task_candidate_id}"
                aria-pressed="${item.feedback_state === value}">${label}</button>
            `).join("")}
          </div>
        </div>
      </div>
    </article>`;
}

function getFeedbackClientId() {
  if (state.feedbackClientId) return state.feedbackClientId;
  const storageKey = "oss_mentor_feedback_client_id";
  try {
    const stored = window.localStorage.getItem(storageKey);
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(stored || "")) {
      state.feedbackClientId = stored;
      return stored;
    }
    state.feedbackClientId = crypto.randomUUID();
    window.localStorage.setItem(storageKey, state.feedbackClientId);
  } catch (_error) {
    state.feedbackClientId = crypto.randomUUID();
  }
  return state.feedbackClientId;
}

function checkedValues(containerSelector) {
  return [...document.querySelectorAll(`${containerSelector} input:checked`)].map(input => input.value);
}

function collectCustomProfile() {
  const languages = checkedValues("#language-choices");
  const operatingSystems = checkedValues("#os-choices");
  const taskTypes = checkedValues("#task-choices");
  if (!languages.length || !operatingSystems.length || !taskTypes.length) {
    throw new Error("请至少选择一种语言、一个操作系统和一种任务类型。");
  }
  const skills = Object.fromEntries(
    [...document.querySelectorAll("[data-skill]")].map(input => [input.dataset.skill, Number(input.value)])
  );
  const track = elements.customTrack.value;
  return {
    display_name: track === "newcomer" ? "我的首次贡献画像" : "我的进阶画像",
    service_track: track,
    preferred_languages: languages,
    operating_systems: operatingSystems,
    preferred_task_types: taskTypes,
    max_code_difficulty: Number(document.querySelector("#code-difficulty").value),
    max_setup_difficulty: Number(document.querySelector("#setup-difficulty").value),
    desired_skill_stretch: Number(document.querySelector("#skill-stretch").value),
    skills,
  };
}

function applyTrackDefaults() {
  const growth = elements.customTrack.value === "growth";
  document.querySelector("#code-difficulty").value = growth ? "3" : "1";
  document.querySelector("#setup-difficulty").value = growth ? "3" : "2";
  document.querySelector("#skill-stretch").value = growth ? "1" : "0";
  const levels = growth
    ? { python: 2, javascript: 2, testing: 2, git: 3, build_tooling: 1, documentation: 1 }
    : { python: 1, javascript: 0, testing: 1, git: 1, build_tooling: 0, documentation: 0 };
  document.querySelectorAll("[data-skill]").forEach(input => {
    input.value = String(levels[input.dataset.skill] ?? 0);
  });
}

function setMode(mode) {
  state.mode = mode;
  const custom = mode === "custom";
  elements.presetFields.hidden = custom;
  elements.customFields.hidden = !custom;
  elements.presetMode.classList.toggle("active", !custom);
  elements.customMode.classList.toggle("active", custom);
  elements.presetMode.setAttribute("aria-pressed", String(!custom));
  elements.customMode.setAttribute("aria-pressed", String(custom));
  elements.privacyNote.textContent = custom
    ? "自定义画像不写入数据库；反馈状态仅保存在本地。"
    : "预设画像和推荐反馈保存在本地 SQLite 中。";
  elements.button.disabled = custom ? false : !state.selected;
  elements.profileError.hidden = true;
}

async function loadProfiles() {
  try {
    const health = await getJson("/health");
    if (!health.database_ready) throw new Error("本地数据库尚未准备好");
    elements.status.textContent = "本地服务已连接";
    elements.status.className = "status-pill online";

    const payload = await getJson("/api/v1/profiles");
    state.profiles = payload.items;
    if (state.profiles.length) {
      elements.select.innerHTML = state.profiles.map(profile =>
        `<option value="${escapeHtml(profile.profile_key)}">${escapeHtml(profile.display_name)}</option>`
      ).join("");
      const newcomer = state.profiles.find(profile => profile.service_track === "newcomer");
      state.selected = newcomer || state.profiles[0];
      elements.select.value = state.selected.profile_key;
      elements.select.disabled = false;
      elements.button.disabled = false;
      renderProfile(state.selected);
      const requestedMode = new URLSearchParams(window.location.search).get("mode");
      if (requestedMode === "custom") {
        setMode("custom");
        showMessage("完善你的画像", "选择你的语言、环境、任务偏好和技能水平后生成推荐。");
      } else {
        await loadRecommendations({ scrollToResults: false });
      }
    } else {
      setMode("custom");
      showMessage("创建你的画像", "当前没有预设模板，请填写自定义画像后生成推荐。");
    }
  } catch (error) {
    elements.status.textContent = "本地服务不可用";
    elements.status.className = "status-pill offline";
    elements.button.disabled = true;
    showMessage("无法加载画像", error.message, "error");
  }
}

async function loadRecommendations({ scrollToResults = true } = {}) {
  if (state.mode === "preset" && !state.selected) return;
  elements.profileError.hidden = true;
  let request;
  try {
    if (state.mode === "custom") {
      const profile = collectCustomProfile();
      request = getJson("/api/v1/recommendations/custom", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile, limit: 10, feedback_client_id: getFeedbackClientId() }),
      });
    } else {
      const query = new URLSearchParams({ profile_key: state.selected.profile_key, limit: "10" });
      request = getJson(`/api/v1/recommendations?${query}`);
    }
  } catch (error) {
    elements.profileError.textContent = error.message;
    elements.profileError.hidden = false;
    return;
  }

  elements.button.disabled = true;
  elements.buttonLabel.textContent = "正在计算推荐";
  elements.message.hidden = true;
  elements.list.innerHTML = '<div class="skeleton" aria-label="正在加载推荐"></div>';
  try {
    const payload = await request;
    state.feedbackContext = payload.feedback_context;
    elements.count.textContent = `找到 ${payload.count} 个通过门槛的任务`;
    if (!payload.items.length) {
      elements.list.innerHTML = "";
      showMessage("暂时没有合适任务", "可以适当提高难度上限、增加操作系统，或同步更多候选任务后再试。", "empty");
      return;
    }
    elements.list.innerHTML = payload.items.map(renderRecommendation).join("");
    if (scrollToResults) {
      document.querySelector("#results-title").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (error) {
    elements.list.innerHTML = "";
    elements.count.textContent = "推荐加载失败";
    showMessage("无法生成推荐", error.message, "error");
  } finally {
    elements.button.disabled = state.mode === "preset" && !state.selected;
    elements.buttonLabel.textContent = "查看我的推荐";
  }
}

async function saveFeedback(button) {
  if (!state.feedbackContext) {
    showMessage("暂时无法保存反馈", "请先重新生成一次推荐。", "error");
    return;
  }
  const section = button.closest(".feedback-section");
  const buttons = [...section.querySelectorAll(".feedback-button")];
  const status = section.querySelector(".feedback-status");
  buttons.forEach(item => { item.disabled = true; });
  status.textContent = "正在保存…";
  try {
    const payload = await getJson("/api/v1/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task_candidate_id: Number(button.dataset.taskCandidateId),
        feedback_context: state.feedbackContext,
        feedback_state: button.dataset.feedbackState,
      }),
    });
    const savedState = payload.feedback.feedback_state;
    buttons.forEach(item => {
      const active = item.dataset.feedbackState === savedState;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    status.textContent = payload.feedback.changed
      ? `已保存：${feedbackLabels[savedState]}`
      : `当前状态：${feedbackLabels[savedState]}`;
  } catch (error) {
    status.textContent = `保存失败：${error.message}`;
  } finally {
    buttons.forEach(item => { item.disabled = false; });
  }
}

elements.presetMode.addEventListener("click", () => setMode("preset"));
elements.customMode.addEventListener("click", () => setMode("custom"));
elements.customTrack.addEventListener("change", applyTrackDefaults);
elements.select.addEventListener("change", event => {
  state.selected = state.profiles.find(profile => profile.profile_key === event.target.value);
  renderProfile(state.selected);
});
elements.form.addEventListener("submit", event => {
  event.preventDefault();
  loadRecommendations();
});
elements.list.addEventListener("click", event => {
  const button = event.target.closest("[data-feedback-state]");
  if (button) saveFeedback(button);
});

loadProfiles();
