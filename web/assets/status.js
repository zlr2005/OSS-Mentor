"use strict";

(function () {
  const ui = window.OMUI;
  const elements = {
    message: document.getElementById("status-message"),
    updated: document.getElementById("status-updated"),
    statusPill: document.getElementById("status-pill"),
    statusGrid: document.getElementById("status-grid"),
    qualityGrid: document.getElementById("quality-grid"),
    feedbackGrid: document.getElementById("feedback-grid"),
    runBanner: document.getElementById("run-banner"),
    runGrid: document.getElementById("run-grid"),
    variantChips: document.getElementById("variant-chips"),
    warnings: document.getElementById("run-warnings"),
    runLink: document.getElementById("run-detail-link"),
    matrix: document.getElementById("status-matrix"),
  };
  const requestedRunId = new URLSearchParams(window.location.search).get("run_id") || "";
  const storedBatch = ui.readBatch();
  const runId = requestedRunId || storedBatch?.run_id || "";

  const variants = [
    ["coder_original", "原版 CODER baseline；未接入时不能使用此标识。"],
    ["coder_profile", "仅新增增强画像的模型变体。"],
    ["coder_recent", "仅新增近期行为/当前意图的模型变体。"],
    ["coder_combined", "增强画像 + 近期行为的组合变体。"],
    ["rule_legacy", "既有规则引擎，仅作为兼容/诊断模式。"],
  ];
  const matrices = [
    ["loading", "加载中", "pending", "推荐请求已发出，数据层尚未返回", "骨架卡片，不显示任何分数", "等待 2–5 秒；超过 15 秒查看系统状态"],
    ["empty", "空结果", "ok: 0", "筛选过窄或画像与可领取 Issue 没有交集", "空状态面板，明确说明没有匹配结果", "放宽语言、任务类型或难度上限"],
    ["no-profile", "未完成画像", "profile: incomplete", "已登录但画像缺少必要字段", "提示先完成画像，不返回模型推荐", "前往个人信息页补齐画像"],
    ["model-503", "模型不可用", "503", "模型服务不可达或版本未就绪", "明确显示模型不可用，不用规则结果冒充模型", "查看系统状态；规则模式只能由用户显式选择"],
    ["upstream", "上游失败", "upstream: error", "GitHub API 限流或超时，无法核验可用性", "不展示未核验的推荐，说明无法确认状态", "稍后重试，或以 GitHub 原文为准"],
    ["stale", "数据过期", "dataset: stale", "候选快照超过复核窗口", "列表可展示，但可用性标签标记待复核", "提交前打开 GitHub 原文确认"],
    ["stale-preview", "过期预览", "409 preview_stale", "确认画像或问卷预览时版本已经变化", "明确提示预览过期，不写入旧回答", "重新生成预览并再次确认"],
    ["version-mismatch", "版本不匹配", "model: version_mismatch", "模型权重、配置与输入模式不一致", "拒绝加载并返回明确原因", "由 D 重新登记模型或回退到已登记版本"],
    ["migration-failure", "数据库迁移失败", "database: migration_failed", "迁移执行失败或 schema 版本不匹配", "服务返回 503，不返回半成品推荐", "检查迁移日志并恢复数据库"],
  ];

  function showMessage(code, title, body) {
    elements.message.hidden = false;
    elements.message.innerHTML = `<p><span class="mono">${ui.escapeHtml(code)}</span> <b>${ui.escapeHtml(title)}</b> ${ui.escapeHtml(body)}</p>`;
  }

  function kpi(label, value, sub = "") {
    return `<div class="status-kpi"><dt>${ui.escapeHtml(label)}</dt><dd>${ui.escapeHtml(String(value ?? "—"))}${sub ? `<span class="sub"> ${ui.escapeHtml(sub)}</span>` : ""}</dd></div>`;
  }

  function percent(value) {
    const number = Number(value);
    return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : "—";
  }

  function formatTimestamp(value) {
    if (!value) return "未记录";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const pad = (part) => String(part).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function renderStatus(status) {
    elements.statusGrid.innerHTML = [
      kpi("数据库", status.database_ready ? "ready" : "not ready", status.database_error || ""),
      kpi("仓库", status.repository_count),
      kpi("候选任务", status.candidate_count),
      kpi("可推荐任务", status.eligible_count),
      kpi("新人友好", status.newcomer_count),
      kpi("最后同步", formatTimestamp(status.last_sync_at)),
    ].join("");
    elements.qualityGrid.innerHTML = [
      kpi("任务类型识别率", percent(status.type_identification_rate), `${status.type_identified_count ?? "—"} / ${status.eligible_count ?? "—"}`),
      kpi("技能要求覆盖率", percent(status.skill_coverage_rate), `${status.skill_coverage_count ?? "—"} / ${status.eligible_count ?? "—"}`),
      kpi("已提取特征", status.features_extracted_count, `可匹配 ${status.matchable_count ?? "—"}`),
    ].join("");
    if (elements.statusPill) {
      elements.statusPill.textContent = status.database_ready ? "ok" : "degraded";
      elements.statusPill.className = `pill ${status.database_ready ? "pill--ok" : "pill--danger"}`;
    }
    if (elements.updated) {
      elements.updated.textContent = `读取于 ${new Date().toLocaleString("zh-CN")}`;
    }
    if (status.model) renderVariantChips(status.model);
    if (!status.database_ready) {
      showMessage(
        status.database_error || "database_not_ready",
        "数据库未就绪",
        "推荐与画像接口会返回 503；请先初始化独立数据库或修复迁移。"
      );
    }
  }

  function renderFeedback(summary) {
    const current = summary?.summary?.current || summary?.current || {};
    elements.feedbackGrid.innerHTML = [
      kpi("反馈总量", current.total ?? 0),
      kpi("感兴趣", current.interested ?? 0),
      kpi("不适合", current.not_suitable ?? 0),
      kpi("已开始", current.started ?? 0),
      kpi("已完成", current.completed ?? 0),
    ].join("");
  }

  function renderVariantChips(model) {
    elements.variantChips.innerHTML = variants.map(([variant, description]) => `
      <span class="variant ${model.variant === variant ? "is-active" : ""}">
        <span class="v-name">${ui.escapeHtml(variant)}${model.variant === variant ? " · 当前" : ""}</span>
        <span class="v-desc">${ui.escapeHtml(description)}</span>
      </span>`).join("");
  }

  function renderRun(run, fallbackModel) {
    const model = run?.model || fallbackModel || {};
    const profileVersion = run?.profile?.profile_version
      || (
        run?.run_id && storedBatch?.run_id === run.run_id
          ? storedBatch?.profile?.profile_version
          : null
      )
      || "仅以快照哈希保存";
    const warnings = [
      ...(run?.warnings || []),
      ...(model.warnings || []),
    ];
    const warningLabels = {
      coder_model_not_connected: "CODER 模型未接入；当前使用规则兼容模式，不冒充原版或优化版模型。",
      project_model_not_connected: "独立项目模型未接入；项目卡片只能使用 Issue 批次聚合。",
      public_candidates_are_not_personalized: "公共候选不是个性化结果。",
      project_ranking_from_issue_aggregation: "项目排序来自 Issue 推荐聚合，不是独立项目模型。",
    };
    elements.runGrid.innerHTML = [
      ["run_id", run?.run_id || "未读取"],
      ["model_variant", model.variant || "未读取"],
      ["model_version", model.version || "未读取"],
      ["dataset_version", model.dataset_version || "未登记"],
      ["profile_version", profileVersion],
      ["mapping_version", model.mapping_version || "未登记"],
      ["model_status", model.status || "未知"],
      ["warnings", warnings.length ? `${warnings.length} 条` : "0 条"],
    ].map(([key, value], index) => `<div class="run-item"><dt>${ui.escapeHtml(key)}</dt><dd class="${key === "warnings" && warnings.length ? "is-warn" : ""}">${ui.escapeHtml(String(value))}</dd></div>`).join("");
    elements.warnings.innerHTML = warnings.length
      ? warnings.map((warning) => `<li><span class="warn-tag">WARN</span><span>${ui.escapeHtml(warningLabels[warning] || warning)}</span></li>`).join("")
      : `<li><span class="warn-tag">OK</span><span>本次运行没有额外 warnings。</span></li>`;
    if (elements.runLink) {
      elements.runLink.hidden = !run?.run_id;
      elements.runLink.href = run?.run_id ? `/recommendation-run?run_id=${encodeURIComponent(run.run_id)}` : "/recommendation-run";
    }
    const banners = [];
    if (!run && !storedBatch) {
      banners.push(`<p><b>还没有本次推荐批次。</b>先从首页或推荐页生成一次推荐，系统状态页才能显示完整的 run_id、warnings 与画像版本。</p><a class="btn btn--sm btn--ghost" href="/recommendations">去生成推荐</a>`);
    }
    if (model.status === "compat") {
      banners.push(`<p><b>当前为规则兼容模式。</b>model_variant=rule_legacy；CODER 原版与优化版尚未接入，页面不会把它们混在一起显示。</p><a class="btn btn--sm btn--ghost" href="/recommendation-run">查看推荐说明</a>`);
    }
    if (banners.length) {
      elements.runBanner.hidden = false;
      elements.runBanner.innerHTML = banners.join("");
    }
  }

  function renderMatrix() {
    elements.matrix.innerHTML = matrices.map(([id, name, code, trigger, sees, action]) => `
      <article class="status-card">
        <header class="ic-top"><span class="pill pill--neutral">${ui.escapeHtml(name)}</span><span class="mono">${ui.escapeHtml(code)}</span></header>
        <div class="d-block"><h4>触发条件</h4><p>${ui.escapeHtml(trigger)}</p></div>
        <div class="d-block"><h4>用户看到</h4><p>${ui.escapeHtml(sees)}</p></div>
        <div class="d-block"><h4>下一步</h4><p>${ui.escapeHtml(action)}</p></div>
        <a class="btn btn--sm btn--ghost" href="/recommendations?state=${encodeURIComponent(id)}">在推荐区预览</a>
      </article>`).join("");
  }

  async function init() {
    renderMatrix();
    try {
      const [status, feedback] = await Promise.all([
        ui.requestJson("/api/v1/status"),
        ui.requestJson("/api/v1/feedback/summary").catch(() => ({ summary: { current: {} } })),
      ]);
      renderStatus(status);
      renderFeedback(feedback);
      let run = null;
      if (runId) {
        try {
          run = await ui.requestJson(`/api/v1/recommendation-runs/${encodeURIComponent(runId)}`);
        } catch (error) {
          showMessage(error.code || `http: ${error.status}`, "推荐运行不可读取", error.message);
        }
      }
      renderRun(run, status.model);
    } catch (error) {
      showMessage(error.code || `http: ${error.status}`, "系统状态暂不可用", error.message);
    }
  }

  init();
})();
