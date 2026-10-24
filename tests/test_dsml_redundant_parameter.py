"""Terminal repeated closers cannot change values or authorize a partial batch."""
import json
from itertools import product
import unittest
from unittest.mock import patch

from server.openai_format import ToolCallError
from server.tool_protocol import validate_reply
from server.tool_protocol_new import validate_reply as validate_new

TOOLS = [
    {'function': {'name': 'bash', 'parameters': {'type': 'object', 'properties': {
        'command': {'type': 'string'}, 'description': {'type': 'string'}},
        'required': ['command', 'description'], 'additionalProperties': False}}},
    {'function': {'name': 'read', 'parameters': {'type': 'object', 'properties': {
        'file_path': {'type': 'string'}, 'limit': {'type': 'integer'}},
        'required': ['file_path'], 'additionalProperties': False}}},
]
COMMAND = 'printf \'"quoted" \\\\path <tag> &amp; русский\\n\'\n  pwd\n'


def packet(marker='｜DSML｜'):
    return f'''<{marker} calls>
<{marker} invoke name="bash">
<{marker} parameter name="description" string="true">Inspect owned directory</{marker} parameter>
<{marker} parameter name="command" string="true">{COMMAND}</{marker} parameter>
</{marker} parameter>
</{marker} invoke>
<{marker} invoke name="read">
<{marker} parameter name="limit" string="false">12</{marker} parameter>
<{marker} parameter name="file_path" string="true">owned/PROGRESS.md</{marker} parameter>
</{marker} parameter>
</{marker} invoke>
</{marker} calls>'''


