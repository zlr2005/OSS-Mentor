"use strict";

(function () {
  const ui = window.OMUI;
  const params = new URLSearchParams(window.location.search);
  const requestedRunId = params.get("run_id") || "";
  const storedBatch = ui.readBatch();
  const runId = requestedRunId || storedBatch?.run_id || "";
  const elements = {
    message: document.getElementById("run-message"),
    pill: document.getElementById("run-pill"),
    idMeta: document.getElementById("run-id-meta"),
    summary: document.getElementById("run-summary"),
    summarySection: document.getElementById("run-summary-section"),
    profile: document.getElementById("profile-summary"),
    warnings: document.getElementById("run-warnings"),
    items: document.getElementById("run-items"),
  };
  const warningLabels = {
    coder_model_not_connected: "CODER 模型未接入；当前为规则兼容模式，不是原版或优化版模型输出。",
    project_model_not_connected: "独立项目模型未接入；项目卡片不能冒充模型项目推荐。",
    public_candidates_are_not_personalized: "公共候选不是个性化结果。",
    diversity_repository_cap_relaxed: "候选仓库不足，已放宽单仓库数量限制。",
    diversity_task_type_relaxed: "候选类型不足，结果可能集中在同一任务类型。",
    project_ranking_from_issue_aggregation: "项目排序来自 Issue 推荐聚合，不是独立项目模型。",
  };

  function showMessage(code, title, body, actions = "") {
    elements.message.hidden = false;
    elements.message.innerHTML = `<p><span class="mono">${ui.escapeHtml(code)}</span> <b>${ui.escapeHtml(title)}</b> ${ui.escapeHtml(body)}</p>${actions ? `<div class="panel-actions">${actions}</div>` : ""}`;
  }

  function summaryRows(batch, profile) {
    const model = batch.model || {};
    return [
      ["run_id", batch.run_id || "—"],
      ["service_track", ui.trackLabel(batch.service_track)],
      ["match_version", batch.match_version || "—"],
      ["model_variant", model.variant || "—"],
      ["model_version", model.version || "—"],
      ["dataset_version", model.dataset_version || "未登记"],
      ["profile_version", profile?.profile_version || "仅以快照哈希保存"],
      ["mapping_version", model.mapping_version || "未登记"],
    ].map(([key, value]) => `<div class="run-item"><dt>${ui.escapeHtml(key)}</dt><dd>${ui.escapeHtml(String(value))}</dd></div>`).join("");
  }

  function renderProfile(batch, profile) {
    const values = [
      ["画像", profile?.display_name || profile?.profile_key || "未随批次缓存"],
      ["来源", profile?.profile_source || "未随批次缓存"],
      ["版本", profile?.profile_version || "仅以快照哈希保存"],
      ["快照哈希", batch.profile_snapshot_hash || "—"],
    ];
    elements.profile.innerHTML = values.map(([key, value]) => `<div><dt>${ui.escapeHtml(key)}</dt><dd class="mono">${ui.escapeHtml(String(value))}</dd></div>`).join("");
  }

  function renderWarnings(batch) {
    const modelWarnings = batch.model?.warnings || [];
    const warnings = [...new Set([...(batch.warnings || []), ...modelWarnings])];
    elements.warnings.innerHTML = warnings.length
      ? warnings.map((warning) => `<li><span class="warn-tag">WARN</span><span>${ui.escapeHtml(warningLabels[warning] || warning)}</span></li>`).join("")
      : `<li><span class="warn-tag">OK</span><span>本次批次没有额外 warnings。</span></li>`;
  }

  function renderItems(batch) {
    const items = batch.items || [];
    if (!items.length) {
      elements.items.innerHTML = ui.panel("ok: 0", "本次批次没有推荐条目", "画像与候选池没有交集，或批次已被清空。", `<a class="link-ext" href="/recommendations">返回推荐</a>`);
      return;
    }
    elements.items.innerHTML = items.map((item) => {
      const score = Math.round(Number(item.score || 0) * 100);
      const query = new URLSearchParams({ task_candidate_id: String(item.task_candidate_id) });
      if (batch.feedback_context) query.set("feedback_context", batch.feedback_context);
      return `<article class="run-item-row">
        <div>
          <h4><a href="/task?${query.toString()}">${ui.escapeHtml(item.title)}</a></h4>
          <p>${ui.escapeHtml(item.repository_full_name)}#${item.issue_number} · ${ui.escapeHtml(ui.trackLabel(item.service_track))} · 已覆盖 ${(item.matched_skills || []).length} 项 / 待补齐 ${(item.missing_skills || []).length} 项</p>
          <details class="reasons" style="margin-top:10px">
            <summary><span class="chev" aria-hidden="true"></span>推荐依据 <span class="count mono">${(item.reasons || []).length} 条</span></summary>
            <ul class="reason-list">${ui.reasonList(item.reasons || [])}</ul>
          </details>
        </div>
        <span class="run-score">${score}</span>
      </article>`;
    }).join("");
  }

  function render(batch, profile) {
    elements.pill.textContent = "ok";
    elements.pill.className = "pill pill--ok";
    elements.idMeta.textContent = `run_id ${batch.run_id || "—"}`;
    elements.summary.innerHTML = summaryRows(batch, profile);
    renderProfile(batch, profile);
    renderWarnings(batch);
    renderItems(batch);
  }

  async function init() {
    if (!runId) {
      if (elements.summarySection) elements.summarySection.hidden = true;
      elements.items.innerHTML = ui.panel(
        "run: missing",
        "还没有可展示的推荐批次",
        "先从首页或全部推荐生成一次推荐，再打开推荐说明。",
        `<a class="btn btn--sm btn--ghost" href="/recommendations">去生成推荐</a>`
      );
      elements.pill.textContent = "missing";
      elements.pill.className = "pill pill--warn";
      return;
    }
    try {
      const batch = await ui.requestJson(`/api/v1/recommendation-runs/${encodeURIComponent(runId)}`);
      const profile = batch.profile
        || (storedBatch?.run_id === batch.run_id ? storedBatch.profile : null);
      render(batch, profile);
      if (!profile) {
        showMessage(
          "profile: snapshot_hash_only",
          "画像版本未随运行缓存",
          "本次运行的画像快照只保存了哈希。要查看画像名称与来源，请从推荐页进入推荐说明。"
        );
      }
    } catch (error) {
      elements.pill.textContent = "error";
      elements.pill.className = "pill pill--danger";
      elements.items.innerHTML = ui.panel(
        error.code || `http: ${error.status}`,
        "推荐说明不可读取",
        error.message,
        `<a class="btn btn--sm btn--ghost" href="/recommendations">返回推荐</a><a class="link-ext" href="/status">查看系统状态</a>`
      );
    }
  }

  init();
})();
