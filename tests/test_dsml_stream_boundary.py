"""Sanitized regression for the observed split-marker stream, without accounts.

The independently written expected text is deliberately not produced by the
DSML decoder. Adjacent identical APPEND values are two actual characters, not
duplicate network frames to deduplicate or missing tags to repair.
"""
import unittest
from unittest.mock import patch

from chat_protocol import Reply
from server import api, api_new
from server.openai_format import ToolCallError
from server.schemas import ChatCompletionRequest
from tests.test_stream import event, snapshot, parse
from tests.test_tools import FLAT_TOOLS

BROKEN = '''<｜DSML｜｜ calls>
<｜DSML｜｜ invoke name="bash">
<｜DSML｜｜ parameter name="command" string="true">pwd</｜DSML｜｜ parameter>
<｜DSML｜｜ parameter name="description" string="true">Inspect fixture</｜DSML｜｜>
<｜DSML｜｜ invoke name="bash">
<｜DSML｜｜ parameter name="command" string="true">ls -la</｜DSML｜｜ parameter>
<｜DSML｜｜ parameter name="description" string="true">List fixture</｜DSML｜｜>
<｜DSML｜｜>
</｜DSML｜｜ calls>'''


def fragmented_payload():
    pieces = ['｜', 'DS', 'ML', '｜', '｜', ' calls', '>\n']
    # Preserve the repeated delimiter at the exact observed SSE boundary.
    pieces.extend(BROKEN.split('\n', 1)[1])
    wire = snapshot('<', 'WIP')
    wire += event({'p': 'response/fragments/-1/content', 'o': 'APPEND', 'v': pieces[0]})
    wire += ''.join(event({'v': piece}) for piece in pieces[1:])
    wire += event({'p': 'response/status', 'o': 'SET', 'v': 'FINISHED'})
    wire += 'event: finish\ndata: {}\n\n'
    return wire


class DsmlStreamBoundaryTests(unittest.TestCase):
    def test_completed_transport_preserves_malformed_source_exactly(self):
        text, meta = parse(fragmented_payload())
        self.assertEqual(text, BROKEN)
        self.assertEqual(meta, {'message_id': 2, 'finish_reason': 'stop'})

    def test_both_api_contracts_reject_without_repair_or_partial_calls(self):
        text, meta = parse(fragmented_payload())
        request = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=FLAT_TOOLS)
        for validate, module in ((api._validate_tool_reply, api), (api_new.validate_tool_reply, api_new)):
            with self.subTest(contract=module.__name__), \
                    patch('server.config.DEEPSEEK_TOOL_PROTOCOL', 'dsml'), \
                    patch.object(module, 'save_tool_failure') as saved, \
                    patch.object(api, '_chat_with_retry', side_effect=AssertionError('Unexpected generation')):
                with self.assertRaises(ToolCallError):
                    validate(Reply(text, 'fixture:2', meta['finish_reason']), request,
                             allow_continuation=False)
                saved.assert_called_once()
                self.assertEqual(saved.call_args.args[0].text, BROKEN)
