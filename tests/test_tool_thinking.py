"""Transport thinking policy must not rewrite durable request identity."""
import copy
import os
import unittest
from unittest.mock import Mock, patch

from chat_protocol import Reply
from server import api, config
from server.schemas import ChatCompletionRequest
from tests.support import isolated_access
from tests.test_tools import FLAT_TOOLS


class ToolThinkingTests(unittest.TestCase):
    def setUp(self):
        isolated_access(self)

    def request(self, **kwargs):
        return ChatCompletionRequest(model='deepseek-expert', messages=[],
                                     **{'tools': FLAT_TOOLS, **kwargs})

    def test_defaults_explicit_override_and_provider_isolation(self):
        for enabled in (False, True):
            with patch.object(config, 'DEEPSEEK_TOOL_THINKING', enabled):
                self.assertEqual(config.effective_thinking(self.request()), enabled)
                self.assertFalse(config.effective_thinking(self.request(thinking=False)))
                self.assertTrue(config.effective_thinking(self.request(thinking=True)))
                self.assertFalse(config.effective_thinking(self.request(tools=None)))
                with patch.object(config, 'model_provider', return_value='qwen'):
                    self.assertFalse(config.effective_thinking(self.request()))

    def test_buffered_and_stream_dispatch_once_preserve_original_request(self):
        client = Mock()
        client.chat.return_value = Reply('fixture', 'fixture:2')
        client.stream.return_value = iter(())
        for cid in (None, 'fixture:2'):
            for explicit in ({}, {'thinking': False}, {'thinking': True}):
                request = self.request(conversation_id=cid, **explicit)
                before = copy.deepcopy(request.model_dump())
                fields = set(request.model_fields_set)
                expected = explicit.get('thinking', True)
                with patch.object(config, 'DEEPSEEK_TOOL_THINKING', True), \
                        patch.object(api, '_request_client', return_value=client):
                    client.reset_mock()
                    api._chat_with_retry('fixture', request, 'expert')
                    client.chat.assert_called_once_with('fixture', cid, 'expert', expected, False)
                    api._open_stream(client, 'fixture', request, 'expert')
                    client.stream.assert_called_once_with('fixture', conversation_id=cid,
                        model='expert', thinking=expected, search=False)
                self.assertEqual(request.model_dump(), before)
                self.assertEqual(request.model_fields_set, fields)

    def test_setting_rejects_ambiguous_value(self):
        with patch.dict(os.environ, DEEPSEEK_TOOL_THINKING='yes'), self.assertRaises(ValueError):
            config._transport('DEEPSEEK_TOOL_THINKING', '0', ('0', '1'))
