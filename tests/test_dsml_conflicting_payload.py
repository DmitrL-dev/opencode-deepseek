"""Do not salvage valid calls around a malformed second argument encoding."""
import unittest
from unittest.mock import patch

from chat_protocol import Reply
from server import api
from server.openai_format import ToolCallError
from server.schemas import ChatCompletionRequest
from tests.test_stream import event, snapshot, parse


TOOLS = [{'type': 'function', 'function': {
    'name': 'inspect_fixture',
    'parameters': {'type': 'object', 'properties': {
        'pattern': {'type': 'string'}, 'path': {'type': 'string'}},
        'required': ['pattern', 'path'], 'additionalProperties': False},
}}]
VALUE = '{"pattern": "complete", "path": "/fixture/source.py"}'
VALID = ('<｜｜DSML｜｜ invoke name="inspect_fixture">\n'
         '<｜｜DSML｜｜ parameter name="arguments" string="false">'
         + VALUE + '</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>')
BROKEN = VALID.replace(
    '<｜｜DSML｜｜ parameter name="arguments"',
    '<｜｜DSML｜｜ parameters="' + VALUE + '}" name="inspect_fixture">\n'
    '<｜｜DSML｜｜ parameter name="arguments"')


def batch(*calls):
    return '<｜｜DSML｜｜ calls>\n' + '\n'.join(calls) + '\n</｜｜DSML｜｜ calls>'


class DsmlConflictingPayloadTests(unittest.TestCase):
    def test_finished_stream_keeps_unknown_tag_and_extra_brace(self):
        expected = batch(BROKEN, VALID)
        marker = expected.index('parameters=') + len('parameters=')
        wire = snapshot(expected[:marker], 'WIP')
        wire += event({'p': 'response/fragments/-1/content', 'o': 'APPEND',
                       'v': expected[marker:]})
        wire += event({'p': 'response/status', 'o': 'SET', 'v': 'FINISHED'})
        wire += 'event: finish\ndata: {}\n\n'
        text, meta = parse(wire)
        self.assertEqual(text, expected)
        self.assertEqual(meta['finish_reason'], 'stop')

    def test_neither_api_drops_unknown_tag_or_returns_partial_batch(self):
        request = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=TOOLS)
        for protocol in ('json', 'dsml'):
            for text in (batch(BROKEN, VALID), batch(VALID, BROKEN)):
                for validate, module in ((api._validate_tool_reply, api),):
                    with self.subTest(protocol=protocol, text=text, api=module.__name__), \
                            patch('server.config.DEEPSEEK_TOOL_PROTOCOL', protocol), \
                            patch.object(module, 'save_tool_failure') as saved, \
                            patch.object(api, '_chat_with_retry',
                                         side_effect=AssertionError('Unexpected generation')):
                        with self.assertRaises(ToolCallError):
                            validate(Reply(text, 'fixture:2', 'stop'), request,
                                     allow_continuation=False)
                        saved.assert_called_once()
                        self.assertEqual(saved.call_args.args[0].text, text)


if __name__ == '__main__':
    unittest.main()
