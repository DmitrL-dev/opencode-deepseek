"""Opt-in installed Harness -> synthetic Messages API; no account traffic.

Use the same RUN_HARNESS_FIXTURES / HARNESS_COMMAND as test_harness_runtime.
The fixture never reads user credentials or invokes a web-bridge parser.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest

from integrations.harness import render, yaml_codec


@unittest.skipUnless(os.getenv("RUN_HARNESS_FIXTURES") == "1", "opt-in installed Harness fixture")
class HarnessOfficialRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harness-official-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "input.txt").write_text("OFFICIAL_INPUT_73\n")
        self.payload = 'Result: 73\n"quoted" \\path\n```python\nprint(73)\n```\n'
        self.calls, self.paths = [], []
        self.reject = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.calls.append(body)
                owner.paths.append(self.path)
                if owner.reject:
                    data = json.dumps({"error": {"type": "overloaded_error", "message": "Owned 503 fixture"}}).encode()
                    self.send_response(503)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                step = len(owner.calls)
                if step == 1:
                    name, args = "read", {"file_path": str(owner.workspace / "input.txt")}
                elif step == 2:
                    name, args = "write", {"file_path": str(owner.workspace / "report.md"), "content": owner.payload}
                elif step == 3:
                    name, args = "read", {"file_path": str(owner.workspace / "report.md")}
                else:
                    name, args = None, None
                events = [{"type": "message_start", "message": {
                    "id": "msg_owned_" + str(step), "type": "message", "role": "assistant",
                    "model": "deepseek-flash", "content": [], "stop_reason": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0}}}]
                if name:
                    events += [
                        {"type": "content_block_start", "index": 0, "content_block": {
                            "type": "tool_use", "id": "call_owned_" + str(step), "name": name, "input": {}}},
                        {"type": "content_block_delta", "index": 0, "delta": {
                            "type": "input_json_delta", "partial_json": json.dumps(args)}},
                    ]
                else:
                    events += [
                        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OFFICIAL_HARNESS_OK"}},
                    ]
                events += [
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "tool_use" if name else "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 20}},
                    {"type": "message_stop"},
                ]
                wire = "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(wire)))
                self.end_headers()
                self.wfile.write(wire)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        source = Path(__file__).resolve().parents[1] / "integrations/harness_official.patch.yml"
        document = yaml_codec().load(source.read_text())
        native = next(item for item in document if item.get("id") == "llm-deepseek")
        native["config"].update(baseURL=f"http://127.0.0.1:{self.server.server_port}/anthropic", thinking="disabled", reasoningEffort="off")
        self.overlay = self.root / "fixture.patch.yml"
        self.overlay.write_bytes(render(document))

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def run_harness(self):
        env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "SYSTEMROOT", "ELECTRON_RUN_AS_NODE") if key in os.environ}
        env.update(HOME=str(self.root), DSH_HOME=str(self.root / "dsh"),
                   DSH_TELEMETRY_MODE="DISABLED", DEEPSEEK_API_KEY="owned-fake-key",
                   NO_PROXY="127.0.0.1,::1")
        command = json.loads(os.environ["HARNESS_COMMAND"])
        return subprocess.run(command + ["--profile", "headless", "--patch", str(self.overlay),
                              "--json", "Complete the owned read/write verification."],
                              cwd=self.workspace, env=env, capture_output=True, text=True, timeout=45)

    def test_native_tool_chain_round_trips_exact_arguments(self):
        result = self.run_harness()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OFFICIAL_HARNESS_OK", result.stdout)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(path == "/anthropic/v1/messages" for path in self.paths), self.paths)
        self.assertEqual((self.workspace / "report.md").read_text(), self.payload)
        for call in self.calls:
            self.assertEqual(call["model"], "deepseek-flash")
            self.assertEqual(call["max_tokens"], 8192)
            self.assertTrue(call["stream"])
            read = next(tool for tool in call["tools"] if tool["name"] == "read")
            self.assertIn("file_path", read["input_schema"]["required"])
        for index in range(1, 4):
            results = [part for message in self.calls[index]["messages"]
                       for part in message.get("content", []) if isinstance(part, dict)
                       and part.get("type") == "tool_result"]
            self.assertEqual(len(results), index)
            self.assertEqual(results[-1]["tool_use_id"], "call_owned_" + str(index))
            self.assertFalse(results[-1].get("is_error"))
        self.assertIn("OFFICIAL_INPUT_73", json.dumps(self.calls[1]["messages"]))
        self.assertIn("print(73)", json.dumps(self.calls[3]["messages"]))

    def test_503_does_not_retry(self):
        self.reject = True
        result = self.run_harness()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("Owned 503 fixture", result.stdout + result.stderr)
        self.assertFalse((self.workspace / "report.md").exists())
