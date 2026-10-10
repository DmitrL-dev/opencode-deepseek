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

### Qwen session

Before use, run `python -m qwen.auth --keep-open` in a separate bridge terminal
and leave the Qwen window open. Website tokens are short-lived (about 15 minutes
in the observed session). The command saves changes made by the website; the
server reads the updated session before the next request. It does not call a
refresh API or replay rejected messages. Complete any new sign-in manually.
Closing the window stops the handoff. `login_required` means no usable session
is available; selecting a model in the menu does not verify authentication.

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
