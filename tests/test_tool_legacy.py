"""TOOLCALL_LEGACY=1 (api/tool_protocol) vs 0 (api_new/tool_protocol_new)."""
import unittest
from unittest.mock import Mock, patch

from chat_protocol import Reply
from server import api, api_new
from server.openai_format import ToolCallError
from server.schemas import ChatCompletionRequest, ChatMessage
from server.api import looks_truncated
from server.tool_protocol import validate_reply
from tests.support import isolated_access
from tests.test_tools import block

WRITE = {"type": "function", "function": {
    "name": "write", "parameters": {
        "type": "object", "required": ["file_path", "content"],
        "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}},
        "additionalProperties": False}}}


class ToolLegacyUnitTests(unittest.TestCase):
    def test_looks_truncated_requires_reply_to_start_with_fence(self):
        cut = '```tool_calls\n[{"name":"write","arguments":{"content":"x'
        mid = 'Intro.\n```tool_calls\n[{"name":"write","arguments":{"content":"x'
        self.assertTrue(looks_truncated(cut))
        self.assertFalse(looks_truncated(mid))

    def test_validate_is_single_block_even_for_browser_provider_name(self):
        prose = 'First.\n\n' + block([{"name": "write", "arguments": {
            "file_path": "/x", "content": "y"}}]) + '\n\nDone.'
        with self.assertRaises(ToolCallError):
            validate_reply(prose, [WRITE], provider='claude')


class ToolLegacyApiTests(unittest.TestCase):
    def setUp(self):
        isolated_access(self)
        self.req = ChatCompletionRequest(
            model='claude-web', tools=[WRITE],
            messages=[ChatMessage(role='user', content='write')])

    def chat(self, replies, *, legacy, invalid_mode='text'):
        with patch.object(api, '_LEGACY', legacy), \
                patch.object(api, '_FORMAT_REPAIR', False), \
                patch.object(api_new, '_INVALID_MODE', invalid_mode), \
                patch.object(api, '_chat_with_retry', side_effect=replies) as chat:
            return api._conversation_tool_chat('p', self.req, None), chat

    def test_legacy_ignores_invalid_mode_text(self):
        bad = block([{"name": "write", "arguments": {"file_path": "/x"}}])
        with self.assertRaises(ToolCallError):
            self.chat([Reply(bad, 'claude:1')], legacy=True, invalid_mode='text')

    def test_new_path_can_return_bridge_notice(self):
        bad = block([{"name": "write", "arguments": {"file_path": "/x"}}])
        (reply, content, calls), chat = self.chat(
            [Reply(bad, 'claude:1')], legacy=False, invalid_mode='text')
        self.assertEqual(chat.call_count, 1)
        self.assertIsNone(calls)
        self.assertIsNotNone(reply.bridge_notice)
        self.assertIn('BRIDGE_TOOL_NOT_EXECUTED', content)

    def test_legacy_mid_message_tool_fence_errors_without_demote(self):
        advice = ('How to prompt:\n```tool_calls\n'
                  '[{"arguments": {"file_path": "/x"}}]\n```\nDone.')
        with self.assertRaises(ToolCallError) as ctx:
            self.chat([Reply(advice, 'claude:1')], legacy=True)
        self.assertIn('Unsupported tool-call markup', str(ctx.exception))

    def test_new_path_demotes_advice(self):
        advice = ('How to prompt:\n```tool_calls\n'
                  '[{"arguments": {"file_path": "/x"}}]\n```\nDone.')
        (_, content, calls), chat = self.chat([Reply(advice, 'claude:1')], legacy=False)
        self.assertEqual(chat.call_count, 1)
        self.assertIsNone(calls)
        self.assertIn('```text', content)
        self.assertNotIn('```tool_calls', content)

    def test_new_path_demotes_on_deepseek_too(self):
        advice = ('How to prompt:\n```tool_calls\n'
                  '[{"arguments": {"file_path": "/x"}}]\n```\nDone.')
        req = self.req.model_copy(update={'model': 'deepseek-chat'})
        fake = Mock()
        with patch.object(api, '_LEGACY', False), \
                patch.object(api, '_FORMAT_REPAIR', False), \
                patch.object(api, 'get_client', return_value=fake), \
                patch.object(api, '_chat_with_retry',
                             side_effect=[Reply(advice, 'ds:1')]) as chat:
            _, content, calls = api._conversation_tool_chat('p', req, None)
        self.assertEqual(chat.call_count, 1)
        self.assertIsNone(calls)
        self.assertIn('```text', content)
        self.assertNotIn('```tool_calls', content)


if __name__ == '__main__':
    unittest.main()
