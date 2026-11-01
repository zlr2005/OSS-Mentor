"use strict";

(function () {
  const status = document.getElementById("questionnaire-status");
  const shell = document.getElementById("questionnaire-shell");
  if (!status || !shell) return;

  const form = document.getElementById("questionnaire-form");
  const questionsRoot = document.getElementById("questionnaire-questions");
  const formError = document.getElementById("questionnaire-form-error");
  const previewButton = document.getElementById("questionnaire-preview-button");
  const previewPanel = document.getElementById("questionnaire-preview");
  const summaryRoot = document.getElementById("questionnaire-preview-summary");
  const featuresRoot = document.getElementById("questionnaire-features");
  const suggestionsRoot = document.getElementById("questionnaire-suggestions");
  const decisionCount = document.getElementById("questionnaire-decision-count");
  const confirmButton = document.getElementById("questionnaire-confirm-button");
  const resetButton = document.getElementById("questionnaire-reset-button");
  const result = document.getElementById("questionnaire-result");

  const state = {
    questionnaire: null,
    answers: {},
    submissionId: null,
    preview: null,
    plan: null,
    decisions: {},
    confirmed: false,
  };

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function setStatus(message, stateName = "") {
    status.textContent = message;
    status.dataset.state = stateName;
  }

  function setFormError(message) {
    formError.textContent = message || "";
    formError.hidden = !message;
  }

  function setBusy(button, busy, label) {
    if (!button) return;
    button.disabled = busy;
    button.setAttribute("aria-busy", busy ? "true" : "false");
    const text = button.querySelector("span");
    if (!text) return;
    if (busy) {
      if (!text.dataset.idle) text.dataset.idle = text.textContent;
      if (label) text.textContent = label;
    } else if (text.dataset.idle) {
      text.textContent = text.dataset.idle;
    }
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(body.error?.message || `HTTP ${response.status}`);
      error.status = response.status;
      error.code = body.error?.code;
      throw error;
    }
    return body;
  }

  function stableHash(text) {
    let hash = 2166136261;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
  }

  function conditionMatches(question) {
    const condition = question.applies_when;
    if (!condition) return true;
    const answer = state.answers[condition.question_id];
    if (Array.isArray(answer)) return answer.includes(condition.includes);
    return answer === condition.includes;
  }

  function visibleQuestions() {
    return (state.questionnaire?.questions || []).filter(conditionMatches);
  }

  function selectedValues(question) {
    const value = state.answers[question.id];
    if (question.kind === "multi") return Array.isArray(value) ? value : [];
    return typeof value === "string" ? [value] : [];
  }

  function invalidatePreview() {
    state.preview = null;
    state.plan = null;
    state.submissionId = null;
    state.decisions = {};
    state.confirmed = false;
    previewPanel.hidden = true;
    result.hidden = true;
    result.textContent = "";
  }

  function renderQuestions() {
    questionsRoot.replaceChildren();
    for (const question of visibleQuestions()) {
      const fieldset = element("fieldset", "questionnaire-question");
      const legend = element("legend", "questionnaire-prompt", question.prompt);
      const meta = element(
        "span",
        "questionnaire-question-meta",
        question.required ? "必答" : "选答",
      );
      legend.appendChild(meta);
      fieldset.appendChild(legend);

      if (question.kind === "multi") {
        const limits = [];
        if (question.min_selections) limits.push(`至少 ${question.min_selections} 项`);
        if (question.max_selections) limits.push(`最多 ${question.max_selections} 项`);
        if (limits.length) {
          fieldset.appendChild(element("p", "questionnaire-hint", limits.join("，")));
        }
      }

      const options = element("div", "questionnaire-options");
      const selected = new Set(selectedValues(question));
      for (const option of question.options || []) {
        const label = element("label", "questionnaire-option");
        const input = document.createElement("input");
        input.type = question.kind === "multi" ? "checkbox" : "radio";
        input.name = question.id;
        input.value = option.id;
        input.checked = selected.has(option.id);
        label.appendChild(input);
        label.appendChild(element("span", "questionnaire-option-label", option.label));

        input.addEventListener("change", () => {
          setFormError("");
          invalidatePreview();
          if (question.kind === "multi") {
            const values = Array.from(
              options.querySelectorAll("input:checked"),
              (node) => node.value,
            );
            if (question.max_selections && values.length > question.max_selections) {
              input.checked = false;
              setFormError(`“${question.prompt}”最多选择 ${question.max_selections} 项`);
              return;
            }
            state.answers[question.id] = values;
          } else {
            state.answers[question.id] = option.id;
            renderQuestions();
          }
        });
        options.appendChild(label);
      }
      fieldset.appendChild(options);
      questionsRoot.appendChild(fieldset);
    }
  }

  function collectAnswers() {
    const answers = {};
    for (const question of visibleQuestions()) {
      const values = selectedValues(question);
      if (question.required && values.length === 0) {
        throw new Error(`请回答：${question.prompt}`);
      }
      if (
        question.min_selections &&
        values.length > 0 &&
        values.length < question.min_selections
      ) {
        throw new Error(`“${question.prompt}”至少选择 ${question.min_selections} 项`);
      }
      if (question.max_selections && values.length > question.max_selections) {
        throw new Error(`“${question.prompt}”最多选择 ${question.max_selections} 项`);
      }
      if (values.length === 0) continue;
      answers[question.id] = question.kind === "multi" ? values : values[0];
    }
    return answers;
  }

  function addSummaryCard(label, value, detail) {
    const card = element("div", "questionnaire-summary-card");
    card.appendChild(element("span", "questionnaire-summary-label", label));
    card.appendChild(element("strong", "", String(value)));
    if (detail) card.appendChild(element("small", "", detail));
    summaryRoot.appendChild(card);
  }

  function renderPreview() {
    const features = state.preview?.features || [];
    const observed = features.filter((feature) => feature.observed);
    const suggestions = state.plan?.suggestions || [];
    const pending = suggestions.filter((suggestion) => suggestion.status === "pending");
    const blocked = suggestions.filter((suggestion) => suggestion.status === "blocked");

    summaryRoot.replaceChildren();
    addSummaryCard("回答已保存", "预览", state.questionnaire.questionnaire_version);
    addSummaryCard("已观测特征", observed.length, `共 ${features.length} 维`);
    addSummaryCard("待确认建议", pending.length, `${blocked.length} 项因锁定不可改`);

    featuresRoot.replaceChildren();
    if (observed.length === 0) {
      featuresRoot.appendChild(
        element("p", "questionnaire-empty", "本次回答没有形成可观测特征，请补充回答。"),
      );
    } else {
      for (const feature of observed) {
        const row = element("div", "questionnaire-feature");
        row.appendChild(element("code", "", feature.feature_name));
        row.appendChild(element("strong", "", `${Math.round(feature.proposed_value * 100)}%`));
        const tags = [];
        if (feature.conflict) tags.push("证据冲突");
        if (feature.blocked_reason === "field_locked") tags.push("字段已锁定");
        row.appendChild(element("span", "questionnaire-feature-tag", tags.join(" · ") || "已观测"));
        featuresRoot.appendChild(row);
      }
    }

    suggestionsRoot.replaceChildren();
    state.decisions = {};
    if (suggestions.length === 0) {
      suggestionsRoot.appendChild(
        element("p", "questionnaire-empty", "当前画像与本次回答一致，没有需要确认的变化。"),
      );
    }
    for (const suggestion of suggestions) {
      const card = element("article", "questionnaire-suggestion");
      card.dataset.suggestionId = suggestion.suggestion_id;
      card.dataset.status = suggestion.status;
      const head = element("div", "questionnaire-suggestion-head");
      head.appendChild(element("code", "", suggestion.feature_name));
      head.appendChild(
        element(
          "span",
          "questionnaire-suggestion-value",
          `${Math.round(suggestion.current.value * 100)}% → ${Math.round(
            suggestion.proposed.value * 100,
          )}%`,
        ),
      );
      card.appendChild(head);
      card.appendChild(
        element(
          "p",
          "questionnaire-suggestion-evidence",
          `置信度 ${Math.round(suggestion.proposed.confidence * 100)}% · ${
            suggestion.proposed.evidence_refs.length
          } 条证据${suggestion.conflict ? " · 存在证据冲突" : ""}`,
        ),
      );

      if (suggestion.status === "blocked") {
        card.appendChild(
          element("p", "questionnaire-blocked", "字段已锁定，本次确认不会覆盖它。"),
        );
      } else {
        const actions = element("div", "questionnaire-decision-actions");
        for (const [decision, label] of [
          ["accept", "接受建议"],
          ["reject", "拒绝"],
        ]) {
          const button = element("button", "questionnaire-decision", label);
          button.type = "button";
          button.dataset.decision = decision;
          button.setAttribute("aria-pressed", "false");
          button.addEventListener("click", () => {
            state.decisions[suggestion.suggestion_id] = decision;
            for (const peer of actions.querySelectorAll("button")) {
              const selected = peer.dataset.decision === decision;
              peer.classList.toggle("is-selected", selected);
              peer.setAttribute("aria-pressed", selected ? "true" : "false");
            }
            updateDecisionCount();
          });
          actions.appendChild(button);
        }
        card.appendChild(actions);
      }
      suggestionsRoot.appendChild(card);
    }

    previewPanel.hidden = false;
    updateDecisionCount();
    previewPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function updateDecisionCount() {
    const pending = (state.plan?.suggestions || []).filter(
      (suggestion) => suggestion.status === "pending",
    );
    const decided = pending.filter(
      (suggestion) => state.decisions[suggestion.suggestion_id],
    ).length;
    decisionCount.textContent = `${decided} / ${pending.length}`;
    confirmButton.disabled =
      state.confirmed || pending.length === 0 || decided !== pending.length;
  }

  async function previewAssessment(event) {
    event.preventDefault();
    setFormError("");
    let answers;
    try {
      answers = collectAnswers();
    } catch (error) {
      setFormError(error.message);
      return;
    }

    setBusy(previewButton, true, "正在生成预览…");
    try {
      const idempotencyKey = `ui-preview-${stableHash(
        JSON.stringify({
          version: state.questionnaire.questionnaire_version,
          answers,
        }),
      )}`;
      const body = await requestJson("/api/v1/me/profile/assessment-preview", {
        method: "POST",
        body: JSON.stringify({
          questionnaire_version: state.questionnaire.questionnaire_version,
          answers,
          idempotency_key: idempotencyKey,
        }),
      });
      state.submissionId = body.submission_id;
      state.preview = body.preview;
      state.plan = body.plan;
      renderPreview();
    } catch (error) {
      if (error.status === 404 && error.code === "profile_required") {
        setFormError("请先保存手工画像，再生成问答预览。");
      } else if (error.status === 409) {
        setFormError("回答内容与已有预览冲突，请修改一处回答后重试。");
      } else {
        setFormError(`生成预览失败：${error.message}`);
      }
    } finally {
      setBusy(previewButton, false, "");
    }
  }

  async function confirmAssessment() {
    if (!state.plan || state.confirmed) return;
    const decisions = { ...state.decisions };
    setBusy(confirmButton, true, "正在确认…");
    result.hidden = true;
    try {
      const idempotencyKey = `ui-confirm-${stableHash(
        JSON.stringify({ plan_id: state.plan.plan_id, decisions }),
      )}`;
      const body = await requestJson("/api/v1/me/profile/assessment-confirm", {
        method: "POST",
        body: JSON.stringify({
          plan_id: state.plan.plan_id,
          decisions,
          idempotency_key: idempotencyKey,
        }),
      });
      const confirmation = body.confirmation;
      result.textContent =
        `画像已更新。新版本：${confirmation.new_profile_version}。` +
        `已接受 ${confirmation.applied_suggestion_ids.length} 项，拒绝 ${confirmation.rejected_suggestion_ids.length} 项。`;
      result.hidden = false;
      result.dataset.state = "success";
      state.confirmed = true;
      confirmButton.disabled = true;
      setStatus("画像确认已保存；刷新页面后会读取当前版本。", "success");
    } catch (error) {
      result.textContent =
        error.status === 409
          ? "确认计划已过期或已被处理，请重新生成预览。"
          : `确认失败：${error.message}`;
      result.hidden = false;
      result.dataset.state = "error";
    } finally {
      setBusy(confirmButton, false, "");
      updateDecisionCount();
    }
  }

  async function loadQuestionnaire() {
    setStatus("正在加载成长问答…", "loading");
    try {
      const body = await requestJson("/api/v1/me/profile/questionnaire");
      state.questionnaire = body.questionnaire;
      setStatus(
        `问卷已加载：${state.questionnaire.questionnaire_version}`,
        "ready",
      );
      shell.hidden = false;
      renderQuestions();
    } catch (error) {
      shell.hidden = true;
      if (error.status === 401) {
        setStatus("登录后才能填写成长问答；当前画像仍可手工维护。", "empty");
      } else if (error.status === 503) {
        setStatus("问卷服务暂未就绪，请稍后重试。", "error");
      } else {
        setStatus(`无法加载问卷：${error.message}`, "error");
      }
    }
  }

  form.addEventListener("submit", previewAssessment);
  confirmButton.addEventListener("click", confirmAssessment);
  resetButton.addEventListener("click", () => {
    invalidatePreview();
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  loadQuestionnaire();
})();
