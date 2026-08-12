function card(cls, heading, value, detail) {
  var clsAttr = cls ? ' class="' + cls + '"' : "";
  return (
    '<div class="card"><h3>' +
    heading +
    '</h3><div' + clsAttr + '>' +
    value +
    "</div>" +
    (detail ? '<div class="detail">' + detail + "</div>" : "") +
    "</div>"
  );
}

function showError(msg) {
  var el = document.getElementById("error");
  el.hidden = false;
  el.className = "error-msg";
  el.textContent = "无法加载系统状态: " + msg;
}

function renderStatus(status, feedback) {
  document.getElementById("update-time").textContent =
    "最后更新: " + new Date().toLocaleString("zh-CN");

  var dbReady = status.database_ready;
  var dbClass = dbReady ? "ok" : "error";
  var dbText = dbReady ? "就绪" : "未连接";

  var html = "";
  html += card("value " + dbClass, "数据库", dbText, status.database_path || "—");
  html += card("value", "仓库", status.repository_count ?? "—", "已接入仓库");
  html += card("value", "候选任务", status.candidate_count ?? "—", "合格: " + (status.eligible_count ?? "—"));
  html += card("value", "可推荐任务", status.matchable_count ?? "—", "已提取特征且合格");
  html += card("value", "新人友好", status.newcomer_count ?? "—", "含新人标签信号");
  var syncText = status.last_sync_at
    ? new Date(status.last_sync_at).toLocaleString("zh-CN")
    : "从未";
  html += card("value", "最后同步", '<span style="font-size:0.9rem;">' + syncText + "</span>", "");
  document.getElementById("status-grid").innerHTML = html;

  var typeRate = (status.type_identification_rate ?? 0) * 100;
  var skillRate = (status.skill_coverage_rate ?? 0) * 100;
  var typeClass = "value ";
  typeClass += typeRate >= 90 ? "ok" : typeRate >= 70 ? "warn" : "error";
  var skillClass = "value ";
  skillClass += skillRate >= 90 ? "ok" : skillRate >= 70 ? "warn" : "error";

  var qhtml = "";
  qhtml += card(typeClass, "任务类型识别率", typeRate.toFixed(1) + "%", (status.type_identified_count ?? 0) + " / " + (status.eligible_count ?? 0) + " 合格任务");
  qhtml += card(skillClass, "技能要求覆盖率", skillRate.toFixed(1) + "%", (status.skill_coverage_count ?? 0) + " / " + (status.eligible_count ?? 0) + " 合格任务");
  qhtml += card("value", "已提取特征", status.features_extracted_count ?? "—", "任务特征版本: 当前");
  document.getElementById("quality-grid").innerHTML = qhtml;

  if (feedback) {
    var cur = feedback.current || {};
    var fhtml = "";
    fhtml += card("value", "反馈总量", cur.total ?? 0, "");
    fhtml += card("value", "感兴趣", cur.interested ?? 0, "");
    fhtml += card("value", "不适合", cur.not_suitable ?? 0, "");
    fhtml += card("value", "已开始", cur.started ?? 0, "");
    fhtml += card("value", "已完成", cur.completed ?? 0, "");
    document.getElementById("feedback-grid").innerHTML = fhtml;

    var trans = feedback.transitions || {};
    document.getElementById("transitions-table").innerHTML =
      "<thead><tr><th>转化路径</th><th>次数</th></tr></thead>" +
      "<tbody>" +
      "<tr><td>感兴趣 → 已开始</td><td>" + (trans.interested_to_started ?? 0) + "</td></tr>" +
      "<tr><td>已开始 → 已完成</td><td>" + (trans.started_to_completed ?? 0) + "</td></tr>" +
      "</tbody>";
  }

  document.getElementById("version-table").innerHTML =
    "<thead><tr><th>组件</th><th>版本</th></tr></thead>" +
    "<tbody>" +
    "<tr><td>API</td><td>" + (status.api_version || "—") + "</td></tr>" +
    "<tr><td>匹配算法</td><td>" + (status.match_version || "—") + "</td></tr>" +
    "</tbody>";
}

function loadStatus() {
  var statusReq = new XMLHttpRequest();
  var feedbackReq = new XMLHttpRequest();
  var statusDone = false;
  var feedbackDone = false;
  var statusData = null;
  var feedbackData = null;

  function checkDone() {
    if (!statusDone || !feedbackDone) return;
    if (statusData) {
      renderStatus(statusData, feedbackData);
    }
  }

  statusReq.open("GET", "/api/v1/status");
  statusReq.onload = function () {
    statusDone = true;
    if (statusReq.status === 200) {
      try {
        statusData = JSON.parse(statusReq.responseText);
      } catch (e) {
        showError("Status JSON parse error: " + e.message);
        return;
      }
    }
    checkDone();
  };
  statusReq.onerror = function () {
    statusDone = true;
    showError("Status API request failed");
    checkDone();
  };
  statusReq.send();

  feedbackReq.open("GET", "/api/v1/feedback/summary");
  feedbackReq.onload = function () {
    feedbackDone = true;
    if (feedbackReq.status === 200) {
      try {
        feedbackData = JSON.parse(feedbackReq.responseText);
      } catch (e) {
        // feedback is optional, ignore parse errors
      }
    }
    checkDone();
  };
  feedbackReq.onerror = function () {
    feedbackDone = true;
    checkDone();
  };
  feedbackReq.send();
}

loadStatus();
