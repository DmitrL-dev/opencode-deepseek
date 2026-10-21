"""Synthetic native DeepSeek framing; no execution, accounts or private source."""
import json
import unittest
from unittest.mock import patch

from server.openai_format import ToolCallError, parse_tool_calls
from server.tool_protocol import validate_reply
from tests.test_tools import FLAT_TOOLS

OPEN = '<｜｜DSML｜｜ calls>'
CLOSE = '</｜｜DSML｜｜ calls>'


def invoke(name, parameters):
    parts = [f'<｜｜DSML｜｜ invoke name="{name}">']
    for key, value, string in parameters:
        parts.append(f'<｜｜DSML｜｜ parameter name="{key}" string="{str(string).lower()}">'
                     + value + '</｜｜DSML｜｜ parameter>')
    return '\n'.join([*parts, '</｜｜DSML｜｜ invoke>'])


def batch(*calls):
    return '\n'.join([OPEN, *calls, CLOSE])


def observed():
    return batch(invoke('bash', [('command', 'git status --short | sed -n \'1,40p\'', True),
                                  ('description', 'Inspect owned fixture', True)]))


class DsmlTests(unittest.TestCase):
    def test_observed_native_batch_passes_common_schema_parser_unchanged(self):
        content, calls = validate_reply(observed(), FLAT_TOOLS)
        self.assertEqual(content, '')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['function']['name'], 'bash')
        self.assertEqual(json.loads(calls[0]['function']['arguments']), {
            'command': 'git status --short | sed -n \'1,40p\'',
            'description': 'Inspect owned fixture'})

    def test_default_parser_does_not_opt_into_native_markup(self):
        text = observed()
        self.assertEqual(parse_tool_calls(text, {'bash'}), (text, None))
        self.assertEqual(parse_tool_calls(text, {'bash'}, allow_dsml=True), (text, None))

    def test_general_tool_names_typed_values_and_string_bytes(self):
        values = {'literal': '  \r\n"quoted" \\path <tag> &amp; &#65; русский 🚀\n```\n',
                  'integer': 9, 'boolean': False, 'nullable': None,
                  'array': ['a', {'b': 3}], 'object': {'q': '"\\\n'}}
        schema = {'type': 'object', 'properties': {k: {} for k in values},
                  'required': list(values), 'additionalProperties': False}
        names = ('read', 'write', 'arbitrary_custom_tool', 'custom.namespace')
        tools = [{'type': 'function', 'function': {'name': name, 'parameters': schema}} for name in names]
        text = batch(*(invoke(name, [(k, v if k == 'literal' else json.dumps(v), k == 'literal')
                                     for k, v in values.items()]) for name in names))
        _, calls = validate_reply(text, tools)
        self.assertEqual([c['function']['name'] for c in calls], list(names))
        self.assertEqual([json.loads(c['function']['arguments']) for c in calls], [values] * len(names))
        self.assertEqual(len({c['id'] for c in calls}), len(names))

    def test_string_flag_never_guesses_types(self):
        tools = [{'function': {'name': 'numeric', 'parameters': {'type': 'object',
                 'properties': {'value': {'type': 'integer'}}, 'required': ['value']}}}]
        with self.assertRaises(ToolCallError):
            validate_reply(batch(invoke('numeric', [('value', '7', True)])), tools)
        _, calls = validate_reply(batch(invoke('numeric', [('value', '7', False)])), tools)
        self.assertEqual(json.loads(calls[0]['function']['arguments']), {'value': 7})

    def test_empty_invocation_requires_a_schema_that_accepts_it(self):
        text = batch(invoke('custom', []))
        _, calls = validate_reply(text, [{'function': {'name': 'custom', 'parameters': {
            'type': 'object', 'additionalProperties': False}}}])
        self.assertEqual(json.loads(calls[0]['function']['arguments']), {})
        with self.assertRaises(ToolCallError):
            validate_reply(batch(invoke('read', [])), FLAT_TOOLS)

    def test_unknown_missing_and_wrong_schema_values_reject_whole_batch(self):
        for bad in (invoke('unknown', []), invoke('read', []),
                    invoke('read', [('file_path', '42', False)]),
                    invoke('bash', [('command', 'pwd', True)])):
            with self.subTest(bad=bad), self.assertRaises(ToolCallError):
                validate_reply(batch(invoke('read', [('file_path', 'owned.txt', True)]), bad), FLAT_TOOLS)

    def test_duplicate_parameters_attributes_and_nested_or_incomplete_tags_rejected(self):
        good = observed()
        bad = [batch(invoke('bash', [('command', 'pwd', True), ('command', 'ls', True),
                                    ('description', 'x', True)])),
               good.replace('name="bash"', 'name="bash" name="read"'),
               good.replace('string="true"', 'string="true" extra="x"', 1),
               good.replace('string="true"', 'string="TRUE"', 1),
               good.replace('</｜｜DSML｜｜ parameter>', '</｜｜DSML｜｜ invoke>', 1),
               good.replace('Inspect owned fixture', '<｜｜DSML｜｜ calls>'),
               good[:-len(CLOSE)], good.replace('</｜｜DSML｜｜ invoke>', ''),
               batch(), good + '\nTool (bash): success', good + '\n' + good]
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ToolCallError):
                validate_reply(text, FLAT_TOOLS)

    def test_bad_typed_json_or_nonfinite_numbers_never_recovered(self):
        tools = [{'function': {'name': 'custom', 'parameters': {'type': 'object'}}}]
        for raw in ('{', '{"key":1,"key":2}', 'NaN', 'Infinity', '{"x":NaN}',
                    'false trailing', '"unterminated'):
            with self.subTest(raw=raw), self.assertRaises(ToolCallError):
                validate_reply(batch(invoke('custom', [('value', raw, False)])), tools)

    def test_quoted_nested_examples_and_preambles_never_become_calls(self):
        text = observed()
        for quoted in ('\n'.join('> ' + line for line in text.splitlines()),
                       '```xml\n' + text + '\n```', '~~~\n' + text + '\n~~~',
                       'Example:\n' + text, '<blockquote>' + text + '</blockquote>'):
            try:
                _, calls = validate_reply(quoted, FLAT_TOOLS)
            except ToolCallError:
                continue
            self.assertIsNone(calls)

    def test_only_deepseek_normal_stop_and_requested_tool_policy(self):
        for finish in ('length', 'error', 'content_filter'):
            with self.assertRaises(ToolCallError):
                validate_reply(observed(), FLAT_TOOLS, finish_reason=finish)
        with self.assertRaises(ToolCallError):
            validate_reply(observed(), FLAT_TOOLS, provider='qwen')
        with self.assertRaises(ToolCallError):
            validate_reply(observed(), FLAT_TOOLS, tool_choice='none')
        with self.assertRaises(ToolCallError):
            validate_reply(observed(), FLAT_TOOLS,
                           tool_choice={'type': 'function', 'function': {'name': 'read'}})

    def test_remote_schema_and_xml_entities_never_fetch_anything(self):
        tools = [{'function': {'name': 'bash', 'parameters': {'$ref': 'https://offline.invalid/schema'}}}]
        with patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')):
            with self.assertRaises(ToolCallError):
                validate_reply(observed(), tools)
            with self.assertRaises(ToolCallError):
                validate_reply('<!DOCTYPE x SYSTEM "https://offline.invalid/x">\n' + observed(), FLAT_TOOLS)

    def test_errors_never_include_private_argument_values(self):
        text = batch(invoke('bash', [('command', 'SECRET_PRIVATE_COMMAND', False)]))
        with self.assertRaises(ToolCallError) as error:
            validate_reply(text, FLAT_TOOLS)
        self.assertNotIn('SECRET_PRIVATE_COMMAND', str(error.exception))

    def test_api_validation_of_saved_shape_never_requests_format_correction(self):
        from server import api
        from server.schemas import ChatCompletionRequest
        from chat_protocol import Reply
        req = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=FLAT_TOOLS)
        with patch.object(api, '_chat_with_retry', side_effect=AssertionError('No generation')):
            reply, content, calls = api._validate_tool_reply(Reply(observed(), 'fixture:2'), req)
        self.assertEqual(reply.conversation_id, 'fixture:2')
        self.assertEqual(content, '')
        self.assertEqual(len(calls), 1)

    def test_rework_retains_main_native_contract_without_generation(self):
        from server import api_new
        from server.schemas import ChatCompletionRequest
        from server.tool_protocol_new import validate_reply as validate_new
        from chat_protocol import Reply
        req = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=FLAT_TOOLS)
        with patch('server.api._chat_with_retry', side_effect=AssertionError('No generation')):
            reply, content, calls = api_new.validate_tool_reply(Reply(observed(), 'fixture:2'), req)
        self.assertEqual((reply.conversation_id, content, len(calls)), ('fixture:2', '', 1))
        self.assertEqual(reply.parts[0]['call'], calls[0])
        for text, kwargs in ((observed(), {'finish_reason': 'length'}),
                             (observed(), {'provider': 'qwen'}),
                             (observed(), {'tool_choice': 'none'}),
                             (observed() + '\nTool (bash): success', {}),
                             (observed().replace('name="bash"', 'name="unknown"'), {})):
            with self.subTest(kwargs=kwargs), self.assertRaises(ToolCallError):
                validate_new(text, FLAT_TOOLS, **kwargs)

    def test_rework_demote_does_not_rewrite_native_string_arguments(self):
        from server.tool_protocol_new import demote_mistagged_tool_reply, validate_reply as validate_new
        example = 'Notes:\n```tool_calls\n[{"name":"fake","arguments":{}}]\n```\nEnd.'
        text = batch(invoke('write', [('content', example, True)]))
        tools = [{'function': {'name': 'write', 'parameters': {'type': 'object',
                    'properties': {'content': {'type': 'string'}}, 'required': ['content']}}}]
        self.assertIsNone(demote_mistagged_tool_reply(text, tools))
        _, calls = validate_new(text, tools)
        self.assertEqual(json.loads(calls[0]['function']['arguments']), {'content': example})
