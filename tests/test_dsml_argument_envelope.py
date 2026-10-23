"""Explicit typed DSML input envelopes preserve values and schema boundaries."""
import copy
import json
import unittest
from unittest.mock import patch

from chat_protocol import Reply
from server import api, api_new
from server.openai_format import ToolCallError
from server.schemas import ChatCompletionRequest
from server.tool_protocol import validate_reply
from server.tool_protocol_new import validate_reply as validate_new
from tests.test_dsml import batch, invoke


def tool(name='custom', **schema):
    return {'function': {'name': name, 'parameters': {
        'type': 'object', 'properties': {'value': {'type': 'string'}},
        'required': ['value'], 'additionalProperties': False, **schema}}}


def wrapped(value, name='custom', string=False):
    return invoke(name, [('arguments', json.dumps(value, ensure_ascii=False), string)])


class DsmlArgumentEnvelopeTests(unittest.TestCase):
    def test_generic_typed_envelope_preserves_every_value(self):
        values = {'literal': '  \r\n"quoted" \\path <tag> &amp; русский 🚀\n```\n',
                  'integer': 9, 'boolean': False, 'nullable': None,
                  'array': ['a', {'b': 3}], 'object': {'q': '"\\\n'}}
        names = ('bash', 'read', 'write', 'arbitrary_custom_tool', 'custom.namespace')
        tools = [tool(name, properties={k: {} for k in values}, required=list(values))
                 for name in names]
        raw = batch(*(wrapped(values, name) for name in names))
        before = copy.deepcopy(tools)
        for validate in (validate_reply, validate_new):
            with self.subTest(api=validate.__module__):
                _, calls = validate(raw, tools)
                self.assertEqual([c['function']['name'] for c in calls], list(names))
                self.assertEqual([json.loads(c['function']['arguments']) for c in calls],
                                 [values] * len(names))
                self.assertEqual(tools, before)

    def test_declared_arguments_field_is_never_unwrapped(self):
        schema = tool(properties={'arguments': {'type': 'object'}}, required=['arguments'])
        value = {'value': 'literal field data'}
        for validate in (validate_reply, validate_new):
            _, calls = validate(batch(wrapped(value)), [schema])
            self.assertEqual(json.loads(calls[0]['function']['arguments']), {'arguments': value})

    def test_zero_required_fields_leave_permissive_data_untouched(self):
        schema = tool(required=[], additionalProperties=True)
        value = {'value': 'literal data, not an unambiguous envelope'}
        for validate in (validate_reply, validate_new):
            _, calls = validate(batch(wrapped(value)), [schema])
            self.assertEqual(json.loads(calls[0]['function']['arguments']), {'arguments': value})

    def test_json_call_arguments_do_not_use_dsml_envelope_decoding(self):
        raw = '```tool_calls\n' + json.dumps([{
            'name': 'custom', 'arguments': {'arguments': {'value': 'x'}}}]) + '\n```'
        for validate in (validate_reply, validate_new):
            with self.subTest(api=validate.__module__), self.assertRaises(ToolCallError):
                validate(raw, [tool()])

    def test_no_guessing_merging_string_decoding_or_filtering(self):
        tools = [tool(additionalProperties=True)]
        bad = [wrapped({'value': 'x'}, string=True), wrapped({'value': 42}),
               wrapped({}), wrapped({'value': 'x', 'surprise': True}),
               wrapped({'arguments': {'value': 'x'}}), wrapped(['value', 'x']),
               invoke('custom', [('arguments', '{"value":"x"}', False),
                                 ('value', 'different', True)]),
               invoke('custom', [('arguments', '{"value":"x","value":"y"}', False)]),
               invoke('custom', [('arguments', '{"value":NaN}', False)])]
        for validate in (validate_reply, validate_new):
            for raw in bad:
                with self.subTest(api=validate.__module__, raw=raw), self.assertRaises(ToolCallError):
                    validate(batch(wrapped({'value': 'good'}), raw), tools)

    def test_composed_ambiguous_and_unknown_schemas_do_not_enable_unwrapping(self):
        base = tool()
        cases = [[tool(required=[])], [tool(required=['missing'])],
                 [tool(allOf=[{'required': ['value']}])],
                 [tool(patternProperties={'^arg': {'type': 'object'}})],
                 [tool(unevaluatedProperties=False)],
                 [tool(**{'$ref': 'https://invalid.example/schema'})],
                 [tool(**{'x-unknown-applicator': {}})], [base, copy.deepcopy(base)], []]
        for validate in (validate_reply, validate_new):
            for tools in cases:
                with self.subTest(api=validate.__module__, tools=tools), self.assertRaises(ToolCallError):
                    validate(batch(wrapped({'value': 'x'})), tools)

    def test_provider_finish_choice_and_quotes_still_gate_the_whole_batch(self):
        raw = batch(wrapped({'value': 'x'}))
        for validate in (validate_reply, validate_new):
            for options in ({'provider': 'qwen'}, {'finish_reason': 'length'},
                            {'tool_choice': 'none'}, {'tool_choice': {
                                'type': 'function', 'function': {'name': 'other'}}}):
                with self.subTest(api=validate.__module__, options=options), self.assertRaises(ToolCallError):
                    validate(raw, [tool()], **options)
            for quoted in ('```xml\n'+raw+'\n```', '> '+raw.replace('\n', '\n> ')):
                try:
                    _, calls = validate(quoted, [tool()])
                except ToolCallError:
                    continue
                self.assertIsNone(calls)

    def test_invalid_later_call_never_delivers_valid_envelope_or_requests_repair(self):
        raw = batch(wrapped({'value': 'valid'}), invoke('custom', [('value', '42', False)]))
        request = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=[tool()])
        for validate, module in ((api._validate_tool_reply, api),
                                 (api_new.validate_tool_reply, api_new)):
            with self.subTest(api=module.__name__), \
                    patch.object(module, 'save_tool_failure') as saved, \
                    patch.object(api, '_chat_with_retry', side_effect=AssertionError('No generation')):
                with self.assertRaises(ToolCallError):
                    validate(Reply(raw, 'fixture:2', 'stop'), request, allow_continuation=False)
                saved.assert_called_once()
