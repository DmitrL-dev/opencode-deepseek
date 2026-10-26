"""Explicit named envelope closers cannot repair parameter or value boundaries."""
import json
import unittest
from unittest.mock import patch

from chat_protocol import Reply
from server import api
from server.openai_format import ToolCallError
from server.schemas import ChatCompletionRequest
from server.tool_protocol import validate_reply

TOOLS = [{'function': {'name': 'fixture.save', 'parameters': {
    'type': 'object', 'properties': {'path': {'type': 'string'},
                                    'body': {'type': 'string'}},
    'required': ['path', 'body'], 'additionalProperties': False}}},
    {'function': {'name': 'fixture.inspect', 'parameters': {
        'type': 'object', 'properties': {}, 'additionalProperties': False}}}]
TEXT = ' # Report\n\nUnicode: строка; quotes: "x"; slash: \\n\n'
SAVE = ('<｜DSML｜ invoke name="fixture.save">\n'
        '<｜｜DSML｜｜ parameter name="path" string="true">/fixture/report.md</｜｜DSML｜｜ parameter>\n'
        '<｜｜DSML｜｜ parameter name="body" string="true">' + TEXT
        + '</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>')
INSPECT = '<｜DSML｜ invoke name="fixture.inspect">\n\n</DSML｜ invoke>'
PACKET = '<｜DSML｜ calls>\n' + SAVE + '\n' + INSPECT + '\n</DSML｜ calls>'


class DsmlNamedCloserTests(unittest.TestCase):
    def test_known_named_closers_preserve_all_values_and_empty_call(self):
        for validate in (validate_reply,):
            with self.subTest(api=validate.__module__):
                _, calls = validate(PACKET, TOOLS, allow_dsml_wrapper_tolerances=True)
                self.assertEqual([c['function']['name'] for c in calls],
                                 ['fixture.save', 'fixture.inspect'])
                self.assertEqual([json.loads(c['function']['arguments']) for c in calls],
                                 [{'path': '/fixture/report.md', 'body': TEXT}, {}])

    def test_opt_in_applies_only_to_named_outer_closers(self):
        with self.assertRaises(ToolCallError):
            validate_reply(PACKET, TOOLS)
        bad = [PACKET.replace('</DSML｜ invoke>', '</DSML｜>'),
               PACKET.replace('</DSML｜ invoke>', '</DSML｜ calls>'),
               PACKET.replace('</DSML｜ calls>', '</DSML｜ invoke>'),
               PACKET.replace('</DSML｜ calls>', '</DSML｜>'),
               PACKET.replace('</DSML｜ invoke>', ''),
               PACKET.replace('</DSML｜ calls>', ''),
               PACKET.replace('</｜｜DSML｜｜ parameter>', '</DSML｜ parameter>', 1),
               PACKET.replace('</｜｜DSML｜｜ parameter>', '', 1),
               PACKET.replace('<｜DSML｜ invoke', '<DSML｜ invoke', 1),
               PACKET.replace('</DSML｜ invoke>', '</DSML| invoke>'),
               PACKET.replace('</DSML｜ invoke>', '</DSML｜｜ invoke>'),
               PACKET.replace('</DSML｜ invoke>', '</DSML｜ invented>'),
               PACKET + '\nTool: invented output', PACKET + PACKET,
               PACKET.replace(TEXT, 'literal </DSML｜ invoke>'),
               PACKET.replace('name="path"', 'name="unknown"'),
               PACKET.replace('name="fixture.inspect"', 'name="unknown"')]
        for validate in (validate_reply,):
            for text in bad:
                with self.subTest(api=validate.__module__, text=text), self.assertRaises(ToolCallError):
                    validate(text, TOOLS, allow_dsml_wrapper_tolerances=True)

    def test_quoted_examples_stay_inert_and_string_escapes_are_exact(self):
        for text in ('```xml\n' + PACKET + '\n```',
                     '\n'.join('> ' + line for line in PACKET.splitlines()),
                     '\n'.join('    ' + line for line in PACKET.splitlines())):
            for validate in (validate_reply,):
                _, calls = validate(text, TOOLS, allow_dsml_wrapper_tolerances=True)
                self.assertIsNone(calls)
        value = 'literal </DSML｜ invoke> and </DSML｜ calls>'
        encoded = json.dumps(value, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e')
        text = PACKET.replace('name="body" string="true">' + TEXT,
                              'name="body" string="false">' + encoded)
        for validate in (validate_reply,):
            _, calls = validate(text, TOOLS, allow_dsml_wrapper_tolerances=True)
            self.assertEqual(json.loads(calls[0]['function']['arguments'])['body'], value)

    def test_finish_provider_choice_and_schema_remain_required(self):
        for validate in (validate_reply,):
            for options in ({'finish_reason': 'length'}, {'provider': 'qwen'},
                            {'tool_choice': 'none'}, {'tool_choice': {
                                'type': 'function', 'function': {'name': 'fixture.inspect'}}}):
                with self.subTest(options=options), self.assertRaises(ToolCallError):
                    validate(PACKET, TOOLS, allow_dsml_wrapper_tolerances=True, **options)

    def test_saved_shape_needs_no_format_repair_in_either_api(self):
        request = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=TOOLS)
        for validate in (api._validate_tool_reply,):
            with self.subTest(api=validate.__module__), \
                    patch('server.config.DEEPSEEK_TOOL_PROTOCOL', 'dsml'), \
                    patch.object(api, '_chat_with_retry', side_effect=AssertionError('No generation')):
                _, _, calls = validate(Reply(PACKET, 'fixture:4', 'stop'), request,
                                       allow_continuation=False)
                self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