class RedundantParameterTests(unittest.TestCase):
    def test_entire_batch_preserves_strings_and_typed_values(self):
        for validator in (validate_reply, validate_new):
            for marker in ('｜DSML｜', '｜｜DSML｜｜'):
                with self.subTest(validator=validator.__module__, marker=marker):
                    _, calls = validator(packet(marker), TOOLS, allow_dsml_wrapper_tolerances=True)
                    self.assertEqual([c['function']['name'] for c in calls], ['bash', 'read'])
                    self.assertEqual([json.loads(c['function']['arguments']) for c in calls], [
                        {'command': COMMAND, 'description': 'Inspect owned directory'},
                        {'file_path': 'owned/PROGRESS.md', 'limit': 12}])

    def test_terminal_runs_preserve_the_same_batch_for_all_lengths_and_whitespace(self):
        for validator in (validate_reply, validate_new):
            for marker in ('｜DSML｜', '｜｜DSML｜｜'):
                raw = packet(marker)
                _, expected = validator(raw, TOOLS, allow_dsml_wrapper_tolerances=True)
                expected = [c['function'] for c in expected]
                end = f'</{marker} parameter>\n</{marker} invoke>'
                for count in (0, 1, 2, 3, 64):
                    for space in ('', ' ', '\n\r\t'):
                        terminal = (f'</{marker} parameter>' + space) * count + f'</{marker} invoke>'
                        with self.subTest(marker=marker, count=count, space=space):
                            _, calls = validator(raw.replace(end, terminal), TOOLS,
                                                 allow_dsml_wrapper_tolerances=True)
                            self.assertEqual([c['function'] for c in calls], expected)

    def test_only_known_named_closers_before_named_invoke_end(self):
        raw = packet()
        end = '</｜DSML｜ parameter>\n</｜DSML｜ invoke>'
        bad = [
            raw.replace(end, '</｜｜｜DSML｜｜｜ parameter>\n</｜DSML｜ invoke>', 1),
            raw.replace(end, '</｜DSML｜ parameter>\n</｜DSML｜>', 1),
            raw.replace(end, '</｜DSML｜ parameter>\n', 1),
            raw.replace('Inspect owned directory</｜DSML｜ parameter>',
                        'Inspect owned directory</｜DSML｜ parameter>\n</｜DSML｜ parameter>'),
            raw.replace('<｜DSML｜ invoke name="bash">',
                        '<｜DSML｜ invoke name="bash">\n</｜DSML｜ parameter>'),
            raw.replace(end, '</｜DSML｜ parameter>\nstray text\n</｜DSML｜ invoke>', 1),
            raw.replace(end, '</｜DSML｜ parameter>' * 12 + '\nstray text\n</｜DSML｜ invoke>', 1),
            raw.replace(end, '</｜DSML｜ parameter>' * 12 + '\n</DSML｜ parameter>\n</｜DSML｜ invoke>', 1),
            raw.replace(COMMAND, COMMAND + '<｜DSML｜ invoke name="read">'),
            raw.replace('</｜DSML｜ calls>', ''),
        ]
        for validator in (validate_reply, validate_new):
            for text in bad:
                with self.subTest(text=text, validator=validator.__module__), self.assertRaises(ToolCallError):
                    validator(text, TOOLS, allow_dsml_wrapper_tolerances=True)

    def test_mixed_alias_runs_preserve_values_for_both_decoders(self):
        aliases = ('｜DSML｜', '｜｜DSML｜｜')
        for validator in (validate_reply, validate_new):
            for first, *repeats in product(aliases, repeat=4):
                raw = packet()
                end = '</｜DSML｜ parameter>\n</｜DSML｜ parameter>\n</｜DSML｜ invoke>'
                replacement = ''.join(f'</{marker} parameter>\n\t' for marker in (first, *repeats)) + '</｜DSML｜ invoke>'
                mixed = raw.replace(end, replacement)
                with self.subTest(validator=validator.__module__, markers=(first,*repeats)):
                    _, expected = validator(raw, TOOLS, allow_dsml_wrapper_tolerances=True)
                    _, actual = validator(mixed, TOOLS, allow_dsml_wrapper_tolerances=True)
                    self.assertEqual([c['function'] for c in actual], [c['function'] for c in expected])
                    with self.assertRaises(ToolCallError):
                        validator(mixed, TOOLS)

    def test_mixed_aliases_do_not_skip_values_or_relax_schema(self):
        mixed = packet().replace('</｜DSML｜ parameter>\n</｜DSML｜ invoke>',
                                 '</｜｜DSML｜｜ parameter>\n</｜DSML｜ invoke>')
        bad = [
            mixed.replace('name="file_path"', 'name="missing"'),
            mixed.replace('string="false">12', 'string="false">"12"'),
            mixed.replace('name="read"', 'name="unknown"'),
            mixed.replace('Inspect owned directory</｜DSML｜ parameter>',
                          'Inspect owned directory</｜DSML｜ parameter>\n</｜｜DSML｜｜ parameter>'),
            mixed.replace('</｜｜DSML｜｜ parameter>\n</｜DSML｜ invoke>',
                          '</｜｜DSML｜｜ parameter>text</｜DSML｜ invoke>'),
            mixed.replace('</｜｜DSML｜｜ parameter>\n</｜DSML｜ invoke>',
                          '</｜｜DSML｜｜ parameter>\n</｜DSML｜>'),
            mixed.replace(COMMAND, COMMAND + '</｜｜DSML｜｜ unknown>'),
        ]
        for validator in (validate_reply, validate_new):
            for value in bad:
                with self.subTest(validator=validator.__module__, value=value), self.assertRaises(ToolCallError):
                    validator(value, TOOLS, allow_dsml_wrapper_tolerances=True)
            for options in ({'finish_reason':'length'}, {'tool_choice':'none'}, {'provider':'qwen'}):
                with self.subTest(options=options), self.assertRaises(ToolCallError):
                    validator(mixed, TOOLS, allow_dsml_wrapper_tolerances=True, **options)

    def test_full_schema_choice_and_finish_checks_still_apply(self):
        for validator in (validate_reply, validate_new):
            for kwargs in ({}, {'finish_reason': 'length'}, {'provider': 'qwen'},
                           {'tool_choice': 'none'}):
                options = {'allow_dsml_wrapper_tolerances': True, **kwargs} if kwargs else {}
                with self.subTest(kwargs=kwargs), self.assertRaises(ToolCallError):
                    validator(packet(), TOOLS, **options)
            for bad in (packet().replace('name="file_path"', 'name="unknown"'),
                        packet().replace('name="read"', 'name="unknown"'),
                        packet().replace('string="false">12', 'string="false">NaN'),
                        packet().replace('string="false">12', 'string="false">"12"'),
                        packet().replace('string="false">12', 'string="false">{"x":1,"x":2}')):
                with self.subTest(bad=bad), self.assertRaises(ToolCallError):
                    validator(bad, TOOLS, allow_dsml_wrapper_tolerances=True)

    def test_quotes_and_examples_stay_inert(self):
        for validator in (validate_reply, validate_new):
            for text in ('```xml\n' + packet() + '\n```', 'Example:\n' + packet(),
                         '\n'.join('> ' + s for s in packet().splitlines()),
                         '\n'.join('    ' + s for s in packet().splitlines())):
                try:
                    _, calls = validator(text, TOOLS, allow_dsml_wrapper_tolerances=True,
                                         allow_dsml_preamble=True)
                except ToolCallError:
                    continue
                self.assertIsNone(calls)

    def test_saved_shape_passes_both_api_validators_without_generation(self):
        from chat_protocol import Reply
        from server import api, api_new
        from server.schemas import ChatCompletionRequest
        request = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=TOOLS)
        for validator in (api._validate_tool_reply, api_new.validate_tool_reply):
            with patch('server.config.DEEPSEEK_TOOL_PROTOCOL', 'dsml'), \
                    patch.object(api, '_chat_with_retry', side_effect=AssertionError('No generation')):
                _, _, calls = validator(Reply(packet(), 'owned:4'), request)
                self.assertEqual(len(calls), 2)
