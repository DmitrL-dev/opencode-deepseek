"""Bridge notice payloads still read from stored history by conversation_cache."""
import unittest

from server.tool_notice import (
    STATUS_CODE, bridge_notice_payload, provider_tool_error_followup, tool_failure_notice,
)
from server.openai_format import ToolCallError, completion_response


class ToolNoticeTests(unittest.TestCase):
    def test_bridge_notice_is_on_openai_completion_and_refusal(self):
        exc = ToolCallError('bad', kind='schema', tool='ask_user_question')
        notice = bridge_notice_payload(exc)
        body = completion_response('claude-web', 'raw\n\n' + notice['message'], 'p',
                                   'claude:1', bridge_notice=notice)
        self.assertEqual(body['bridge_notice']['code'], STATUS_CODE)
        self.assertEqual(body['choices'][0]['message']['refusal'], notice['message'])
        self.assertIn(STATUS_CODE, body['choices'][0]['message']['content'])

    def test_provider_followup_looks_like_tool_result_error(self):
        notice = tool_failure_notice(
            ToolCallError('bad', kind='unknown_tool', tool='made_up'))
        text = provider_tool_error_followup(notice, 'retry please')
        self.assertTrue(text.startswith('Tool result (made_up):'))
        self.assertIn('retry please', text)


if __name__ == '__main__':
    unittest.main()
