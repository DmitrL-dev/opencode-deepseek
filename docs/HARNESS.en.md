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

The filter retains enabled Qwen, DeepSeek, Gemini and the explicitly enabled
experimental `kimi-instant`, `kimi-k3` and `kimi-k2.8` models
([qualification and capacity limits](KIMI.md)). It excludes
GLM, `kimi-web`, Grok and Mistral because those adapters require a connected tab.
It does not move web sessions to another transport, enable providers, or clear
pauses. This selects the catalog at installation time; pass `--without-browser`
again when updating the profile. Other Harness providers and existing sessions
are preserved. If the previous default is excluded, supply a new
`--default-model`; setup refuses to write until you choose one.

Qwen passed live Harness verification with the browser closed. Initial sign-in
and sign-in after the refresh cookie expires still need a browser. On October 10,
2026, DeepSeek Chat completed a `read` and answer task in Harness 0.2.0-rc.2 in
12 seconds with its browser closed. Normal website access was checked first;
the client was fixed to accept both create-chat response shapes. Expert later
passed a bounded native Harness task: `read`, `write`, and a final answer in
three generations / 24.5 seconds, with the browser closed. Written multiline
text, quotes and backslashes matched byte for byte. No automatic continuation
or retry was used. This qualifies that small file task, not an unrestricted
long-running goal. Gemini uses the official CLI, which rejected our test by
region. Not needing a tab does not establish account availability.

Mistral hit its quota during continuation, and Grok's response capture did not
complete; neither belongs in a verified working set. Kimi has frequency limits
and a [shared credit pool](https://www.kimi.ai/help/membership/membership-overview),
but this account's allowance has not been established. On October 10, 2026,
Qwen3.8-Max returned a daily-limit notice as normal answer text, allowing Harness
to continue its goal repeatedly. The bridge now classifies this known notice as
`quota_exceeded` and durably pauses all Qwen requests instead of reporting success.
Resume only after manually checking access; Flash limits and the exact reset
time remain unknown. A lack of quota refusals from GLM does not prove unlimited access. The official Z.ai API is a separate,
key-based access method: its [pricing](https://docs.z.ai/guides/overview/pricing)
distinguishes free GLM-4.7-Flash / GLM-4.5-Flash from paid GLM-5.3-Flash.
Setup never substitutes another model or paid API for the selected web model.

### DeepSeek session

`python -m deepseek.auth --keep-open --channel chromium` leaves the manual
sign-in window open until it is closed or interrupted with Ctrl+C. Use it to
inspect restrictions on the normal website. Changed credentials are saved
privately; token capture does not confirm access or clear a pause. Use
`--channel chrome` for installed Chrome. This inspection mode never falls
back to another browser channel.

Harness requests use the saved cache only. After six hours it is considered
stale and requires manual sign-in; DeepSeek has no automatic renewal. Failure
logs contain only the protocol phase, HTTP status and fixed error category,
without credentials, response bodies or prompts.

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

After the repeated October 10 restriction, live qualification is stopped. Keep `TOOLCALL_FORMAT_REPAIR=0` and `TOOLCALL_MAX_CONTINUATIONS=0`. The bridge now resumes saved conversations and sends only new results; unknown history stops without a new-chat fallback. Durable load budgets and a 20-reply conversation cap apply. See [account safety](ACCOUNT_SAFETY.md).

For unattended DeepSeek goals, optionally set `TOOLCALL_FORMAT_REPAIR=1` in
the bridge process environment and restart the bridge. After a normally
completed malformed JSON reply, commentary mixed with a tool block, or unsupported
DSML, the bridge asks once for a newly formatted response in the same conversation.
The original calls are not executed and argument values are never guessed.
A second invalid reply is terminal; quota/account refusals, cancellation and
output-limit replies do not trigger correction. The additional generation costs
quota and observes the normal request interval. This setting defaults to off.

Create the goal in the repository's own workspace: Workspace Write permissions
apply to that directory. A successful text response alone does not establish
that tools ran or the goal completed.

On October 10, 2026, live DeepSeek Expert goals were tested in Harness 0.2.0-rc.2
with this option enabled. A regression-test coding goal finished in 4 minutes
5 seconds and 23 steps: the model fixed its failing assertion and completed the
goal itself. An intermediate task exposed two further response-handling defects:
a missing outer `]` and a false positive on a protocol mention in the final text.
Both were reproduced offline and fixed. The final fresh control goal on the
fixed bridge finished in 64 seconds and 7 steps: 67 tests, diff validation,
goal completion and a final answer. Neither successful run received manual
steering after its initial task; no subagents or runtime browser were used.
This qualifies bounded tasks, not error-free execution of every possible goal.

## Offline verification

The current suite was verified with installed Harness **0.2.0-rc.2**: 13 real CLI scenarios and setup
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

[GLM browserless access blockers](GLM.md).
