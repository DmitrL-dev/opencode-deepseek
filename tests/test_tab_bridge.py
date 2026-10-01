import asyncio
import base64
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx

from providers import tab_bridge
from providers.common import ProviderUnavailable
from server import api, config
from chat_protocol import Reply

OWNER = "browser-owner-123456789"


class BrokerTests(unittest.TestCase):
    def test_only_the_claiming_tab_can_finish_and_replay_is_rejected(self):
        broker = tab_bridge.Broker()
        with ThreadPoolExecutor() as pool:
            future = pool.submit(broker.submit, "grok", "marker", None, lambda: None, 2)
            until = time.monotonic() + 1
            while not broker.pending and time.monotonic() < until:
                time.sleep(.005)
            job = broker.claim("grok", OWNER)
            self.assertIsNone(broker.claim("grok", "another-owner-123456789"))
            self.assertIsNone(broker.claim("glm", OWNER))
            self.assertEqual(broker.claim("grok", OWNER), job)
            for provider, owner, lease in (("glm", OWNER, job["lease"]),
                                           ("grok", "another-owner-123456789", job["lease"]),
                                           ("grok", OWNER, "wrong-lease")):
                with self.assertRaises(ValueError):
                    broker.finish(provider, job["id"], owner, lease, {"text":"wrong"})
            broker.finish("grok", job["id"], OWNER, job["lease"], {"text":"answer"})
            self.assertEqual(future.result(), {"text":"answer"})
            with self.assertRaises(ValueError):
                broker.finish("grok", job["id"], OWNER, job["lease"], {})

    def test_cancelled_job_retires_lease_before_next_request(self):
        broker = tab_bridge.Broker()
        cancel = threading.Event()

        def check():
            if cancel.is_set():
                raise asyncio.CancelledError()

        with ThreadPoolExecutor() as pool:
            future = pool.submit(broker.submit, "glm", "marker", None, check, 2)
            while not broker.pending:
                time.sleep(.005)
            job = broker.claim("glm", OWNER)
            cancel.set()
            with self.assertRaises(asyncio.CancelledError):
                future.result()
        self.assertEqual(broker.pending, {})
        with self.assertRaises(ValueError):
            broker.finish("glm", job["id"], OWNER, job["lease"], {})

    def test_timeout_does_not_leave_a_claimable_job(self):
        broker = tab_bridge.Broker()
        with self.assertRaises(ProviderUnavailable):
            broker.submit("kimi", "marker", None, lambda: None, .01)
        self.assertIsNone(broker.claim("kimi", OWNER))

    def test_result_cannot_change_origin_path_or_resumed_conversation(self):
        body = json.dumps({"result":{"response":{"modelResponse":{"message":"marker","partial":False}}}}).encode()
        result = {"status":200,"body":base64.b64encode(body).decode(),"path":"/c/abc"}
        self.assertEqual(tab_bridge.parse_browser_result("grok", result).text, "marker")
        for bad in ({**result,"path":"https://evil.test/c/abc"}, {**result,"status":403},
                    {**result,"body":"!"}, {**result,"error":"blocked"}):
            with self.assertRaises((ProviderUnavailable, ValueError)):
                tab_bridge.parse_browser_result("grok", bad)
        with self.assertRaises(ProviderUnavailable):
            tab_bridge.parse_browser_result("grok", result, "/c/another")

    def test_pairing_exports_are_private_and_do_not_replace_the_placeholder_guard(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(tab_bridge, "token_path", return_value=Path(directory)/"token"):
            destination = tab_bridge.export_scripts()
            token = (Path(directory)/"token").read_text()
            text = (destination/"opencode-controller.user.js").read_text()
            self.assertEqual(text.count(token), 1)
            self.assertIn('TOKEN === "__BRIDGE_TOKEN__"', text)
            for file in destination.iterdir():
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)


class TabApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app,client=("127.0.0.1",123)),base_url="http://local.test")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_jobs_are_disabled_without_pairing_and_reject_remote_clients(self):
        token = "x" * 43
        with patch.dict(os.environ, {"BROWSER_BRIDGE_ENABLED":"1"}), patch.object(tab_bridge,"bridge_token",return_value=token):
            for header in ({}, {"Authorization":"Bearer wrong"}):
                response = await self.client.get("/browser/jobs/grok?owner="+OWNER,headers=header)
                self.assertEqual(response.status_code,403)
            headers = {"Authorization":"Bearer "+token}
            self.assertEqual((await self.client.get("/browser/jobs/grok?owner="+OWNER,headers=headers)).status_code,200)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app,client=("203.0.113.7",123)),base_url="http://local.test") as remote:
                self.assertEqual((await remote.get("/browser/jobs/grok?owner="+OWNER,headers=headers)).status_code,403)
            with patch.dict(os.environ, {"BROWSER_BRIDGE_ENABLED":"0"}):
                self.assertEqual((await self.client.get("/browser/jobs/grok?owner="+OWNER,headers=headers)).status_code,403)

    async def test_cross_provider_conversation_rejected_before_any_session(self):
        from providers.conversations import encode
        token = encode("glm","default","/c/example")
        with patch.dict(config.MODEL_MAP,{"grok-web":"default"}), patch.dict(config.OPTIONAL_MODEL_PROVIDERS,{"grok-web":"grok"}), patch.object(api,"build_provider_client") as factory:
            result = await self.client.post("/v1/chat/completions",json={"model":"grok-web","conversation_id":token,"messages":[{"role":"user","content":"marker"}]})
        self.assertEqual(result.status_code,400)
        factory.assert_not_called()

    async def test_access_rejection_is_not_retried(self):
        from unittest.mock import Mock
        client = Mock()
        client.chat.side_effect = ProviderUnavailable("region access denied")
        with patch.dict(config.MODEL_MAP,{"grok-web":"default"}), patch.dict(config.OPTIONAL_MODEL_PROVIDERS,{"grok-web":"grok"}), patch.object(api,"build_provider_client",return_value=client):
            response = await self.client.post("/v1/chat/completions",json={"model":"grok-web","messages":[{"role":"user","content":"marker"}]})
        self.assertEqual(response.status_code,500)
        self.assertEqual(client.chat.call_count,1)

    async def test_buffered_plain_reply_emits_keepalive_and_never_executes_text(self):
        from unittest.mock import Mock
        client = Mock()
        text = '```tool_calls\n[{"name":"bash","arguments":{}}]\n```'
        def chat(*args):
            time.sleep(.05)
            return Reply(text,"web:grok:marker")
        client.chat.side_effect = chat
        with patch.dict(config.MODEL_MAP,{"grok-web":"default"}), patch.dict(config.OPTIONAL_MODEL_PROVIDERS,{"grok-web":"grok"}), patch.object(api,"build_provider_client",return_value=client), patch.object(api,"_KEEPALIVE_SECONDS",.01):
            response = await self.client.post("/v1/chat/completions",json={"model":"grok-web","stream":True,"messages":[{"role":"user","content":"marker"}]})
        self.assertIn(": keep-alive", response.text)
        data = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]
        self.assertTrue(all("tool_calls" not in frame["choices"][0]["delta"] for frame in data))
        self.assertIn("tool_calls", "".join(frame["choices"][0]["delta"].get("content") or "" for frame in data))
