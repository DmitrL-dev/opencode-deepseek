"""Exact named shorthand is structural; schemas and complete batches still gate it."""
import copy
import json
import unittest

from server.dsml import decode
from server.openai_format import ToolCallError, _tool_json
from server.tool_protocol import validate_reply

TOOLS = [
    {'function': {'name': 'create_goal', 'parameters': {'type': 'object', 'properties': {
        'objective': {'type': 'string'}, 'max_goal_rounds': {'type': 'number'}}, 'required': ['objective']}}},
    {'function': {'name': 'bash', 'parameters': {'type': 'object', 'properties': {
        'command': {'type': 'string'}, 'description': {'type': 'string'}},
        'required': ['command', 'description'], 'additionalProperties': False}}},
]
OBJECTIVE = 'довести до полной реализации всех планов'
COMMAND = 'set -euo pipefail\ncd /owned/workspace\nwc -l docs/PLAN.md\ngit status --short'


def packet(key='objective', value=OBJECTIVE, flag='true'):
    return f'''<｜DSML｜ calls>
<｜DSML｜ invoke name="create_goal">
<｜DSML｜ {key}" string="{flag}">{value}</｜DSML｜ parameter>
<｜DSML｜ parameter name="max_goal_rounds" string="false">241</｜DSML｜ parameter>
</｜DSML｜ invoke>
<｜DSML｜ invoke name="bash">
<｜DSML｜ parameter name="command" string="true">{COMMAND}</｜DSML｜ parameter>
<｜DSML｜ parameter name="description" string="true">Inspect plan size</｜DSML｜ parameter>
</｜DSML｜ parameter>
</｜DSML｜ invoke>
</｜DSML｜ calls>'''


class ShortParameterTests(unittest.TestCase):
    def check(self, text, tools=TOOLS, **kwargs):
        for validate in (validate_reply,):
            with self.subTest(validator=validate.__module__):
                yield validate(text, tools, allow_dsml_wrapper_tolerances=True, **kwargs)

    def rejected(self, text, tools=TOOLS, **kwargs):
        for validate in (validate_reply,):
            with self.subTest(validator=validate.__module__), self.assertRaises(ToolCallError):
                validate(text, tools, allow_dsml_wrapper_tolerances=True, **kwargs)

    def test_saved_failure_shape_preserves_all_arguments_and_order(self):
        for text in [packet(), packet().replace('｜DSML｜', '｜｜DSML｜｜')]:
            for content, calls in self.check(text):
                self.assertEqual(content, '')
                self.assertEqual([c['function']['name'] for c in calls], ['create_goal', 'bash'])
                self.assertEqual([json.loads(c['function']['arguments']) for c in calls], [
                    {'objective': OBJECTIVE, 'max_goal_rounds': 241},
                    {'command': COMMAND, 'description': 'Inspect plan size'}])

    def test_generic_declared_field_not_tool_specific(self):
        for _, calls in self.check(packet().replace('parameter name="command"', 'command"')):
            self.assertEqual(json.loads(calls[1]['function']['arguments'])['command'], COMMAND)
        value = '  "quoted" \\path\n<&amp;> ```example```\r\n'
        for _, calls in self.check(packet(value=value)):
            self.assertEqual(json.loads(calls[0]['function']['arguments'])['objective'], value)

    def test_no_opt_in_no_schema_no_normal_stop(self):
        for validate in (validate_reply,):
            with self.assertRaises(ToolCallError): validate(packet(), TOOLS)
        with self.assertRaises(ValueError): decode(packet(), _tool_json, allow_wrapper_tolerances=True)
        for finish in ['length', 'content_filter', None]: self.rejected(packet(), finish_reason=finish)
        self.rejected(packet(), provider='qwen')
        self.rejected(packet(), tool_choice='none')
        self.rejected(packet(), tool_choice={'type': 'function', 'function': {'name': 'read'}})

    def test_never_guess_unknown_or_missing_name(self):
        for key in ['', 'objectiv', 'Objective', 'objective_extra', 'objective name=', 'calls', 'invoke', 'parameter']:
            self.rejected(packet(key=key))
        for opening in ['<｜DSML｜ objective string="true">', '<｜DSML｜ name="objective" string="true">',
                        '<｜DSML｜ objective" string=true>', '<｜DSML｜ objective" string="True">',
                        '<｜DSML｜ objective" extra="x" string="true">', '<|DSML| objective" string="true">']:
            self.rejected(packet().replace('<｜DSML｜ objective" string="true">', opening))

    def test_schema_constraints_duplicates_and_entire_batch(self):
        self.rejected(packet(value='123', flag='false'))
        self.rejected(packet().replace('>241</', '>"241"</'))
        self.rejected(packet().replace('>241</', '>NaN</'))
        self.rejected(packet().replace('parameter name="max_goal_rounds"', 'parameter name="objective"'))
        self.rejected(packet().replace('parameter name="command"', 'parameter name="unknown"'))
        self.rejected(packet().replace('parameter name="description"', 'parameter name="command"'))
        tools = copy.deepcopy(TOOLS); tools[0]['function']['parameters']['properties']['objective']['maxLength'] = 3
        self.rejected(packet(), tools)
        tools = copy.deepcopy(TOOLS); tools[0]['function']['parameters']['required'].append('missing')
        self.rejected(packet(), tools)

    def test_no_ambiguous_schema_or_nested_framing(self):
        self.rejected(packet(), TOOLS + [TOOLS[0]])
        for key, value in [('allOf', []), ('$ref', '#/x'), ('patternProperties', {'.*': {'type': 'string'}})]:
            tools = copy.deepcopy(TOOLS); tools[0]['function']['parameters'][key] = value
            self.rejected(packet(), tools)
        self.rejected(packet(value='before<｜DSML｜ objective" string="true">after'))
        for changed in [packet().replace('</｜DSML｜ calls>', ''),
                        packet().replace('</｜DSML｜ parameter>', '', 1), packet() + '\ntrailing',
                        packet().replace('</｜DSML｜ invoke>', '', 1)]:
            self.rejected(changed)

    def test_quoted_fenced_examples_remain_inert(self):
        for text in ['```text\n' + packet() + '\n```', '> ' + packet().replace('\n', '\n> ')]:
            for content, calls in self.check(text):
                self.assertIsNone(calls); self.assertEqual(content, text)
