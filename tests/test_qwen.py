import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import httpx

from deepseek.auth import Session as DeepSeekSession
from qwen.auth import LoginRequired, Session, _capture, get_session
from qwen.client import QwenClient, QwenStreamError, _decode_cid, _parse_sse

CHAT = "00000000-0000-4000-8000-000000000001"
MESSAGE = "00000000-0000-4000-8000-000000000002"


def session():
    return Session("fake-qwen-token", [], "test-agent", time.time(), time.time() + 900)


def events(*objects, done=True):
    result = []
    for obj in objects:
        result += ["data: " + json.dumps(obj), ""]
    if done:
        result += ["data: [DONE]", ""]
    return result


def answer(content, **extra):
    return {"response_id": MESSAGE, "choices": [{"index": 0, "delta": {"phase": "answer", "content": content, **extra}}]}


class QwenSessionTests(unittest.TestCase):
    def test_provider_cookies_never_cross_the_boundary(self):
        qwen = session()
        qwen.cookies = [
            {"name": "qwen", "value": "fake", "domain": ".qwen.ai", "path": "/", "secure": True},
            {"name": "auth", "value": "fake", "domain": "auth.qwen.ai", "path": "/api/v2/auths", "secure": True},
            {"name": "deepseek", "value": "fake", "domain": ".deepseek.com", "path": "/"},
            {"name": "google", "value": "fake", "domain": ".google.com", "path": "/"},
        ]
        with httpx.Client(cookies=qwen.cookie_jar()) as client:
            self.assertEqual(client.build_request("GET", "https://chat.qwen.ai/api/v2/models/").headers["cookie"], "qwen=fake")
            self.assertNotIn("cookie", client.build_request("GET", "https://chat.deepseek.com/").headers)
            self.assertNotIn("cookie", client.build_request("GET", "https://google.com/").headers)
            self.assertNotIn("cookie", client.build_request("GET", "http://chat.qwen.ai/").headers)
        ds = DeepSeekSession("fake", qwen.cookies, "test", time.time())
        with httpx.Client(cookies=ds.cookie_jar()) as client:
            self.assertNotIn("cookie", client.build_request("GET", "https://chat.qwen.ai/").headers)

    def test_capture_never_inspects_an_oauth_provider(self):
        page = Mock(url="https://accounts.google.com/")
        self.assertIsNone(_capture(Mock(), page))
        page.evaluate.assert_not_called()

    def test_capture_rejects_expired_or_invalid_expiry(self):
        page = Mock(url="https://chat.qwen.ai/")
        for expires in (time.time() - 1, float("nan"), "invalid"):
            page.evaluate.return_value = {"token": "fake", "expires_at": expires}
            self.assertIsNone(_capture(Mock(), page))

    def test_session_storage_and_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qwen" / "session.json"
            saved = session()
            saved.save(path)
            loaded = Session.load(path)
            self.assertTrue(loaded.usable)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            loaded.expires_at = time.time() + 30
            self.assertFalse(loaded.usable)

    def test_short_expiry_triggers_headless_refresh_even_for_a_fresh_capture(self):
        cached, fresh = session(), session()
        cached.expires_at = time.time() + 30
        with patch.object(Session, "load", return_value=cached), patch("qwen.auth._capture_profile", return_value=fresh) as capture, patch.object(Session, "save") as save:
            self.assertIs(get_session(allow_interactive=False), fresh)
        self.assertTrue(capture.call_args.args[1])
        save.assert_called_once()

    def test_disabled_fallback_and_headless_mode_never_open_login(self):
        with patch.object(Session, "load", return_value=None), patch("qwen.auth._capture_profile", return_value=None) as capture, patch("qwen.auth.login") as login:
            with self.assertRaises(LoginRequired):
                get_session(allow_interactive=False, fallback_channel="")
        self.assertEqual(capture.call_count, 1)
        self.assertTrue(capture.call_args.args[1])
        login.assert_not_called()


