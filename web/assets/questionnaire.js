"use strict";

(function () {
  const status = document.getElementById("questionnaire-status");
  if (!status) return;

  fetch("/api/v1/me/profile/questionnaire", {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
  })
    .then(async (response) => {
      const body = await response.json().catch(() => ({}));
      if (response.status === 401) {
        status.textContent = "登录后可填写成长问答；当前画像仍可以通过下方手工表单维护。";
        return;
      }
      if (response.status === 503) {
        status.textContent = "问卷接口已预留，但成员 B 的题目与计分配置尚未交付（questionnaire_not_ready）。当前不会在浏览器里伪造评分。";
        return;
      }
      if (!response.ok) {
        status.textContent = `问卷接口暂时不可用：${body.error?.message || response.status}`;
        return;
      }
      status.textContent = "问卷配置已加载；评估预览与确认接口待成员 B 接入。";
    })
    .catch(() => {
      status.textContent = "无法连接问卷接口；当前画像仍可以通过下方手工表单维护。";
    });
})();
