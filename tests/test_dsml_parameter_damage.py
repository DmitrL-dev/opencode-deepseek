"""Missing parameter framing is never repaired from a recognizable field name."""
import unittest
from unittest.mock import patch

from chat_protocol import Reply
from server import api, api_new
from server.openai_format import ToolCallError
from server.schemas import ChatCompletionRequest
from tests.test_stream import event, snapshot, parse


TOOLS = [{'function': {'name': 'fixture.inspect', 'parameters': {
    'type': 'object', 'properties': {'target': {'type': 'string'},
                                    'description': {'type': 'string'}},
    'required': ['target', 'description'], 'additionalProperties': False}}}]
PREFIX = 'parameter name="'
CALL = ('<｜｜DSML｜｜ invoke name="fixture.inspect">\n'
        '<｜｜DSML｜｜ parameter name="target" string="true">/fixture/input'
        '</｜｜DSML｜｜ parameter>\n'
        '<｜｜DSML｜｜ parameter name="description" string="true">Inspect fixture'
        '</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>')


def batch(*calls):
    return '<｜｜DSML｜｜ calls>\n' + '\n'.join(calls) + '\n</｜｜DSML｜｜ calls>'


class DsmlParameterDamageTests(unittest.TestCase):
    def test_torn_headers_never_deliver_valid_neighbors_or_generate_a_repair(self):
        request = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=TOOLS)
        # Delete every nonempty prefix of the structural spelling. A complete
        # field name/value and valid neighboring calls must not invent that prefix.
        mutations = [CALL.replace(PREFIX + 'target"', PREFIX[n:] + 'target"', 1)
                     for n in range(1, len(PREFIX) + 1)]
        mutations += [CALL.replace('<｜｜DSML｜｜ parameter name="target"',
                                   '</｜｜DSML｜｜ target"', 1),
                      CALL.replace('<｜｜DSML｜｜ parameter name="description"',
                                   '</｜｜DSML｜｜ description"', 1)]
        for broken in mutations:
            for text in (batch(broken, CALL), batch(CALL, broken)):
                for validate, module in ((api._validate_tool_reply, api),
                                         (api_new.validate_tool_reply, api_new)):
                    with self.subTest(broken=broken, text=text, api=module.__name__), \
                            patch('server.config.DEEPSEEK_TOOL_PROTOCOL', 'dsml'), \
                            patch.object(module, 'save_tool_failure') as saved, \
                            patch.object(api, '_chat_with_retry',
                                         side_effect=AssertionError('No regeneration')):
                        with self.assertRaises(ToolCallError):
                            validate(Reply(text, 'fixture:8', 'stop'), request,
                                     allow_continuation=False)
                        saved.assert_called_once()
                        self.assertEqual(saved.call_args.args[0].text, text)

    def test_complete_sse_retains_damage_even_when_chunks_split_the_header(self):
        text = batch(CALL.replace(PREFIX + 'target"', 'target"', 1))
        boundary = text.index('target"')
        wire = snapshot(text[:boundary], 'WIP')
        wire += event({'p': 'response/fragments/-1/content', 'o': 'APPEND',
                       'v': text[boundary:boundary + 4]})
        wire += event({'v': text[boundary + 4:]})
        wire += event({'p': 'response/status', 'o': 'SET', 'v': 'FINISHED'})
        wire += 'event: finish\ndata: {}\n\n'
        answer, metadata = parse(wire)
        self.assertEqual(answer, text)
        self.assertEqual(metadata['finish_reason'], 'stop')


if __name__ == '__main__':
    unittest.main()
