<p align="center">
  <img src="docs/media/bridge-hero.svg" alt="Local Account Bridge — DeepSeek Harness and OpenCode" width="100%">
</p>

<p align="center">
  <a href="README.md">Русский</a> ·
  <a href="#quick-start">Install</a> ·
  <a href="#connect-harness">Harness</a> ·
  <a href="#connect-opencode">OpenCode</a> ·
  <a href="#provider-status">Providers</a> ·
  <a href="docs/HARNESS.en.md">Harness guide</a>
</p>

# Your AI accounts — in DeepSeek Harness and OpenCode

One local server connects **DeepSeek Harness or OpenCode** to your DeepSeek,
Qwen, Kimi and GLM accounts through an OpenAI-compatible API. Your client runs
project tools; the bridge sends messages to the selected provider. Website
adapters do not require a separate provider API key.

**Harness works independently: you do not need OpenCode to use it.**
Everything documented here is on this fork's `main` branch.

[![Main checks](https://github.com/DmitrL-dev/opencode-deepseek/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/DmitrL-dev/opencode-deepseek/actions/workflows/tests.yml)

> This is not an unlimited API. Account quotas and local budgets apply.
> Automating a website chat can lead to restrictions: our DeepSeek account was
> restricted repeatedly, with the exact cause undetermined. A passing test does
> not guarantee the next task. [Account safety](docs/ACCOUNT_SAFETY.md).

## Quick start

You need **Python 3.9+** (3.12 recommended), Git, an account with your selected
provider, and either [DeepSeek Harness](https://www.deepseek.com/en/harness/) or
[OpenCode](https://opencode.ai/docs/). These commands target macOS/Linux;
see [Windows and detailed setup](docs/SETUP.en.md).

### 1. Install the bridge

```bash
git clone https://github.com/DmitrL-dev/opencode-deepseek.git
cd opencode-deepseek
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

When updating an existing installation, preserve `.env` and `session/`.
All providers start disabled; enable only the ones you intend to use.

### 2. Connect an account

This first-run example uses **Qwen Flash**:

```bash
python -m qwen.auth
```

Complete sign-in yourself in the opened window and wait for the session to be
saved. Change this setting in `.env`:

```dotenv
QWEN_ENABLED=1
```

Initial sign-in needs a browser; requests do not need open tabs afterward.
Tokens are short-lived. Optionally set `QWEN_RENEW_SESSION=1` to renew before a
new request. Once the sign-in cookie expires, manual sign-in is required again.
[Renewal details](docs/HARNESS.en.md#qwen-session).

**Using DeepSeek instead?** Run `python -m deepseek.auth`, sign in, and set
`DEEPSEEK_ENABLED=1`. Replace `qwen3.8-omni-flash` in the Harness command below
with `deepseek-chat` or `deepseek-expert`. DeepSeek has no automatic renewal;
saved sessions have no local age cutoff. Token rejection (`40002`/`40003`) stops requests without retry and requires manual sign-in. Stop if the
normal website shows an account restriction.

For Kimi, follow [HTTP session setup](docs/KIMI.md); for GLM, use the
[Safari tab adapter](browser/README.md#safari).

### 3. Start the server

```bash
python app.py
```

Keep this terminal running. One process serves all enabled providers; do not
start another instance. In a separate terminal:

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/v1/models
```

These local checks send no model requests. Confirm your model appears in the
catalog, then connect **your client** below.

## Connect Harness

1. Install and open [official Harness](https://www.deepseek.com/en/harness/)
   once. During initial setup, you can choose **Add API Key → Set up later**.
2. In another terminal, enter the bridge directory, activate
   `source venv/bin/activate`, and run this while the server is running:

```bash
python -m integrations.harness --install desktop --without-browser --default-model qwen3.8-omni-flash
```

3. Select your **project folder**, create a **new session**, and choose
   **Local account bridge** → your model in Harness. Restart Harness if the
   group has not appeared. Existing sessions may retain their previous model.
4. Start with a small task, such as “Read this project's README and briefly
   explain its purpose.” Check the tool calls and final response.

The installer reads the local catalog, backs up the profile and updates
`~/.dsh/profiles/desktop/cordis.patch.yml`. It disables automatic retries and
model-generated titles. Setup sends no account requests.

| Connection | Harness configuration |
| --- | --- |
| DeepSeek, Qwen, direct HTTP Kimi | Use the command above with `--without-browser` and an available `--default-model` |
| GLM or `kimi-web` through a tab | Set up the tab; run installation **without** `--without-browser`, selecting `glm-web` or `kimi-web` |
| Web UI / CLI | For an initialized profile, replace `--install desktop` with `--install web` / `--install headless` |
| Enabled or disabled another provider | Restart the bridge and reinstall the catalog with your intended flags |

`--without-browser` filters adapters; it does not remove initial sign-in or
turn GLM into a direct HTTP connection. Catalog visibility does not confirm
account availability. [Full Harness guide](docs/HARNESS.en.md).

### Counters and reset inside Harness

After [installing the local budget plugin](docs/HARNESS.en.md#local-budget-commands),
these commands run **without calling a model**:

| Command | Result |
| --- | --- |
| `/bridge-budget` | Six rolling DeepSeek budgets: generations, new chats and HTTP requests per hour/day, with remaining capacity |
| `/bridge-budget-reset` | Preview only; no counters changed |
| `/bridge-budget-reset CONFIRM` | Explicit confirmation in the same session within 60 seconds |

Reset clears local usage only. It does not clear provider pauses, change website
quotas, request intervals or conversation history. Active or unresolved requests,
and usage changes since the preview, prevent reset. The plugin requires its own
private token; installing the model catalog alone does not install these commands.

### Refresh DeepSeek through Safari

On macOS, select **deepseek-login** in the Harness command menu or enter
`/deepseek-login`. Safari opens; sign in to the same account if needed. The
session transfers locally without an inspector or copying tokens. Check the
result with `/deepseek-login-status` and resume your goal manually.

One-time setup: update the local budget plugin, install
[DeepSeek session transfer](browser/deepseek-session-transfer.user.js) in the
Userscripts app's Save Location, and allow access to `chat.deepseek.com` and
`127.0.0.1`. Restart the updated bridge and Harness after active work finishes.
This flow requires macOS and `http://127.0.0.1:8000`.
[Setup and recovery rules](docs/HARNESS.en.md#update-the-session-through-safari).

History, counters and request intervals are preserved; goals never resume
automatically. The first transfer asks you to confirm the same account; later
transfers compare its saved identifier. This is manual session recovery, not
background renewal. Offline checks passed; this new flow has not yet been
verified end to end with a live account.

### If Harness stops

| Message / symptom | Check |
| --- | --- |
| `Local request budget exhausted` | Rolling budgets, conversation reply cap or prompt size. A new chat or restart does not clear rolling usage |
| `MISSING_CREDENTIAL` for `deepseek-official` | The official API route needs a key. To use the bridge, select **Local account bridge** |
| `login_required` | Saved authentication expired; sign in to the selected provider again |
| `quota_exceeded`, `user is muted`, security refusal | Stop and inspect the normal website; do not retry automatically |
| Missing group or model | Check `.env`, the running server and `/v1/models`, then reinstall the catalog |

DeepSeek defaults are **12/60 generations, 3/6 new chats and 40/200 HTTP requests
per hour/day**, with 20 accepted replies per conversation and a 30-second
interval. `DEEPSEEK_MAX_*` settings belong to the bridge process environment.
Raising a local ceiling does not raise the website's allowance.
[Configuration and error guide](docs/HARNESS.en.md#local-limits-and-errors).

## Connect OpenCode

Use the same bridge and account setup above. Put the example
[`opencode.json`](examples/opencode.json) in your project. For existing configs,
merge settings while preserving your `provider`, `model` and `small_model`.
Set `small_model` explicitly: title generation can also consume messages.

```bash
cd /path/to/your-project
opencode --agent plan --model local-qwen/qwen3.8-omni-flash
```

For DeepSeek, select `local-deepseek/deepseek-chat` or
`local-deepseek/deepseek-expert`. API URL: `http://127.0.0.1:8000/v1`.
`apiKey: "unused"` is an SDK placeholder, not an account key or API access control.
[Agents and tool plugin](docs/SETUP.en.md#opencode-integration).

## Provider status

Implementation and historical checks as of **October 11, 2026**. A passing test
on one account does not establish current access or reliable unlimited goals.
Provider documentation retains detailed qualification results.

| Provider / model ID | Browser during requests | Status |
| --- | --- | --- |
| DeepSeek · `deepseek-chat`, `deepseek-expert` | Not needed after manual sign-in | Small Harness tasks passed; repeated account restrictions occurred. No automatic renewal |
| Qwen · `qwen3.8-omni-flash`, `qwen3.8-max` | Not needed after sign-in | Tested in Harness; Max reached a daily limit. [Session](docs/HARNESS.en.md#qwen-session) |
| Kimi · `kimi-instant` | Not needed after capture | Experimental HTTP: reply, renewal and Harness `read` passed |
| Kimi · `kimi-k3`, `kimi-k2.8` | Not needed after capture | Implemented, not qualified end to end: K3 refused free-user capacity; K2.8 was not attempted afterward. [Details](docs/KIMI.md) |
| GLM · `glm-web` | Connected tab required | Previously tested with GLM-5.3-Flash; browserless access remains incomplete. [Blockers](docs/GLM.md) |
| Kimi · `kimi-web` | Connected tab required | Older tab adapter; model selected on the website |
| Mistral / Grok | Tab required | Experimental: Mistral continuation returned `429`; Grok capture did not complete |
| Gemini / Antigravity | Official CLI | Request rejected by region in our test; working access not established |

All connections start disabled. Website versions, quotas and availability may
change. Chinese providers also have limits; the exact Qwen Max daily allowance
and reset time have not been established.

## Architecture

![Harness or OpenCode → local bridge → HTTP session, browser tab or CLI](docs/media/bridge-architecture.svg)

**Harness or OpenCode runs tools on your computer.** Prompts and tool-read data
are sent to the selected provider. The bridge validates responses and
`tool_calls`, saves conversation state and sends new tool results to the existing
chat. Unknown history stops instead of silently creating a new chat.

Refusal pauses survive restart. Client setup, ordinary sign-in and local budget
reset do not clear them. The explicit Safari transfer can clear an authentication
pause after a rejected challenge when there is no uncertain generation; it does
not clear account restrictions or quotas. Inspect pauses with
`python -m providers.access status`.
[Resume rules](docs/ACCOUNT_SAFETY.md).

The API binds to `127.0.0.1`; `session/` and `.env` are excluded from Git.
Do not publish them, personalized Userscripts or budget credentials.
Supported: text and emulated tool calls. Images, audio and video are not uploaded,
even for models named Omni.

## OpenCode demonstrations

Historical OpenCode 1.18.34 captures of a small fixture-file read.
These are **not Harness screenshots** or proof of current account availability.

| Qwen Flash | Qwen Max |
| --- | --- |
| ![OpenCode Qwen Flash](docs/media/qwen-flash-opencode.png) | ![OpenCode Qwen Max](docs/media/qwen-max-opencode.png) |

[GLM/Kimi captures and recording method](docs/media/README.md).

## Documentation and development

- [Harness: setup, sessions, budgets, offline verification](docs/HARNESS.en.md)
- [Full setup, OpenCode, API and autostart](docs/SETUP.en.md)
- [Kimi HTTP](docs/KIMI.md) · [GLM](docs/GLM.md) · [Safari Userscripts](browser/README.md)
- [Account safety](docs/ACCOUNT_SAFETY.md) · [API examples](examples/README.md)
- [Standalone DeepSeek terminal chat](docs/TERMINAL_CHAT.md)

CI covers Python 3.9/3.12, PoW WASM, shell/JavaScript and isolated browser fixtures.
Locally: `python -m unittest discover -s tests -t . -v`.
[Real Harness CLI fixtures with mocked providers](docs/HARNESS.en.md#offline-verification).

**MIT · unofficial project.** Based on
[Tsuev/opencode-deepseek](https://github.com/Tsuev/opencode-deepseek) and
[sums001/Deepseek-API](https://github.com/sums001/Deepseek-API).
Use accounts under each service's terms. [License](LICENSE).
