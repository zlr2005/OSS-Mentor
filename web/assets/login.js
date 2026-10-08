"use strict";
const loginLink = document.getElementById("github-login");
const errorBox = document.getElementById("error");
const loadingEl = document.getElementById("loading");
let ready = false;
loginLink.addEventListener("click", (event) => {
  if (!ready) event.preventDefault();
  else loadingEl.hidden = false;
});
function showError(message) {
  loginLink.hidden = true;
  errorBox.textContent = message;
  errorBox.classList.add("show");
}
const returnTo = new URLSearchParams(window.location.search).get("return_to") || "/profile";
fetch("/api/v1/auth/github/start?return_to=" + encodeURIComponent(returnTo))
  .then(async (response) => {
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || "无法开始登录");
    if (!data.oauth_configured) {
      showError(data.message || "GitHub OAuth 未配置");
      return;
    }
    if (!data.authorize_url) throw new Error("登录地址不可用");
    loginLink.href = data.authorize_url;
    ready = true;
  })
  .catch((error) => showError(error.message || "无法连接本地服务"));
