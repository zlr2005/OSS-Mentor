"use strict";

/*
 * OSS-Mentor · Developer Profile
 * B5 profile-page interaction.
 *
 * Current integration boundary:
 * - Manual profile editing works without GitHub.
 * - Demo mode uses deterministic fixture-compatible data.
 * - GitHub suggestions never silently overwrite manual fields.
 * - Accept / reject is explicit.
 * - The public API routes remain owned by member D.
 *
 * Live API is the default. ?mode=demo explicitly selects local fixtures.
 * The gateway consumes:
 *   GET  /api/v1/me/profile
 *   PUT  /api/v1/me/profile
 *   POST /api/v1/me/profile/import-github
 *
 * Decisions use /suggestions/{id}/accept|reject and server-owned provenance.
 */

const PROFILE_STORAGE_KEY = "oss-mentor.profile.v05";
const SUGGESTION_STORAGE_KEY = "oss-mentor.profile-suggestions.v05";
const IMPORT_STORAGE_KEY = "oss-mentor.github-import.v05";

const PROFILE_API = "/api/v1/me/profile";
const GITHUB_IMPORT_API = "/api/v1/me/profile/import-github";

const PROFILE_SOURCE_LABELS = {
  default: "默认值",
  github_weak_inference: "GitHub 弱推断",
  github_explicit_evidence: "GitHub 明确证据",
  user_input: "用户填写",
  user_confirmed: "用户已确认",
};

const TRACK_LABELS = {
  newcomer: "Newcomer · 首次或早期开源贡献",
  growth: "Growth · 成长型贡献者",
};

const TASK_TYPE_LABELS = {
  bug_fix: "Bug 修复",
  testing: "测试",
  documentation: "文档",
  feature: "功能开发",
  refactor: "重构",
  build_tooling: "构建工具",
};

const SKILL_NAMES = [
  "Python",
  "JavaScript",
  "TypeScript",
  "Java",
  "Go",
  "Rust",
  "testing",
  "git",
  "documentation",
  "build_tooling",
];

const DEFAULT_PROFILE = {
  profile_key: "local-profile",
  display_name: "",
  service_track: "newcomer",
  preferred_languages: ["Python"],
  operating_systems: ["windows"],
  preferred_task_types: ["bug_fix", "testing"],
  max_code_difficulty: 1,
  max_setup_difficulty: 2,
  desired_skill_stretch: 0,
  skills: {
    Python: 1,
    testing: 1,
    git: 1,
  },
  field_metadata: {
    preferred_languages: {
      source: "user_input",
      locked: false,
      observed_at: null,
    },
    "skills.Python": {
      source: "user_input",
      locked: false,
      observed_at: null,
    },
    "skills.testing": {
      source: "user_input",
      locked: false,
      observed_at: null,
    },
    "skills.git": {
      source: "user_input",
      locked: false,
      observed_at: null,
    },
  },
};

/*
 * Fixed fallback data mirrors the sanitized github_user.json contract.
 * It exists only so B can develop/test the page before D's API glue is
 * merged. No private repository, token, email, cookie, or OAuth state.
 */
const DEMO_GITHUB_PAYLOAD = {
  schema_version: "github-profile-input-v0.1",
  observed_at: "2026-07-29T12:30:00Z",
  consent_version: "profile-import-consent-v0.1",
  user: {
    login: "fixture-dev",
    name: "Fixture Developer",
  },
  repositories: [
    {
      full_name: "fixture-dev/python-cli",
      private: false,
      archived: false,
      languages: {
        Python: 8000,
        Shell: 2000,
      },
      contributions: {
        commits: 12,
        pull_requests: 3,
        issues: 1,
        reviews: 2,
      },
      first_contribution_at: "2025-11-10T09:00:00Z",
      last_contribution_at: "2026-07-27T10:00:00Z",
      contributed_paths: [
        "src/cli.py",
        "tests/test_cli.py",
        "pyproject.toml",
        ".github/workflows/test.yml",
        "README.md",
      ],
    },
    {
      full_name: "community/web-docs",
      private: false,
      archived: false,
      languages: {
        JavaScript: 6000,
        CSS: 2000,
        HTML: 2000,
      },
      contributions: {
        commits: 5,
        pull_requests: 1,
        issues: 2,
        reviews: 1,
      },
      first_contribution_at: "2026-03-05T08:00:00Z",
      last_contribution_at: "2026-07-20T15:00:00Z",
      contributed_paths: [
        "src/app.js",
        "docs/getting-started.md",
        "package.json",
        "README.md",
      ],
    },
    {
      full_name: "community/parser",
      private: false,
      archived: false,
      languages: {
        Python: 5000,
      },
      contributions: {
        commits: 2,
        pull_requests: 2,
        issues: 0,
        reviews: 4,
      },
      first_contribution_at: "2026-01-15T11:00:00Z",
      last_contribution_at: "2026-06-15T11:00:00Z",
      contributed_paths: [
        "tests/test_parser.py",
        "tox.ini",
        "docs/api.md",
      ],
    },
  ],
};

const state = {
  profile: clone(DEFAULT_PROFILE),
  suggestions: [],
  githubImport: null,
  dirty: false,
  gatewayMode: new URLSearchParams(window.location.search).get("mode") === "demo" ? "demo" : "api",
  ready: false,
  persisted: false,
  busy: false,
  consentVersion: "profile-import-consent-v0.1",
};


/* ================================================================
   DOM helpers
   ================================================================ */

function byId(id) {
  return document.getElementById(id);
}

