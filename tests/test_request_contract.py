"""Request provenance must survive logging without changing effective settings."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from chat_protocol import Reply
from server import api, config, diagnostics
from server.schemas import ChatCompletionRequest
from server.request_contract import capture, request_payload, restore, record_dispatch
from server.conversation_cache import digest
from tests.test_tools import FLAT_TOOLS


class RequestContractTests(unittest.TestCase):
    def request(self, **kwargs):
        return ChatCompletionRequest(model='deepseek-chat', messages=[{'role':'user','content':'PRIVATE_INPUT'}],
                                     tools=FLAT_TOOLS, **kwargs)

    def test_old_log_reproduces_thinking_change_but_new_round_trip_does_not(self):
        with patch.object(config, 'DEEPSEEK_TOOL_THINKING', True):
            req = self.request()
            old = ChatCompletionRequest.model_validate_json(req.model_dump_json(exclude_none=True))
            self.assertTrue(config.effective_thinking(req))
            self.assertFalse(config.effective_thinking(old))
            # Do not migrate or re-key existing history to repair diagnostics.
            self.assertEqual(digest(req, req.messages), digest(old, old.messages))
            payload = request_payload(req)
            self.assertNotIn('thinking', payload)
            restored = restore(json.loads(json.dumps(payload)), capture(req))
            self.assertEqual(restored.model_fields_set, req.model_fields_set)
            self.assertTrue(config.effective_thinking(restored))
            self.assertEqual(digest(restored, restored.messages), digest(req, req.messages))

    def test_all_explicit_options_and_false_are_preserved(self):
        for default in (True, False):
            for explicit in ({}, {'thinking':True}, {'thinking':False}):
                with self.subTest(default=default, explicit=explicit), patch.object(config, 'DEEPSEEK_TOOL_THINKING', default):
                    req = self.request(**explicit, search=True, stream=True, tool_choice=None,
                                       temperature=0.0, top_p=None, max_tokens=1, user='fixture')
                    restored = restore(request_payload(req), capture(req))
                    self.assertEqual(restored.model_fields_set, req.model_fields_set)
                    self.assertEqual(restored.model_dump(), req.model_dump())
                    self.assertEqual(config.effective_thinking(restored), explicit.get('thinking', default))
                    self.assertIn('top_p', request_payload(restored))

    def test_old_missing_forged_or_changed_contract_is_not_replayable(self):
        req = self.request(); receipt = capture(req); payload = request_payload(req)
        for bad in ({}, {**receipt, 'version':0}, {**receipt, 'replay_authorized':True},
                    {**receipt, 'explicit_fields':[]}, {**receipt, 'request_sha256':'wrong'}):
            with self.subTest(receipt=bad), self.assertRaises(ValueError): restore(payload, bad)
        for bad in ({**payload, 'thinking':False}, {**payload,'unknown':1}, {**payload,'search':True}):
            with self.assertRaises(ValueError): restore(bad, receipt)
        for field,value in [('DEEPSEEK_TOOL_THINKING',True),('DEEPSEEK_TOOL_PROTOCOL','dsml'),
                            ('TOOL_INSTRUCTIONS_HTTP','light')]:
            with patch.object(config,field,value), self.assertRaises(ValueError): restore(payload,receipt)

    def test_effective_dispatch_is_saved_at_call_not_recomputed_at_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            req = self.request()
            with patch.object(config, 'DEEPSEEK_TOOL_THINKING', True):
                record_dispatch(req, 'PRIVATE_WIRE_PROMPT', None, True)
            path = Path(directory)/'failure.json'
            with patch.object(diagnostics,'FAILURE_FILE',path), patch.dict(os.environ,TOOLCALL_DIAGNOSTICS='1'):
                diagnostics.save_tool_failure(Reply('broken', 'fixture:2'), req)
            saved=json.loads(path.read_text())['dispatch_contract']
            self.assertTrue(saved['effective']['thinking'])
            self.assertIsNone(saved['dispatch']['model_type'])
            self.assertFalse(saved['replay_authorized'])
            self.assertNotIn('PRIVATE_', json.dumps(saved))

    def test_stream_passes_exact_logged_effective_parameters(self):
        req=self.request(); client=Mock()
        with patch.object(config,'DEEPSEEK_TOOL_THINKING',True):
            api._open_stream(client,'owned prompt',req,'expert')
        self.assertTrue(client.stream.call_args.kwargs['thinking'])
        self.assertTrue(req._dispatch_contract['effective']['thinking'])
        self.assertEqual(req._dispatch_contract['dispatch']['model_type'],'expert')

    def test_receipt_cannot_be_supplied_as_private_request_attribute(self):
        req=ChatCompletionRequest.model_validate({**request_payload(self.request()), '_dispatch_contract':{'fake':True}})
        self.assertIsNone(req._dispatch_contract)

    def test_goal_policy_does_not_instruct_model_to_override_acceptance(self):
        from server.openai_format import messages_to_prompt
        for protocol in ('json','dsml'):
            with patch.object(config,'DEEPSEEK_TOOL_PROTOCOL',protocol):
                prompt=messages_to_prompt(self.request().messages,FLAT_TOOLS,provider='deepseek')
            self.assertNotIn('mark it complete once the objective is achieved',prompt)
            self.assertIn('independent review requirement forbids model self-certification',prompt)
            self.assertIn('If request_goal_review is advertised',prompt)