class QwenStreamTests(unittest.TestCase):
    def test_only_answer_text_is_exposed(self):
        meta = {"chat_id": CHAT}
        stream = events(
            {"response.created": {"response_id": MESSAGE, "chat_id": CHAT}},
            {"choices": [{"delta": {"phase": "think", "content": "hidden reasoning", "status": "finished"}}]},
            answer("hello"), answer(" world", status="finished"),
        )
        self.assertEqual("".join(_parse_sse(stream, meta)), "hello world")
        self.assertEqual(meta["message_id"], MESSAGE)
        self.assertEqual(meta["finish_reason"], "stop")

    def test_eof_and_reasoning_completion_do_not_count_as_success(self):
        for stream in (events(answer("partial"), done=False),
                       events({"choices": [{"delta": {"phase": "think", "status": "finished"}}]}, done=False)):
            with self.assertRaises(QwenStreamError):
                list(_parse_sse(stream, {}))

    def test_errors_and_stopped_responses_are_not_successful(self):
        for error in ({"error": {"message": "failed"}}, {"success": False, "data": {"code": "invalid_token"}},
                      {"response.stopped": {"response_id": MESSAGE}}):
            with self.assertRaises(QwenStreamError):
                list(_parse_sse(events(answer("partial"), error), {}))

    def test_output_limit_is_reported(self):
        meta = {}
        self.assertEqual("".join(_parse_sse(events({"choices": [{"delta": {"content": "cut"}, "finish_reason": "length"}]}), meta)), "cut")
        self.assertEqual(meta["finish_reason"], "length")

    def test_invalid_and_foreign_conversation_ids_are_rejected(self):
        for cid in ("deepseek:2", f"qwen:other:{CHAT}:{MESSAGE}", "qwen:qwen3.8-max:../foreign:invalid"):
            with self.assertRaises(ValueError):
                _decode_cid(cid)

    def test_different_chat_ids_are_rejected(self):
        with self.assertRaises(QwenStreamError):
            list(_parse_sse(events({"response.created": {"chat_id": MESSAGE}}), {"chat_id": CHAT}))

    def test_transport_model_and_resume_contract(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path == "/api/v2/chats/new":
                return httpx.Response(200, json={"success": True, "data": {"id": CHAT}})
            self.assertEqual(request.url.path, "/api/v2/chat/completions")
            text = "\n".join(events({"response.created": {"response_id": MESSAGE, "chat_id": CHAT}}, answer("OK")))
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        client = QwenClient(session(), transport=httpx.MockTransport(handler))
        try:
            reply = client.chat("hello", model="qwen3.8-max")
            self.assertEqual(reply.text, "OK")
            self.assertEqual(reply.conversation_id, f"qwen:qwen3.8-max:{CHAT}:{MESSAGE}")
            request = json.loads(requests[-1].content)
            self.assertEqual(request["model"], "qwen3.8-max")
            self.assertFalse(request["messages"][0]["feature_config"]["auto_search"])
            client.chat("continue", reply.conversation_id)
            self.assertEqual(len([r for r in requests if r.url.path.endswith("/chats/new")]), 1)
            self.assertEqual(json.loads(requests[-1].content)["parent_id"], MESSAGE)
        finally:
            client.close()

    def test_non_sse_business_error_is_read_and_preserves_auth_rejection(self):
        def handler(request):
            if request.url.path == "/api/v2/chats/new":
                return httpx.Response(200, json={"success": True, "data": {"id": CHAT}})
            payload = json.dumps({"success": False, "data": {"code": "invalid_token", "message": "Sign in again"}}).encode()
            return httpx.Response(200, stream=httpx.ByteStream(payload), headers={"content-type": "application/json"})
        client = QwenClient(session(), transport=httpx.MockTransport(handler))
        try:
            with self.assertRaisesRegex(QwenStreamError, "invalid_token"):
                client.chat("hello")
        finally:
            client.close()
