// ==UserScript==
// @name         OpenCode signed-in tab controller
// @namespace    opencode-local-bridge
// @version      0.1.5
// @description  Opt-in local jobs in your existing signed-in browser tab.
// @match        https://chat.z.ai/*
// @match        https://grok.com/*
// @match        https://chat.mistral.ai/*
// @match        https://www.kimi.com/*
// @match        https://www.kimi.ai/*
// @inject-into  content
// @run-at       document-end
// @noframes
// @grant        GM.xmlHttpRequest
// @grant        GM.getValue
// @grant        GM.setValue
// ==/UserScript==

(async () => {
  "use strict";
  const TOKEN = "__BRIDGE_TOKEN__"; // Replaced only in ignored private exports.
  const BASE = "http://127.0.0.1:8000";
  const sites = {
    "chat.z.ai": { provider: "glm", home: "/", input: "#chat-input", path: /^\/c\/[\w-]+$/ },
    "grok.com": { provider: "grok", home: "/", input: 'textarea[aria-label="Ask Grok anything"], [contenteditable=true][aria-label="Ask Grok anything"]', path: /^\/c\/[\w-]+$/ },
    "chat.mistral.ai": { provider: "mistral", home: "/work", input: ".ProseMirror[contenteditable=true]", path: /^\/(?:chat|work)\/[\w-]+$/ },
    "www.kimi.com": { provider: "kimi", home: "/", input: ".chat-input-editor[contenteditable=true]", path: /^\/chat\/[\w-]+$/ },
    "www.kimi.ai": { provider: "kimi", home: "/", input: ".chat-input-editor[contenteditable=true]", path: /^\/chat\/[\w-]+$/ },
  };
  const site = sites[location.hostname];
  if (!site || window.top !== window) return;
  const key = "opencode-bridge-tab-v1";
  let owner = sessionStorage.getItem(key);
  if (!owner) { owner = crypto.randomUUID(); sessionStorage.setItem(key, owner); }
  const activeKey = site.provider + ":" + owner;
  const documentId = crypto.randomUUID();
  let enabled = await GM.getValue(activeKey, false);
  let pending = null;
  let busy = false;
  let finishing = false;
  let timer;

  const badge = document.createElement("div");
  const shadow = badge.attachShadow({ mode: "closed" });
  badge.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647";
  const button = document.createElement("button");
  button.style.cssText = "font:13px system-ui;padding:10px 14px;border:1px solid #777;border-radius:8px;background:#fff;color:#111;cursor:pointer";
  shadow.append(button);
  document.documentElement.append(badge);
  const label = message => { button.textContent = message || (enabled ? "OpenCode: подключено · отключить" : "Подключить вкладку к OpenCode"); };
  label();

  async function rpc(path, method = "GET", data) {
    const response = await GM.xmlHttpRequest({
      url: BASE + path, method, timeout: 10000,
      headers: { Authorization: "Bearer " + TOKEN, "Content-Type": "application/json" },
      ...(data ? { data: JSON.stringify(data) } : {}),
    });
    if (response.status !== 200) throw new Error("Local bridge rejected the request");
    return JSON.parse(response.responseText);
  }

  const identity = job => ({provider:site.provider, id:job.id, owner, lease:job.lease, document:documentId});
  function retire() {
    pending = null;
    document.dispatchEvent(new CustomEvent("opencode-local-job-v1", { detail: "null" }));
    label();
  }

  async function finish(result) {
    const job = pending;
    retire();
    if (!job) return;
    finishing = true;
    try { await rpc("/browser/result", "POST", { ...identity(job), result }); }
    finally { finishing = false; }
  }

  button.addEventListener("click", async () => {
    if (TOKEN === "__BRIDGE_TOKEN__") { label("Сначала экспортируй приватные скрипты моста"); return; }
    enabled = !enabled;
    await GM.setValue(activeKey, enabled);
    if (!enabled && pending) await finish({ error: "Tab disconnected by user" }).catch(() => {});
    label();
    if (enabled) void poll();
  });

  const visible = element => !!element && element.getClientRects().length > 0;
  const editor = () => Array.from(document.querySelectorAll(site.input)).find(visible);
  const draft = input => input?.isContentEditable ? input.textContent : input?.value;
  const answers = () => Array.from(document.querySelectorAll(".segment:has(.segment-assistant-actions) .segment-content-box"));

  function markdown(element) {
    const root = element.cloneNode(true);
    root.querySelectorAll("button,svg,[aria-hidden=true]").forEach(e => e.remove());
    root.querySelectorAll("pre").forEach(pre => {
      const code = pre.querySelector("code") || pre;
      const language = (code.className.match(/language-([\w-]+)/) || [])[1] || "";
      pre.replaceWith(document.createTextNode("\n```" + language + "\n" + code.textContent + "\n```\n"));
    });
    root.querySelectorAll("br").forEach(e => e.replaceWith(document.createTextNode("\n")));
    root.querySelectorAll("p,li,h1,h2,h3,h4,blockquote").forEach(e => e.appendChild(document.createTextNode("\n")));
    return root.textContent.trim();
  }

  document.addEventListener("opencode-local-response-v1", async event => {
    let value;
    try { value = JSON.parse(event.detail); } catch (_) { return; }
    if (!pending || value?.nonce !== pending.nonce) return;
    if (value.error) { await finish({ error: "Provider response could not be observed" }).catch(() => {}); return; }
    const job = pending;
    // Give the frontend a bounded chance to render its final answer and route.
    for (let attempt = 0; attempt < 50 && pending === job; attempt++) {
      if (site.path.test(location.pathname) && (site.provider !== "kimi" || answers().length > job.previousAnswers)) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    if (pending !== job) return;
    let text;
    if (site.provider === "kimi") {
      const current = answers();
      if (current.length <= job.previousAnswers) {
        await finish({ error: "No new assistant answer was rendered" }).catch(() => {}); return;
      }
      text = markdown(current[current.length - 1]);
    }
    await finish({ status: value.status, body: value.body, path: location.pathname, ...(text ? { text } : {}) }).catch(() => label("OpenCode: ответ не принят"));
  });

  async function execute(job) {
    let input = editor();
    if (draft(input)?.trim()) throw new Error("The tab has an unsent draft");
    const target = job.path || site.home;
    if (target !== site.home && !site.path.test(target)) throw new Error("Invalid conversation route");
    if (location.pathname !== target) {
      // Keep only a tab id and execution flag across navigation. No prompts,
      // response bodies or local pairing key are put in page storage.
      // The old document remains alive until navigation commits. Stop polling
      // now so it cannot repeatedly abort a slow navigation to the same route.
      await rpc("/browser/navigate", "POST", identity(job));
      clearInterval(timer);
      location.assign(location.origin + target);
      return;
    }
    // The extension can run before the SPA hydrates its signed-in editor.
    for (let attempt = 0; !input && enabled && attempt < 50; attempt++) {
      if (attempt % 5 === 0 && !(await rpc("/browser/check", "POST", identity(job))).active) {
        throw new Error("The browser job was cancelled");
      }
      await new Promise(resolve => setTimeout(resolve, 100));
      input = editor();
    }
    if (!enabled || location.pathname !== target) throw new Error("The tab changed before submission");
    if (!input) throw new Error("The signed-in chat input is unavailable");
    if (draft(input)?.trim()) throw new Error("The tab has an unsent draft");
    if (input.disabled || input.getAttribute("aria-disabled") === "true") throw new Error("The chat input is busy");
    if (job.submitted) throw new Error("The prompt was already submitted");
    if (!Number.isFinite(job.expires_in) || job.expires_in <= 0) throw new Error("Invalid browser job deadline");
    pending = { ...job, nonce: crypto.randomUUID(), previousAnswers: answers().length,
      deadline: performance.now() + Math.min(job.expires_in, 1800) * 1000 };
    // Use strings across Safari's isolated/page worlds. Do not send upstream
    // until the observer confirms that this exact job can be captured.
    await new Promise((resolve, reject) => {
      const nonce = pending.nonce;
      const timeout = setTimeout(() => {
        document.removeEventListener("opencode-local-ready-v1", ready);
        reject(new Error("Reload the tab to load the response observer"));
      }, 1500);
      function ready(event) {
        let value;
        try { value = JSON.parse(event.detail); } catch (_) { return; }
        if (value?.nonce !== nonce) return;
        clearTimeout(timeout);
        document.removeEventListener("opencode-local-ready-v1", ready);
        resolve();
      }
      document.addEventListener("opencode-local-ready-v1", ready);
      document.dispatchEvent(new CustomEvent("opencode-local-job-v1", {
        detail: JSON.stringify({ nonce, prompt: job.prompt }),
      }));
    });
    await rpc("/browser/submit", "POST", identity(job));
    if (!enabled || pending?.id !== job.id) throw new Error("The tab was disconnected before submission");
    if (location.pathname !== target || editor() !== input || draft(input)?.trim()) {
      throw new Error("The editor changed before submission");
    }
    input.focus();
    if (input.isContentEditable) {
      if (!document.execCommand("insertText", false, job.prompt)) throw new Error("Chat editor did not accept the prompt");
    } else {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      if (!setter) throw new Error("Unsupported chat input");
      setter.call(input, job.prompt);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
    // Invoke the site's own send handler. The observer never constructs an
    // upstream request or touches the user's account credentials.
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));
    label("OpenCode: запрос выполняется · отключить");
  }

  async function poll() {
    if (busy || finishing || !enabled) return;
    busy = true;
    try {
      if (pending) {
        const job = pending;
        if (performance.now() >= job.deadline || !(await rpc("/browser/check", "POST", identity(job))).active) {
          if (pending === job) retire();
        }
        return;
      }
      const { job } = await rpc("/browser/jobs/" + site.provider + "?owner=" + encodeURIComponent(owner)
        + "&document=" + encodeURIComponent(documentId));
      if (!job) label();
      if (job) {
        try { await execute(job); }
        catch (_) {
          pending = { ...job };
          await finish({ error: "Tab unavailable, reloaded, or contains an unsent draft" });
          enabled = false;
          await GM.setValue(activeKey, false);
          label("OpenCode: остановлено · подключить");
        }
      }
    } catch (_) { label("OpenCode: локальный мост недоступен"); }
    finally { busy = false; }
  }
  timer = setInterval(() => void poll(), 500);
  window.addEventListener("pagehide", () => clearInterval(timer), { once: true });
  if (enabled && TOKEN !== "__BRIDGE_TOKEN__") void poll();
})();
