"""Single tool pipeline (api/tool_protocol): strict, never demotes or notices."""
import unittest
from unittest.mock import patch

from chat_protocol import Reply
from server import api
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

    def chat(self, replies):
        with patch.object(api, '_FORMAT_REPAIR', False), \
                patch.object(api, '_chat_with_retry', side_effect=replies) as chat:
            return api._conversation_tool_chat('p', self.req, None), chat

    def test_invalid_reply_raises(self):
        bad = block([{"name": "write", "arguments": {"file_path": "/x"}}])
        with self.assertRaises(ToolCallError):
            self.chat([Reply(bad, 'claude:1')])

    def test_mid_message_tool_fence_errors_without_demote(self):
        advice = ('How to prompt:\n```tool_calls\n'
                  '[{"arguments": {"file_path": "/x"}}]\n```\nDone.')
        with self.assertRaises(ToolCallError) as ctx:
            self.chat([Reply(advice, 'claude:1')])
        self.assertIn('Unsupported tool-call markup', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
