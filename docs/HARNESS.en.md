# DeepSeek Harness

The bridge connects to the installed [DeepSeek Harness](https://www.deepseek.com/en/harness/)
through its standard `openai-completions` adapter. Website credentials, request
queues, and durable provider pauses remain in the bridge.

## Setup

Open the official Harness app once. Its welcome screen offers **Add API Key →
Set up later**. Configure your chosen bridge providers using the
[setup guide](SETUP.en.md) and start `python app.py` unless it is already running.
From the bridge checkout with the virtualenv activated:

```bash
python -m pip install -r requirements.txt
python -m integrations.harness --install desktop --default-model qwen3.8-omni-flash
```

Setup reads only `GET http://127.0.0.1:8000/v1/models`, backs up the exact previous
configuration, and updates `~/.dsh/profiles/desktop/cordis.patch.yml`. Enabled
bridge models appear under **Local account bridge**. A requested default absent
from that catalog fails before writing. Harness reloads the configuration;
restart the app if necessary. Select a workspace and start a new session.
Existing sessions may retain their previous model.

Omit `--default-model` to preserve your current default. Use `--install web` for
the initialized Web UI profile or `--install headless` for the initialized CLI
profile. `--home` / `DSH_HOME` selects another Harness home; `--base-url` selects
another **loopback** bridge endpoint.

All bridge adapters share this protocol: DeepSeek, Qwen Flash/Max, GLM, Kimi,
Grok, Mistral, and Gemini models configured through `GEMINI_MODELS`. Their actual
availability and qualification differ: see [provider status](../README.en.md).

### Run without an open browser

To include only adapters that do not need a browser while serving requests:

```bash
python -m integrations.harness --install desktop --without-browser --default-model qwen3.8-omni-flash
```

The filter retains enabled Qwen, DeepSeek, and Gemini models. It excludes GLM,
Kimi, Grok, and Mistral because their current adapters require a connected tab.
It does not move web sessions to another transport, enable providers, or clear
pauses. This selects the catalog at installation time; pass `--without-browser`
again when updating the profile. Other Harness providers and existing sessions
are preserved. If the previous default is excluded, supply a new
`--default-model`; setup refuses to write until you choose one.

Qwen passed live Harness verification with the browser closed. Initial sign-in
and sign-in after the refresh cookie expires still need a browser. DeepSeek
also uses a saved session, but restored access to the restricted account has
not been confirmed. Gemini uses the official CLI, which rejected our test by
region. Not needing a tab does not establish account availability.

Mistral hit its quota during continuation, and Grok's response capture did not
complete; neither belongs in a verified working set. Kimi has frequency limits
and a [shared credit pool](https://www.kimi.ai/help/membership/membership-overview),
but this account's allowance has not been established. No observed quota refusal
for Qwen or GLM proves unlimited access. The official Z.ai API is a separate,
key-based access method: its [pricing](https://docs.z.ai/guides/overview/pricing)
distinguishes free GLM-4.7-Flash / GLM-4.5-Flash from paid GLM-5.3-Flash.
Setup never substitutes another model or paid API for the selected web model.

### Qwen session

Sign in with `python -m qwen.auth`. Website tokens are short-lived (about 15
minutes in the observed session). For longer use, separately set
`QWEN_RENEW_SESSION=1` in `.env` and restart the bridge. It is off by default.

Only before a new user request and before creating a chat, the bridge may call
Qwen's normal renewal endpoint once with the existing sign-in cookie. It checks
account continuity, advanced expiry, and an unchanged local session. Renewal
belongs to the same guarded attempt: cancellation, refusal, uncertain outcome,
or persistence failure stops the request. Rejected messages are never replayed,
pauses are never cleared, and no browser starts. There are no background timers.
When the sign-in cookie expires too, manual sign-in is required. For a persistent
pause, inspect the normal website and follow ACCOUNT_SAFETY.md first.

Without this option, the server reads cached sessions only and `login_required`
requires manual sign-in. The optional `python -m qwen.auth --keep-open` observes
an open website window but does not guarantee renewal while idle. Use it as a
manual mode, separately from server renewal. Selecting a model in the menu does
not verify authentication.

On October 10, 2026, live requests in Harness **0.2.0-rc.2** passed after the
safety changes: Max returned a control answer; Flash executed `read` on a small
owned fixture and returned its contents. A later Flash test started with a
genuinely expired cached session and the browser closed. One guarded HTTP renewal
advanced the expiry, then Flash read `probe.txt` and returned its exact contents
in two steps over 15 seconds; the Qwen guard remained clear. The renewal used the
normal frontend `Timezone` header derived from actual host time and UTC offset.
Max then returned `QWEN_MAX_READY` in five seconds using the renewed session.
These results qualify the tested account and session only; they do not guarantee
future renewal or access. An open window alone does not guarantee renewal while
idle.

## Behavior and limits

- Harness retries are explicitly disabled with `retryPolicy: {mode: normal,
  maxRetries: 0}`; its standard SDK also disables internal retries.
- The profile's `session-title-llm` is disabled to avoid a separate model request
  for chat titles. Additional user-installed plugins may make their own calls.
- Other ordinary provider settings and comments are preserved. Custom bundle,
  root, or home overrides are refused instead of being silently overwritten.
  `!!js` is never evaluated by setup. Export for manual integration with
  `python -m integrations.harness --output session/harness.patch.yml`.
  The exported `llm-pi-ai.config` replaces that entry's entire configuration;
  merge existing `providers` before applying it to a custom profile.
- This integration supports text and emulated tool calls through Chat Completions.
  Images, Responses API, and Harness controls for thinking/search are unsupported.
- The 32768 context / 4096 output values are conservative client budgets, not
  website guarantees. The bridge does not forward token caps or sampling options.
- `Authorization: Bearer unused` is a public placeholder for the keyless loopback
  API, not an account token or API access control. Keep the bridge port local.

Setup never enables providers, clears pauses, or sends an account verification
prompt. An expired restriction date does not prove restored access. Resume only
after a manual website check; see [account safety](ACCOUNT_SAFETY.md). All
providers have account limits, including Chinese models. Tool continuations can
consume another message.

## Offline verification

Verified with installed Harness **0.1.7-rc.1** and **0.2.0-rc.2**: four real CLI scenarios and setup
tests. For an owned HTTP 503 fixture, the default policy made six requests;
the bridge configuration made one. No real accounts were used.

```bash
python -m unittest tests.test_harness -v
HARNESS_COMMAND='["/absolute/path/to/dsh"]' RUN_HARNESS_FIXTURES=1 \
  python -m unittest tests.test_harness_runtime -v
```

The second command uses the real Harness CLI with temporary HOME/DSH_HOME and
workspace, the real local FastAPI server, and a replaced provider transport.
It checks SSE, a native file-read tool round trip, durable refusal handling,
and no retry after HTTP 503. This does not qualify live accounts.

Reference: [official Harness model configuration](https://deepseek-harness.github.io/deepseek-harness/en/guide/providers).
