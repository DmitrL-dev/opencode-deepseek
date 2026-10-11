// ==UserScript==
// @name         OpenCode signed-in tab controller
// @namespace    opencode-local-bridge
// @version      0.3.33
// @description  Opt-in local jobs in your existing signed-in browser tab.
// @match        https://chat.z.ai/*
// @match        https://grok.com/*
// @match        https://chat.mistral.ai/*
// @match        https://www.kimi.com/*
// @match        https://www.kimi.ai/*
// @match        https://chat.qwen.ai/*
// @match        https://chatgpt.com/*
// @match        https://chat.openai.com/*
// @match        https://claude.ai/*
// @match        https://gemini.google.com/*
// @match        https://www.google.com/*
// @inject-into  content
// @run-at       document-end
// @noframes
// @grant        GM.xmlHttpRequest
// @grant        GM.getValue
// @grant        GM.setValue
// ==/UserScript==

(async () => {
  "use strict";
  class BridgeFailure extends Error {
    constructor(message) {
      super(message);
      this.name = "BridgeFailure";
    }
  }
  const bridgeFailureReason = error => {
    if (error instanceof BridgeFailure || error?.name === "BridgeFailure") {
      return typeof error.message === "string" && error.message ? error.message : "Bridge failure";
    }
    return null;
  };
  const TOKEN = "__BRIDGE_TOKEN__"; // Replaced only in ignored private exports.
  const BASE = "http://127.0.0.1:8000";
  const geminiInput = 'div.ql-editor[contenteditable="true"], rich-textarea [contenteditable="true"], div[contenteditable="true"][role="textbox"], textarea[aria-label], textarea';
  const chatgptInput = '[data-composer-input] div.ProseMirror[contenteditable="true"], div.ProseMirror[contenteditable="true"][data-composer-markdown], div.ProseMirror[contenteditable="true"][role="textbox"], #prompt-textarea, div#prompt-textarea[contenteditable="true"], textarea#prompt-textarea';
  // Claude.ai: ProseMirror composer with a stable data-testid; keep ChatGPT-like fill flags.
  const claudeInput = '[data-testid="chat-input"][contenteditable="true"], div.ProseMirror[contenteditable="true"][role="textbox"], div.ProseMirror[contenteditable="true"], [contenteditable="true"][role="textbox"], textarea';
  // Click-or-Enter composers share one submit path. `send` is attached after
  // the button helpers exist. DOM exceptions stay as flags, not copied branches.
  const kimiSite = {
    provider: "kimi", home: "/", input: ".chat-input-editor[contenteditable=true]",
    path: /^\/chat\/[\w-]+$/, ignoreMarkup: true, stalls: true,
  };
  const chatgptSite = {
    provider: "chatgpt", home: "/", input: chatgptInput, path: /^\/c\/[\w-]+$/,
    // Empty ProseMirror keeps ZWSP and a placeholder paragraph. React also
    // replaces the node and rewrites innerHTML after the text is committed.
    looseEmpty: true, rebindEditor: true, ignoreMarkup: true,
    fillDelayMs: 40, fillFallback: "paragraph", clearForeignDraft: true, stalls: true,
  };
  const claudeSite = {
    provider: "claude", home: "/new", input: claudeInput, path: /^\/chat\/[\w-]+$/,
    looseEmpty: true, rebindEditor: true, ignoreMarkup: true,
    fillDelayMs: 40, fillFallback: "paragraph", clearForeignDraft: true, stalls: true,
  };
  const sites = {
    "chat.z.ai": { provider: "glm", home: "/", input: "#chat-input", path: /^\/c\/[\w-]+$/ },
    "grok.com": {
      provider: "grok", home: "/", stalls: true,
      input: 'form[data-composer=true] .query-bar-editor[contenteditable=true], form[data-composer=true] textarea[aria-label]',
      path: /^\/c\/[\w-]+$/,
      send: { mode: "form", form: "form[data-composer=true]" },
    },
    "chat.mistral.ai": {
      provider: "mistral", home: "/work", stalls: true,
      input: ".ProseMirror[contenteditable=true], [contenteditable=true][role='textbox'], textarea",
      path: /^\/(?:chat|work|code)\/[\w-]+$/,
    },
    "www.kimi.com": kimiSite,
    "www.kimi.ai": kimiSite,
    "chat.qwen.ai": {
      provider: "qwen", home: "/", stalls: true,
      input: "textarea.message-input-textarea, textarea", path: /^\/c\/[\w-]+$/,
    },
    "chatgpt.com": chatgptSite,
    "chat.openai.com": chatgptSite,
    "claude.ai": claudeSite,
    "gemini.google.com": {
      provider: "gemini", home: "/app", input: geminiInput, stalls: true,
      path: /^\/(?:u\/\d+\/)?app\/[\w-]+$/,
    },
    // Google AI Mode empty chat is /search?udm=50 (extra Chrome params ignored).
    // Follow-ups may carry /app/<id> and continue on gemini.google.com.
    "www.google.com": {
      provider: "gemini", home: "/search?udm=50", input: geminiInput, stalls: true,
      path: /^\/search$|^\/(?:u\/\d+\/)?app\/[\w-]+$/,
    },
  };
  const site = sites[location.hostname];
  if (!site || window.top !== window) return;
  const geminiAppRoute = value => {
    const match = String(value).match(/^\/(?:u\/\d+\/)?(app(?:\/[\w-]+)?)$/);
    return match ? "/" + match[1].replace(/\/$/, "") : value;
  };
  // Synthetic token for AI Mode threads that never leave /search?udm=50.
  // Must not navigate to gemini.google.com/app/aimode (different product UI).
  const geminiAiModeToken = value => {
    const path = String(value || "");
    return path === "/app/aimode" || path === "/search" || path.startsWith("/search?");
  };
  const googleAiModePage = () => location.hostname === "www.google.com"
    && location.pathname === "/search"
    && new URLSearchParams(location.search).get("udm") === "50";
  const sameRoute = (pathname, target) => {
    if (location.hostname === "www.google.com") {
      let targetPath = target;
      try { targetPath = new URL(target, location.origin).pathname; } catch (_) {}
      if (geminiAiModeToken(target) || geminiAiModeToken(targetPath)) {
        return pathname === "/search"
          && new URLSearchParams(location.search).get("udm") === "50";
      }
      if (targetPath === "/search") {
        return pathname === "/search"
          && new URLSearchParams(location.search).get("udm") === "50";
      }
      return geminiAppRoute(pathname) === geminiAppRoute(targetPath);
    }
    if (pathname === target) return true;
    if (site.provider !== "gemini") return false;
    // gemini.google.com cannot host the AI Mode placeholder token.
    if (geminiAiModeToken(target)) return false;
    return geminiAppRoute(pathname) === geminiAppRoute(target);
  };
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

  async function rpc(path, method = "GET", data, timeout = 10000) {
    const response = await GM.xmlHttpRequest({
      url: BASE + path, method, timeout,
      headers: { Authorization: "Bearer " + TOKEN, "Content-Type": "application/json" },
      ...(data ? { data: JSON.stringify(data) } : {}),
    });
    if (response.status !== 200) throw new BridgeFailure("Local bridge rejected the request");
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
    button.title = "";
    enabled = !enabled;
    await GM.setValue(activeKey, enabled);
    if (!enabled && pending) await finish({ error: "Tab disconnected by user" }).catch(() => {});
    label();
    if (enabled) void poll();
  });

  const visible = element => !!element && element.getClientRects().length > 0
    && getComputedStyle(element).visibility === "visible" && !element.closest('[inert], [aria-hidden=true]');
  const editor = () => Array.from(document.querySelectorAll(site.input)).find(visible);
  const block = node => node.nodeType === Node.ELEMENT_NODE && /^(P|DIV|LI|PRE|BLOCKQUOTE)$/.test(node.tagName);
  function editorText(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent;
    if (node.nodeName === "BR") {
      // A lone BR is an empty paragraph's caret placeholder.
      return block(node.parentNode) && node.parentNode.childNodes.length === 1 ? "" : "\n";
    }
    let text = "", previous = null;
    for (const child of node.childNodes) {
      if (previous && (block(previous) || block(child))) text += "\n";
      text += editorText(child);
      previous = child;
    }
    return text;
  }
  const draft = input => input?.isContentEditable ? editorText(input) : input?.value;
  const normText = value => (value || "").replace(/\r\n/g, "\n").replace(/\u00a0/g, " ");
  const samePrompt = (value, prompt) => normText(value) === normText(prompt);
  // ChatGPT empty ProseMirror keeps ZWSP / trailing breaks that String.trim()
  // does not remove. Do NOT use innerText here: CSS placeholder
  // ("Спросить ChatGPT") can leak into innerText and false-fail as a draft.
  const draftIsEmpty = input => {
    if (!input) return true;
    const cleaned = normText(draft(input) || "").replace(/[\u200b\ufeff]/g, "").trim();
    if (!cleaned) return true;
    if (site.looseEmpty) {
      const emptyPara = input.querySelector?.('[data-empty-paragraph="true"]');
      const placeholder = emptyPara?.getAttribute("data-placeholder")
        || input.getAttribute("aria-label")
        || "";
      if (placeholder && cleaned === normText(placeholder).trim()) return true;
      // Structural empty composer: only the placeholder paragraph remains.
      if (emptyPara && input.querySelectorAll("p").length <= 1
          && cleaned.length <= (placeholder ? placeholder.length : 0)) {
        return true;
      }
    }
    return false;
  };
  // Contenteditable (Kimi/Vue/ChatGPT ProseMirror) often adds a trailing
  // block newline, NBSP, or zero-width spaces around the inserted prompt.
  const retainsPrompt = (input, prompt) => {
    const wanted = normText(prompt).replace(/\n+$/, "").replace(/[\u200b\ufeff]/g, "");
    const variants = [
      draft(input),
      input?.isContentEditable ? input.innerText : null,
      input?.isContentEditable ? input.textContent : null,
    ];
    return variants.some(value => {
      if (value == null) return false;
      const got = normText(value).replace(/\n+$/, "").replace(/[\u200b\ufeff]/g, "");
      return got === wanted;
    });
  };
  async function clearLeftoverDraft(input, prompt) {
    if (!input || draftIsEmpty(input)) return;
    if (samePrompt(draft(input), prompt) || retainsPrompt(input, prompt)) {
      await clearDraft(input);
      return;
    }
    // Exclusive ChatGPT tab: clear foreign/stale composer text instead of
    // failing closed. Manual chat on a connected tab is unsupported anyway.
    if (site.clearForeignDraft) {
      await clearDraft(input);
      if (!draftIsEmpty(input)) {
        const left = draft(input) || "";
        throw new BridgeFailure(
          "The tab has an unsent draft: " + JSON.stringify(left.slice(0, 80)),
        );
      }
      return;
    }
    throw new BridgeFailure("The tab has an unsent draft");
  }
  const geminiSendButton = () => Array.from(document.querySelectorAll(
    'button[aria-label="Send message"], button[aria-label="Send"], button.send-button',
  )).find(visible) || null;
  const chatgptSendButton = () => {
    const direct = Array.from(document.querySelectorAll([
      'button[data-testid="send-button"]',
      "#composer-submit-button",
      'button[data-testid="composer-submit-button"]',
    ].join(", "))).find(visible);
    if (direct) return direct;
    return Array.from(document.querySelectorAll(
      '[data-composer-body] button[aria-label], form button[aria-label], button[aria-label]',
    )).find(btn => {
      if (!visible(btn)) return false;
      const label = (btn.getAttribute("aria-label") || "").toLowerCase();
      if (!/(send|отправ)/.test(label)) return false;
      return !/(voice|голос|дикт|microphone|разговор|фото|файл|attach|stop|остан)/.test(label);
    }) || null;
  };
  const claudeSendButton = () => {
    const direct = Array.from(document.querySelectorAll([
      'button[aria-label="Send Message"]',
      'button[aria-label="Send message"]',
      'button[data-testid="send-button"]',
      'button[aria-label="Send"]',
    ].join(", "))).find(visible);
    if (direct) return direct;
    return Array.from(document.querySelectorAll("button[aria-label], button[type='submit']")).find(btn => {
      if (!visible(btn)) return false;
      const label = (btn.getAttribute("aria-label") || btn.textContent || "").toLowerCase();
      if (!/(send|отправ)/.test(label)) return false;
      return !/(voice|голос|дикт|microphone|stop|остан|attach|файл)/.test(label);
    }) || null;
  };
  // Must return the <button>, not .message-input-right-button-send (a wrapper div).
  const qwenSendButton = () => {
    const selectors = [
      ".message-input-right-button-send button.send-button",
      ".message-input-right-button-send button[aria-label]",
      "div.chat-prompt-send-button button",
      'button.send-button[aria-label="Send"]',
      'button[aria-label="发送消息"]',
      'button[aria-label="Send"]',
      'button[aria-label="Send message"]',
    ];
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      if (node && visible(node)) return node;
    }
    return null;
  };
  // Kimi uses a clickable div (not <button>) with the Send SVG inside.
  const kimiSendButton = () => Array.from(document.querySelectorAll(
    ".send-button-container, [class*='send-button-container']",
  )).find(visible) || null;
  const sendArmed = button => {
    if (!button || button.disabled || button.getAttribute("aria-disabled") === "true"
        || button.classList.contains("disabled")) return false;
    const wrap = button.closest(".chat-prompt-send-button, .send-button-container")
      || button.parentElement;
    if (wrap) {
      const style = getComputedStyle(wrap);
      if (style.pointerEvents === "none" || Number(style.opacity) < 0.5) return false;
      if (wrap.classList.contains("disabled")) return false;
    }
    const style = getComputedStyle(button);
    if (style.pointerEvents === "none" || Number(style.opacity) < 0.5) return false;
    return true;
  };
  const activateClick = element => {
    // Avoid PointerEvent: some content-script worlds throw on its constructor.
    const opts = { bubbles: true, cancelable: true, view: window, button: 0 };
    try {
      element.dispatchEvent(new MouseEvent("mousedown", opts));
      element.dispatchEvent(new MouseEvent("mouseup", opts));
      element.dispatchEvent(new MouseEvent("click", opts));
    } catch (_) {
      element.click();
    }
  };
  const pressEnter = (input, keyup) => {
    input.focus();
    const event = type => input.dispatchEvent(new KeyboardEvent(type, {
      key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true,
    }));
    event("keydown");
    if (keyup) event("keyup");
  };
  // Qwen, Gemini, Kimi, and ChatGPT: wait until Send is armed, click it, else Enter.
  // Grok submits its composer form. GLM and Mistral send Enter with no button.
  const clickSend = (button, options = {}) => ({
    mode: "click", button, wait: 0, settleMs: 0, keyup: true, extra: null, failIfPending: null,
    ...options,
  });
  const qwenSend = clickSend(() => qwenSendButton(), {
    wait: 40,
    settleMs: 50,
    extra: (button, input, prompt) => {
      const wrap = button.closest(".chat-prompt-send-button");
      if (wrap && samePrompt(draft(input), prompt)) activateClick(wrap);
    },
    failIfPending: (input, prompt) => !sendArmed(qwenSendButton()) && samePrompt(draft(input), prompt),
  });
  const geminiSend = clickSend(() => geminiSendButton(), { keyup: false });
  const kimiSend = clickSend(() => kimiSendButton(), {
    wait: 40,
    settleMs: 50,
    extra: (button, input, prompt) => {
      const icon = button.querySelector(".send-icon, svg.send-icon, svg[name='Send']");
      if (icon && retainsPrompt(input, prompt)) activateClick(icon);
    },
    failIfPending: (input, prompt) => retainsPrompt(input, prompt) && !sendArmed(kimiSendButton()),
  });
  const chatgptSend = clickSend(() => chatgptSendButton(), {
    wait: 40,
    settleMs: 50,
    failIfPending: (input, prompt) => retainsPrompt(input, prompt) && !sendArmed(chatgptSendButton()),
  });
  const claudeSend = clickSend(() => claudeSendButton(), {
    wait: 40,
    settleMs: 50,
    failIfPending: (input, prompt) => retainsPrompt(input, prompt) && !sendArmed(claudeSendButton()),
  });
  sites["chat.qwen.ai"].send = qwenSend;
  sites["gemini.google.com"].send = geminiSend;
  sites["www.google.com"].send = geminiSend;
  kimiSite.send = kimiSend;
  chatgptSite.send = chatgptSend;
  claudeSite.send = claudeSend;
  async function submitComposer(input, prompt, form) {
    const send = site.send;
    if (!send) {
      pressEnter(input, false);
      return;
    }
    if (send.mode === "form") {
      form.requestSubmit();
      return;
    }
    let button = send.button();
    for (let attempt = 0; send.wait && !sendArmed(button) && attempt < send.wait; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 50));
      button = send.button();
    }
    if (button && sendArmed(button)) {
      activateClick(button);
      if (send.extra) send.extra(button, input, prompt);
      return;
    }
    pressEnter(input, send.keyup);
    if (send.failIfPending && send.failIfPending(input, prompt)) {
      throw new BridgeFailure("The chat send control is unavailable");
    }
  }
  async function clearDraft(input) {
    input.focus();
    if (input.isContentEditable) {
      const range = document.createRange();
      range.selectNodeContents(input);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      document.execCommand("delete", false, null);
    } else {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      if (!setter) throw new BridgeFailure("Unsupported chat input");
      setter.call(input, "");
      input.dispatchEvent(new InputEvent("input", { bubbles: true, cancelable: true, inputType: "deleteContentBackward" }));
    }
  }
  const placeCaret = (input, collapseEnd = true) => {
    const range = document.createRange();
    range.selectNodeContents(input);
    if (collapseEnd) range.collapse(false);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  };
  async function fillContentEditable(input, prompt) {
    // Vue/TipTap/ProseMirror composers often ignore a silent DOM write from the
    // content-script world unless insertText/paste is paired with input events.
    input.focus();
    try { input.click(); } catch (_) { /* focus is enough */ }
    await new Promise(resolve => setTimeout(resolve, site.fillDelayMs || 0));
    placeCaret(input, false);
    document.execCommand("selectAll", false, null);
    document.execCommand("delete", false, null);
    placeCaret(input, true);
    let accepted = false;
    try {
      accepted = document.execCommand("insertText", false, prompt);
    } catch (_) { accepted = false; }
    if (accepted) {
      input.dispatchEvent(new InputEvent("input", {
        bubbles: true, cancelable: true, inputType: "insertText", data: prompt,
      }));
    }
    if (!retainsPrompt(input, prompt)) {
      placeCaret(input, false);
      document.execCommand("selectAll", false, null);
      document.execCommand("delete", false, null);
      placeCaret(input, true);
      let pasted = false;
      try {
        const transfer = new DataTransfer();
        transfer.setData("text/plain", prompt);
        pasted = input.dispatchEvent(new ClipboardEvent("paste", {
          bubbles: true, cancelable: true, clipboardData: transfer,
        }));
      } catch (_) { pasted = false; }
      if (!retainsPrompt(input, prompt)) {
        input.dispatchEvent(new InputEvent("beforeinput", {
          bubbles: true, cancelable: true, inputType: "insertText", data: prompt,
        }));
        input.dispatchEvent(new InputEvent("beforeinput", {
          bubbles: true, cancelable: true, inputType: "insertFromPaste", data: prompt,
        }));
        if (!document.execCommand("insertText", false, prompt) && !pasted) {
          // Last resort: replace the empty placeholder paragraph and notify.
          if (site.fillFallback === "paragraph") {
            input.innerHTML = "";
            const paragraph = document.createElement("p");
            paragraph.textContent = prompt;
            input.append(paragraph);
          } else {
            input.textContent = prompt;
          }
        }
        input.dispatchEvent(new InputEvent("input", {
          bubbles: true, cancelable: true, inputType: "insertFromPaste", data: prompt,
        }));
      }
    }
    for (let attempt = 0; attempt < 40 && !retainsPrompt(input, prompt); attempt++) {
      await new Promise(resolve => setTimeout(resolve, 25));
      if (site.rebindEditor && !retainsPrompt(input, prompt)) {
        const current = editor();
        if (current && current !== input && retainsPrompt(current, prompt)) {
          return current;
        }
      }
    }
    if (site.rebindEditor) {
      const current = editor();
      if (current && retainsPrompt(current, prompt)) return current;
    }
    if (!retainsPrompt(input, prompt)) {
      throw new BridgeFailure("Chat editor did not accept the prompt");
    }
    return input;
  }
  async function fillPrompt(input, prompt) {
    input.focus();
    if (input.isContentEditable) {
      return await fillContentEditable(input, prompt);
    }
    // Qwen arms Send only after composer-like keystrokes, not a silent value set.
    if (typeof input.setSelectionRange === "function") {
      input.setSelectionRange(0, input.value.length);
    }
    document.execCommand("selectAll", false);
    if (document.execCommand("insertText", false, prompt) && samePrompt(draft(input), prompt)) {
      await new Promise(resolve => setTimeout(resolve, 80));
      // Composers that arm Send from keystrokes (Qwen) keep going when the
      // button is still disabled; submitComposer waits for it.
      if (!site.send?.wait || sendArmed(site.send.button())) return input;
    }
    // Fallback: a few real insertText keystrokes arm React composers (Qwen Send,
    // Gemini/Google AI Mode, etc.); never type the full system prompt one char
    // at a time — paste the remainder in one shot.
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    if (!setter) throw new BridgeFailure("Unsupported chat input");
    setter.call(input, "");
    input.dispatchEvent(new InputEvent("input", { bubbles: true, cancelable: true, inputType: "deleteContentBackward" }));
    const armUntil = Math.min(prompt.length, 24);
    let built = "";
    for (let offset = 0; offset < armUntil; offset++) {
      const chunk = prompt[offset];
      built += chunk;
      setter.call(input, built);
      input.dispatchEvent(new InputEvent("beforeinput", {
        bubbles: true, cancelable: true, inputType: "insertText", data: chunk,
      }));
      input.dispatchEvent(new InputEvent("input", {
        bubbles: true, cancelable: true, inputType: "insertText", data: chunk,
      }));
      if (offset % 8 === 7) await new Promise(resolve => setTimeout(resolve, 0));
    }
    if (built.length < prompt.length) {
      if (typeof input.setSelectionRange === "function") input.setSelectionRange(built.length, built.length);
      const rest = prompt.slice(built.length);
      if (!(document.execCommand("insertText", false, rest) && samePrompt(draft(input), prompt))) {
        setter.call(input, prompt);
        input.dispatchEvent(new InputEvent("input", {
          bubbles: true, cancelable: true, inputType: "insertFromPaste", data: rest,
        }));
      }
    }
    input.dispatchEvent(new Event("change", { bubbles: true }));
    for (let attempt = 0; attempt < 25 && !samePrompt(draft(input), prompt); attempt++) {
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    return input;
  }
  document.addEventListener("opencode-local-response-v1", async event => {
    let value;
    try { value = JSON.parse(event.detail); } catch (_) { return; }
    if (!pending || value?.nonce !== pending.nonce) return;
    if (value.observing === true) {
      pending.sawCapture = true;
      button.title = "Receiving the matched site response";
      return;
    }
    if (value.error) {
      await finish({ error: "Completion stream observation failed" }).catch(() => {});
      return;
    }
    const job = pending;
    // Gemini AI Mode: never accept a completion body that still carries the
    // pasted OpenCode tool preamble — that is an early shell, not the answer.
    if (site.provider === "gemini" && typeof value.body === "string") {
      let decoded = "";
      try { decoded = atob(value.body); } catch (_) { decoded = ""; }
      const plain = decoded.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
      if (plain.includes("Available tools, given as JSON Schema")
          || (plain.includes("```tool_calls") && plain.includes("You can call functions"))) {
        const wanted = (job.prompt || "").replace(/\s+/g, " ").trim();
        const rest = wanted && plain.includes(wanted)
          ? plain.split(wanted).join(" ").replace(/\s+/g, " ").trim()
          : "";
        if (!rest) {
          button.title = "Gemini: skipped prompt echo, still waiting";
          document.dispatchEvent(new CustomEvent("opencode-gemini-reopen-v1", {
            detail: JSON.stringify({ nonce: job.nonce }),
          }));
          return;
        }
      }
    }
    // Give the frontend a bounded chance to render its final answer and route.
    for (let attempt = 0; attempt < 50 && pending === job; attempt++) {
      if (site.path.test(location.pathname)) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    if (pending !== job) return;
    let reported = typeof value.path === "string" && site.path.test(value.path)
      ? value.path : location.pathname;
    if (site.provider === "gemini") {
      const match = reported.match(/^\/(?:u\/\d+\/)?app\/([\w-]+)$/);
      reported = match ? "/app/" + match[1] : reported;
      // Prefer DOM Markdown over forwarding the raw folif/wire body. See the
      // "Gemini browser answer capture" note in providers/browser_protocol.py
      // (gemini_answer): other tab providers unwrap JSON/SSE; AI Mode does not
      // yet expose an equivalent stable text frame.
      if (await finishGeminiDomAnswer(job.prompt, reported)) return;
    }
    await finish({ status: value.status, body: value.body, path: reported,
      ...(value.request_turn ? { request_turn: value.request_turn } : {}),
    }).catch(() => label("OpenCode: ответ не принят"));
  });

  async function execute(job) {
    if (!Number.isFinite(job.expires_in) || job.expires_in <= 0) throw new BridgeFailure("Invalid browser job deadline");
    const deadline = performance.now() + Math.min(job.expires_in, 1800) * 1000;
    let input = editor();
    // Leftover text from a previous failed bridge attempt matches this job's
    // prompt; clear it. Any other draft is the user's — do not overwrite.
    // looseEmpty: empty ProseMirror ZWSP must not count as an unsent draft.
    await clearLeftoverDraft(input, job.prompt);
    // New jobs (path=null) used to always navigate to site.home (/work), which
    // forces a fresh Vibe chat every turn. Stay on the visible conversation.
    let target = job.path || site.home;
    if (!job.path && site.path.test(location.pathname)) target = location.pathname;
    if (target !== site.home && !site.path.test(target)) throw new BridgeFailure("Invalid conversation route");
    if (!sameRoute(location.pathname, target)) {
      // Keep only a tab id and execution flag across navigation. No prompts,
      // response bodies or local pairing key are put in page storage.
      // The old document remains alive until navigation commits. Stop polling
      // now so it cannot repeatedly abort a slow navigation to the same route.
      await rpc("/browser/navigate", "POST", identity(job));
      clearInterval(timer);
      // /app/aimode is only a conversation token for AI Mode on google.com.
      // Bounce that token to the AI Mode search URL — never gemini.google.com.
      let navTarget = target;
      if (geminiAiModeToken(target)) navTarget = "/search?udm=50";
      const origin = location.hostname === "www.google.com"
        && /^\/(?:u\/\d+\/)?app\//.test(navTarget)
        && navTarget !== "/app/aimode"
        ? "https://gemini.google.com"
        : location.origin;
      location.assign(origin + navTarget);
      return;
    }
    // The extension can run before the SPA hydrates its signed-in editor.
    for (let attempt = 0; !input && enabled && attempt < 50; attempt++) {
      if (attempt % 5 === 0 && !(await rpc("/browser/check", "POST", identity(job))).active) {
        throw new BridgeFailure("The browser job was cancelled");
      }
      await new Promise(resolve => setTimeout(resolve, 100));
      input = editor();
    }
    if (!enabled || !sameRoute(location.pathname, target)) throw new BridgeFailure("The tab changed before submission");
    if (!input) throw new BridgeFailure("The signed-in chat input is unavailable");
    await clearLeftoverDraft(input, job.prompt);
    if (input.disabled || input.getAttribute("aria-disabled") === "true") throw new BridgeFailure("The chat input is busy");
    if (job.submitted) throw new BridgeFailure("The prompt was already submitted");
    pending = { ...job, nonce: crypto.randomUUID(), deadline };
    // Use strings across Safari's isolated/page worlds. Do not send upstream
    // until the observer confirms that this exact job can be captured.
    await new Promise((resolve, reject) => {
      const nonce = pending.nonce;
      const timeout = setTimeout(() => {
        document.removeEventListener("opencode-local-ready-v1", ready);
        reject(new BridgeFailure("Reload the tab to load the response observer"));
      }, 1500);
      function ready(event) {
        let value;
        try { value = JSON.parse(event.detail); } catch (_) { return; }
        if (value?.nonce !== nonce) return;
        clearTimeout(timeout);
        document.removeEventListener("opencode-local-ready-v1", ready);
        if (value.observing !== true) {
          reject(new BridgeFailure("The site replaced the response observer; reload the tab"));
          return;
        }
        resolve();
      }
      document.addEventListener("opencode-local-ready-v1", ready);
      document.dispatchEvent(new CustomEvent("opencode-local-job-v1", {
        detail: JSON.stringify({ nonce, prompt: job.prompt, path: job.path ?? null }),
      }));
    });
    // Submission may legitimately wait for the provider's pacing interval.
    // The job deadline still bounds this RPC; ordinary status calls stay short.
    await rpc("/browser/submit", "POST", identity(job), Math.max(1, Math.ceil(deadline - performance.now())));
    if (performance.now() >= deadline || !(await rpc("/browser/check", "POST", identity(job))).active
        || performance.now() >= deadline) {
      throw new BridgeFailure("The browser lease ended before insertion");
    }
    if (!enabled || pending?.id !== job.id) throw new BridgeFailure("The tab was disconnected before submission");
    if (!sameRoute(location.pathname, target) || editor() !== input
        || (site.looseEmpty ? !draftIsEmpty(input) : !!draft(input)?.trim())) {
      throw new BridgeFailure("The editor changed before submission");
    }
    const originalDraft = draft(input);
    let insertedDraft, insertedMarkup, dispatching = false;
    try {
      const filled = await fillPrompt(input, job.prompt);
      if (filled && filled !== input) input = filled;
      insertedDraft = draft(input);
      // Invoke the site's own send handler. The observer never constructs an
      // upstream request or touches the user's account credentials.
      if (site.provider !== "glm") {
        // Allow the frontend to commit the insertion before its send handler.
        await new Promise(resolve => setTimeout(resolve, site.send?.settleMs || 0));
        // Safari can expose the inserted text only on the next event-loop turn.
        // Rich editors also represent line breaks with DOM nodes, not text.
        // ChatGPT ProseMirror often replaces the node after React commits.
        if (site.rebindEditor) {
          const current = editor();
          if (current) input = current;
        }
        if (!retainsPrompt(input, job.prompt)) {
          throw new BridgeFailure("The editor did not retain the requested prompt");
        }
        insertedDraft = draft(input);
        insertedMarkup = input.isContentEditable ? input.innerHTML : null;
        if (!(await rpc("/browser/check", "POST", identity(job))).active) {
          throw new BridgeFailure("The browser job was cancelled after insertion");
        }
        if (!enabled || pending?.id !== job.id) throw new BridgeFailure("The tab was disconnected after insertion");
        if (!sameRoute(location.pathname, target)) throw new BridgeFailure("The conversation changed after insertion");
        if (site.rebindEditor) {
          const current = editor();
          if (!current || !retainsPrompt(current, job.prompt)) {
            throw new BridgeFailure("The editor draft changed after insertion");
          }
          input = current;
        } else if (editor() !== input) {
          throw new BridgeFailure("The editor was replaced after insertion");
        }
        // Vue may normalize DOM (extra <br>/NBSP) after await; keep text identity.
        if (!retainsPrompt(input, job.prompt)) {
          throw new BridgeFailure("The editor draft changed after insertion");
        }
        insertedDraft = draft(input);
        // Kimi/ChatGPT rewrite innerHTML while keeping the same text.
        if (!site.ignoreMarkup && insertedMarkup !== null && input.innerHTML !== insertedMarkup) {
          throw new BridgeFailure("The editor markup changed after insertion");
        }
      }
      if (performance.now() >= deadline) throw new BridgeFailure("The browser lease expired before sending");
      // A missing form must leave the inserted draft in place for cleanup.
      let form = null;
      if (site.send?.mode === "form") {
        form = input.closest(site.send.form);
        if (!form) throw new BridgeFailure("The chat submission form is unavailable");
      }
      dispatching = true;
      await submitComposer(input, job.prompt, form);
      label("OpenCode: запрос выполняется · отключить");
    } finally {
      // Remove only our unchanged insertion when no send handler was invoked.
      // Local cleanup must not depend on a cancelled lease accepting a result.
      if (!dispatching && insertedDraft !== undefined && input.isConnected
          && sameRoute(location.pathname, target) && editor() === input && draft(input) === insertedDraft
          && (insertedMarkup === undefined || insertedMarkup === null || input.innerHTML === insertedMarkup)) {
        input.focus();
        if (input.isContentEditable) {
          const range = document.createRange();
          range.selectNodeContents(input);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          document.execCommand(originalDraft ? "insertText" : "delete", false, originalDraft || null);
        } else {
          Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, originalDraft);
          input.dispatchEvent(new Event("input", { bubbles: true }));
        }
      }
    }
    // Sites flagged stalls may leave the HTTP body open after the UI finishes.
    // A capture that has started waits until the job deadline, which is
    // PROVIDER_RESPONSE_TIMEOUT. Twenty seconds applies only when no bytes were seen.
    // Gemini AI Mode: DOM scrape runs only after a completion is observed (or
    // as a late stall fallback), never as an early 8s race against the stream.
    if (site.stalls && dispatching && pending?.id === job.id) {
      const sentAt = performance.now();
      while (pending?.id === job.id && performance.now() < deadline) {
        if ((site.provider === "mistral" || site.provider === "claude")
            && performance.now() - sentAt > 8000) {
          if (site.provider === "mistral" && await finishMistralDomAnswer(job.prompt)) break;
          if (site.provider === "claude" && await finishClaudeDomAnswer(job.prompt)) break;
        }
        if (performance.now() - sentAt > 20000 && !pending.sawCapture) {
          if (site.provider === "mistral" && await finishMistralDomAnswer(job.prompt)) break;
          if (site.provider === "claude" && await finishClaudeDomAnswer(job.prompt)) break;
          if (site.provider === "gemini" && await finishGeminiDomAnswer(job.prompt)) break;
          throw new BridgeFailure(site.provider === "grok"
            ? "Grok completion was not observed over REST or WebSocket"
            : "Completion request was not observed");
        }
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      if (pending?.id === job.id && pending.sawCapture) {
        if (site.provider === "mistral" && await finishMistralDomAnswer(job.prompt)) return;
        if (site.provider === "claude" && await finishClaudeDomAnswer(job.prompt)) return;
        if (site.provider === "gemini" && await finishGeminiDomAnswer(job.prompt)) return;
        throw new BridgeFailure("Completion stream observation stalled");
      }
    }
  }

  const geminiHtmlToMarkdown = root => {
    const walk = (node, opts = {}) => {
      if (!node) return "";
      if (node.nodeType === 3) return node.nodeValue || "";
      if (node.nodeType !== 1) return "";
      const tag = node.tagName.toLowerCase();
      if (tag === "script" || tag === "style" || tag === "svg" || tag === "button") return "";
      const cls = typeof node.className === "string" ? node.className : "";
      // Query rewrite / OpenCode paste lives in h3.MheKwc — never the model answer.
      if (tag === "h3" && /\bMheKwc\b/.test(cls)) return "";
      // Citation chips, "use code carefully", share/feedback chrome.
      if (/\b(?:DHPVt|NMq1me|iMHp6c|vIWmYe|ofHStc|UrecDd|YOTKvb)\b/.test(cls)) return "";
      // AI Mode fenced code: language chip + pre/code body.
      if (/\baiModeUiCodeBlock__Container\b/.test(cls) || /\bBpxUDf\b/.test(cls)) {
        const lang = (node.querySelector(".GlO4G")?.textContent || "").trim();
        const code = (node.querySelector("pre code")?.textContent
          || node.querySelector("pre")?.textContent || "").replace(/\n+$/, "");
        if (!code) return "";
        return "\n\n```" + lang + "\n" + code + "\n```\n\n";
      }
      const heading = /^h[1-6]$/.test(tag)
        || node.getAttribute("role") === "heading"
        || /\b(?:otQkpb|ZLzG0d)\b/.test(cls);
      // Citation chips stay hidden, but AI Mode often puts section numbers
      // ("1.", "2.") in aria-hidden spans inside headings — keep those.
      if (node.getAttribute("aria-hidden") === "true" && !opts.inHeading) {
        const label = (node.textContent || "").trim();
        if (!/^\d+[.)]?$/.test(label)) return "";
      }
      const kids = (next = opts) => Array.from(node.childNodes).map(child => walk(child, next)).join("");
      if (tag === "br") return "\n";
      if (tag === "hr") return "\n---\n\n";
      if (heading) {
        const levelMatch = tag.match(/^h([1-6])$/);
        let level = levelMatch ? Number(levelMatch[1]) : Number(node.getAttribute("aria-level") || 2);
        if (!Number.isFinite(level) || level < 1) level = 2;
        if (level > 6) level = 6;
        const inner = kids({ inHeading: true }).replace(/\s+/g, " ").trim();
        if (!inner) return "";
        return "\n\n" + "#".repeat(level) + " " + inner + "\n\n";
      }
      if (tag === "strong" || tag === "b") return "**" + kids() + "**";
      if (tag === "em" || tag === "i") return "*" + kids() + "*";
      if (tag === "code") {
        const body = kids().replace(/`/g, "\\`");
        return node.closest("pre") ? body : "`" + body + "`";
      }
      if (tag === "pre") return "```\n" + kids().replace(/\n+$/, "") + "\n```\n\n";
      if (tag === "li") {
        const mark = opts.ordered ? `${opts.index || 1}. ` : "- ";
        return mark + kids().trim().replace(/\n+/g, " ") + "\n";
      }
      if (tag === "ul") return kids({ ordered: false }) + "\n";
      if (tag === "ol") {
        let index = Number(node.getAttribute("start") || 1) || 1;
        return Array.from(node.children).map(child => {
          if (child.tagName && child.tagName.toLowerCase() !== "li") return walk(child, opts);
          const row = walk(child, { ordered: true, index });
          index += 1;
          return row;
        }).join("") + "\n";
      }
      if (tag === "a") {
        const href = node.getAttribute("href") || "";
        const label = kids().trim() || href;
        if (href && !href.startsWith("javascript:") && !href.includes("google.com/search")) {
          return "[" + label + "](" + href + ")";
        }
        return label;
      }
      // Answer paragraphs are div.n6owBd — keep as blocks. Other wrappers just flatten.
      if (/\bn6owBd\b/.test(cls)) {
        const inner = kids().trim();
        return inner ? inner + "\n\n" : "";
      }
      if (tag === "p" || tag === "section") {
        const inner = kids().trim();
        return inner ? inner + "\n\n" : "";
      }
      return kids();
    };
    let text = walk(root).replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
    // Google AI Mode footer (any language variant we know).
    text = text.replace(
      /(?:\n|\s)*(?:В ответах искусственного интеллекта могут быть ошибки\.|AI responses may (?:include|contain) mistakes\.)[\s\S]*$/i,
      "",
    ).trim();
    return text;
  };

  const scrapeGeminiVisibleAnswer = prompt => {
    const norm = value => (value || "").replace(/\s+/g, " ").replace(/[\u200b\ufeff]/g, "").trim();
    const wanted = norm(prompt);
    const looksLikePrompt = text => {
      const got = norm(text);
      if (!got) return true;
      if (wanted && (got === wanted || got.startsWith(wanted) && got.length <= wanted.length + 96)) {
        return true;
      }
      if (got.includes("Available tools, given as JSON Schema")) return true;
      if (got.includes("```tool_calls") && got.includes("You can call functions")) return true;
      if (wanted && wanted.length >= 48 && got.includes(wanted.slice(0, 80))
          && got.length <= wanted.length + 160) return true;
      return false;
    };
    const lastOf = sel => {
      const nodes = document.querySelectorAll(sel);
      return nodes.length ? nodes[nodes.length - 1] : null;
    };
    // Latest AI Mode turn (.Zkbeff) → answer column only (skip h3.MheKwc rewrite).
    const turn = lastOf(".Zkbeff");
    const root = (turn && (
      turn.querySelector('[data-container-id="main-col"]')
      || turn.querySelector(".mZJni")
    ))
      || lastOf('[data-container-id="main-col"]')
      || lastOf(".mZJni")
      || lastOf('[data-subtree="aimc"]')
      || lastOf('[data-message-author-role="model"]')
      || lastOf("message-content")
      || lastOf(".model-response-text");
    if (root) {
      const clone = root.cloneNode(true);
      clone.querySelectorAll("h3.MheKwc, .DHPVt, .ofHStc, .UrecDd, .YOTKvb").forEach(el => el.remove());
      const text = geminiHtmlToMarkdown(clone);
      if (text && !looksLikePrompt(text)) return text;
    }
    for (const sel of [
      '[data-container-id="main-col"]',
      ".mZJni",
      '[data-subtree="aimc"]',
      '[data-message-author-role="model"]',
      ".model-response-text",
      "message-content",
    ]) {
      const nodes = Array.from(document.querySelectorAll(sel));
      for (let i = nodes.length - 1; i >= 0; i--) {
        const el = nodes[i];
        if (el.closest('[contenteditable="true"], textarea, form, rich-textarea')) continue;
        const clone = el.cloneNode(true);
        clone.querySelectorAll("h3.MheKwc, .DHPVt").forEach(node => node.remove());
        const text = geminiHtmlToMarkdown(clone);
        if (text && !looksLikePrompt(text)) return text;
      }
    }
    return null;
  };

  // AI Mode final answer → Markdown POST. Deliberate fork from Grok/Mistral/…
  // (those finish with the captured network body). Revisit wire-first if late
  // /async/folif gains plain assistant text; keep this as fallback. Details:
  // providers/browser_protocol.py above gemini_answer.
  const finishGeminiDomAnswer = async (prompt, reportedPath) => {
    if (site.provider !== "gemini") return false;
    const text = scrapeGeminiVisibleAnswer(prompt);
    if (!text) return false;
    const bytes = new TextEncoder().encode(text);
    let encoded = "";
    for (let i = 0; i < bytes.length; i += 16384) {
      encoded += String.fromCharCode(...bytes.subarray(i, i + 16384));
    }
    let reported = typeof reportedPath === "string" ? reportedPath : location.pathname;
    const match = reported.match(/^\/(?:u\/\d+\/)?app\/([\w-]+)$/);
    if (match) reported = "/app/" + match[1];
    else if (googleAiModePage() || reported === "/search" || reported.startsWith("/search?")) {
      // Same synthetic token the server stores after the first AI Mode turn.
      reported = "/app/aimode";
    }
    await finish({ status: 200, body: btoa(encoded), path: reported });
    return true;
  };

  const scrapeMistralVisibleAnswer = prompt => {
    const norm = value => (value || "").replace(/\s+/g, " ").replace(/[\u200b\ufeff]/g, "").trim();
    const wanted = norm(prompt);
    const pick = el => norm(el?.innerText || el?.textContent || "");
    const selectors = [
      '[data-message-role="assistant"]',
      '[data-role="assistant"]',
      '[data-testid*="assistant"]',
      '[class*="assistant-message"]',
      '[class*="AssistantMessage"]',
      '[class*="assistant"] .markdown',
      '[class*="assistant"] .prose',
      '[role="article"]',
    ];
    for (const sel of selectors) {
      const nodes = Array.from(document.querySelectorAll(sel));
      for (let i = nodes.length - 1; i >= 0; i--) {
        const el = nodes[i];
        if (el.closest('[contenteditable="true"], textarea, form[data-composer], rich-textarea')) continue;
        const text = pick(el);
        if (!text || text === wanted) continue;
        if (wanted && text.length <= wanted.length + 8 && text.includes(wanted.slice(0, 32))) continue;
        return (el.innerText || el.textContent || "").trim();
      }
    }
    return null;
  };

  const finishDomSseAnswer = async (text, path) => {
    if (!text) return false;
    const bytes = new TextEncoder().encode(
      "data: " + JSON.stringify({ type: "message.delta", text }) + "\n\n"
      + "data: " + JSON.stringify({ type: "message.completed" }) + "\n\n"
      + "data: [DONE]\n\n",
    );
    let encoded = "";
    for (let i = 0; i < bytes.length; i += 16384) {
      encoded += String.fromCharCode(...bytes.subarray(i, i + 16384));
    }
    await finish({ status: 200, body: btoa(encoded), path });
    return true;
  };

  const finishMistralDomAnswer = async prompt => {
    if (site.provider !== "mistral") return false;
    const text = scrapeMistralVisibleAnswer(prompt);
    const reported = site.path.test(location.pathname) ? location.pathname : location.pathname;
    return finishDomSseAnswer(text, reported);
  };

  const scrapeClaudeVisibleAnswer = prompt => {
    const wanted = String(prompt || "").replace(/\u200b|\ufeff/g, "").trim();
    const pick = el => (el.innerText || el.textContent || "").replace(/\u200b|\ufeff/g, "").trim();
    const usable = text => {
      if (!text || text === wanted) return false;
      if (wanted && text.length <= wanted.length + 8 && text.includes(wanted.slice(0, 32))) return false;
      return true;
    };
    // Prefer completed assistant turns (data-is-streaming="false") + markdown blocks.
    const streaming = Array.from(document.querySelectorAll(
      'div[data-is-streaming="false"] .standard-markdown, div[data-is-streaming="false"], .font-claude-response, [data-testid="assistant-message"]',
    ));
    for (let i = streaming.length - 1; i >= 0; i--) {
      const el = streaming[i];
      if (el.closest('[contenteditable="true"], textarea, [data-testid="chat-input"]')) continue;
      if (el.getAttribute("data-is-streaming") === "true") continue;
      const text = pick(el);
      if (usable(text)) return text;
    }
    return scrapeMistralVisibleAnswer(prompt);
  };

  const finishClaudeDomAnswer = async prompt => {
    if (site.provider !== "claude") return false;
    // Still streaming — wait for data-is-streaming to settle.
    if (document.querySelector('div[data-is-streaming="true"]')) return false;
    const text = scrapeClaudeVisibleAnswer(prompt);
    const reported = site.path.test(location.pathname) ? location.pathname : "/";
    return finishDomSseAnswer(text, reported);
  };

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
        catch (error) {
          const reason = bridgeFailureReason(error)
            || "Tab unavailable, reloaded, or contains an unsent draft";
          pending = { ...job };
          await finish({ error: reason }).catch(() => {});
          enabled = false;
          await GM.setValue(activeKey, false);
          // Surface the failure on the badge so the tab does not fail silently.
          const detail = bridgeFailureReason(error)
            || [error?.name, error?.message].filter(Boolean).join(": ")
            || reason;
          label("ERROR: " + String(detail).slice(0, 90));
          button.title = detail;
        }
      }
    } catch (error) {
      label("OpenCode: локальный мост недоступен");
    }
    finally { busy = false; }
  }
  timer = setInterval(() => void poll(), 500);
  window.addEventListener("pagehide", () => clearInterval(timer), { once: true });
  if (enabled && TOKEN !== "__BRIDGE_TOKEN__") void poll();
})();
