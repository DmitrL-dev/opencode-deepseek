import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from openai import OpenAI, PermissionDeniedError, APIError

from deepseek.auth import get_session, LoginRequired
from deepseek.client import DeepSeekClient, _biz, _parse_sse
from providers.access import AccessGuard, ProviderRejected
from providers.common import ProviderUnavailable
from server import api
from server.config import DEEPSEEK_MODEL_MAP, QWEN_MODEL_MAP
from tests.test_api import TOOLS
from tests.test_stream import event
from tests.test_tools import block

MUTED = {"code": 0, "msg": "", "data": {"biz_code": 5, "biz_msg": "user is muted", "biz_data": None}}


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "pauses.json"

    def test_pause_survives_restart_and_resume_in_separate_process_view(self):
        guard = AccessGuard(self.path, interval=0)
        guard.reject("deepseek", ProviderRejected("account_restricted"))
        restarted = AccessGuard(self.path, interval=0)
        with self.assertRaises(ProviderRejected) as failure:
            restarted.check("deepseek")
        self.assertEqual(failure.exception.code, "account_restricted")
        restarted.check("qwen")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        restarted.resume("deepseek")
        guard.check("deepseek")

    def test_malformed_state_and_failed_write_stop_requests(self):
        for text in ('{', '[]', '{"qwen": []}', '{"qwen": "invented"}'):
            self.path.write_text(text)
            with self.subTest(text=text), self.assertRaises(ProviderRejected) as error:
                AccessGuard(self.path, interval=0).check("qwen")
            self.assertEqual(error.exception.code, "safety_state_unavailable")
        self.path.unlink()
        guard = AccessGuard(self.path, interval=0)
        with patch.object(guard, "_save", side_effect=OSError("disk full")):
            error = guard.reject("qwen", RuntimeError("quota secret=private"))
        self.assertEqual(error.code, "safety_state_unavailable")
        with self.assertRaises(ProviderRejected):
            guard.check("qwen")

    def test_pacing_can_be_cancelled_without_starting_another_attempt(self):
        guard = AccessGuard(path=None, interval=60)
        guard.wait("qwen")
        with self.assertRaises(asyncio.CancelledError):
            guard.wait("qwen", lambda: (_ for _ in ()).throw(asyncio.CancelledError()))

    def test_business_errors_are_checked_in_json_and_sse_and_sanitized(self):
        for operation in (lambda: _biz(MUTED), lambda: list(_parse_sse(event(MUTED).splitlines()))):
            with self.assertRaises(ProviderRejected) as error:
                operation()
            self.assertEqual(error.exception.code, "account_restricted")
            self.assertNotIn("biz_data", str(error.exception))
        with self.assertRaises(ProviderRejected) as error:
            _biz({"code": 123, "msg": "secret=private"})
        self.assertNotIn("private", str(error.exception))

    def test_expired_session_does_not_launch_or_recapture_a_browser(self):
        with patch("deepseek.auth.Session.load", return_value=None), patch("deepseek.auth._headless_refresh") as refresh, patch("deepseek.auth.login") as login:
            with self.assertRaises(LoginRequired):
                get_session(allow_interactive=True)
            refresh.assert_not_called()
            login.assert_not_called()

    def test_clean_install_has_no_implicitly_enabled_provider(self):
        env = {**os.environ, "DEEPSEEK_ENABLED": "0", "QWEN_ENABLED": "0", **{name + "_ENABLED": "0" for name in ("GROK", "MISTRAL", "KIMI", "GLM", "GEMINI")}}
        result = subprocess.run([sys.executable, "-c", "from server.config import MODEL_MAP; print(MODEL_MAP)"], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "{}")


class SafetySdkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.guard = AccessGuard(Path(self.directory.name) / "pauses.json", interval=0)
        for patcher in (patch.object(api, "guard", self.guard), patch.dict(api.MODEL_MAP, {**DEEPSEEK_MODEL_MAP, **QWEN_MODEL_MAP}, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.transport = TestClient(api.app)
        self.transport.__enter__()
        self.addCleanup(self.transport.__exit__, None, None, None)
        # Keep the SDK's actual default retry policy enabled.
        self.sdk = OpenAI(api_key="offline", base_url="http://testserver/v1", http_client=self.transport)
        self.addCleanup(self.sdk.close)
        self.messages = [{"role": "user", "content": "offline fixture"}]

    def deepseek_fixture(self, rejected_path):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            if request.url.path == rejected_path:
                return httpx.Response(200, json=MUTED)
            data = {"chat_session": {"id": "fixture"}} if request.url.path.endswith("/create") else {"challenge": {}}
            return httpx.Response(200, json={"code": 0, "data": {"biz_code": 0, "biz_data": data}})
        client = DeepSeekClient.__new__(DeepSeekClient)
        client._request_lock = threading.Lock()
        client._pow_lock = threading.Lock()
        client._pow = Mock()
        client._pow.make_header.return_value = "fixture"
        client._http = httpx.Client(base_url="https://offline.invalid", transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client, calls

    def test_muted_at_each_protocol_step_stops_sdk_and_later_requests(self):
        for path in ("/api/v0/chat_session/create", "/api/v0/chat/create_pow_challenge", "/api/v0/chat/completion"):
            self.guard.resume("deepseek")
            client, calls = self.deepseek_fixture(path)
            with self.subTest(path=path), patch.object(api, "get_client", return_value=client) as factory:
                for _ in range(2):
                    with self.assertRaises(PermissionDeniedError) as failure:
                        self.sdk.chat.completions.create(model="deepseek-chat", messages=self.messages)
                    self.assertFalse(failure.exception.body["retryable"])
                    self.assertEqual(failure.exception.body["code"], "account_restricted")
                self.assertEqual(calls.count(path), 1)
                self.assertEqual(factory.call_count, 1)

    def test_stream_error_is_terminal_and_reconnect_is_blocked_before_auth(self):
        for tools in (None, TOOLS):
            self.guard.resume("deepseek")
            client, calls = self.deepseek_fixture("/api/v0/chat/completion")
            with patch.object(api, "get_client", return_value=client) as factory:
                with self.sdk.chat.completions.create(model="deepseek-chat", messages=self.messages, tools=tools, stream=True) as stream:
                    with self.assertRaises(APIError):
                        list(stream)
                with self.assertRaises(PermissionDeniedError):
                    self.sdk.chat.completions.create(model="deepseek-chat", messages=self.messages, tools=tools, stream=True)
                self.assertEqual(calls.count("/api/v0/chat/completion"), 1)
                self.assertEqual(factory.call_count, 1)

    def test_tool_continuation_does_not_mask_or_retry_protective_refusal(self):
        client = Mock()
        from chat_protocol import Reply
        client.chat.side_effect = [Reply('```tool_calls\n[{"name":"write","arguments":{"content":"', "fixture:2", "length"), ProviderRejected("quota_exceeded")]
        with patch.object(api, "get_client", return_value=client):
            with self.assertRaises(PermissionDeniedError) as error:
                self.sdk.chat.completions.create(model="deepseek-chat", messages=self.messages, tools=TOOLS)
        self.assertEqual(error.exception.body["code"], "quota_exceeded")
        self.assertEqual(client.chat.call_count, 2)

    def test_browser_or_cli_denial_is_terminal_and_provider_scoped(self):
        for provider in ("grok", "mistral", "kimi", "glm", "gemini"):
            name = provider + "-fixture"
            fake = Mock()
            fake.chat.side_effect = ProviderUnavailable("quota or regional restriction")
            with patch.dict(api.MODEL_MAP, {name: "fixture"}), patch.dict(api.model_provider.__globals__["OPTIONAL_MODEL_PROVIDERS"], {name: provider}), patch.object(api, "build_provider_client", return_value=fake) as factory:
                for _ in range(2):
                    with self.assertRaises(PermissionDeniedError):
                        self.sdk.chat.completions.create(model=name, messages=self.messages)
                self.assertEqual(factory.call_count, 1)
                self.assertEqual(fake.chat.call_count, 1)
        self.guard.check("qwen")

    def test_disabled_deepseek_never_initializes_a_session(self):
        with patch.dict(api.MODEL_MAP, QWEN_MODEL_MAP, clear=True), patch.object(api, "get_client") as factory:
            response = self.transport.post("/v1/chat/completions", json={"model": "deepseek-chat", "messages": self.messages})
        self.assertEqual(response.status_code, 404)
        factory.assert_not_called()

    def test_no_background_capture_even_with_legacy_refresh_opt_in(self):
        with patch.object(api, "SESSION_REFRESH_ENABLED", True), patch.object(api, "_build_client") as build:
            with TestClient(api.app):
                pass
        build.assert_not_called()
