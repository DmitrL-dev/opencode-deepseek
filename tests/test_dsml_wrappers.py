"""Native envelope aliases never infer or rewrite tool inputs."""
import json
import unittest
from unittest.mock import patch

from chat_protocol import Reply
from server import api
from server.openai_format import ToolCallError
from server.schemas import ChatCompletionRequest
from server.tool_protocol import validate_reply
from tests.test_dsml import OPEN, CLOSE, batch, invoke

GENERIC_END = '</｜｜DSML｜｜>'
TOOLS = [{'function': {'name': 'custom.execute', 'parameters': {
    'type': 'object', 'properties': {
        'command': {'type': 'string'},
        'sandbox_permissions': {'enum': ['workspace-write', 'danger-full-access']},
        'justification': {'type': 'string'}},
    'required': ['command'], 'additionalProperties': False}}}]
VALUES = {'command': 'pwd; printf "quoted\\ntext"\n# owned fixture',
          'sandbox_permissions': 'danger-full-access', 'justification': 'Owned fixture'}


def action(values=VALUES):
    return invoke('custom.execute', [(key, value, True) for key, value in values.items()])


def variant(*calls):
    return OPEN + '\n' + batch(*calls).replace('</｜｜DSML｜｜ invoke>', GENERIC_END)


class DsmlWrapperTests(unittest.TestCase):
    def check(self, raw, **kwargs):
        return validate_reply(raw, TOOLS, allow_dsml_wrapper_tolerances=True, **kwargs)

    def test_one_adjacent_repeated_header_and_untyped_invocation_close_preserve_inputs(self):
        raw = variant(action())
        for validator in (validate_reply,):
            with self.subTest(validator=validator.__module__):
                with self.assertRaises(ToolCallError):
                    validator(raw, TOOLS)
                _, calls = validator(raw, TOOLS, allow_dsml_wrapper_tolerances=True)
                self.assertEqual(json.loads(calls[0]['function']['arguments']), VALUES)
                self.assertEqual(calls[0]['function']['name'], 'custom.execute')

    def test_plain_preamble_still_requires_its_separate_explicit_opt_in(self):
        raw = 'Inspecting owned files.\n\n' + variant(action())
        with self.assertRaises(ToolCallError):
            self.check(raw)
        _, calls = self.check(raw, allow_dsml_preamble=True)
        self.assertEqual(json.loads(calls[0]['function']['arguments']), VALUES)

    def test_aliases_apply_generally_and_preserve_call_order(self):
        raw = variant(action({'command': 'first'}), action({'command': 'second'}))
        _, calls = self.check(raw)
        self.assertEqual([json.loads(c['function']['arguments']) for c in calls],
                         [{'command': 'first'}, {'command': 'second'}])
        for raw in (OPEN + '\n' + batch(action()),
                    batch(action()).replace('</｜｜DSML｜｜ invoke>', GENERIC_END)):
            with self.subTest(raw=raw):
                _, calls = self.check(raw)
                self.assertEqual(json.loads(calls[0]['function']['arguments']), VALUES)

    def test_incomplete_ambiguous_quoted_and_extra_packets_never_escape(self):
        raw = variant(action())
        invalid = [OPEN+'\n'+raw,
                   raw.replace(GENERIC_END, ''),
                   raw.replace('</｜｜DSML｜｜ parameter>', GENERIC_END, 1),
                   raw.replace(CLOSE, GENERIC_END),
                   raw.replace(GENERIC_END, '</｜｜DSML｜｜ unknown>'),
                   raw+'\n'+batch(action()), raw+'\nTool: fabricated result',
                   batch(action(), OPEN+'\n'+action()),
                   'Example:\n'+raw, '```xml\n'+raw+'\n```',
                   '~~~\n'+raw+'\n~~~', '````\n'+raw+'\n````',
                   '\n'.join('> '+line for line in raw.splitlines()),
                   '\n'.join('    '+line for line in raw.splitlines()),
                   '<blockquote>\n'+raw+'\n</blockquote>',
                   raw.replace('owned fixture', GENERIC_END),
                   raw.replace('string="true"', 'string="false"', 1)]
        for validator in (validate_reply,):
            for value in invalid:
                with self.subTest(validator=validator.__module__, value=value):
                    try:
                        _, calls = validator(value, TOOLS, allow_dsml_preamble=True,
                                             allow_dsml_wrapper_tolerances=True)
                    except ToolCallError:
                        continue
                    self.assertIsNone(calls)

    def test_finish_provider_choice_and_every_schema_are_still_required(self):
        raw = variant(action())
        for kwargs in ({'finish_reason': 'length'}, {'provider': 'qwen'},
                       {'tool_choice': 'none'}, {'tool_choice': {
                           'type': 'function', 'function': {'name': 'other'}}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ToolCallError):
                self.check(raw, **kwargs)
        for bad in (action({}), action({'command': 'x', 'sandbox_permissions': 'invented'}),
                    action({'command': 'x', 'unknown': 'must not be dropped'}),
                    invoke('unknown', [])):
            with self.subTest(bad=bad), self.assertRaises(ToolCallError):
                self.check(variant(action(), bad))

    def test_selected_native_api_validates_saved_shape_without_a_new_generation(self):
        request = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=TOOLS)
        for validator in (api._validate_tool_reply,):
            with self.subTest(validator=validator.__module__), \
                    patch('server.config.DEEPSEEK_TOOL_PROTOCOL', 'dsml'), \
                    patch.object(api, '_chat_with_retry', side_effect=AssertionError('No generation')):
                _, _, calls = validator(Reply(variant(action()), 'fixture:4'), request)
                self.assertEqual(json.loads(calls[0]['function']['arguments']), VALUES)