function all(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function nowIso() {
  return new Date().toISOString();
}

function safeJsonParse(value, fallback) {
  if (!value) {
    return fallback;
  }

  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

function setText(id, value) {
  const element = byId(id);

  if (element) {
    element.textContent = value;
  }
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value
    : {};
}

function clampNumber(value, minimum, maximum, fallback = minimum) {
  const number = Number(value);

  if (!Number.isFinite(number)) {
    return fallback;
  }

  return Math.min(maximum, Math.max(minimum, number));
}


/* ================================================================
   Local persistence
   ================================================================ */

function loadLocalProfile() {
  const stored = safeJsonParse(
    window.localStorage.getItem(PROFILE_STORAGE_KEY),
    null,
  );

  if (!stored) {
    return clone(DEFAULT_PROFILE);
  }

  return normalizeProfile(stored);
}

function saveLocalProfile(profile) {
  window.localStorage.setItem(
    PROFILE_STORAGE_KEY,
    JSON.stringify(profile),
  );
}

function loadLocalSuggestions() {
  const suggestions = safeJsonParse(
    window.localStorage.getItem(SUGGESTION_STORAGE_KEY),
    [],
  );

  return Array.isArray(suggestions) ? suggestions : [];
}

function saveLocalSuggestions(suggestions) {
  window.localStorage.setItem(
    SUGGESTION_STORAGE_KEY,
    JSON.stringify(suggestions),
  );
}

function loadLocalImport() {
  return safeJsonParse(
    window.localStorage.getItem(IMPORT_STORAGE_KEY),
    null,
  );
}

function saveLocalImport(githubImport) {
  window.localStorage.setItem(
    IMPORT_STORAGE_KEY,
    JSON.stringify(githubImport),
  );
}


/* ================================================================
   Gateway boundary
   ================================================================ */

const profileGateway = {
  async loadProfile() {
    if (state.gatewayMode === "demo") {
      state.suggestions = loadLocalSuggestions().map(normalizeSuggestion);
      const imported = loadLocalImport();
      state.githubImport = imported ? normalizeGithubImport(imported) : null;
      return loadLocalProfile();
    }
    try {
      const payload = await profileRequest(PROFILE_API);
      state.suggestions = payload.suggestions.map(normalizeSuggestion);
      state.consentVersion = payload.consent_version;
      state.persisted = true;
      return payload.profile;
    } catch (error) {
      if (error.status !== 404) throw error;
      state.persisted = false;
      state.suggestions = [];
      return clone(DEFAULT_PROFILE);
    }
  },

  async saveProfile(profile) {
    if (state.gatewayMode === "api") {
      const payload = await profileRequest(PROFILE_API, "PUT", editableProfile(profile));
      state.persisted = true;
      return payload.profile;
    }

    saveLocalProfile(profile);
    return clone(profile);
  },

  async importGithub() {
    if (state.gatewayMode === "api") {
      return profileRequest(GITHUB_IMPORT_API, "POST", {consent_version: state.consentVersion});
    }

    return buildDemoGithubImport(
      state.profile,
      DEMO_GITHUB_PAYLOAD,
    );
  },

  async resolveSuggestion(suggestion, decision) {
    if (state.gatewayMode === "api") {
      const id = suggestion.profile_field_suggestion_id;
      if (!Number.isSafeInteger(id) || id <= 0 || !["accept", "reject"].includes(decision)) {
        throw new Error("无效的建议操作。");
      }
      return profileRequest(`${PROFILE_API}/suggestions/${id}/${decision}`, "POST", {});
    }

    return resolveDemoSuggestion(suggestion, decision);
  },
};

function editableProfile(profile) {
  const payload = {};
  for (const key of ["display_name", "service_track", "preferred_languages", "operating_systems",
    "preferred_task_types", "max_code_difficulty", "max_setup_difficulty", "desired_skill_stretch", "skills"]) {
    payload[key] = clone(profile[key]);
  }
  payload.locks = {};
  for (const [field, metadata] of Object.entries(profile.field_metadata || {})) {
    if (Object.hasOwn(payload, field) || (field.startsWith("skills.") && Object.hasOwn(payload.skills, field.slice(7)))) {
      payload.locks[field] = Boolean(metadata.locked);
    }
  }
  return payload;
}

async function profileRequest(path, method = "GET", body) {
  const controller = new AbortController();
  // Collection may perform up to 12 bounded upstream calls.
  const timer = setTimeout(() => controller.abort(), method === "GET" ? 15000 : 150000);
  try {
    const response = await fetch(path, {method, credentials: "same-origin", cache: "no-store",
      headers: {Accept: "application/json", ...(body === undefined ? {} : {"Content-Type": "application/json"})},
      body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const hints = {401: "请先登录，或重新登录恢复会话。", 403: "请求被拒绝，请检查访问权限。",
        409: "建议已变化，请重新加载页面。", 422: "提交内容未通过校验。",
        429: "GitHub 请求限额已用完，请稍后重试。", 503: "服务或 GitHub 凭据未就绪；凭据失效时请重新登录。"};
      const detail = payload.error || {};
      const error = new Error(`${hints[response.status] || "请求失败。"} ${detail.message || ""}（HTTP ${response.status}${payload.request_id ? `，请求 ${payload.request_id}` : ""}）`);
      error.status = response.status;
      if (response.status === 401) {
        state.ready = false;
        state.profile = clone(DEFAULT_PROFILE);
        state.suggestions = [];
        state.githubImport = null;
        hydrateProfileForm(state.profile);
        renderSuggestions();
        renderGithubImport(null);
        showFormError(error.message);
        setText("profile-api-status", "需要登录");
      }
      throw error;
    }
    if (!payload || typeof payload !== "object" || !payload.api_version) throw new Error("服务返回了无效的 API 响应。");
    return payload;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时，操作结果尚未确认；请重新加载后检查再操作。");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function updateInteractionState() {
  for (const element of all("#profile-form input, #profile-form select, #profile-form button, #github-import-consent, .suggestion-card button")) {
    element.disabled = !state.ready || state.busy;
  }
  for (const button of all('.suggestion-card [data-action]')) {
    const id = Number(button.closest(".suggestion-card").dataset.suggestionId);
    const suggestion = state.suggestions.find(item => item.profile_field_suggestion_id === id);
    if (suggestion && ((button.dataset.action === "accept" && suggestion.blocked_reason) || suggestion.status !== "pending")) button.disabled = true;
  }
  const button = byId("github-import-button");
  if (button) button.disabled = !state.ready || state.busy || !byId("github-import-consent").checked;
}

function canChangeServerProfile() {
  if (!state.ready || state.busy) return false;
  if (state.gatewayMode === "api" && (!state.persisted || state.dirty)) {
    showFormError("请先保存手工画像，再导入或处理建议，以免覆盖未保存修改。");
    return false;
  }
  return true;
}


/* ================================================================
   Profile normalization
   ================================================================ */

function normalizeProfile(rawProfile) {
  const raw = asObject(rawProfile);
  const profile = clone(DEFAULT_PROFILE);

  profile.profile_key =
    typeof raw.profile_key === "string" && raw.profile_key.trim()
      ? raw.profile_key.trim()
      : profile.profile_key;

  profile.display_name =
    typeof raw.display_name === "string"
      ? raw.display_name.trim()
      : "";

  profile.service_track =
    raw.service_track === "growth" ? "growth" : "newcomer";

  profile.preferred_languages = uniqueStrings(
    asArray(raw.preferred_languages),
  );

  profile.operating_systems = uniqueStrings(
    asArray(raw.operating_systems),
  );

  profile.preferred_task_types = uniqueStrings(
    asArray(raw.preferred_task_types),
  );

  profile.max_code_difficulty = clampNumber(
    raw.max_code_difficulty,
    0,
    3,
    1,
  );

  profile.max_setup_difficulty = clampNumber(
    raw.max_setup_difficulty,
    0,
    3,
    2,
  );

  profile.desired_skill_stretch = clampNumber(
    raw.desired_skill_stretch,
    0,
    2,
    0,
  );

  profile.skills = {};

  for (const [skillName, level] of Object.entries(
    asObject(raw.skills),
  )) {
    const normalizedLevel = clampNumber(level, 0, 4, 0);

    profile.skills[skillName] = normalizedLevel;
  }

  profile.field_metadata = {};

  for (const [fieldName, metadata] of Object.entries(
    asObject(raw.field_metadata),
  )) {
    profile.field_metadata[fieldName] =
      normalizeFieldMetadata(metadata);
  }

  if (profile.preferred_languages.length === 0) {
    profile.preferred_languages = ["Python"];
  }

  if (profile.operating_systems.length === 0) {
    profile.operating_systems = ["windows"];
  }

  if (profile.preferred_task_types.length === 0) {
    profile.preferred_task_types = ["bug_fix", "testing"];
  }

  return profile;
}

function normalizeFieldMetadata(rawMetadata) {
  const metadata = asObject(rawMetadata);

  return {
    source: PROFILE_SOURCE_LABELS[metadata.source]
      ? metadata.source
      : "default",
    locked: Boolean(metadata.locked),
    observed_at:
      typeof metadata.observed_at === "string"
        ? metadata.observed_at
        : null,
    ...(typeof metadata.accepted_source === "string"
      ? { accepted_source: metadata.accepted_source }
      : {}),
    ...(typeof metadata.confidence === "number"
      ? { confidence: metadata.confidence }
      : {}),
    ...(Array.isArray(metadata.evidence)
      ? { evidence: clone(metadata.evidence) }
      : {}),
  };
}

function uniqueStrings(values) {
  const output = [];
  const seen = new Set();

  for (const value of values) {
    if (typeof value !== "string") {
      continue;
    }

    const normalized = value.trim();

    if (!normalized || seen.has(normalized)) {
      continue;
    }

    seen.add(normalized);
    output.push(normalized);
  }

  return output;
}


/* ================================================================
   Form hydration
   ================================================================ */

function hydrateProfileForm(profile) {
  byId("display-name").value = profile.display_name;
  byId("service-track").value = profile.service_track;

  setCheckedValues(
    'input[name="preferred_languages"]',
    profile.preferred_languages,
  );

  setCheckedValues(
    'input[name="operating_systems"]',
    profile.operating_systems,
  );

  setCheckedValues(
    'input[name="preferred_task_types"]',
    profile.preferred_task_types,
  );

  byId("max-code-difficulty").value =
    String(profile.max_code_difficulty);

  byId("max-setup-difficulty").value =
    String(profile.max_setup_difficulty);

  byId("desired-skill-stretch").value =
    String(profile.desired_skill_stretch);

  for (const skillName of SKILL_NAMES) {
    const select = document.querySelector(
      `[data-skill="${cssEscape(skillName)}"]`,
    );

    if (select) {
      select.value = String(profile.skills[skillName] || 0);
    }

    const lockInput = document.querySelector(
      `[data-lock-field="${cssEscape(`skills.${skillName}`)}"]`,
    );

    const metadata =
      profile.field_metadata[`skills.${skillName}`] || {};

    if (lockInput) {
      lockInput.checked = Boolean(metadata.locked);
    }

    updateSkillRowState(skillName);
  }

  renderProfileOverview(profile);
}

function setCheckedValues(selector, values) {
  const selected = new Set(values);

  for (const input of all(selector)) {
    input.checked = selected.has(input.value);
  }
}

function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === "function") {
    return window.CSS.escape(value);
  }

  return String(value).replace(
    /["\\]/g,
    "\\$&",
  );
}


/* ================================================================
   Form collection / validation
   ================================================================ */

function collectProfileFromForm() {
  const previous = state.profile;
  const observedAt = nowIso();

  const profile = {
    profile_key: previous.profile_key || "local-profile",
    display_name: byId("display-name").value.trim(),
    service_track: byId("service-track").value,
    preferred_languages: checkedValues(
      'input[name="preferred_languages"]',
    ),
    operating_systems: checkedValues(
      'input[name="operating_systems"]',
    ),
    preferred_task_types: checkedValues(
      'input[name="preferred_task_types"]',
    ),
    max_code_difficulty: Number(
      byId("max-code-difficulty").value,
    ),
    max_setup_difficulty: Number(
      byId("max-setup-difficulty").value,
    ),
    desired_skill_stretch: Number(
      byId("desired-skill-stretch").value,
    ),
    skills: clone(previous.skills),
    field_metadata: clone(
      previous.field_metadata || {},
    ),
  };

  for (const select of all("[data-skill]")) {
    const skillName = select.dataset.skill;
    const level = clampNumber(select.value, 0, 4, 0);

    const lockInput = document.querySelector(
      `[data-lock-field="${cssEscape(`skills.${skillName}`)}"]`,
    );

    const locked = Boolean(lockInput && lockInput.checked);

    // Zero is a real level for an existing/locked skill, not deletion.
    if (level > 0 || locked || Object.hasOwn(previous.skills, skillName)) {
      profile.skills[skillName] = level;
    }

    const prior = previous.field_metadata[`skills.${skillName}`];
    profile.field_metadata[`skills.${skillName}`] = prior && previous.skills[skillName] === level
      ? {...prior, locked}
      : {source: "user_input", locked, observed_at: observedAt};
  }

  const manualFields = [
    "display_name",
    "service_track",
    "preferred_languages",
    "operating_systems",
    "preferred_task_types",
    "max_code_difficulty",
    "max_setup_difficulty",
    "desired_skill_stretch",
  ];

  for (const fieldName of manualFields) {
    const previousMetadata =
      profile.field_metadata[fieldName] || {};

    profile.field_metadata[fieldName] = JSON.stringify(profile[fieldName]) === JSON.stringify(previous[fieldName])
      ? previousMetadata
      : {...previousMetadata, source: "user_input", locked: Boolean(previousMetadata.locked), observed_at: observedAt};
  }

  return profile;
}

function checkedValues(selector) {
  return all(`${selector}:checked`).map(
    (input) => input.value,
  );
}

function validateProfile(profile) {
  const errors = [];

  if (!profile.display_name) {
    errors.push("请填写显示名称。");
  }

  if (Array.from(profile.display_name).length > 60) {
    errors.push("显示名称不能超过 60 个字符。");
  }

  if (!["newcomer", "growth"].includes(profile.service_track)) {
    errors.push("贡献阶段无效。");
  }

  if (profile.preferred_languages.length === 0) {
    errors.push("至少选择一种偏好语言。");
  }

  if (profile.operating_systems.length === 0) {
    errors.push("至少选择一种可用操作系统。");
  }

  if (profile.preferred_task_types.length === 0) {
    errors.push("至少选择一种任务类型。");
  }

  if (
    profile.max_code_difficulty < 0
    || profile.max_code_difficulty > 3
  ) {
    errors.push("代码难度上限必须在 0–3 之间。");
  }

  if (
    profile.max_setup_difficulty < 0
    || profile.max_setup_difficulty > 3
  ) {
    errors.push("环境难度上限必须在 0–3 之间。");
  }

  if (
    profile.desired_skill_stretch < 0
    || profile.desired_skill_stretch > 2
  ) {
    errors.push("期望技能跨度必须在 0–2 之间。");
  }

  return errors;
}


/* ================================================================
   Profile overview rendering
   ================================================================ */

function renderProfileOverview(profile) {
  const displayName =
    profile.display_name || "尚未创建画像";

  setText("profile-display-name", displayName);

  const summaryLine = profile.display_name
    ? `${TRACK_LABELS[profile.service_track]}`
    : "可以从手工画像开始，不需要连接 GitHub。";

  setText("profile-summary-line", summaryLine);

  setText(
    "overview-track",
    TRACK_LABELS[profile.service_track]
      || profile.service_track,
  );

  setText(
    "overview-languages",
    profile.preferred_languages.length
      ? profile.preferred_languages.join(" · ")
      : "未设置",
  );

  setText(
    "overview-code-difficulty",
    String(profile.max_code_difficulty),
  );

  setText(
    "overview-setup-difficulty",
    String(profile.max_setup_difficulty),
  );

  const avatar = byId("profile-avatar");

  if (avatar) {
    avatar.textContent = initialsFor(displayName);
  }

  const badge = byId("profile-state-badge");

  if (badge) {
    badge.dataset.state = state.dirty ? "dirty" : "ready";
    badge.textContent = !state.ready ? "未加载" : state.dirty ? "有未保存修改"
      : state.gatewayMode === "api" && !state.persisted ? "尚未创建" : "画像已加载";
  }

  renderSkillMetadata(profile);
}

function initialsFor(displayName) {
  const name = String(displayName || "").trim();

  if (!name || name === "尚未创建画像") {
    return "OM";
  }

  const words = name.split(/\s+/).filter(Boolean);

  if (words.length >= 2) {
    return (
      words[0].slice(0, 1)
      + words[1].slice(0, 1)
    ).toUpperCase();
  }

  return name.slice(0, 2).toUpperCase();
}

function renderSkillMetadata(profile) {
  for (const skillName of SKILL_NAMES) {
    const metadata =
      profile.field_metadata[`skills.${skillName}`]
      || {
        source: "default",
        locked: false,
      };

    const sourceElement = document.querySelector(
      `[data-skill-source="${cssEscape(skillName)}"]`,
    );

    if (sourceElement) {
      sourceElement.dataset.source = metadata.source;
      sourceElement.textContent =
        PROFILE_SOURCE_LABELS[metadata.source]
        || metadata.source;
    }

    updateSkillRowState(skillName);
  }
}

function updateSkillRowState(skillName) {
  const row = document.querySelector(
    `[data-skill-row="${cssEscape(skillName)}"]`,
  );

  const lockInput = document.querySelector(
    `[data-lock-field="${cssEscape(`skills.${skillName}`)}"]`,
  );

  if (row) {
    row.dataset.locked = lockInput && lockInput.checked
      ? "true"
      : "false";
  }
}


/* ================================================================
   Form state
   ================================================================ */

function markProfileDirty() {
  state.dirty = true;

  const preview = collectProfileFromForm();
  renderProfileOverview(preview);

  setText(
    "profile-save-state",
    "有尚未保存的手工修改",
  );

  const badge = byId("profile-state-badge");

  if (badge) {
    badge.dataset.state = "dirty";
    badge.textContent = "有未保存修改";
  }
}

function clearFormError() {
  const errorElement = byId("profile-form-error");

  if (!errorElement) {
    return;
  }

  errorElement.hidden = true;
  errorElement.textContent = "";
}

function showFormError(message) {
  const errorElement = byId("profile-form-error");

  if (!errorElement) {
    return;
  }

  errorElement.hidden = false;
  errorElement.textContent = message;
}


/* ================================================================
   Manual profile save
   ================================================================ */

async function handleProfileSubmit(event) {
  event.preventDefault();
  if (!state.ready || state.busy) return;
  clearFormError();

  const profile = collectProfileFromForm();
  const errors = validateProfile(profile);

  if (errors.length > 0) {
    showFormError(errors.join(" "));
    setText(
      "profile-save-state",
      "画像校验未通过",
    );
    return;
  }

  const button = byId("save-profile-button");

  setBusy(button, true);
  state.busy = true;
  updateInteractionState();
  setText("profile-save-state", "正在保存画像…");

  try {
    const saved = await profileGateway.saveProfile(profile);

    state.profile = normalizeProfile(saved);
    state.dirty = false;

    hydrateProfileForm(state.profile);

    setText(
      "profile-save-state",
      state.gatewayMode === "api"
        ? "画像已保存"
        : "画像已保存到本地演示存储",
    );

    const badge = byId("profile-state-badge");

    if (badge) {
      badge.dataset.state = "saved";
      badge.textContent = "已保存";
    }
  } catch (error) {
    showFormError(
      error instanceof Error
        ? error.message
        : "画像保存失败。",
    );

    setText(
      "profile-save-state",
      "保存失败",
    );
  } finally {
    setBusy(button, false);
    state.busy = false;
    updateInteractionState();
  }
}


/* ================================================================
   GitHub import demo builder
   ================================================================ */

function buildDemoGithubImport(profile, githubPayload) {
  const publicRepositories = asArray(
    githubPayload.repositories,
  ).filter(
    (repository) =>
      repository
      && repository.private === false,
  );

  const activeRepositories = publicRepositories.filter(
    (repository) => repository.archived !== true,
  );

  const observedAt = githubPayload.observed_at;

  const recentRepositories = activeRepositories.filter(
    (repository) =>
      isRecentRepository(
        repository.last_contribution_at,
        observedAt,
        180,
      ),
  );

  const languageDistribution =
    buildLanguageDistribution(activeRepositories);

  const pathEvidence =
    collectPathEvidence(activeRepositories);

  const suggestions = buildDemoSuggestions(
    profile,
    {
      languageDistribution,
      pathEvidence,
      observedAt,
    },
  );

  return {
    schema_version: "github-profile-import-v0.1",
    login:
      githubPayload.user
      && typeof githubPayload.user.login === "string"
        ? githubPayload.user.login
        : "unknown",
    consent_version: githubPayload.consent_version,
    observed_at: observedAt,
    public_repository_count: publicRepositories.length,
    recent_repository_count: recentRepositories.length,
    language_distribution: languageDistribution,
    activity: aggregateActivity(activeRepositories),
    suggestions,
  };
}

function isRecentRepository(
  lastContributionAt,
  observedAt,
  days,
) {
  const last = Date.parse(lastContributionAt);
  const observed = Date.parse(observedAt);

  if (!Number.isFinite(last) || !Number.isFinite(observed)) {
    return false;
  }

  const windowMilliseconds =
    days * 24 * 60 * 60 * 1000;

  return observed - last <= windowMilliseconds;
}

function buildLanguageDistribution(repositories) {
  const totals = {};

  for (const repository of repositories) {
    for (const [language, bytes] of Object.entries(
      asObject(repository.languages),
    )) {
      const numericBytes = Number(bytes);

      if (!Number.isFinite(numericBytes) || numericBytes <= 0) {
        continue;
      }

      totals[language] =
        (totals[language] || 0) + numericBytes;
    }
  }

  const grandTotal = Object.values(totals).reduce(
    (sum, value) => sum + value,
    0,
  );

  if (grandTotal <= 0) {
    return [];
  }

  return Object.entries(totals)
    .map(([language, bytes]) => ({
      language,
      bytes,
      proportion: bytes / grandTotal,
      source: "github_weak_inference",
    }))
    .sort(
      (left, right) =>
        right.proportion - left.proportion,
    );
}

function collectPathEvidence(repositories) {
  const evidence = {
    testing: [],
    documentation: [],
    build_tooling: [],
  };

  for (const repository of repositories) {
    for (const rawPath of asArray(
      repository.contributed_paths,
    )) {
      if (typeof rawPath !== "string") {
        continue;
      }

      const path = rawPath.trim();

      if (!path) {
        continue;
      }

      const lower = path.toLowerCase();
      const item = `${repository.full_name}: ${path}`;

      if (
        /(^|\/)(tests?|test_[^/]+|[^/]+_test\.[^/]+)(\/|$)/.test(
          lower,
        )
        || lower.includes("test")
      ) {
        evidence.testing.push(item);
      }

      if (
        lower.startsWith("docs/")
        || lower.includes("/docs/")
        || lower.endsWith("readme.md")
        || lower.endsWith("readme.rst")
      ) {
        evidence.documentation.push(item);
      }

      if (
        lower === "pyproject.toml"
        || lower === "package.json"
        || lower === "tox.ini"
        || lower.startsWith(".github/workflows/")
        || lower.includes("/.github/workflows/")
      ) {
        evidence.build_tooling.push(item);
      }
    }
  }

  for (const key of Object.keys(evidence)) {
    evidence[key] = uniqueStrings(evidence[key]);
  }

  return evidence;
}

function aggregateActivity(repositories) {
  const activity = {
    commits: 0,
    pull_requests: 0,
    issues: 0,
    reviews: 0,
  };

  for (const repository of repositories) {
    const contributions = asObject(
      repository.contributions,
    );

    for (const key of Object.keys(activity)) {
      const value = Number(contributions[key]);

      if (Number.isFinite(value) && value > 0) {
        activity[key] += value;
      }
    }
  }

  return activity;
}

function buildDemoSuggestions(
  profile,
  {
    languageDistribution,
    pathEvidence,
    observedAt,
  },
) {
  const suggestions = [];

  const explicitSkills = [
    ["testing", pathEvidence.testing],
    ["documentation", pathEvidence.documentation],
    ["build_tooling", pathEvidence.build_tooling],
  ];

  for (const [skillName, evidence] of explicitSkills) {
    if (evidence.length === 0) {
      continue;
    }

    const currentLevel = Number(
      profile.skills[skillName] || 0,
    );

    /*
     * GitHub evidence never directly claims high proficiency.
     * For the deterministic page demo the suggested baseline is 1.
     */
    const proposedLevel = Math.max(currentLevel, 1);

    if (proposedLevel === currentLevel) {
      continue;
    }

    const fieldName = `skills.${skillName}`;
    const metadata =
      profile.field_metadata[fieldName] || {};

    suggestions.push({
      profile_field_suggestion_id:
        stableSuggestionId(fieldName),
      field_name: fieldName,
      current_value: currentLevel,
      proposed_value: proposedLevel,
      suggestion_source:
        "github_explicit_evidence",
      confidence: 0.82,
      evidence,
      observed_at: observedAt,
      status: "pending",
      blocked_reason: metadata.locked
        ? "该字段已由用户锁定，GitHub 建议不能覆盖。"
        : null,
    });
  }

  /*
   * Repository language is deliberately treated as weak evidence.
   * Only suggest a missing language skill at level 1.
   */
  for (const item of languageDistribution) {
    if (item.proportion < 0.20) {
      continue;
    }

    if (!SKILL_NAMES.includes(item.language)) {
      continue;
    }

    const currentLevel = Number(
      profile.skills[item.language] || 0,
    );

    if (currentLevel > 0) {
      continue;
    }

    const fieldName = `skills.${item.language}`;
    const metadata =
      profile.field_metadata[fieldName] || {};

    suggestions.push({
      profile_field_suggestion_id:
        stableSuggestionId(fieldName),
      field_name: fieldName,
      current_value: currentLevel,
      proposed_value: 1,
      suggestion_source:
        "github_weak_inference",
      confidence: Math.min(
        0.70,
        Math.max(0.50, item.proportion),
      ),
      evidence: [
        `${item.language} 占公开仓库语言字节的 ${formatPercent(
          item.proportion,
        )}`,
      ],
      observed_at: observedAt,
      status: "pending",
      blocked_reason: metadata.locked
        ? "该字段已由用户锁定，GitHub 建议不能覆盖。"
        : null,
    });
  }

  return deduplicateSuggestions(suggestions);
}

function stableSuggestionId(fieldName) {
  let hash = 0;

  for (const character of fieldName) {
    hash = (
      (hash * 31)
      + character.charCodeAt(0)
    ) >>> 0;
  }

  return hash;
}

function deduplicateSuggestions(suggestions) {
  const byField = new Map();

  for (const suggestion of suggestions) {
    if (!byField.has(suggestion.field_name)) {
      byField.set(
        suggestion.field_name,
        suggestion,
      );
    }
  }

  return Array.from(byField.values()).sort(
    (left, right) =>
      left.field_name.localeCompare(
        right.field_name,
      ),
  );
}


/* ================================================================
   GitHub import interaction
   ================================================================ */

function handleConsentChange() {
  const consent = byId("github-import-consent");
  const button = byId("github-import-button");

  if (!consent || !button) {
    return;
  }

  updateInteractionState();

  setText(
    "github-import-status",
    consent.checked
      ? "已获得本次公开数据导入同意。"
      : "勾选同意后可以开始导入。",
  );
}

async function handleGithubImport() {
  if (!canChangeServerProfile()) return;
  const consent = byId("github-import-consent");

  if (!consent || !consent.checked) {
    setGithubImportStatus(
      "请先明确同意使用 GitHub 公开数据。",
      "error",
    );
    return;
  }

  const button = byId("github-import-button");
  setBusy(button, true);
  state.busy = true;
  updateInteractionState();

  setGithubImportStatus(
    "正在生成 GitHub 画像建议…",
    "loading",
  );

  try {
    const result = await profileGateway.importGithub();

    const normalized = normalizeGithubImportResponse(
      result,
    );

    state.githubImport = normalized.githubImport;
    state.suggestions = normalized.suggestions;

    if (state.gatewayMode === "demo") {
      saveLocalImport(state.githubImport);
      saveLocalSuggestions(state.suggestions);
    }

    renderGithubImport(state.githubImport);
    renderSuggestions();

    setGithubImportStatus(
      state.suggestions.length
        ? `导入完成，生成 ${state.suggestions.length} 条画像建议。`
        : "导入完成，本次没有需要调整的画像字段。",
      "success",
    );
  } catch (error) {
    setGithubImportStatus(
      error instanceof Error
        ? error.message
        : "GitHub 画像导入失败。",
      "error",
    );
  } finally {
    setBusy(button, false);
    state.busy = false;
    updateInteractionState();
  }
}

function normalizeGithubImportResponse(rawResult) {
  const raw = asObject(rawResult);

  /*
   * Supports both the local demo object and the ProfileService response:
   * {
   *   github_import,
   *   merge_preview,
   *   suggestions
   * }
   */
  const githubImport = raw.github_import
    ? normalizeGithubImport(raw.github_import)
    : normalizeGithubImport(raw);

  let suggestions = [];

  if (Array.isArray(raw.suggestions)) {
    suggestions = raw.suggestions;
  } else if (
    raw.merge_preview
    && Array.isArray(raw.merge_preview.suggestions)
  ) {
    suggestions = raw.merge_preview.suggestions;
  } else if (Array.isArray(raw.suggestions)) {
    suggestions = raw.suggestions;
  } else if (Array.isArray(githubImport.suggestions)) {
    suggestions = githubImport.suggestions;
  }

  suggestions = suggestions.map(
    (suggestion, index) =>
      normalizeSuggestion(suggestion, index),
  );

  return {
    githubImport,
    suggestions,
  };
}

function normalizeGithubImport(rawImport) {
  const raw = asObject(rawImport);

  return {
    schema_version:
      raw.schema_version
      || raw.import_version
      || "github-profile-import-v0.1",

    login:
      raw.login
      || raw.github_login
      || (
        raw.user
        && raw.user.login
      )
      || "—",

    consent_version:
      raw.consent_version
      || "profile-import-consent-v0.1",

    observed_at:
      raw.observed_at
      || null,

    public_repository_count:
      Number(
        raw.public_repository_count
        ?? raw.public_repo_count
        ?? 0,
      ),

    recent_repository_count:
      Number(
        raw.recent_repository_count
        ?? raw.recent_active_repository_count
        ?? raw.recent_repo_count
        ?? 0,
      ),

    language_distribution:
      normalizeLanguageDistribution(
        raw.language_distribution
        || raw.languages
        || [],
      ),

    activity: asObject(raw.activity || raw.activity_summary),

    suggestions: asArray(raw.suggestions),
  };
}

function normalizeLanguageDistribution(rawLanguages) {
  if (Array.isArray(rawLanguages)) {
    return rawLanguages
      .map((item) => {
        if (!item || typeof item !== "object") {
          return null;
        }

        const language =
          item.language
          || item.name;

        const proportion = Number(
          item.proportion
          ?? item.share
          ?? 0,
        );

        if (
          typeof language !== "string"
          || !Number.isFinite(proportion)
        ) {
          return null;
        }

        return {
          language,
          proportion,
        };
      })
      .filter(Boolean)
      .sort(
        (left, right) =>
          right.proportion - left.proportion,
      );
  }

  if (rawLanguages && typeof rawLanguages === "object") {
    const entries = Object.entries(rawLanguages)
      .map(([language, proportion]) => ({
        language,
        proportion: Number(proportion),
      }))
      .filter(
        (item) =>
          Number.isFinite(item.proportion),
      );

    const total = entries.reduce(
      (sum, item) => sum + item.proportion,
      0,
    );

    if (total > 1.0001) {
      return entries
        .map((item) => ({
          ...item,
          proportion: item.proportion / total,
        }))
        .sort(
          (left, right) =>
            right.proportion - left.proportion,
        );
    }

    return entries.sort(
      (left, right) =>
        right.proportion - left.proportion,
    );
  }

  return [];
}

function setGithubImportStatus(message, stateName) {
  const element = byId("github-import-status");

  if (!element) {
    return;
  }

  element.textContent = message;
  element.dataset.state = stateName;
}


/* ================================================================
   GitHub import rendering
   ================================================================ */

function renderGithubImport(githubImport) {
  const importState = byId("github-import-state");

  if (!githubImport) {
    if (importState) {
      importState.dataset.state = "empty";
      importState.textContent = "暂无数据";
    }

    setText("github-summary-login", "—");
    setText("github-summary-public-repos", "—");
    setText("github-summary-recent-repos", "—");
    setText("github-summary-observed-at", "—");
    setText("github-connection-summary", "本次页面会话暂无导入摘要；已保存的建议从服务器加载，不代表没有历史导入。");

    renderLanguageDistribution([]);
    return;
  }

  if (importState) {
    importState.dataset.state = "ready";
    importState.textContent = "已导入";
  }

  setText(
    "github-connection-summary",
    `最近导入 GitHub 用户 ${githubImport.login} 的公开贡献数据。`,
  );

  setText(
    "github-summary-login",
    githubImport.login || "—",
  );

  setText(
    "github-summary-public-repos",
    String(githubImport.public_repository_count || 0),
  );

  setText(
    "github-summary-recent-repos",
    String(githubImport.recent_repository_count || 0),
  );

  setText(
    "github-summary-observed-at",
    formatDateTime(githubImport.observed_at),
  );

  renderLanguageDistribution(
    githubImport.language_distribution,
  );
}

function renderLanguageDistribution(distribution) {
  const container = byId("github-language-list");

  if (!container) {
    return;
  }

  container.replaceChildren();

  if (!distribution || distribution.length === 0) {
    const empty = document.createElement("p");
    empty.className = "profile-empty-copy";
    empty.textContent =
      "导入后显示语言占比和对应证据。";

    container.appendChild(empty);
    return;
  }

  for (const item of distribution.slice(0, 6)) {
    const row = document.createElement("div");
    row.className = "github-language-item";

    const name = document.createElement("span");
    name.className = "github-language-name";
    name.textContent = item.language;

    const bar = document.createElement("div");
    bar.className = "github-language-bar";

    const fill = document.createElement("span");
    fill.style.width =
      `${Math.max(
        0,
        Math.min(100, item.proportion * 100),
      )}%`;

    bar.appendChild(fill);

    const percent = document.createElement("span");
    percent.className = "github-language-percent";
    percent.textContent = formatPercent(
      item.proportion,
    );

    row.append(name, bar, percent);
    container.appendChild(row);
  }
}


/* ================================================================
   Suggestions
   ================================================================ */

function normalizeSuggestion(rawSuggestion, index = 0) {
  const raw = asObject(rawSuggestion);

  const id = Number(
    raw.profile_field_suggestion_id
    ?? raw.suggestion_id
    ?? index + 1,
  );

  return {
    profile_field_suggestion_id:
      Number.isFinite(id) ? id : index + 1,

    field_name:
      typeof raw.field_name === "string"
        ? raw.field_name
        : `field_${index + 1}`,

    current_value:
      raw.current_value !== undefined
        ? raw.current_value
        : raw.current_value_json,

    proposed_value:
      raw.proposed_value !== undefined
        ? raw.proposed_value
        : raw.proposed_value_json,

    suggestion_source:
      PROFILE_SOURCE_LABELS[raw.suggestion_source]
        ? raw.suggestion_source
        : "github_weak_inference",

    confidence:
      clampNumber(raw.confidence, 0, 1, 0),

    evidence:
      normalizeEvidence(
        raw.evidence
        ?? raw.evidence_json
        ?? [],
      ),

    observed_at:
      typeof raw.observed_at === "string"
        ? raw.observed_at
        : null,

    status:
      ["pending", "accepted", "rejected"].includes(
        raw.status,
      )
        ? raw.status
        : "pending",

    blocked_reason:
      typeof raw.blocked_reason === "string"
      && raw.blocked_reason.trim()
        ? raw.blocked_reason.trim()
        : null,
  };
}

function normalizeEvidence(rawEvidence) {
  if (Array.isArray(rawEvidence)) {
    return rawEvidence.map(formatEvidenceItem);
  }

  if (typeof rawEvidence === "string") {
    const parsed = safeJsonParse(
      rawEvidence,
      null,
    );

    if (Array.isArray(parsed)) {
      return parsed.map(formatEvidenceItem);
    }

    return [rawEvidence];
  }

  if (rawEvidence && typeof rawEvidence === "object") {
    return [formatEvidenceItem(rawEvidence)];
  }

  return [];
}

function formatEvidenceItem(item) {
  if (typeof item === "string") {
    return item;
  }

  if (!item || typeof item !== "object") {
    return String(item);
  }

  // Keep repository/path details as well as the source tag. Rendering uses textContent.
  return Object.entries(item).map(([key, value]) =>
    `${key}: ${typeof value === "object" ? JSON.stringify(value) : String(value)}`
  ).join(" · ");
}

function renderSuggestions() {
  const container = byId("suggestion-list");
  const empty = byId("suggestion-empty-state");
  const filter = byId("suggestion-status-filter");

  if (!container || !empty) {
    return;
  }

  for (const existing of all(
    ".suggestion-card",
    container,
  )) {
    existing.remove();
  }

  const filterValue = filter
    ? filter.value
    : "pending";

  const visibleSuggestions = state.suggestions.filter(
    (suggestion) =>
      filterValue === "all"
      || suggestion.status === filterValue,
  );

  const pendingCount = state.suggestions.filter(
    (suggestion) =>
      suggestion.status === "pending",
  ).length;

  setText(
    "pending-suggestion-count",
    `${pendingCount} 条待处理建议`,
  );

  setText(
    "suggestion-toolbar-note",
    state.suggestions.length
      ? `共 ${state.suggestions.length} 条 GitHub 画像建议`
      : "GitHub 导入后会在这里显示建议。",
  );

  empty.hidden = visibleSuggestions.length > 0;

  if (visibleSuggestions.length === 0) {
    const heading = empty.querySelector("h3");
    const copy = empty.querySelector("p");

    if (filterValue === "pending" && state.suggestions.length > 0) {
      heading.textContent = "没有待处理建议";
      copy.textContent =
        "当前 GitHub 画像建议都已经处理。";
    } else if (
      filterValue !== "all"
      && state.suggestions.length > 0
    ) {
      heading.textContent =
        `没有${statusLabel(filterValue)}建议`;

      copy.textContent =
        "可以切换筛选条件查看其他建议。";
    } else {
      heading.textContent =
        "还没有 GitHub 画像建议";

      copy.textContent =
        "你可以继续使用手工画像；GitHub 导入不是使用推荐功能的前置条件。";
    }

    return;
  }

  const template = byId("suggestion-card-template");

  if (!template) {
    return;
  }

  for (const suggestion of visibleSuggestions) {
    const fragment =
      template.content.cloneNode(true);

    const card = fragment.querySelector(
      ".suggestion-card",
    );

    card.dataset.suggestionId =
      String(
        suggestion.profile_field_suggestion_id,
      );

    card.dataset.status = suggestion.status;

    const fieldNameElement =
      card.querySelector(".suggestion-field-name");

    fieldNameElement.textContent =
      suggestion.field_name;

    const title =
      card.querySelector(".suggestion-title");

    title.textContent =
      suggestionTitle(suggestion.field_name);

    const source =
      card.querySelector(".suggestion-source");

    source.dataset.source =
      suggestion.suggestion_source;

    source.textContent =
      PROFILE_SOURCE_LABELS[
        suggestion.suggestion_source
      ] || suggestion.suggestion_source;

    card.querySelector(
      ".suggestion-confidence",
    ).textContent =
      `置信度 ${formatPercent(
        suggestion.confidence,
      )}`;

    card.querySelector(
      ".suggestion-current-value",
    ).textContent =
      formatSuggestionValue(
        suggestion.current_value,
      );

    card.querySelector(
      ".suggestion-proposed-value",
    ).textContent =
      formatSuggestionValue(
        suggestion.proposed_value,
      );

    card.querySelector(
      ".suggestion-observed-at",
    ).textContent =
      formatDateTime(
        suggestion.observed_at,
      );

    const evidenceList =
      card.querySelector(
        ".suggestion-evidence-list",
      );

    const evidence =
      suggestion.evidence.length
        ? suggestion.evidence
        : ["暂无额外证据描述。"];

    for (const item of evidence) {
      const li = document.createElement("li");
      li.textContent = item;
      evidenceList.appendChild(li);
    }

    const blockedReason =
      card.querySelector(
        ".suggestion-blocked-reason",
      );

    if (suggestion.blocked_reason) {
      blockedReason.hidden = false;
      blockedReason.textContent = ({higher_priority_current_source: "当前手工填写或确认值优先，不能用此建议覆盖。",
        locked: "该字段已锁定，不能接受覆盖建议。"})[suggestion.blocked_reason] || suggestion.blocked_reason;
    }

    const acceptButton =
      card.querySelector(
        '[data-action="accept"]',
      );

    const rejectButton =
      card.querySelector(
        '[data-action="reject"]',
      );

    if (suggestion.status !== "pending") {
      acceptButton.disabled = true;
      rejectButton.disabled = true;

      acceptButton.textContent =
        suggestion.status === "accepted"
          ? "已接受"
          : "接受建议";

      rejectButton.textContent =
        suggestion.status === "rejected"
          ? "已拒绝"
          : "拒绝";
    } else if (suggestion.blocked_reason) {
      acceptButton.disabled = true;
      acceptButton.textContent = "不可覆盖";
    }

    container.appendChild(fragment);
  }
}

function suggestionTitle(fieldName) {
  if (fieldName.startsWith("skills.")) {
    const skillName =
      fieldName.slice("skills.".length);

    return `${skillDisplayName(skillName)} 技能建议`;
  }

  const labels = {
    preferred_languages: "偏好语言建议",
    operating_systems: "操作系统建议",
    preferred_task_types: "任务类型建议",
    max_code_difficulty: "代码难度建议",
    max_setup_difficulty: "环境难度建议",
    desired_skill_stretch: "技能跨度建议",
  };

  return labels[fieldName] || "GitHub 画像建议";
}

function skillDisplayName(skillName) {
  const labels = {
    testing: "Testing",
    git: "Git",
    documentation: "Documentation",
    build_tooling: "Build Tooling",
  };

  return labels[skillName] || skillName;
}

function statusLabel(status) {
  const labels = {
    pending: "待处理",
    accepted: "已接受",
    rejected: "已拒绝",
  };

  return labels[status] || "";
}

function formatSuggestionValue(value) {
  if (Array.isArray(value)) {
    return value.length
      ? value.join(" · ")
      : "空";
  }

  if (
    value
    && typeof value === "object"
  ) {
    return JSON.stringify(value);
  }

  if (
    value === null
    || value === undefined
    || value === ""
  ) {
    return "未设置";
  }

  return String(value);
}


/* ================================================================
   Suggestion decisions
   ================================================================ */

async function handleSuggestionAction(event) {
  if (!canChangeServerProfile()) return;
  const button = event.target.closest(
    "[data-action]",
  );

  if (!button) {
    return;
  }

  const card = button.closest(
    ".suggestion-card",
  );

  if (!card) {
    return;
  }

  const suggestionId = Number(
    card.dataset.suggestionId,
  );

  const suggestion = state.suggestions.find(
    (item) =>
      item.profile_field_suggestion_id
      === suggestionId,
  );

  if (!suggestion) {
    return;
  }

  const action = button.dataset.action;

  if (!["accept", "reject"].includes(action)) {
    return;
  }

  if (
    action === "accept"
    && suggestion.blocked_reason
  ) {
    return;
  }

  setSuggestionCardBusy(card, true);
  state.busy = true;
  updateInteractionState();

  try {
    const result =
      await profileGateway.resolveSuggestion(
        suggestion,
        action,
      );

    state.profile = normalizeProfile(
      result.profile,
    );

    state.suggestions =
      result.suggestions.map(
        (item, index) =>
          normalizeSuggestion(item, index),
      );

    if (state.gatewayMode === "demo") {
      saveLocalProfile(state.profile);
      saveLocalSuggestions(state.suggestions);
    }

    hydrateProfileForm(state.profile);
    renderSuggestions();

    setText(
      "profile-save-state",
      action === "accept"
        ? "已接受 GitHub 建议并更新画像"
        : "已拒绝 GitHub 建议，画像保持不变",
    );
  } catch (error) {
    const blockedReason =
      card.querySelector(
        ".suggestion-blocked-reason",
      );

    if (blockedReason) {
      blockedReason.hidden = false;
      blockedReason.textContent =
        error instanceof Error
          ? error.message
          : "建议处理失败。";
    }
  } finally {
    setSuggestionCardBusy(card, false);
    state.busy = false;
    updateInteractionState();
  }
}

function resolveDemoSuggestion(
  suggestion,
  decision,
) {
  if (suggestion.status !== "pending") {
    throw new Error("这条建议已经处理。");
  }

  if (
    decision === "accept"
    && suggestion.blocked_reason
  ) {
    throw new Error(suggestion.blocked_reason);
  }

  const updatedProfile = clone(state.profile);

  const updatedSuggestions =
    state.suggestions.map(
      (item) => clone(item),
    );

  const target = updatedSuggestions.find(
    (item) =>
      item.profile_field_suggestion_id
      === suggestion.profile_field_suggestion_id,
  );

  if (!target) {
    throw new Error("未找到对应的画像建议。");
  }

  if (decision === "accept") {
    applySuggestionToProfile(
      updatedProfile,
      target,
    );

    target.status = "accepted";
  } else if (decision === "reject") {
    target.status = "rejected";
  } else {
    throw new Error("不支持的建议处理方式。");
  }

  return {
    profile: updatedProfile,
    suggestions: updatedSuggestions,
  };
}

function applySuggestionToProfile(
  profile,
  suggestion,
) {
  const fieldName = suggestion.field_name;

  if (fieldName.startsWith("skills.")) {
    const skillName =
      fieldName.slice("skills.".length);

    const proposedLevel = clampNumber(
      suggestion.proposed_value,
      0,
      4,
      0,
    );

    if (proposedLevel > 0) {
      profile.skills[skillName] =
        proposedLevel;
    } else {
      delete profile.skills[skillName];
    }
  } else if (
    Object.prototype.hasOwnProperty.call(
      profile,
      fieldName,
    )
  ) {
    profile[fieldName] =
      clone(suggestion.proposed_value);
  } else {
    throw new Error(
      `页面暂不支持应用字段 ${fieldName}。`,
    );
  }

  profile.field_metadata[fieldName] = {
    source: "user_confirmed",
    locked: false,
    observed_at:
      suggestion.observed_at || nowIso(),
    accepted_source:
      suggestion.suggestion_source,
    confidence:
      suggestion.confidence,
    evidence:
      clone(suggestion.evidence),
  };
}

function setSuggestionCardBusy(card, busy) {
  card.classList.toggle(
    "profile-is-busy",
    busy,
  );

  for (const button of all(
    "button",
    card,
  )) {
    button.disabled = busy;
  }
}


/* ================================================================
   Formatting
   ================================================================ */

function formatPercent(value) {
  const numeric = Number(value);

  if (!Number.isFinite(numeric)) {
    return "—";
  }

  return `${Math.round(numeric * 100)}%`;
}

function formatDateTime(value) {
  if (!value) {
    return "—";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  return new Intl.DateTimeFormat(
    "zh-CN",
    {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    },
  ).format(date);
}

function setBusy(element, busy) {
  if (!element) {
    return;
  }

  element.disabled = busy;
  element.classList.toggle(
    "profile-is-busy",
    busy,
  );
}


/* ================================================================
   Event wiring
   ================================================================ */

function bindEvents() {
  const form = byId("profile-form");

  if (form) {
    form.addEventListener(
      "submit",
      handleProfileSubmit,
    );

    form.addEventListener(
      "input",
      (event) => {
        clearFormError();

        if (
          event.target
          && event.target.matches(
            "[data-lock-field]",
          )
        ) {
          const fieldName =
            event.target.dataset.lockField;

          if (
            fieldName
            && fieldName.startsWith("skills.")
          ) {
            updateSkillRowState(
              fieldName.slice("skills.".length),
            );
          }
        }

        markProfileDirty();
      },
    );

    form.addEventListener(
      "change",
      () => {
        clearFormError();
        markProfileDirty();
      },
    );
  }

  const consent = byId(
    "github-import-consent",
  );

  if (consent) {
    consent.addEventListener(
      "change",
      handleConsentChange,
    );
  }

  const importButton = byId(
    "github-import-button",
  );

  if (importButton) {
    importButton.addEventListener(
      "click",
      handleGithubImport,
    );
  }

  const filter = byId(
    "suggestion-status-filter",
  );

  if (filter) {
    filter.addEventListener(
      "change",
      () => { renderSuggestions(); updateInteractionState(); },
    );
  }

  const suggestionList = byId(
    "suggestion-list",
  );

  if (suggestionList) {
    suggestionList.addEventListener(
      "click",
      handleSuggestionAction,
    );
  }
}


/* ================================================================
   Initial state
   ================================================================ */

function renderGatewayStatus() {
  const status = byId("profile-api-status");

  if (!status) {
    return;
  }

  if (state.gatewayMode === "api") {
    status.classList.add("online");
    status.classList.remove("offline");
    status.textContent = "画像服务已连接";
  } else {
    status.classList.remove("online", "offline");
    status.textContent = "本地演示模式";
  }
}

async function initializeProfilePage() {
  bindEvents();
  updateInteractionState();
  setText("profile-mode-note", state.gatewayMode === "api"
    ? "真实服务模式：画像和建议保存在当前登录账户下，不写入浏览器演示存储。"
    : "演示模式：使用固定样例和浏览器本地存储，不连接画像 API，也不读取真实 GitHub 数据。");

  setText(
    "profile-save-state",
    "正在加载画像…",
  );

  try {
    state.profile = normalizeProfile(
      await profileGateway.loadProfile(),
    );

    state.dirty = false;
    state.ready = true;

    hydrateProfileForm(state.profile);
    renderGatewayStatus();
    renderGithubImport(state.githubImport);
    renderSuggestions();

    setText(
      "profile-save-state",
      state.profile.display_name
        ? "画像已加载"
        : "可以从手工画像开始",
    );

    handleConsentChange();
  } catch (error) {
    const status = byId(
      "profile-api-status",
    );

    if (status) {
      status.classList.add("offline");
      status.textContent = "画像加载失败";
    }

    showFormError(
      error instanceof Error
        ? error.message
        : "画像初始化失败。",
    );

    state.profile = clone(DEFAULT_PROFILE);
    hydrateProfileForm(state.profile);
    renderSuggestions();
    setText("profile-save-state", "未加载，不能保存；请登录或重新加载页面。");
  } finally {
    updateInteractionState();
  }
}

document.addEventListener(
  "DOMContentLoaded",
  initializeProfilePage,
);
