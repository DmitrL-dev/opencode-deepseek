"""Opt-in real Harness CLI -> local API fixtures; no provider network calls.

HARNESS_COMMAND must be a JSON array, e.g. '["/absolute/path/to/dsh"]'.
Run with RUN_HARNESS_FIXTURES=1 python -m unittest tests.test_harness_runtime -v.
"""

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.responses import JSONResponse
import uvicorn

from deepseek.client import Reply
from integrations.harness import ROUTE, catalog_models, patch_document, render, yaml_codec
from providers import access
from providers.access import AccessGuard, ProviderRejected
from server import api
from tests.test_tools import block

FILE_PAYLOAD = 'line 1\n"quoted" and \\backslash\n```python\nprint("fixture")\n```\n'

@unittest.skipUnless(os.getenv("RUN_HARNESS_FIXTURES") == "1", "opt-in installed Harness fixture")
class HarnessRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.command = json.loads(os.environ["HARNESS_COMMAND"])
        self.temp = tempfile.TemporaryDirectory(prefix="harness-bridge-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "fixture.txt").write_text("HARNESS_OWNED_FILE_73\n")
        self.guard = AccessGuard(self.root / "pauses.json", interval=0)
        self.calls, self.prompts = [], []
        self.behavior = "text"
        self.addCleanup(patch.stopall)
        patch.object(api, "guard", self.guard).start()
        patch.object(access, "guard", self.guard).start()
        patch.dict(api.MODEL_MAP, {"deepseek-chat": "default"}, clear=True).start()
        patch.object(api, "get_client", return_value=self).start()
        patch.object(api, "get_qwen_client", side_effect=AssertionError("No real Qwen fixture")).start()
        # The bridge has no credentials in this checkout. The only selected
        # provider factory is replaced above, including every continuation.
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.base = "http://127.0.0.1:" + str(sock.getsockname()[1]) + "/v1"

        async def observed(scope, receive, send):
            if scope["type"] == "http" and scope["method"] == "POST":
                body = b""
                while True:
                    message = await receive()
                    body += message.get("body", b"")
                    if not message.get("more_body"):
                        break
                self.calls.append(json.loads(body))
                if self.behavior == "503":
                    await JSONResponse({"error": {"message": "Owned fixture unavailable"}}, 503)(scope, receive, send)
                    return
                consumed = False

                async def replay():
                    nonlocal consumed
                    if not consumed:
                        consumed = True
                        return {"type": "http.request", "body": body}
                    return await receive()

                await api.app(scope, replay, send)
            else:
                await api.app(scope, receive, send)

        self.server = uvicorn.Server(uvicorn.Config(observed, log_level="error", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [sock]}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 10
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.server.started)
        models = catalog_models({"data": [{"id": "deepseek-chat", "owned_by": "deepseek"}]})
        self.overlay = self.root / "fixture.patch.yml"
        self.overlay.write_bytes(render(patch_document([], self.base, models, "deepseek-chat")))

    def stop_server(self):
        self.server.should_exit = True
        self.thread.join(10)
        self.assertFalse(self.thread.is_alive())

    def chat(self, prompt, *args):
        attempt = access.current_attempt("deepseek")
        attempt.dispatch()
        self.prompts.append(prompt)
        if self.behavior == "refused":
            raise ProviderRejected("account_restricted")
        if self.behavior == "flat_tools":
            if len(self.prompts) == 1:
                answer = block([
                    {"name": "read", "file_path": str(self.workspace / "fixture.txt"), "offset": 1, "limit": 60},
                    {"name": "bash", "command": "printf HARNESS_FLAT_COMMAND_29",
                     "description": "Print the owned fixture marker"}])
            else:
                self.assertIn("HARNESS_OWNED_FILE_73", prompt)
                self.assertIn("HARNESS_FLAT_COMMAND_29", prompt.rsplit("Tool (bash,", 1)[1])
                answer = "HARNESS_BRIDGE_OK"
            attempt.complete()
            return Reply(answer, "owned-fixture:2")
        if self.behavior in ("tools", "tools_without_fence", "write_without_fence", "redundant_closer") and len(self.prompts) == 1:
            self.assertTrue(any(tool["function"]["name"] == "read" for tool in self.calls[-1]["tools"]))
            answer = block([{"name": "read", "arguments": {"file_path": str(self.workspace / "fixture.txt")}}])
            if self.behavior == "tools_without_fence":
                answer = answer[:-4]
            elif self.behavior == "redundant_closer":
                answer = answer[:-4] + '}\n```'
        elif self.behavior == "malformed_tools":
            answer = ('```tool_calls\n[{"name":"read","arguments":'
                      '{"file_path":"CONFIDENTIAL_TOOL_PAYLOAD_927\npath"}}]\n```')
        elif self.behavior == "write_without_fence" and len(self.prompts) == 2:
            answer = block([{"name": "write", "arguments": {
                "file_path": str(self.workspace / "fixture.txt"), "content": FILE_PAYLOAD}}])[:-4]
        else:
            if self.behavior in ("tools", "tools_without_fence", "redundant_closer"):
                self.assertIn("HARNESS_OWNED_FILE_73", prompt)
            answer = "HARNESS_BRIDGE_OK"
        attempt.complete()
        return Reply(answer, "owned-fixture:2")

    def run_harness(self):
        # Do not inherit account credentials, proxies, a user's DSH_HOME, or a
        # user's workspace .env. HOME is isolated as well as Harness storage.
        env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "SYSTEMROOT", "ELECTRON_RUN_AS_NODE") if key in os.environ}
        env.update(HOME=str(self.root), DSH_HOME=str(self.root / "dsh"),
                   DSH_TELEMETRY_MODE="DISABLED", NO_PROXY="127.0.0.1,::1")
        result = subprocess.run(self.command + ["--profile", "headless", "--patch", str(self.overlay),
                                               "--json", "Run the owned bridge fixture."],
                                cwd=self.workspace, env=env, capture_output=True, text=True, timeout=90)
        # Only owned synthetic messages can enter this transcript.
        self.assertNotIn("MISSING_CREDENTIAL", result.stdout + result.stderr)
        return result

    def test_text_uses_one_streamed_request(self):
        result = self.run_harness()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("HARNESS_BRIDGE_OK", result.stdout)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0]["stream"])
        self.assertEqual(self.calls[0]["model"], "deepseek-chat")
        self.assertEqual(len(self.prompts), 1)

    def test_native_read_tool_result_returns_to_bridge(self):
        self.behavior = "tools"
        result = self.run_harness()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("HARNESS_BRIDGE_OK", result.stdout)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.prompts), 2)
        self.assertTrue(any(message["role"] == "tool" for message in self.calls[1]["messages"]))

    def test_complete_json_without_closing_wrapper_executes_without_continuations(self):
        self.behavior = "tools_without_fence"
        result = self.run_harness()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("HARNESS_BRIDGE_OK", result.stdout)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.prompts), 2)
        self.assertNotIn(api._CONTINUE_PROMPT, self.prompts)

    def test_redundant_object_closer_round_trip_without_replay(self):
        self.behavior = 'redundant_closer'
        result = self.run_harness()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('HARNESS_BRIDGE_OK', result.stdout)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.prompts), 2)
        self.assertNotIn(api._CONTINUE_PROMPT, self.prompts)

    def test_flat_read_and_bash_batch_round_trip_without_replay(self):
        self.behavior = 'flat_tools'
        result = self.run_harness()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('HARNESS_BRIDGE_OK', result.stdout)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.prompts), 2)
        self.assertNotIn(api._CONTINUE_PROMPT, self.prompts)

    def test_native_write_preserves_quoted_multiline_content_without_closing_wrapper(self):
        self.behavior = "write_without_fence"
        result = self.run_harness()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "fixture.txt").read_text(), FILE_PAYLOAD)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(self.prompts), 3)
        self.assertTrue(any(message["role"] == "tool" for message in self.calls[2]["messages"]))

    def test_streamed_refusal_stops_and_remains_paused_on_next_run(self):
        self.behavior = "refused"
        first = self.run_harness()
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(self.guard.status()["deepseek"], "account_restricted")
        self.behavior = "text"
        second = self.run_harness()
        self.assertEqual(second.returncode, 1, second.stdout + second.stderr)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.prompts), 1, "paused provider must not dispatch again")

    def test_malformed_tool_json_stops_once_with_safe_diagnostics(self):
        self.behavior = "malformed_tools"
        result = self.run_harness()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unescaped_control_character", result.stdout + result.stderr)
        self.assertNotIn("CONFIDENTIAL_TOOL_PAYLOAD_927", result.stdout + result.stderr)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.prompts), 1)

    def test_transient_http_failure_is_not_retried(self):
        self.behavior = "503"
        treatment = self.overlay.read_bytes()
        baseline = yaml_codec().load(treatment.decode())
        baseline[0]["config"]["providers"][ROUTE].pop("retryPolicy")
        self.overlay.write_bytes(render(baseline))
        old = self.run_harness()
        self.assertEqual(old.returncode, 1, old.stdout + old.stderr)
        self.assertEqual(len(self.calls), 6, "Harness default: initial call plus five retries")
        self.calls.clear()
        self.overlay.write_bytes(treatment)
        result = self.run_harness()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.prompts, [])
