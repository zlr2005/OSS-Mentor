"use strict";

(function () {
  const widget = document.querySelector("[data-session-nav]");
  if (!widget) return;

  const avatar = widget.querySelector(".avatar-mono");
  const who = widget.querySelector(".who");
  const pill = widget.querySelector(".pill");
  const actions = widget.querySelector(".session-actions");

  function returnTo() {
    const value = `${window.location.pathname}${window.location.search}`;
    return value.startsWith("/") && !value.startsWith("//") ? value : "/";
  }

  function loginUrl() {
    return `/login?return_to=${encodeURIComponent(returnTo())}`;
  }

  function setActions(authenticated) {
    if (!actions) return;
    actions.innerHTML = authenticated
      ? `<button class="session-action session-action--quiet" type="button" data-session-action="relogin" title="退出当前实例并重新登录">重新登录</button>` +
        `<button class="session-action session-action--danger" type="button" data-session-action="logout" title="结束当前本地登录会话">退出登录</button>`
      : `<a class="session-action session-action--primary" href="${loginUrl()}">登录</a>`;
  }

  function setError(message) {
    if (!who) return;
    const detail = who.querySelector("small") || document.createElement("small");
    detail.textContent = message;
    detail.classList.add("session-error");
    who.appendChild(detail);
  }

  function renderGithubAvatar(user, fallback) {
    if (!avatar) return;
    const login = typeof user?.github_login === "string" ? user.github_login.trim() : "";
    avatar.replaceChildren();
    if (!login) {
      avatar.textContent = fallback;
      return;
    }
    const image = document.createElement("img");
    image.alt = "";
    image.decoding = "async";
    image.referrerPolicy = "no-referrer";
    image.addEventListener("error", () => {
      avatar.textContent = fallback;
    }, { once: true });
    image.src = `https://github.com/${encodeURIComponent(login)}.png?size=80`;
    avatar.appendChild(image);
  }

  function renderUnauthenticated() {
    if (avatar) {
      avatar.replaceChildren();
      avatar.textContent = "访";
    }
    if (who) who.innerHTML = "未登录<small>可浏览公共候选</small>";
    if (pill) {
      pill.hidden = false;
      pill.textContent = "public";
      pill.className = "pill pill--neutral";
    }
    setActions(false);
  }

  function renderAuthenticated(user) {
    const name = user?.display_name || user?.github_login || "已登录用户";
    renderGithubAvatar(user, String(name).slice(0, 1).toUpperCase());
    if (who) {
      who.textContent = name;
    }
    if (pill) {
      pill.hidden = true;
      pill.textContent = "";
    }
    setActions(true);
  }

  function renderUnavailable(message) {
    if (avatar) {
      avatar.replaceChildren();
      avatar.textContent = "!";
    }
    if (who) who.innerHTML = "登录服务不可用<small>请检查服务状态</small>";
    if (pill) {
      pill.hidden = false;
      pill.textContent = "503";
      pill.className = "pill pill--warn";
    }
    setActions(false);
    if (message) setError(message);
  }

  async function loadSession() {
    try {
      const response = await fetch("/api/v1/me", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (response.status === 401) {
        renderUnauthenticated();
        return;
      }
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        renderUnavailable(payload.error?.message || `HTTP ${response.status}`);
        return;
      }
      renderAuthenticated(payload.user);
    } catch (error) {
      renderUnavailable(error.message || "无法连接本地服务");
    }
  }

  actions?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-session-action]");
    if (!button) return;
    const action = button.dataset.sessionAction;
    if (action !== "logout" && action !== "relogin") return;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = action === "relogin" ? "正在重新登录…" : "正在退出…";
    actions.setAttribute("aria-busy", "true");
    try {
      const response = await fetch("/api/v1/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: "{}",
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error?.message || `退出失败（HTTP ${response.status}）`);
      }
      window.location.assign(loginUrl());
    } catch (error) {
      button.disabled = false;
      button.textContent = original;
      setError(`操作失败：${error.message}`);
    } finally {
      actions.removeAttribute("aria-busy");
    }
  });

  loadSession();
})();
