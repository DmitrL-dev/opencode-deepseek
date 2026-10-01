// ==UserScript==
// @name         OpenCode browser response observer
// @namespace    opencode-local-bridge
// @version      0.1.5
// @description  Observe only the completion caused by an active local bridge job.
// @match        https://chat.z.ai/*
// @match        https://grok.com/*
// @match        https://chat.mistral.ai/*
// @match        https://www.kimi.com/*
// @match        https://www.kimi.ai/*
// @inject-into  page
// @run-at       document-start
// @weight       999
// @noframes
// @grant        none
// ==/UserScript==

(() => {
  "use strict";
  const CHANNEL = "opencode-local-response-v1";
  const LIMIT = 8 * 1024 * 1024;
  const nativeFetch = window.fetch.bind(window);
  const completionPath = path => {
    if (location.hostname === "chat.z.ai") return path === "/api/chat/completions"
      || path === "/api/v2/chat/completions";
    if (location.hostname === "grok.com") return path === "/rest/app-chat/conversations/new"
      || /^\/rest\/app-chat\/conversations\/[\w-]+\/responses$/.test(path);
    if (["www.kimi.com", "www.kimi.ai"].includes(location.hostname)) return path === "/apiv2/kimi.gateway.chat.v1.ChatService/Chat";
    return location.hostname === "chat.mistral.ai";
  };
  let active = null;
  document.addEventListener("opencode-local-job-v1", event => {
    let value;
    try { value = JSON.parse(event.detail); } catch (_) { return; }
    active = value && typeof value.nonce === "string" && typeof value.prompt === "string"
      ? { nonce: value.nonce, prompt: value.prompt } : null;
    if (active) document.dispatchEvent(new CustomEvent("opencode-local-ready-v1", {
      detail: JSON.stringify({ nonce: active.nonce }),
    }));
  });

  async function observe(response, job) {
    const reader = response.body?.getReader();
    if (!reader) throw new Error("No completion response body");
    const parts = [];
    let size = 0;
    while (true) {
      if (active?.nonce !== job.nonce) {
        void reader.cancel().catch(() => {});
        return;
      }
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > LIMIT) {
        void reader.cancel().catch(() => {});
        throw new Error("Completion response exceeded the size limit");
      }
      parts.push(value);
    }
    if (active?.nonce !== job.nonce) return;
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const part of parts) { bytes.set(part, offset); offset += part.length; }
    let binary = "";
    for (let i = 0; i < bytes.length; i += 16384) {
      binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
    }
    document.dispatchEvent(new CustomEvent(CHANNEL, { detail: JSON.stringify({
      nonce: job.nonce, status: response.status, body: btoa(binary),
    }) }));
  }

  window.fetch = async function (input, init) {
    const job = active;
    let candidate = null;
    if (job) {
      try {
        const request = new Request(input instanceof Request ? input.clone() : input, init);
        const url = new URL(request.url);
        if (request.method === "POST" && url.origin === location.origin && completionPath(url.pathname)) {
          // No request headers, cookies, tokens or unrelated responses are
          // read. The site's own request must contain this job's exact prompt.
          candidate = request.clone().text().then(body =>
            body.includes(job.prompt) || body.includes(JSON.stringify(job.prompt).slice(1, -1)));
        }
      } catch (_) { /* Nonstandard requests are left untouched. */ }
    }
    const matches = candidate ? await candidate.catch(() => false) : false;
    const response = await nativeFetch(input, init);
    const compatibleResponse = location.hostname !== "chat.mistral.ai" || response.status !== 200
      || (response.headers.get("Content-Type") || "").includes("text/event-stream");
    if (matches && compatibleResponse && active?.nonce === job.nonce) {
      // Clone before returning: the frontend may immediately consume its own
      // body. Never await the clone's stream or block frontend rendering.
      void observe(response.clone(), job).catch(() => {
        document.dispatchEvent(new CustomEvent(CHANNEL, {
          detail: JSON.stringify({ nonce: job.nonce, error: "Completion observation failed" }),
        }));
      });
    }
    return response;
  };
})();
