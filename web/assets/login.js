"use strict";

const loginLink = document.getElementById("github-login");
const loginLabel = document.getElementById("login-label");
const errorBox = document.getElementById("error");
const errorMessage = document.getElementById("error-message");
const retryButton = document.getElementById("retry-login");
const loadingEl = document.getElementById("loading");

let ready = false;
let starting = false;

function setPending() {
  ready = false;
  loginLink.href = "#";
  loginLink.setAttribute("aria-disabled", "true");
  loginLink.classList.add("is-pending");
  loginLink.classList.remove("is-busy");
  loginLabel.textContent = "正在连接 GitHub…";
  loadingEl.hidden = true;
}

function setReady(authorizeUrl) {
  ready = true;
  loginLink.href = authorizeUrl;
  loginLink.setAttribute("aria-disabled", "false");
  loginLink.classList.remove("is-pending", "is-busy");
  loginLabel.textContent = "使用 GitHub 继续";
  loadingEl.hidden = true;
  errorBox.hidden = true;
}

function setError(message) {
  ready = false;
  loginLink.href = "#";
  loginLink.setAttribute("aria-disabled", "true");
  loginLink.classList.remove("is-pending", "is-busy");
  loginLabel.textContent = "登录暂不可用";
  loadingEl.hidden = true;
  errorMessage.textContent = message;
  errorBox.hidden = false;
}

function setBusy() {
  starting = true;
  loginLink.classList.add("is-busy");
  loginLabel.textContent = "正在前往 GitHub…";
  loadingEl.hidden = false;
}

async function prepareLogin() {
  if (ready || starting) return;
  setPending();
  const returnTo =
    new URLSearchParams(window.location.search).get("return_to") || "/profile";

  try {
    const response = await fetch(
      `/api/v1/auth/github/start?return_to=${encodeURIComponent(returnTo)}`,
      { headers: { Accept: "application/json" } },
    );
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error?.message || "无法开始 GitHub 登录");
    }
    if (!data.oauth_configured) {
      throw new Error(
        data.message ||
          "GitHub OAuth 尚未配置。请运行 start-web.ps1 -Reconfigure 后重启。",
      );
    }
    if (!data.authorize_url) {
      throw new Error("GitHub 授权地址不可用");
    }
    setReady(data.authorize_url);
  } catch (error) {
    setError(error.message || "无法连接本地服务");
  }
}

loginLink.addEventListener("click", (event) => {
  if (!ready) {
    event.preventDefault();
    return;
  }
  setBusy();
});

retryButton.addEventListener("click", () => {
  starting = false;
  errorBox.hidden = true;
  prepareLogin();
});

window.addEventListener("pageshow", () => {
  starting = false;
  loginLink.classList.remove("is-busy");
});

prepareLogin();
