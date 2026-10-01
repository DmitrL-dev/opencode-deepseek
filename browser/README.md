# Experimental providers using your signed-in browser

Grok, Mistral, Kimi and GLM use the normal site's send handler in an existing
signed-in tab. They do not copy cookies, passwords or Google OAuth tokens into
the bridge. Each provider has a separate queue and scoped conversation ids.
All four integrations are opt-in, use the selected web model, and remain
experimental until verified with the provider's current frontend and account.

## Safari

The two scripts work with [Userscripts for Safari](https://github.com/quoid/userscripts),
distributed through the developer's App Store link. The observer runs in the
page context without privileged APIs. The controller runs in the isolated
content context and keeps its local pairing key out of page JavaScript.

1. Run `python -m providers.tab_bridge` in this repository. It writes personalized
   scripts to `session/browser-bridge/userscripts/` with private permissions.
   Never upload, commit or share that directory: the controller contains your
   local pairing key. The generic files in `browser/` contain no key.
2. Install Userscripts and add the two exported `.user.js` files to its scripts
   directory. Grant access only to `chat.z.ai`, `grok.com`, `chat.mistral.ai`, and
   `www.kimi.com`; the bridge does not need access to Google sign-in pages.
3. Keep the API bound to `127.0.0.1:8000`. Set `BROWSER_BRIDGE_ENABLED=1` and enable
   the provider you want, e.g. `GLM_ENABLED=1`, then restart the API.
4. Open the provider in your normal signed-in browser and reload once to load
   both scripts. Click **Подключить вкладку к OpenCode**. Keep that tab open.
   Do not use it for manual chat while a bridge request is running.
5. Point an OpenAI-compatible client at the same `/v1` URL. Model ids are
   `grok-web`, `mistral-web`, `kimi-web`, and `glm-web`. They deliberately do not
   claim a specific model version; select the desired model in the site's UI.

Requests to the local job endpoints require the private pairing key and a
loopback client address. There is no CORS grant. A claimed job is tied to one
tab, provider, random id and lease; cancelled, late and replayed results fail.
Unsent drafts cause a failure rather than being overwritten. A reload during
generation fails the job instead of automatically repeating its prompt.

Completed text is buffered and validated before any tool calls are returned.
SSE keepalives protect client timeouts. Quota, security and region rejections
are terminal; the bridge does not rotate accounts, solve captchas, change
fingerprints, or retry through another endpoint.

## Gemini

Install the [official Antigravity CLI](https://antigravity.google/docs/cli/)
and complete its Google account sign-in yourself. Individual free access to the
old Gemini CLI was retired; do not reuse its OAuth credentials in this bridge.

Run `agy models` and put the account's real slugs into `GEMINI_MODELS`, for
example a JSON object mapping your chosen `gemini-...` public name to the exact
CLI slug. Set `GEMINI_ENABLED=1`. No Gemini models are advertised when the map
is empty. The adapter runs the official CLI in an empty temporary workspace,
with a custom text agent containing `tools: []`, no slash commands and no
permission bypass. OpenCode remains responsible for tool execution.
The CLI's streaming initialization must confirm the selected model, custom
agent, empty tool list and normal permission mode before the prompt is sent.
Only one successful terminal result is accepted; tool/subagent events fail.
An available model list does not prove account eligibility: the CLI checks
regional access again when starting a completion.

If the official CLI's token exchange fails specifically on an IPv6 socket,
`python -m providers.antigravity_ipv4` runs the same official CLI through a
loopback CONNECT tunnel using IPv4 on your existing network route. Google TLS
is not decrypted; OAuth traffic is not logged. This optional workaround refuses
to override an existing proxy. It is not a region-access workaround: stop if
Google rejects your account or region. No system, browser, DNS or VPN settings
are modified.

## Verification

`python -m unittest discover -s tests -q` checks routing, authentication, queue
ownership, cancellation, protocol completion and CLI isolation. To also run the
owned browser fixtures, install Playwright Chromium and use
`RUN_BROWSER_FIXTURES=1 python -m unittest tests.test_userscripts -q`. Those
fixtures intercept all requests; they do not authenticate or contact providers.
Passing fixtures are not proof of a successful live Safari/provider integration.
