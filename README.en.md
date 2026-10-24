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
Local fixes and live verification are identified separately from published code below.

**Security update, October 12, 2026:** the local `/v1` API now requires a bridge
token. After updating, reconfigure your client using
[API access token](#api-access-token). Account binding, replay protection,
shell handling and CLI cleanup fixes were verified offline; this update was
not tested against live accounts in Harness, OpenCode or a browser.

[![Main checks](https://github.com/DmitrL-dev/opencode-deepseek/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/DmitrL-dev/opencode-deepseek/actions/workflows/tests.yml)

> This is not an unlimited API. Account quotas and local budgets apply.
> Automating a website chat can lead to restrictions: our DeepSeek account was
> restricted repeatedly, with the exact cause undetermined. A passing test does
> not guarantee the next task. [Account safety](docs/ACCOUNT_SAFETY.md).

**Authorization and recovery contract, offline:** history import and saved-answer
delivery share a contract binding exact field presence, effective `thinking`,
the tool protocol and schemas. Import also verifies the dispatch prompt hash.
Contract changes stop the operation; legacy permits without verified provenance
are rejected and never automatically rearmed. Existing history keys and consumed
permits are preserved. Recovery keeps omitted parameters omitted; account,
parameter or configuration changes do not authorize saved-answer delivery.
The installed Harness lifecycle is now checked separately with DSML and JSON:
read, one write, verification, acceptance and restarts. This uses a local fixture
provider, not a live generation reliability test. The fixes have been applied
to the running bridge and the bridge restarted; local `/healthz` responds.
History, counters, pauses and the Harness profile are byte-for-byte unchanged.
The goal was not resumed and no account requests were made.

**Current status, October 24:** request provenance and the independent goal
review handoff were fixed offline (`harness-contract-v8`). Logging no longer
turns omitted `thinking` into explicit `false`; a separate receipt records the
effective dispatch settings. `request_goal_review` disarms automatic continuation
without completing the goal. The operator accepts the result with
`/bridge-goal-review` and a one-use `/bridge-goal-complete` command. The installed
Harness completed a local fake-provider lifecycle covering two rounds, write/read,
review readiness and restarts. This does not verify fresh DeepSeek output quality;
autonomous web generation remains unverified. The fixes are installed in the
working bridge and Harness; both were restarted. A local status command confirmed
`harness-contract-v8`; rejecting an empty request verified exact parameter
preservation in the running bridge without provider dispatch. The offline suite
ran 997 tests with 68 skips and no failures. Original history, counters and pause
were preserved; the goal was not continued. Harness appended only session-load
lifecycle and local status-command events.
[Request provenance and goal review](docs/HARNESS.en.md#request-provenance-and-goal-review-v8).

**Subsequent v8 live check:** one explicitly authorized retry from response #36
received #42 with confirmed `thinking=true`. The complete saved stream
(`finish_reason=stop`) already contains a malformed parameter closing tag:
`</｜｜DSML｜｜DSML｜ parameter>`. The entire batch was rejected, no tools executed,
and no further retry occurred. Parameter preservation worked, but DSML output
reliability remains unresolved. The goal is stopped; old history and pause were
preserved, and counters recorded one generation and one HTTP request. No paid
API was used.

**October 23 live check:** a fresh Harness session stopped at step four on
October 23 because malformed DSML parameters were already present in DeepSeek's
complete response. The previously completed goal and green CI do not establish
reliable operation on arbitrary goals. The web transport sends textual
instructions without an enforced generation schema.
[Protocol review and verification limits](docs/PROTOCOL-AUDIT.2026-10-23.md).
The installed CLI was checked offline with three successful actions followed by
a damaged fourth reply: the entire bad batch is rejected, completed work stays
intact, and resubmitting the same history does not generate again. This verifies
replay protection; it does not fix the text generator.


**October 24 goal-control check:** local `harness-contract-v7` prevents the model
from closing the whole goal based on a partial report; separate
[whole-scope acceptance](#whole-goal-completion-control) is required. 50 offline
checks passed. After installation, one web-bridge continuation stopped on a
malformed `create_goal.objective` tag in complete response #28. After the offline
fix, the exact saved batch was recovered without generation: its original
`create_goal` and Bash calls succeeded. The successor goal with 241 remaining
rounds was left inactive. 159 parser/contract checks and 8 recovery checks passed
offline. History and usage were preserved. Full autonomy remains unverified;
these changes are local and were
not published to `main` during this check.

The exact spelling `<｜DSML｜ objective" string="true">…</｜DSML｜ parameter>`
is now decoded only with DeepSeek wrapper tolerance enabled and **that literal
field name** declared in an unambiguous object schema. Names, values and types
are not guessed; the entire batch retains the existing schema checks. Truncated
values, unknown fields, partially damaged attributes and quoted examples never
become calls. Updating the parser alone neither redelivers an old reply nor
resumes a goal.

**Continuation after #28:** two further steps successfully read the plan/backlog
and located related documents. At step three, complete response #34 expressed
`read` arguments as attributes (`file_path="…" offset="192" limit="45"`) rather
than DSML parameters. The original stream reconstructs the same text without
truncation. Both decoders initially rejected the batch; the goal stopped without
executing the call or regenerating. Prior history and usage were preserved;
only actual usage from the three authorized steps was added. This run does not
establish autonomous execution through completion.

**Recovery of #34:** attributes are now accepted only as one complete record
inside `invoke`, with a single explicitly declared scalar type for every field.
Strings stay literal; numbers, booleans and null must parse as JSON and match the
schema type. Unions, `$ref`, absent types, unknown/duplicate fields, mixed
`parameter` records and truncated framing are rejected. All schema constraints
and whole-batch validation remain mandatory; XML entities and escape sequences
are not decoded. 167 parser/contract checks and 8 recovery checks passed offline.
The saved `read` from #34 executed successfully once in Harness without generation;
the goal remains inactive. History, usage and pauses were preserved. The normal
profile was restored and the working bridge restarted with the fix.

**Next continuation:** one Bash step succeeded, but response #38 at step two
entered a repetition loop: 1,344 `description` attributes, 141,907 characters,
and no closing `invoke`/`calls` tags. The provider ended it as `INCOMPLETE`
(`length`), while the local stream capture is complete. No calls from that batch
executed; the goal stopped without retry. Recovery was refused: discarding the
tail and inventing closure is not allowed. This is a generation failure, not
another accepted attribute spelling.

**Offline protection against the #38 loop:** the prompt now requires one canonical
DSML form, each field exactly once, and immediate invocation/batch closure.
Historical compatible forms remain parser-only. For a reply starting with `calls`,
the HTTP client stops reading on a repeated parameter declaration within one
`invoke`, or more than 16 consecutive redundant parameter closers. The entire
batch fails; the connection closes, the private prefix is retained, and existing
replay protection and safety pauses remain in force. No repair request is sent.
This bounds structural repetition, not every possible text loop; closing the
connection does not guarantee that provider-side computation stops.
206 tests passed offline. The exact saved #38 stopped at character 1,186 under
three stream chunk sizes. No closure was invented and no call executed.
The fix was then applied by restarting the working bridge from the updated tree;
the new process returns `ok` from `/healthz`. In the same environment, 45 focused
offline checks passed and exact #38 stops at character 1,186. History, profile,
usage and pause records remain unchanged; the goal was not resumed and no live
generation was performed during deployment.

**One authorized live attempt after the fix:** response #40 from parent #36
finished normally (`stop`) but contained malformed DSML closing tags. This reply
did not loop; the whole batch was rejected, no tools were delivered, and the goal
remains stopped. No further attempt or new chat was made. The exact stream was
replayed offline; 33 tests passed. The Harness journal, profile and every history
binding table were preserved. The budget recorded one generation and one HTTP
request; counters were not reset, with expired entries pruned by the normal
24-hour window. Successful autonomous operation after the fix is not yet verified.

**Offline web-contract fixes, October 24:** an authorized Harness catalog
upgrade now sends the new system instruction and complete schemas once, including
in compact mode. Every mode sends the current `tool_choice`; `auto` explicitly
replaces the preceding reply's tool-selection policy. Verification covered 238
tests and 35 pinned archived cases through the parser and API paths, with network
access denied. These fixes prevent lost request constraints; they **do not repair
the confirmed parameter damage in the latest DeepSeek response**. No account or
working history was used for the test run.
[Evidence and the remaining limitation](docs/WEB-CONTRACT.2026-10-24.md).

For API-key users, the separate [official Harness route](#official-deepseek-api-in-harness-optional)
completed a live native-tool task with all four tests passing.
On October 24, the resumed installer-development goal also completed: its final
continuation ran 12 steps without errors and reached native `complete`.

## DeepSeek review, October 21, 2026

DeepSeek [updated its website, app and API to V4.1-Flash-1017](https://api-docs.deepseek.com/news/news261017/).
The bridge sends `default` (`deepseek-chat`) or `expert` (`deepseek-expert`),
not a weight revision: the service updates the selected mode. This does not
prove a particular response version or automatically add a new model ID.
DeepSeek recommends Harness `0.2.1-rc.1` or newer for the new model; compatibility
of an upgraded Harness with the plugins needs separate verification.

The offline review reproduced insufficient spacing between generations and a
missing PoW expiry check after solving/waiting. The fixes add an
independent generation deadline that survives restart, sign-in and budget reset.
Challenge request latency and solving time count against PoW lifetime, which is
checked again before sending. Expiry stops the request without automatically
fetching another challenge or generating again. These fixes are included in `main`.
The installed Harness upgrade from `0.2.0-rc.2` to `0.2.1-rc.2` was checked with
the profile and history preserved.

Main offline run: **887 tests, 835 passed, 52 skipped**. A new installed Harness
CLI fixture separately verified exact file contents and tool-result delivery
without replay. The child-process check blocked by the sandbox passed separately
in an earlier run. Nine real CLI
scenarios were previously checked on Harness `0.2.1-rc.2` with a synthetic provider
and outbound network isolation: read/write, refusal handling, shell pipelines and
no retry on HTTP 503. Additional checks cover one-use history links during saved
reply recovery and permissions when resuming a CLI session. Incorrect DSML prompt
markers and accepting malformed framing as successful text were fixed.
**October 23 live check completed:** the same long Rentgen goal reached native
`complete`, and the CLI exited with code 0. Harness saved the retained report
through a real `write`, checked the workspace, called `update_goal` and returned
its final answer. All 14,959 report bytes match the retained reply; recovery
did not regenerate it. A new focused Rentgen test selection returned 228 passed,
64 skipped; previously passing checks were not repeated. This completes one
concrete task, not a guarantee of error-free web-model behavior on every task.

A complete DSML `arguments` input
object is accepted only for an unambiguous direct object schema. It must be the
only typed parameter, contain only advertised fields and pass the whole original
schema. A real field named `arguments` is never unwrapped; composed/reference
schemas or schemas without required fields do not enable this decoding.
Malformed JSON, missing fields and invalid values still reject the entire batch.
In DSML mode, the exact named closers `</DSML｜ invoke>` and `</DSML｜ calls>`
are also recognized only at their corresponding outer element boundaries.
This spelling cannot open an element, delimit a parameter or close an unnamed
element; argument contents are never rewritten.
Both API implementations validate the saved reply with identical argument values;
its one-use recovery link was verified on a database copy without account access.
One intermediate refusal is a different case: an unknown extra tag contains malformed
JSON, followed by repeated arguments in a valid tag. An independent fold of the
complete SSE matches the reply. Neither the first encoding nor an invalid call
may be silently discarded. Two new offline tests check exact stream preservation
and atomic refusal by both API implementations without corrective requests.

Final recovery preserved all 469 previous history rows; new links continued the
counter from 16 to 19, and all three Harness sessions retain their old byte
prefixes. Recovery was followed by three normal generations / six HTTP requests,
with no new chat. Counters were not reset; credentials and protective limits
did not change. The verification goal's local round cap was raised from 8 to 10
through the native API while retaining used rounds. Its relay was stopped after
completion. The web transport sends text without
enforced `response_format` or native tools: fully autonomous operation on arbitrary
tasks remains unproven. Schema validation and replay protection were not weakened.
Previous offline
checks completed two native Harness goal rounds using a fixture provider,
which does not establish live model reliability.
[October 23 results and limits](docs/RECHECK.2026-10-23.md).
Thirty seconds is a local setting, not a provider-approved
safe quota. The exact cause of previous temporary restrictions remains unknown:
the original server codes were not retained. An elapsed restriction period does
not clear a local pause or establish the outcome of a pending request.
[Evidence and remaining unknowns](docs/RECHECK.2026-10-21.md).

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
new request; renewal runs inside that request's durable attempt and must return
the same account. Conversation links are bound to the Qwen account, so token
rotation keeps Harness goals resumable. Once the sign-in cookie expires, manual
sign-in is required again.
[Renewal details](docs/HARNESS.en.md#qwen-session).

**Using DeepSeek instead?** Run `python -m deepseek.auth`, sign in, and set
`DEEPSEEK_ENABLED=1`. Replace `qwen3.8-omni-flash` in the Harness command below
with `deepseek-chat` or `deepseek-expert`. DeepSeek has no automatic renewal;
saved sessions have no local age cutoff. Token rejection (`40002`/`40003`) stops requests without retry and requires manual sign-in. A new `python -m deepseek.auth` capture keeps saved Harness histories only for the same verified account; another account is refused (use `--replace-account` deliberately), and an unverified previous session needs `--same-account`. Stop if the
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
curl -H "Authorization: Bearer $(cat "$(python -m server.api_auth path)")" http://127.0.0.1:8000/v1/models
```

These local checks send no model requests. Every `/v1` request needs the
bridge API token (see [API access token](#api-access-token)). Confirm your model appears in the
catalog, then connect **your client** below.

## API access token

The `/v1` API drives your signed-in provider accounts, so it requires
`Authorization: Bearer <token>` and a local `Host`. The server creates the
private token file `session/api-token` (`0600`) on first use;
`python -m server.api_auth path` prints its path, never the token. Requests
without the token, from a browser page (cross-site or DNS rebinding) or for
another Host are refused before any provider or rate-limit access.

Migration from the keyless API:

1. Update the bridge and restart it.
2. Harness: rerun `python -m integrations.harness --install <profile> ...` with
   your previous options. It replaces the `Bearer unused` header with the token
   in the private profile file. Restart Harness.
3. OpenCode: set `"apiKey": "{file:/absolute/path/session/api-token}"` for each
   local provider ([example](examples/opencode.json)). Other OpenAI SDK clients
   pass the token as `api_key`.
4. Only while migrating, `API_AUTH=legacy-loopback` admits tokenless requests
   from a loopback peer with a loopback Host and no browser headers. Remove it
   once clients send the token.

`HOST` other than loopback is refused unless `API_ALLOW_PUBLIC_BIND=1` is set
(token mode only). Then also list the served names in `API_ALLOWED_HOSTS` and
restrict the port with a firewall. This bind check lives only in `python app.py`:
launching `uvicorn server.api:app --host …` directly bypasses it, while Host and
token checks (except the transitional `legacy-loopback`) still apply to every `/v1`.

## Connect Harness

1. Install and open [official Harness](https://www.deepseek.com/en/harness/)
   once. During initial setup, you can choose **Add API Key → Set up later**.
   **Local account bridge** requires neither a Harness account sign-in nor an
   official DeepSeek API key. If Welcome reappears after restarting, repeat
   this choice: in Harness 0.2.1-rc.2 it lasts for the current launch only and
   does not change the provider session stored in the bridge.
2. In another terminal, enter the bridge directory, activate
   `source venv/bin/activate`, and run this while the server is running:

```bash
python -m integrations.harness --install desktop --without-browser --default-model qwen3.8-omni-flash
python -m integrations.harness_shell_safety --profile desktop
```

3. Select your **project folder**, create a **new session**, and choose
   **Local account bridge** → your model in Harness. Restart Harness if the
   group has not appeared. Existing sessions may retain their previous model.
4. Start with a small task, such as “Read this project's README and briefly
   explain its purpose.” Check the tool calls and final response.

The installer reads the local catalog, backs up the profile and updates
`~/.dsh/profiles/desktop/cordis.patch.yml`. It disables automatic retries and
model-generated titles. Setup sends no account requests.
The menu keeps one entry per model: a bare ID and its `provider/model` alias
are combined, preferring the bare ID. If duplicates appear, rerun setup with
your previous options; the profile reloads without changing history or budgets.

| Connection | Harness configuration |
| --- | --- |
| DeepSeek, Qwen, direct HTTP Kimi | Use the command above with `--without-browser` and an available `--default-model` |
| GLM or `kimi-web` through a tab | Set up the tab; run installation **without** `--without-browser`, selecting `glm-web` or `kimi-web` |
| Web UI / CLI | For an initialized profile, replace `--install desktop` with `--install web` / `--install headless` |
| Enabled or disabled another provider | Restart the bridge and reinstall the catalog with your intended flags |

`--without-browser` filters adapters; it does not remove initial sign-in or
turn GLM into a direct HTTP connection. Catalog visibility does not confirm
account availability. [Full Harness guide](docs/HARNESS.en.md).

### Official DeepSeek API in Harness (optional)

With a funded API key, Harness can use its built-in **DeepSeek** provider directly.
This route sends tool schemas and calls through the Messages API; the web bridge's
textual DSML parser is not involved.

1. Open **Settings → Models → DeepSeek**, enter your API key and click **Apply**.
   Do not store the key in the repository or a conversation.
2. Close Harness and run from the repository root in an environment with the
   project dependencies installed:

   ```bash
   python3 -m integrations.harness_official --install desktop --dry-run
   python3 -m integrations.harness_official --install desktop --default-model deepseek-flash
   ```

3. Start Harness and create a new chat. Choose **DeepSeek → DeepSeek-V4.1-Flash**,
   without the `· web` suffix. Existing chats retain their selected model; the
   installer neither migrates their history nor resumes failed steps.

The installer supports initialized standard `desktop`, `web`, and `headless`
profiles; `--home` overrides `DSH_HOME` or `~/.dsh`. Without `--default-model`,
the current selection is preserved. Other providers and profile options are
retained; changed files and backups are written with `0600` permissions.
An unchanged reinstall writes nothing. `--dry-run` creates no files. The installer
makes no network requests, reads no keys or bridge `.env`, and leaves history,
pauses, and counters untouched.

Symlinked paths, ambiguous entries, dynamic configuration in edited plugins, and
custom bundle compositions are refused. For profiles with replaced plugins,
manually merge entries from [harness_official.patch.yml](integrations/harness_official.patch.yml)
through **Settings → Open configuration file**, preserving your settings.
**Do not replace the entire profile** with the example.

The configuration disables automatic retries, model-generated titles, and session
log uploads. Each response is capped at **32768 tokens, including reasoning**.
A long task exhausted the previous 8192-token cap before producing a tool call,
so the cap was increased. Reaching the cap stops the goal without an automatic
retry. This is **not a total spending cap**: the provider bills API usage, while
`/bridge-budget` measures only the web bridge. `strict` schema mode is not enabled.

**Verified October 23, 2026:** installed Harness `0.2.1-rc.2` completed one live API
task in four steps: two `read` calls, `edit`, one test run, then a final answer.
All four tests passed and the test file remained unchanged. Separate offline
fixtures verified exact argument round-tripping, automatic goal continuation
between turns, and stopping after HTTP 503, an incomplete stream, or the output
limit. Separate temporary-profile tests cover installer preservation, backups,
idempotency, rejection without writes, and secret-safe errors. The goal
remains disarmed after failure; explicit resume in a new process retains its
history without repeating the completed file write. These scenarios run the
installed Harness with a temporary profile and a loopback test server, without
an account. This confirms those checks, not error-free operation for every goal.

**Verified October 24, 2026:** after local fixes, the same installer-development
goal resumed. Its final live turn completed 12 steps and 21 tool calls without
errors, finished documentation and diff review, and marked the original goal
`complete`. The previously passing 13 installer tests were not repeated; code
had not changed since those tests. Earlier runs required installer and stop-policy
fixes, so this does not claim fully autonomous execution from the first attempt.
Web-bridge history, pause and counters were preserved. Separate offline checks
cover goal completion after fixing an assertion and retaining the mandatory
`read` guard while precondition recovery is enabled.

The same day, a long Röntgen task completed through the official API:
73 steps, 89 tool calls, and the original goal reached `complete`.
A confirmed notification-receiver initialization defect was fixed and boundary
checks added; the final suite reported 265 passed and 8 skipped. Three local
stops (no grep matches, Python import path, and a temporary-directory symlink)
were diagnosed and resumed by the operator without asking the user; this was
not an unaided model run. There were no API errors or subagents, and web-bridge
history and counters were preserved. The offline driver's turn-end observer
race was also fixed: a delayed callback no longer finishes a later turn based
on its newer goal state. All 9 installed-Harness fixtures using a local test API
passed, including failure without retry.

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
`/deepseek-login`. Safari opens; sign in to the same account if needed. Transfer
accepts the [RFC 6750](https://www.rfc-editor.org/rfc/rfc6750#section-2.1) Bearer
alphabet, including `+`, `/` and trailing `=`; whitespace, line breaks, Unicode
and excess length remain rejected. This fixes local refusals for some tokens
after successful sign-in; same-account checks and the safety pause remain enforced. The
session transfers locally without an inspector or copying tokens. Check the
result with `/deepseek-login-status`. `saved_paused` means the credentials were
saved while the entire safety pause and pending request remain. Review account
restrictions and the previous outcome first; refreshing credentials does not
authorize replaying a generation.

An explicit `40002/40003` refusal during chat creation or challenge retrieval,
before generation, permits session refresh. Ambiguous network failures and
refusals after generation dispatch still require investigation; no automatic replay.


One-time setup: update the local budget plugin, install
[DeepSeek session transfer](browser/deepseek-session-transfer.user.js) in the
Userscripts app's Save Location, and allow access to `chat.deepseek.com` and
`127.0.0.1`. Restart the updated bridge and Harness after active work finishes.
This flow requires macOS and the bridge on `127.0.0.1`. For a `PORT` other than
8000, run `python -m providers.tab_bridge` and install the exported copy from
`session/browser-bridge/userscripts`, and reinstall the budget plugin with
`python -m integrations.harness_budget --base-url http://127.0.0.1:<PORT> ...` (it
defaults to `PORT` from the environment or `.env`, then 8000).
An explicit `--base-url` takes precedence even when `PORT` is invalid; only
a loopback HTTP origin without credentials, a path, query or fragment is allowed.
[Setup and recovery rules](docs/HARNESS.en.md#update-the-session-through-safari).

History, counters and request intervals are preserved; goals never resume
automatically. The first transfer asks you to confirm the same account; later
transfers compare its saved identifier. This is manual session recovery, not
background renewal. Offline checks passed; this new flow has not yet been
verified end to end with a live account.

### Diagnose interrupted DeepSeek responses

Set `DEEPSEEK_STREAM_DIAGNOSTICS=1` and restart the bridge to save failed SSE
streams privately in `session/failed-streams/` (mode `0600`). Storage is limited
to four captures and 8 MiB of received bytes each. Responses may contain private
source code; do not publish these files. Successful responses are not saved.

Offline inspection: `python -m deepseek.stream_diagnostics session/failed-streams/<id>.json`.
Only a complete capture with a recognized completion marker and valid response
identity can produce a private `.review.json` **review candidate**. It is not
permission to execute tools: inspection never changes history, counters or pauses,
and never resumes goals. Missing completion markers or interrupted captures are
refused. An old failure without captured bytes cannot be reconstructed this way.
[Details](docs/HARNESS.en.md#private-stream-diagnostics).

DeepSeek `context_length_exceeded` means the provider conversation has exhausted
its context. It is not a local budget or expired session; resetting counters or
signing in again will not fix it. The bridge preserves history and stops, without
creating a new chat. Migrating the goal requires a separate manual decision.

Optional `TOOLCALL_LOG=1` enables detailed conversation traces in `logs/` (default
`0`, disabled). Each conversation gets one file named `MMDDHHmmss-<provider>.log`;
it accumulates agent requests, assembled provider prompts, raw provider replies,
final JSON/SSE responses to the agent, and errors, separated by `from agent`, `to provider`,
`from provider`, and `to agent` markers. Logs can contain source code, secrets and
tool results; enable only for local diagnosis and do not publish them. Git ignores
the directory. Implementation checks are offline; live-account exchanges were not
verified. Continuations are associated via the persisted conversation ID; if a
provider returns no ID, a later request cannot be linked to this trace.

New mode `TOOL_INSTRUCTIONS_BROWSER=medium` (the default stays `light`, unchanged:
tool names only). On chat continuations the reminder also includes each tool's
argument shape (field names, nesting, `?` = optional; no descriptions) so the model
does not guess arguments from memory; the first turn still gets the full block, as
in `light`. Continuation prompts are longer. A reply like `{"question": ..., "options": [...]}` instead of `{"questions": [...]}`
is rejected with "Tool arguments do not satisfy the advertised schema". No extra
generation.

Temporary switch `TOOLCALL_LEGACY` (default `1`): `1` — main-era contract for
**every** provider (`server/api.py` + `server/tool_protocol.py`: one block,
DeepSeek-only repair, always `invalid_tool_response`; will be removed); `0` — one
tool-rework path in `server/api_new.py` + `server/tool_protocol_new.py` for **every**
provider, including DeepSeek (demote, multi-block, pointed repair,
`TOOLCALL_INVALID_MODE`). Summary in
[SETUP §5](docs/SETUP.en.md#5-why-the-model-doesnt-make-edits-emulated-tool-calling);
longer notes (Russian):
[`docs/toolcall-format-repair-legacy.md`](docs/toolcall-format-repair-legacy.md)
(`LEGACY=1`) and
[`docs/toolcall-format-repair.md`](docs/toolcall-format-repair.md) (`LEGACY=0`).
In both modes, a complete native DeepSeek DSML reply retains the shared schema,
finish and `tool_choice` checks, without an extra generation.
With `TOOLCALL_LEGACY=0`, tool calls are handled like in any agent. Only a top-level
fenced `tool_calls` block is a call, wherever it stands in the message. Prose, text
after the block, several blocks, arrays without that fence (also inside a
```` ```json ```` block) and quoted blocks are plain text. This includes examples
inside `~~~`, fences of four or more backticks, and code indented by four spaces
or a tab: their tools are never executed. Verified offline; no live account was
used for these checks. Before validate/repair the
bridge may demote advice mistagged as a call: if a block has no call-level `name`
(JSON whole or broken) or the name is unknown and there is accompanying prose, the
opening fence is relabeled from `tool_calls` to `text` and the agent gets ordinary
successful `content` — no repair and no `TOOLCALL_INVALID_MODE`. Otherwise every
remaining block must be valid: broken JSON (including a known tool name), an unknown
name with no prose, or arguments off-schema stop the whole message (repair /
`TOOLCALL_INVALID_MODE`); there is no silent validate pass. When every block is
valid, all calls run in order: the agent receives them as `tool_calls` and the rest
of the message, with the call blocks cut out, as ordinary content. With
`stream: true` text and calls arrive as separate frames in the original order
(`text1`, `call1`, `text2`, `call2`, `text3`); `stream: false` still returns one
`content` and one `tool_calls` array. `tool_choice: none` never turns text into
an error. Verified offline.

With `TOOLCALL_FORMAT_REPAIR=1` (off by default), under `LEGACY=0` an erroneous call
gets one pointed correction in the same chat (one extra generation): for an unknown
tool the full list of tools with their argument shapes (field names, nesting, `?` =
optional; not shortened), for wrong arguments that tool's argument shape (no values).
No second correction is requested. If the corrected reply is valid the agent sees only
the final call, with no notice. What happens next is `TOOLCALL_INVALID_MODE` (below).
Under `LEGACY=1`, repair remains DeepSeek-only (main-era prompt). Verified offline.

`TOOLCALL_INVALID_MODE` (default `error`; only when `LEGACY=0`): what to do with an
invalid call after validation or after one failed correction — for **any** provider,
including DeepSeek. With `error` the agent gets
`invalid_tool_response` (fail-closed). With `text` — HTTP success and a live provider
chat, but an explicit failed tool step for the agent:

- `content`: raw reply plus `Bridge notice: BRIDGE_TOOL_NOT_EXECUTED: …` (tool name at
  most; no schemas or values);
- top-level `bridge_notice` (`executed: false`, `code`, `kind`, `tool`, `as_tool_error`
  shaped like a tool-role error);
- `message.refusal`: the same notice text (for clients that honor refusal);
- the next user message is sent to the provider tab as
  `Tool result (name): <notice>` plus the follow-up — as if the tool returned an error.

Executable `tool_calls` are not invented on reject (the agent would run them). Even with
`text`, these still error: `tool_choice` required/specific tool, a reply without a
conversation id, and an unfinished reply. Mistagged advice (no call-level name, or
unknown name with prose) is demoted to a `text` fence before this path.
Verified offline.

A call that repeats, with identical arguments, the call whose result the request
carries (the model answers a tool result with the same call again) is not executed a
second time: with `TOOLCALL_INVALID_MODE=text` the agent gets the same kind of message
(raw reply plus a line saying the call repeats the one just executed); with `error` it
raises. A nonempty new user message after the tool result starts a separate action:
for example, checking CI that was previously `pending` again. An empty message does
not clear this check. Replaying an already consumed history still fails before any
provider request. Verified offline.

In compact browser mode, an agent's tool result (for example the option you picked)
is sent as `Tool result (name id="..."): <result>`. IDs retain result binding;
multiple calls to the same tool also receive an exact call mapping. Complete schemas
are normally omitted, but the current `tool_choice` follows the result even in
`none` mode. `auto` explicitly replaces an earlier per-reply selection; task
constraints and schema validation remain enforced. An authorized catalog upgrade
sends complete schemas once. Invalid-call outcome follows `TOOLCALL_INVALID_MODE` (above).
Verified offline.

For DeepSeek, disabling `TOOLCALL_FORMAT_REPAIR` prevents an extra format-correction generation;
it does not disable tool-markup validation. Malformed tool blocks stop the step
instead of being returned as successful plain text.
After a normal DeepSeek stop, commentary may precede one explicit terminal
`tool_calls` block. The bridge locally normalizes flat arguments against the
advertised schema and known closing DSML markers without rewriting values. Empty
arguments are accepted only when the schema permits them. Every call undergoes
JSON Schema validation (types, required fields, nested structures and constraints);
one invalid call rejects the entire batch. Duplicate keys, unknown tools,
ambiguous envelopes, trailing text and incomplete data are rejected. Generic JSON
fences and quotations do not become calls. Prose quotations may precede a separate
top-level `tool_calls` block with a blank line before its opening fence. Quoted,
nested and ambiguous code blocks are not executed; `>` markers are never stripped
from code. An explicit `tool_calls` block is an
action protocol: do not use it for explanatory examples. Browser providers follow the
rules above instead. Remote schemas are never fetched; local `$ref` references work.
On update, install `requirements.txt` (the new dependency is `jsonschema`) and
restart the bridge.

A normally finished DeepSeek response may also contain **one whole native DSML
batch** (`calls` → `invoke` → `parameter`). The bridge decodes it locally for any
advertised tool and applies the same complete schema and `tool_choice` checks.
Values marked `string="true"` remain literal; `string="false"` requires valid
JSON, with no inferred types. Incomplete tags, duplicate parameters, unknown tools,
trailing text or one invalid call reject the entire batch. Quotations, Markdown
examples are never extracted as actions. DSML after commentary is also inert by
default; the explicitly selected dialect has the narrow exception below. XML entities
are not expanded and external resources are never loaded. No format regeneration
is needed. This fixes the saved `Unsupported tool-call markup` failure with a
complete DSML invocation; upgrading does not redeliver the failed reply or resume
the goal. Verified offline using the saved response and synthetic cases.

Clients that omit `thinking` can opt into `DEEPSEEK_TOOL_THINKING=1`:
DeepThink then applies only to DeepSeek requests with tools. The default is `0`;
explicit `thinking=false` takes precedence. Restart after changing it; the loaded
value appears in authenticated local `/admin/budget` as `tool_thinking_default`.
Selecting Expert alone does not enable DeepThink. The setting changes neither
history keys nor retry policy and does not guarantee valid tool output.

For a manually reviewed format failure, `checkpoint(..., format_recovery=True)`
archives the complete original request privately, preserves instructions and
observed action results, and replaces recent assistant notes with archive
references. Damaged markup is not used as an example for the next reply. This
only prepares context: it sends no request, clears no pause and never resumes a
goal automatically. Configuration and archive checks were performed offline.

DeepSeek can explicitly request `DEEPSEEK_TOOL_PROTOCOL=dsml` after a bridge
restart. The prompt uses the V4.1 reference vocabulary: `<｜DSML｜ calls>`, with
one separator on each side of `DSML` and a space before the tag name
([DeepSeek reference](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/encoding/README.md)).
The previous prompt incorrectly doubled those separators. Complete retained
packets using that older vocabulary remain readable for compatibility. With
`dsml` explicitly selected, both complete symmetric marker spellings are lexical
aliases at tag boundaries, including within one batch. Argument bytes are never
replaced; every parameter, invocation and batch must close. Asymmetric or
incomplete markers remain invalid. This asks for one complete native batch instead of a JSON fence;
literal command/file strings need no JSON quote or newline escaping. Complete
schemas, `tool_choice`, history, counters and replay prevention still apply.
Other providers and the `json` default stay unchanged. Historical calls retain
exact IDs as inert data rather than a second protocol example. Tool and field
names must fit DSML attributes (1–256 characters, no quotes, `<`, `>` or control
characters); strings containing DSML delimiters use a JSON string with
`string="false"` and escaped angle brackets. Broken mixed JSON/DSML packets fail
as a whole, without dropping a second invocation or demoting the failure to
successful text. Only with `dsml` explicitly selected may plain commentary precede
one complete packet whose opening tag starts on its own unindented line.
Blockquotes, Markdown fences, HTML, prefixes with backticks or a terminal colon
remain data. Trailing text, a second batch, incomplete tags and schema errors
remain disallowed. The prefix is never executed and parameter values are never
rewritten. With `dsml` explicitly selected and a normal stop, two envelope variants
are also recognized: one empty repeated `calls` header immediately after the
first opening, and `</｜DSML｜>` as an invocation closer. The latter is accepted
only after every parameter has closed; the root must still end with the named
`</｜DSML｜ calls>` tag. No values or missing tags are added. A third header,
nested batches, unnamed parameter/root closers, incomplete values or any schema
error reject the entire reply. These tolerances are off under the `json` default.
Verified offline against a retained stream and general synthetic packets, with
no format regeneration. Damaged DSML markers, including asymmetric and ASCII
separators, are recognized as format failures and retained in private diagnostics,
rather than returned as successful text. They are never repaired into calls.
Quoted, fenced and indented examples remain data. The latest ambiguous batch
was not restored or executed; upgrading never repeats a generation.
A long live goal with this option is not yet verified.
Keep `TOOLCALL_FORMAT_REPAIR=0` and
`TOOLCALL_MAX_CONTINUATIONS=0` when generation retries are forbidden. The loaded
dialect and those settings are visible locally in the authenticated
`/admin/budget` response under `tool_contract`.

A normal stop with a closed fence also permits swapping the final `]}` to `}]`
only when a string-aware scan confirms that arguments are complete and only the
call object and outer array remain open. No values or delimiters are added. The
entire corrected batch must pass JSON Schema validation; damaged strings, nested
arguments and ambiguous endings are rejected.

A normally finished DeepSeek reply with a closed fence may also recover
missing envelope `}` delimiters between calls or before the array end (up to 32),
only after each complete `arguments` object, with exactly
`name` and `arguments` in that call. Both the prefix and entire batch must parse
strictly after insertion. Argument values are never added or guessed. Every call
is validated against its schema; other syntax defects or damage inside arguments
reject the whole batch without partial execution.

Every reply passes one offline contract in `server/tool_protocol.py`: envelope
parsing → schema validation for every call → tool-choice and finish policy →
release of the entire batch. Transport and private regression replay share this
validator. Incomplete arguments and fabricated tool results remain errors.
This emulates calls through web-chat text, not a native tool API; passing offline
tests does not guarantee that the model's next response will be valid.

Opt-in private diagnostics retain up to 32 distinct failures in
`session/toolcall-failures/` (up to 4 MiB each), without deleting old evidence;
`last-toolcall-error.json` still updates. Replies may contain source code: the
directory is private and excluded from Git. DeepSeek diagnostics also retain
the exact response ID and incoming request hash, without the request text or
HTTP headers. These are binding-review metadata, never permission to redeliver
tools. With `DEEPSEEK_STREAM_DIAGNOSTICS=1` also enabled, the original response
stream for a failed tool reply is kept separately in `session/failed-tool-streams/`
(up to four files, up to 8 MiB of raw stream each, mode `0600`). This does not
consume network-failure evidence slots; duplicates use no additional slots and
old evidence is never deleted. Successful calls do not write streams to disk.
Upgrading cannot recover IDs already lost by an older version, fetch history,
or resume the goal. Run sanitized shared regressions:
`python -m unittest tests.test_tool_protocol tests.test_api tests.test_diagnostics`.
Replay a private corpus with reviewed expectations and pinned SHA-256 hashes:
`python -m server.offline_corpus session/protocol-corpus.json`.
This manifest is private, prepared during diagnostic review and not shipped in
the repository; parser output alone must not establish the expected result.
Each case can pin `protocol: "json" | "dsml"` and
`implementation: "legacy" | "new"`. Missing fields preserve the original
`json` / `legacy` policy, independently of `.env`. Pin immutable diagnostic
copies with SHA-256, rather than the overwritten `last-toolcall-error.json`.
`matched_outcomes` separately counts accepted batches, plain text and expected
refusals. A passing refusal check proves that a bad batch stops, not that a goal
succeeds. Invalid evidence and unknown policies fail verification.

When that directory is full, earlier evidence is never deleted. Private
diagnostics record `stream_capture_status=storage_full` when raw bytes could not
be saved. Authenticated local `/admin/budget` exposes `stream_capture_storage`
with the file count, cap and state, without paths or content. Enabling capture
does not itself guarantee available storage. Set `DEEPSEEK_STREAM_CAPTURE_FILES`
explicitly to change capacity (1–32, default 4), without deleting earlier files.
Each capture remains limited to 8 MiB of raw data.


An intentional stop after delivering a recovered answer returns a normal message
with `finish_reason=stop`, no error and no new provider call. It finishes the
recovery turn, not the user's goal. Existing historical `Failed` entries are not
rewritten. JSON and SSE behavior is checked offline; automatic goal continuation
in live Harness has not been verified by these tests.
On explicit continuation, the local notice does not replace the upstream reply
ID: the bridge verifies the original binding and all tool results, preserving
the single-use claim. No duplicate binding that could replay actions is created.

The offline audit also fixed Qwen session construction during login and renewal:
`expires_at` is passed by name instead of occupying the conversation-binding
field. Existing session files are not automatically changed; no live login was
performed for this verification.

Before each reply, the model receives a compact required-field checklist derived
from the schemas. Harness `edit` requires a successful native `read` of the same
`file_path` first. Viewing a file through `bash` (`cat`, `grep`, `git diff`, etc.)
does not count. The dependent `edit` must wait for the `read` result in a separate
reply; an already satisfied read must not be repeated without a reason. Harness's
own guard stays enabled; using another writer to bypass it is forbidden. When
instructed to stop on errors, the model must not automatically read and retry.
The contract was checked offline and in one real Harness continuation: README
`read` → successful result → a separate `edit` → normal `Completed`, with no tool
errors. This does not guarantee full plan compliance: the same turn unnecessarily
repeated an already passed test run despite instructions not to rerun unchanged tests.
The verification reuse contract now keeps successful evidence valid until relevant
code, tests, dependencies, configuration or environment change, a concrete new
concern appears, or the user explicitly requests a fresh run. README wording alone,
a new turn or "final verification" is not a reason to repeat tests. Repeats must
not be hidden in commands with `git diff` or `git status`; a justified repeat
states its reason in the command description. Reused results must be identified
as previous evidence, and skipped tests are not passes. This contract is checked
offline and in one live turn: the model reused prior results, ran no tests and
read existing CI results instead. This is guidance, not a semantic blocker for
duplicate test runs. To stop on shell errors, install the local Harness plugin:

```bash
python -m integrations.harness_shell_safety --profile desktop
```

Restart Harness after installation. `/bridge-shell-status` checks the active executor locally, without generation. There is no mandatory runtime gate: if the installed plugin copy in `<DSH_HOME>/plugins/opencode-local-bridge/` is deleted or corrupted, Harness only logs the error and keeps running without the protection. Check `/bridge-shell-status` after every Harness start before resuming a goal. The plugin adds `set -e -o pipefail` to Bash
commands: a successful `tail` no longer hides an earlier pipeline failure.
A nonzero foreground result, timeout or tool error stops the current turn using
native Stop, without asking the model to retry or diagnose it. History, completed
actions and counters are preserved. `if`, `!`, `&&` and `||` retain normal Bash
semantics: an explicitly handled nonzero status need not fail the whole command.

When the user authorizes autonomous local test repair, run
`bridge_unittest /path/to/venv/bin/python -v tests.test_module` as a separate command.
This explicit `unittest` helper delivers completed assertion failures as
`status: tests_failed`, preserving the original report and child exit code.
Shell exit `0` means the result was delivered, **not that the tests passed**.
The model can fix code and rerun affected tests after a change; do not repeat
passing tests without a new reason. Loading errors, test exceptions, interruption
and missing completion receipts still stop the goal. Ordinary `python -m unittest`,
other shell commands and API retry policy are unchanged. The helper does not
disable networking; offline verification requires a separate sandbox. A fully skipped suite is
reported as `tests_skipped`, not as a passing run.
Offline checks cover assertions, loading errors, exceptions and early exits.

To allow correction of a confirmed rejection **before any write**, enable:

```bash
python -m integrations.harness_shell_safety --profile desktop --allow-read-repair
```

After restarting, `/bridge-shell-status` must show `FS_NOT_OBSERVED read repair enabled`.
Only native `FsError / FS_NOT_OBSERVED` from `edit`/`write` returns to the model
for correction. Harness still rejects writes until an actual `read` succeeds.
The error stays in history; arguments are not filled in and no tools run on the
model's behalf. Other errors and uncertain outcomes still stop the goal.
This is off by default; `--stop-on-read-error` restores stopping, and ordinary
reinstallation preserves the choice. Checks use the installed Harness offline
with a local fixture server and a separate test profile.

Plain `gh pr checks` (including `--required`/`--repo`) fetches status once and returns JSON: validated failing or pending checks do not stop the goal. With a valid schema and empty stderr, exit `0` means the JSON was retrieved successfully even when checks are `pending` or `fail`; their statuses remain in the response. Nonzero exit codes `1`/`8` are normalized only when they match the check statuses. Network/authentication errors, malformed responses and other pipeline failures still stop the turn. `--watch`, `--web` and custom formatting retain native gh behavior. Requires `python3` on PATH; `/bridge-shell-status` must report `harness-contract-v5`. The `pending` plus exit `0` case that stopped PR #80 check retrieval was fixed and verified offline using the saved JSON and a shell pipeline, without re-querying CI to test the fix. This does not mean the CI checks themselves passed.

When checking whether a branch contains a fix or locating optional markers/sections, no match is a discovery result even when presence is unknown beforehand. The model contract requires `bridge_grep_optional` for this case; plain `grep` remains for mandatory assertions. Use `-E 'marker_a|marker_b'` for alternatives. Producer errors (`git show`, etc.) still stop the pipeline. This is a prompt selection rule, not a guarantee of model behavior; shell safety does not override plain `grep`.

For an optional search, use the `bridge_grep_optional` shell function: it
normalizes only grep's own no-match status `1`. Invalid patterns, unreadable
files (status `2`) and upstream failures remain failures. Quiet mode
(`-q`/`--quiet`/`--silent`) is refused with status `2`: on a match grep would
exit `0` even if another input failed. Use `-c` or `-n` output instead. Early
exit options (`-m`, `-l`) after a large producer make the producer exit `141`
(SIGPIPE); that is a reported failure, never normalized. The function requires
Bash (both Harness executors run `bash -c`); another shell stops with status `2`. Plain `grep` is not
overridden; assertions requiring a match still stop. Never append `|| true` to
the entire pipeline. Prefer `sed -n '1,40p'` to `head` for bounded output while
draining the stream. Offline checks cover empty/matching searches, invalid
patterns, missing files, producer statuses `1`/`17`, and native Harness
continuation versus stopping.

The plugin does not classify output text or promise to catch background-process
failures. It sets `XDG_CACHE_HOME` for `gh` to a private temporary directory
(`0700`), without changing authentication or sandbox permissions. The old cache
is retained; the new cache remains until OS temporary-file cleanup. Offline tests
in the installed Harness verified that pipeline failure cancels the remaining
command and the next call in its batch, with no second model request; cache
writes succeed.

A subsequent live check successfully read the CI log using `gh run view --log-failed`.
The next `gh api | grep | head` pipeline exited with code `1`; Harness automatically
showed Stopped and sent no third generation. The unfiltered stream was not retained,
so the tool result does not establish the cause of that status. `grep` also returns
`1` for no matches: handle an expected case explicitly in the command instead of
disabling protection globally. Prior history and usage records were preserved;
only two generations and two HTTP requests were added.

After the optional-search fix, the next live turn completed without new errors:
five tool calls inspected the saved CI log and local source, without downloading
the log again or rerunning tests. The original grep pattern matched the saved log;
this does not establish the cause of the earlier `gh api` pipeline status. The CI
report identifies a new failure relative to its baseline but lacks the assertion
traceback. Fixing search handling does not mean that the CI failure is fixed.

For the separate PowerShell failure in the test project, the pytest artifact
confirmed a subprocess `TimeoutExpired` after 10 seconds, not an assertion failure.
With PowerShell 7.6.6 installed, its test fixture now bounds startup separately
(30 s) from projection execution (10 s), without retries or baseline changes.
Focused local checks passed, including an injected startup delay; this does not
establish a green CI rerun or native Windows acceptance.

A later turn exposed a separate protocol limitation: the model returned a
`calls` fence as text. Harness displayed Completed but executed no tool. A complete top-level `calls` fence now aliases `tool_calls` only for normally
finished DeepSeek replies, with full advertised-schema validation. No arguments
are inferred; malformed JSON, multiple blocks, quoted or nested code cannot execute.
Invalid framing no longer becomes a successful text response. Offline checks cover
parser boundaries and real Harness execution with a fixture provider. One-use
recovery of previously delivered text preserves its reply ID, history and usage:
it sends no generation, does not increase the conversation reply count, and stops
locally after the tool result.

Live recovery of this case was verified: one saved `bash` call executed
successfully, followed by a local stop. No new DeepSeek generation was sent;
the budget-state file was unchanged, prior history links were retained, and
the recovered link kept the same reply ID and conversation reply count.

The model also receives a reminder to wait for a write result before its verifier.
This is guidance, not a guarantee: missing `file_path` or another required field
still rejects the batch. The bridge never infers a path from content or another
command and does not automatically retry.

The prompt separates past transcript data from the next assistant message and
forbids simulated tool results. Only Harness supplies actual execution results.
A `Tool (...):` suffix after a call block is never stripped: the entire batch is
rejected without execution. Prompt guidance does not guarantee model compliance.

Verified offline against saved replies without another generation. The fix does
not resume a stopped goal, replay an old reply, or reset history or counters.

### If Harness stops

| Message / symptom | Check |
| --- | --- |
| `Local request budget exhausted` | Rolling budgets, conversation reply cap or prompt size. A new chat or restart does not clear rolling usage |
| `MISSING_CREDENTIAL` for `deepseek-official` | The official API route needs a key. To use the bridge, select **Local account bridge** |
| `login_required` | Saved authentication expired; sign in to the selected provider again |
| `History is unknown` / `History already consumed` | The former means no local binding was found; the latter means it was found but is already used. Signing in or resetting budgets cannot repair this. After upgrading Harness, use the verified local schema compatibility below; consumed links remain closed |
| `quota_exceeded`, `user is muted`, security refusal | Stop and inspect the normal website; do not retry automatically |
| Missing group or model | Check `.env`, the running server and `/v1/models`, then reinstall the catalog |

`headless` has no interactive approval channel: a `sandbox_permissions`
escalation may stop before the command starts. Harness persists permissions in
the session; changing the launch-time `DSH_PERMISSION_MODE` does not replace an
already saved mode. Use the selected session's native permission control,
preserve filesystem/network boundaries, and never fabricate a tool result.
This is local Harness state rather than a DeepSeek authentication failure.

For a long goal, use Harness's native **Continue goal** action. An ordinary
"continue" message does not arm a saved goal. In the checked `0.2.1-rc.2` build,
the one-shot `--profile headless` runner can exit with code `0` after the first
turn even while another goal round is starting. That exit code does not prove
goal completion: check its durable state and deliverables. The offline verification
driver waited for native `complete`; it is not installed as an automatic workaround
in users' profiles.

Upgrading Harness from 0.2.0-rc.2 to 0.2.1-rc.2 changed the catalog from 27 to 32
tools and also changed the leading system instruction. Both fields are included
in the history key. A verified version pair can be **registered locally**:

```bash
python -m server.conversation_schemas --model deepseek-expert --catalogs /private/path/catalogs.json
```

The private JSON must contain `old` and `current`: exact OpenAI tool arrays
(`type: function`, `function: {name, description, parameters}`) from saved request
headers. If the leading system instruction changed, also include both exact
strings as `old_system` and `current_system`. Never reconstruct them from memory
or publish the file: descriptions and instructions may contain local paths.
The command only reads the local DeepSeek session and scopes the upgrade to its
account and the selected model; it does not sign in or contact the provider.

The bridge verifies the full old history and tool results, then claims the
**original** single-use link. It creates no extra replay ticket. That request
sends complete current schemas and, if changed, the exact new leading system
instruction. Compact mode and `none` cannot hide the contract upgrade. The next
reply binds to the new contract; ordinary continuations do not resend prior work.
New replies are
validated against current schemas. Unregistered changes, ambiguous parents,
consumed links and previously attempted migrations remain closed. Upgrading
schemas does not reset conversation limits, rolling budgets or migration journals,
and never resumes a goal automatically. Verified offline, including the saved
large history: the old binding was located while preserving its consumed state.
Successful account-backed continuation after this change has not been tested.

DeepSeek defaults are **12/60 generations, 3/6 new chats and 40/200 HTTP requests
per hour/day**, with a shared 20 accepted replies per conversation
(`CONVERSATION_MAX_REPLIES`) and a 30-second interval. `DEEPSEEK_MAX_*` budget
ceilings and `CONVERSATION_MAX_REPLIES` belong to the bridge process environment.
Raising a local ceiling does not raise the website's allowance. `PROVIDER_ACCESS_PAUSE=0`
disables provider pause and pending blocks, including persisted ones; it does not
bypass budgets, provider quotas or conversation-history safeguards, and can allow
retries after an uncertain outcome.
[Configuration and error guide](docs/HARNESS.en.md#local-limits-and-errors).

## Connect OpenCode

Use the same bridge and account setup above. Put the example
[`opencode.json`](examples/opencode.json) in your project. For existing configs,
merge settings while preserving your `provider`, `model` and `small_model`.
Set `small_model` explicitly: title generation can also consume messages.
Do not point title/`small_model` at a browser model — that opens a second tab
chat (Harness disables `session-title-llm` in its profile).

```bash
cd /path/to/your-project
opencode --agent plan --model local-qwen/qwen3.8-omni-flash
```

For DeepSeek, select `local-deepseek/deepseek-chat` or
`local-deepseek/deepseek-expert`. API URL: `http://127.0.0.1:8000/v1`.
`apiKey` must be the bridge API token, e.g. `"{file:/absolute/path/session/api-token}"`
([API access token](#api-access-token)). It is not a provider account key.
[Agents and tool plugin](docs/SETUP.en.md#opencode-integration).

## Provider status

Implementation and historical checks as of **October 11, 2026**. A passing test
on one account does not establish current access or reliable unlimited goals.
Provider documentation retains detailed qualification results.

| Provider / model ID | Browser during requests | Status |
| --- | --- | --- |
| DeepSeek · `deepseek-chat`, `deepseek-expert` | Not needed after manual sign-in | Browserless HTTP; small Harness tasks passed; repeated account restrictions occurred. No automatic renewal |
| Qwen · HTTP via `QWEN_MODELS` / `qwen-web` | HTTP: not needed after sign-in; `qwen-web`: connected tab | HTTP Flash/Max and the browser adapter verified; Max hit a daily limit. `qwen3.7-plus` is catalogued without a separate live check. [Session](docs/HARNESS.en.md#qwen-session) |
| Kimi · `kimi-instant` | Not needed after capture | Experimental HTTP: reply, renewal and Harness `read` passed |
| Kimi · `kimi-k3`, `kimi-k2.8` | Not needed after capture | HTTP implemented, not qualified end to end: K3 refused free-user capacity; K2.8 was not attempted afterward. [Details](docs/KIMI.md) |
| Kimi · `kimi-web` | Connected tab required | Browser adapter verified; large composer pastes (~4KB+, system+tools) become a TXT attachment on the site — confirmed. See `browser/README.md` and `.env.example` |
| GLM · `glm-web` | Connected tab required | Browser adapter verified; browserless access remains incomplete. [Blockers](docs/GLM.md) |
| Mistral · `mistral-web` | Connected tab required | Browser adapter verified — see `browser/README.md` |
| Grok · `grok-web` | Connected tab required | Browser adapter verified — see `browser/README.md` |
| ChatGPT · `chatgpt-web` | Connected tab required | Browser adapter verified — see `browser/README.md` |
| Claude · `claude-web` | Connected tab required | Browser adapter verified (ProseMirror + `/api/*`) — see `browser/README.md` |
| Gemini · `gemini-web` / Antigravity CLI | `gemini-web`: connected tab; CLI: `agy` | Browser adapter verified. CLI was rejected by region in our test |

All connections start disabled. Website versions, quotas and availability may
change. Chinese providers also have limits; the exact Qwen Max daily allowance
and reset time have not been established.

## Architecture

![Harness or OpenCode → local bridge → HTTP session, browser tab or CLI](docs/media/bridge-architecture.svg)

**Harness or OpenCode runs tools on your computer.** Prompts and tool-read data
are sent to the selected provider. The bridge validates responses and
`tool_calls`, saves conversation state and sends new tool results to the existing
chat. History keys keep the original `main` format: updating requires no database
cleanup, sign-in or counter reset. `system` instructions, `<system-reminder>` wrappers
and assistant commentary are matched exactly; changed Harness instructions or
schemas need an [explicit local compatibility registration](#if-harness-stops).
Unknown or already consumed history stops instead of silently creating a new chat.

Refusal pauses survive restart. Client setup, ordinary sign-in and local budget
reset do not clear them. Safari transfer also preserves the entire pause, pending
request and counters. A fresh token does not prove that account restrictions ended. Inspect pauses with
`python -m providers.access status`.
[Resume rules](docs/ACCOUNT_SAFETY.md).

The API binds to `127.0.0.1` by default (local only) and requires the bridge
API token. Non-loopback `HOST` needs `API_ALLOW_PUBLIC_BIND=1`,
`API_ALLOWED_HOSTS` and a firewall; never expose it without them. `session/` and `.env` are excluded from Git.
Do not publish them, personalized Userscripts or budget credentials.
Supported: text and emulated tool calls. Images, audio and video are not uploaded,
even for models named Omni.

Known limits: the browser observer runs in the provider page, so scripts already
running in that origin can forge its response events; the bridge binds a job to
the tab, not to an authenticated answer source. DeepSeek/Qwen streams stop after
300 seconds total or 15 seconds without bytes; such a stop pauses the provider
and is not replayed. A first request (no assistant message yet) has no history
link, so resending it can open a second chat; continuations are one-use.
Set `CONVERSATION_CLEAR_USED_ON_STARTUP=1` in the bridge process environment to
delete consumed/closed history links at startup; the default `0` preserves them.
A repeated first request with exactly identical history otherwise has the same
cache key, so the bridge will not open another provider chat for it while the link
is retained. It may reuse an unused link or reject an already-consumed one. Deleting
used links allows a repeat to start another upstream chat,
but a later continuation may bind to the wrong chat and fail. Links with
`used=0` are not deleted.

## OpenCode demonstrations

Historical OpenCode 1.18.34 captures of a small fixture-file read.
These are **not Harness screenshots** or proof of current account availability.

| Qwen Flash | Qwen Max |
| --- | --- |
| ![OpenCode Qwen Flash](docs/media/qwen-flash-opencode.png) | ![OpenCode Qwen Max](docs/media/qwen-max-opencode.png) |

[GLM/Kimi captures and recording method](docs/media/README.md).

A repeated initial request with **exactly identical history** has the same cache key.
For an assistant message with `tool_calls`, the key is bound to the calls themselves
(ID, name, arguments) and the prior history, not to the text around them: a client
may reassemble streamed text differently (without `\n\n`, or with no text at all).
This does not weaken the binding — call IDs are unique.
While its link is retained, the bridge does not open a new provider chat for that
history: it may reuse a still-unused link or reject a repeat whose link was already
consumed. By default consumed/closed links are retained to avoid binding a repeat to
another provider chat. With `CONVERSATION_CLEAR_USED_ON_STARTUP=1`, startup deletes
only consumed links (`used=1`), so a repeated prompt may create a new upstream chat;
a later continuation may then bind to the wrong chat. For a complete manual
cleanup, run `python -m server.conversation_admin -clear`: it deletes all `links`,
associated log-path mappings (`trace_links`), and migration records (`rollovers`)
from `session/deepseek-conversations.sqlite3`. It does not delete log files or chats
at the provider. This operation is destructive.

## Documentation and development

- [Harness: setup, sessions, budgets, offline verification](docs/HARNESS.en.md)
- [Full setup, OpenCode, API and autostart](docs/SETUP.en.md)
- [Kimi HTTP](docs/KIMI.md) · [GLM](docs/GLM.md) · [Safari Userscripts](browser/README.md)
- [Account safety](docs/ACCOUNT_SAFETY.md) · [API examples](examples/README.md)
- [Standalone DeepSeek terminal chat](docs/TERMINAL_CHAT.md)

CI covers Python 3.9/3.12, PoW WASM, shell/JavaScript and isolated browser fixtures.
Locally: `python -m unittest discover -s tests -t . -v`. The run is hermetic: `.env`
and exported provider flags are ignored, so results match CI. Outside the suite the
same mode is `BRIDGE_SKIP_DOTENV=1`.
[Real Harness CLI fixtures with mocked providers](docs/HARNESS.en.md#offline-verification).

### Browser exchange safety

DOM fallback is disabled: an old visible answer cannot complete a new request.
Claude requires a network terminal marker; truncated, partial or malformed answers are
not reported as complete. Uncertain dispatched outcomes retain pending state
and a safety pause across restart. There is no automatic retry.
Verified offline without live accounts. Re-export and update the userscripts
to apply the browser changes (controller 0.3.35).

Mistral accepts only a request-bound stream with a terminal trailer.
Harness `--without-browser` excludes browser models and their aliases.
The browser controller waits for text insertion, preserves user edits and resumes
only explicitly bound conversations. These cases passed 28 isolated browser
fixtures without accounts.

### Model identifiers

`GET /v1/models` lists bare IDs and `provider/model` aliases, for example
`qwen3.8-max` and `qwen/qwen3.8-max`, or `deepseek-chat` and
`deepseek/deepseek-chat`. Both forms work with the API; the Harness installer
keeps one menu entry per pair, preferring the bare ID. If the catalog advertises
only an alias, that exact ID is retained.
The prefix must match the provider and does not enable disabled models.
DeepSeek aliases share the same history binding and continuation accounting:
changing the spelling neither creates a new chat nor resets replay protection.
Verified offline; this does not verify model access for a live account.

### Harness contract: history and stopping

Before contacting any provider, the bridge validates tool-call history: every
call needs exactly one result with the same ID. Missing, duplicate and unrelated
results are rejected locally. Parallel batch results may arrive out of order.
This also applies to explicit `conversation_id` requests; arguments are not
invented and completed actions are not replayed.

Harness shell safety disarms automatic goal continuation **before** cancelling
a turn on a tool error. The durable goal phase and revision remain unchanged;
resuming requires a separate user action. Install with
`python -m integrations.harness_shell_safety --profile desktop`, then restart
Harness. `/bridge-shell-status` checks shell settings without sending a request;
the current version reports `harness-contract-v5` and `goal disarm before native stop`.
Model reconfiguration preserves the safety plugin.

Verified offline against Harness 0.2.0-rc.2 contract fixtures: SSE, exact call IDs,
existing-conversation continuation and duplicate-step rejection without counter
changes. This is not a live account check or a guarantee that arbitrary goals
will complete.

### Importing official API history into the web bridge

Selecting **DeepSeek Expert · web** changes the route; it does not create a web
conversation for existing API history. `History is unknown` after this switch
requires an explicit import, not a budget reset or another goal resume.

The local command `python -m server.history_import --request REQUEST.json
--archive-root PRIVATE_ARCHIVE_DIR --source-provider deepseek-official` authorizes
**one exact request** for 10 minutes. First retain and review the client's actual
request while it is stopped. Resuming a goal may change that request; a ticket
for the earlier request will then refuse it. Use a private, git-ignored archive
directory that the current Harness workspace's `read` tool can access.

Authorization is offline: it archives the complete request and every observed
result. Only a later submission of **that same** request creates a web conversation
and binds its response to the original Harness history. Existing links, rolling
budgets and pauses survive. The ticket binds account, model, history, schemas and
parameters; it is consumed before dispatch and cannot rearm after failure or
restart. Format repair and truncated-output continuation are disabled. An exact
repetition of an imported call rejects the entire new batch. The exception is
`get_goal`, which reads current goal state after a resume. Goal mutations and
all other calls retain replay protection; rejected replies are saved privately
when diagnostics are enabled.

Verified offline, including retained real history: 181 messages, all 91 results,
a 72.6 KB prompt. One live probe on October 24 created one web conversation and
received one reply; no tools executed because the guard incorrectly rejected
`get_goal`. The cause was fixed offline: 145 tests and validation of the saved
reply passed. Response #2 was then recovered after its prompt and reply exactly
matched provider history. Harness successfully executed `bash` and `get_goal`
once; the next model step was stopped locally. Locating the binding required two
read requests, with no new generations or paid API calls. History and global
counters were preserved, and the new continuation link was checked offline.
This verifies one recovered step, not reliable long-running autonomous operation.
Subsequent live probes exposed terminal duplicate DSML parameter closing tags in
responses #4 and #6. Complete raw streams were retained; each failure stopped Harness
before executing tools. History and accumulated usage were preserved.
A structural decoder tolerance now accepts a terminal run of repeated closing
tags for the last fully parsed parameter, before a named `invoke` closing tag.
Both already supported markers, `｜DSML｜` and `｜｜DSML｜｜`, may be mixed at this
boundary. No other spellings are introduced.
Values are never repaired; schemas and the entire batch are validated before calls
are returned. Repeats elsewhere, missing tags and unfinished replies are rejected.
The run is not limited to a single duplicate: its length cannot change argument
values. Offline verification: 135 tests and both exact saved replies. Response #4
was recovered without generation: Harness successfully executed `bash` and `read`
once each. Response #6 was then recovered without generation: one `bash` call
completed once; all 478 previous links and usage counters were preserved.
Recovery passed 8 offline checks; its new link retained three accepted replies.
The single subsequent request returned #8 with mixed repeated closing tags,
`</｜｜DSML｜｜ parameter>` and `</｜DSML｜ parameter>`. The former identical-marker
requirement rejected the whole batch before execution. This inconsistency is fixed:
the tolerance uses the same two markers as ordinary parameter boundaries.
Offline verification: 137 tests, the exact complete stream for #8, unchanged
`read` and `get_goal` arguments, schema validation and replay protection.
Response #8 was recovered without generation: `read` and `get_goal` completed
once, preserving all 479 prior links. The next two web replies parsed successfully:
`bash` completed, then Harness's local “file has not been read” guard stopped
an `edit` batch after an application restart. Neither file was written; the second
`edit` was aborted before dispatch, with no generation retry. The goal is inactive.
Reliable long-running autonomous operation remains unverified.
With the updated shell plugin (`harness-contract-v6`), Harness appends runtime
context with a fresh identifier after restart. It automatically reminds the model
to perform a native `read` before `edit` or replacing an existing file, and wait
for the successful result in a separate step. Historical reads and `bash/cat`
do not qualify. This is a reminder, not a fabricated read observation: ignoring
it still triggers the native refusal. File version checks, schemas and replay
protection remain intact. Harness appends a normal context event without rewriting
old messages, the system prompt, or counters. Offline checks on Harness `0.2.1-rc.3`
cover restart → `read` → `edit`, refusal without a fresh read, and refusal when the
file changes after reading. Install with
`python -m integrations.harness_shell_safety --profile desktop`, then restart Harness;
`/bridge-shell-status` reports `runtime read reminder active`.
Live web-bridge verification completed three sequential steps: two successful
native reads, then separate successful edits to `REPORT.ru.md` and `PROGRESS.md`.
There were no errors or repeated earlier commands/tests. The bounded probe
stopped the goal normally after three steps; this does not establish completion
of the whole project. No paid API was used. The offline integration, history
binding and protection suite passed 48 tests.
The next continuation, without the probe step cap, reached a normal final reply:
four web steps, `bash`, `get_goal`, and `update_goal`, with no errors or retries.
Harness recorded `turn/end: completed` and goal completion; all 485 prior history
links and 62 prior usage events were preserved. The verified result covers only
`OWNER-NOTIFY-01`: the final reply explicitly excludes a full release and all
product plans. A goal marked `complete` does not by itself establish fulfillment
of its broader wording or long-running autonomous reliability.
Details and limits: [Harness guide](docs/HARNESS.ru.md#перенос-api-истории).


### Whole-goal completion control

The `harness-contract-v7` shell plugin rejects model calls to
`update_goal(action="complete")`. A successful tool, completed plan item or partial
report does not establish completion of the entire objective. Rejection occurs
**before** the native goal changes; the existing safety policy stops the turn
without another request. The model should continue remaining authorized work
without repeating completed actions.

Final acceptance is separate: stop the turn, run `/bridge-goal-review`, compare
the **entire** objective and every criterion with actual evidence, then use the
shown `/bridge-goal-complete <token>`. The single-use review expires after five
minutes and is bound to this session; new work, goal changes or restart invalidate
it. Review disarms automatic continuation. Completion makes no provider request
and does not reset history or counters.

This is a conservative acceptance boundary, **not automated semantic verification**.
The model cannot yet autonomously certify completion of the whole project.
50 offline checks passed, including installed Harness `0.2.1-rc.3` with a mocked
provider: completing one component could not close the broad goal, and no next
request was issued. Install with
`python -m integrations.harness_shell_safety --profile desktop`, restart Harness,
and check `/bridge-shell-status` for `whole-goal completion review required`.

### Automatic context migration

`DEEPSEEK_CONTEXT_ROLLOVER=1` enables a new DeepSeek conversation within the
same Harness goal after restarting the bridge. Only an explicit
`context_length_exceeded` refusal with an empty, cleared answer permits one
migration. Full history is stored privately in `session/context-checkpoints/`;
the model receives exact instructions, a completed-call ledger and result
excerpts. Missing details can be read from the archive. This is not a semantic
summary. Harness history, usage and accepted-reply counts are preserved.
Any new failure stops without retry; existing pauses are not cleared automatically.
Off by default. Verified offline without account requests.
[Conditions and limits](docs/HARNESS.en.md#deepseek-conversation-context-exhausted).

**MIT · unofficial project.** Based on
[Tsuev/opencode-deepseek](https://github.com/Tsuev/opencode-deepseek) and
[sums001/Deepseek-API](https://github.com/sums001/Deepseek-API).
Use accounts under each service's terms. [License](LICENSE).

Browser adapters (userscripts, tab protocol, ChatGPT / Claude / Gemini / Qwen /
Kimi / GLM / Mistral / Grok providers and related checks) are reworked in the
[SimolSem/opencode-deepseek](https://github.com/SimolSem/opencode-deepseek) fork
and contributed here via pull requests.

Old upstream branches do not imply missing features: `feat/browser-providers`
was reconciled with `main`, with no missing functional patches found.
[Commit mapping and documentation differences](docs/BRANCH_RECONCILIATION_20261012.md).
The offline budget installer test checks the real model-catalog import in an
isolated copy and reports the original child-process failure.

With `DEEPSEEK_CONTEXT_ROLLOVER=1`, rollover also handles the local
`CONVERSATION_MAX_REPLIES` cap (200 replies in the working configuration). Before
sending the next step, the bridge archives the full history privately and hands
off instructions, completed-action results and recent notes to a new conversation.
It does not send another request to the capped conversation. Only the new
conversation starts at one accepted reply; global budgets, old history and the
Harness goal binding are preserved. Completed actions are observations, not
commands to execute again. Incomplete/oversized context, archive failure or a new
refusal stops migration without automatic regeneration. Disabled mode retains the
manual stop. Verified offline without account requests; migration happens on the
next Harness request, not on a timer.

Large histories no longer hit a separate 32 KiB instruction cap. The handoff
budget includes tool schemas within the unchanged 131072-byte prompt ceiling.
Every instruction retains its role, order and exact text; only identical
instruction objects share dictionary storage with an ordered sequence of indexes.
When result excerpts do not fit, the packet indexes **every** completed call/result
and the latest reports. Full history and arguments remain in a private, pretty
JSON archive (0600), without truncation. This mode requires an available `read`
tool: the agent must read archived context before choosing further work, instead
of rerunning commands to recover their results. Inaccessible archives or exact
instructions plus indexes that still exceed the budget stop migration. This does
not promise unlimited model context. Offline verification used a saved history of
488 messages, 67 instructions and 218 results; the full API path made one mocked
request to a new conversation. No account request was sent for this verification.

### DeepSeek refusal diagnostics

The bridge distinguishes invalid tokens (`40002/40003`), temporary sending
restrictions (`account_muted`), bans (`account_banned`), IP restrictions
(`ip_restricted`), gateway verification (`gateway_challenge`) and rejected PoW
(`pow_rejected`). Code `5` denotes muted in the completion context; other
operations have separate numeric namespaces. None of these errors triggers replay.

Minimal private metadata is saved automatically under `session/refusal-diagnostics/`:
time, phase, HTTP status, original numeric API/biz codes, gateway category and
local attempt ID when available. Tokens, cookies, headers, response text and
prompts are excluded. The directory is private, files use `0600`, and the limit
is 64 files; existing evidence is not deleted. Full or unwritable storage emits
a warning while the safety stop remains in effect. Do not publish this directory.

Classification and pause retention were verified offline with synthetic responses.
This does not verify account availability or guarantee freedom from restrictions.
