import os
import unittest
from pathlib import Path
from unittest.mock import patch

from server import toolcall_logging


class ToolcallLoggingTests(unittest.TestCase):
    def test_disabled_by_default_and_enabled_only_by_one(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(toolcall_logging.enabled())
        with patch.dict(os.environ, {"TOOLCALL_LOG": "0"}):
            self.assertFalse(toolcall_logging.enabled())
        with patch.dict(os.environ, {"TOOLCALL_LOG": "1"}):
            self.assertTrue(toolcall_logging.enabled())

    def test_new_chat_allocates_unique_file_and_writes_sections(self):
        log_dir = Path(__file__).resolve().parents[1] / "test-logs-fixture"
        files = set()
        chunks = {}

        def fake_mkdir(path, *args, **kwargs):
            return None

        def fake_exists(path):
            return Path(path) in files

        def fake_open(path, flags, mode=0o777, *args, **kwargs):
            path = Path(path)
            if path.parent != log_dir:
                return os.open(str(path), flags, mode)
            if path in files:
                raise FileExistsError(str(path))
            files.add(path)
            return 1

        class MemoryWriter:
            def __init__(self, path):
                self.path = path
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def write(self, value):
                chunks.setdefault(self.path, []).append(value)

        with patch.object(toolcall_logging, "LOG_DIR", log_dir), \
             patch.dict(os.environ, {"TOOLCALL_LOG": "1"}), \
             patch.object(Path, "mkdir", fake_mkdir), \
             patch.object(Path, "exists", fake_exists), \
             patch.object(Path, "chmod", lambda *a, **k: None), \
             patch.object(os, "open", fake_open), \
             patch.object(os, "close", lambda fd: None), \
             patch.object(Path, "open", lambda path, *a, **k: MemoryWriter(path)):
            first = toolcall_logging.new_chat("my/provider")
            second = toolcall_logging.new_chat("my/provider")
            self.assertIsNotNone(first)
            self.assertEqual(first.parent, log_dir)
            self.assertRegex(first.name, r"^\d{10}-my_provider(?:-\d+)?\.log$")
            self.assertNotEqual(first, second)
            for direction, payload in (
                ("from_agent", '{"messages": []}'),
                ("to_provider", "prompt\nexact"),
                ("from_provider", "reply"),
                ("to_agent", '{"choices": []}'),
                ("to_agent", {"choices": [], "content": "Привет"}),
                ("to_agent", type("Response", (), {"body": b'{"error":{"message":"nope"}}'})()),
                ("to_agent", 'data: {"id":"chunk-1","choices":[{"delta":{"content":"hello"}}]}\\n\\n'),
                ("to_agent", 'data: {"id":"chunk-2","choices":[{"delta":{"content":"```tool_calls id=\\"c1\\"\\n[{\\"name\\":\\"read\\",\\"arguments\\":{\\"file_path\\":\\"a.py\\"}}]\\n```"},"finish_reason":null}]}'),
                ("to_agent", 'data: {"id":"chunk-3","choices":[{"delta":{"content":"real\\nline"}}]}'),
                ("add_chat", {"key": "history-key", "used": 0}),
                ("search_chat", {"key": "history-key"}),
                ("consume_chat", {"key": "history-key", "used": 1}),
            ):
                toolcall_logging.record(first, direction, payload)
        text = "".join(chunks[first])
        for marker in toolcall_logging.SEPARATOR.values():
            self.assertIn(marker + "\n", text)
        self.assertIn("prompt\nexact\n", text)
        self.assertIn('{\n  "messages": []\n}', text)
        self.assertIn('{\n  "choices": []\n}', text)
        self.assertIn('{\n  "choices": [],\n  "content": "Привет"\n}', text)
        self.assertIn('{\n  "error": {\n    "message": "nope"\n  }\n}', text)
        self.assertIn('data: {\n  "id": "chunk-1",\n  "choices": [\n    {\n      "delta": {\n        "content": "hello"\n      }\n    }\n  ]\n}', text)
        self.assertIn('data: {\n  "id": "chunk-2",\n  "choices": [', text)
        self.assertIn('```tool_calls id=\\"c1\\"\\n[{\\"name\\":\\"read\\",\\"arguments\\":{\\"file_path\\":\\"a.py\\"}}]\\n```', text)
        self.assertIn('data: {\n  "id": "chunk-3",\n  "choices": [\n    {\n      "delta": {\n        "content": "real\\nline"', text)


    def test_disabled_logging_does_not_create_directory(self):
        with patch.dict(os.environ, {"TOOLCALL_LOG": "0"}):
            self.assertIsNone(toolcall_logging.new_chat("deepseek"))


if __name__ == "__main__":
    unittest.main()
