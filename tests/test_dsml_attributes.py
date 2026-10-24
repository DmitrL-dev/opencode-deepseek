"""Attribute-only DSML records use explicit schema types, never heuristics."""
import copy
import json
import unittest
from unittest.mock import patch

from chat_protocol import Reply
from server import api, api_new
from server.dsml import decode
from server.openai_format import ToolCallError, _tool_json
from server.schemas import ChatCompletionRequest
from server.tool_protocol import validate_reply
from server.tool_protocol_new import validate_reply as validate_new

TOOLS = [{'function': {'name': 'read', 'parameters': {
    'type': 'object', 'properties': {'file_path': {'type': 'string'},
    'offset': {'type': 'number'}, 'limit': {'type': 'number'}},
    'required': ['file_path']}}}]


def record(attrs='file_path="/owned/проект/PLAN.md" offset="192" limit="45"', name='read'):
    return f'<｜DSML｜ invoke name="{name}">\n<｜DSML｜ {attrs}>\n\n</｜DSML｜ invoke>'


def batch(*records):
    return '<｜DSML｜ calls>\n' + '\n'.join(records or [record()]) + '\n</｜DSML｜ calls>'


class AttributeTests(unittest.TestCase):
    def parse(self, text, tools=TOOLS, **options):
        for validator in (validate_reply, validate_new):
            with self.subTest(validator=validator.__module__):
                yield validator(text, tools, allow_dsml_wrapper_tolerances=True, **options)

    def rejected(self, text, tools=TOOLS, **options):
        for validator in (validate_reply, validate_new):
            with self.subTest(validator=validator.__module__), self.assertRaises(ToolCallError):
                validator(text, tools, allow_dsml_wrapper_tolerances=True, **options)

    def test_saved_shape_and_supported_markers(self):
        for text in (batch(), batch().replace('｜DSML｜', '｜｜DSML｜｜')):
            for content, calls in self.parse(text):
                self.assertEqual(content, '')
                self.assertEqual(len(calls), 1)
                self.assertEqual(json.loads(calls[0]['function']['arguments']), {
                    'file_path': '/owned/проект/PLAN.md', 'offset': 192, 'limit': 45})

    def test_explicit_types_generic_tool_and_literal_strings(self):
        tools = copy.deepcopy(TOOLS); fn = tools[0]['function']; fn['name'] = 'custom'
        fn['parameters']['properties'] = {k: {'type': t} for k, t in
            [('file_path', 'string'), ('offset', 'string'), ('integer', 'integer'),
             ('number', 'number'), ('flag', 'boolean'), ('nothing', 'null')]}
        attrs = 'file_path=" C:\\owned\\x &amp; &quot; русский " offset="192" integer="-2" number="1.5e2" flag="false" nothing="null"'
        for _, calls in self.parse(batch(record(attrs, 'custom')), tools):
            self.assertEqual(json.loads(calls[0]['function']['arguments']), {
                'file_path': ' C:\\owned\\x &amp; &quot; русский ', 'offset': '192',
                'integer': -2, 'number': 150.0, 'flag': False, 'nothing': None})

    def test_no_types_from_values_defaults_enum_union_or_reference(self):
        for schema in ({}, {'enum': [192]}, {'default': 192}, {'type': ['number', 'string']},
                       {'type': 'number', 'anyOf': []}, {'type': 'number', '$ref': '#/number'},
                       {'type': 'array'}, {'type': 'object'}, True):
            tools = copy.deepcopy(TOOLS); tools[0]['function']['parameters']['properties']['offset'] = schema
            self.rejected(batch(), tools)
        for extra in ({'allOf': []}, {'$ref': '#/read'}, {'patternProperties': {'.*': {}}}):
            tools = copy.deepcopy(TOOLS); tools[0]['function']['parameters'].update(extra)
            self.rejected(batch(), tools)
        self.rejected(batch(), TOOLS + TOOLS)
        self.rejected(batch(), [])

    def test_nonstring_scalars_are_not_coerced(self):
        for value in ('01', '+2', 'NaN', 'Infinity', '1e999', 'true', 'null', '{}', '[]', '192x', ''):
            self.rejected(batch(record(f'file_path="x" offset="{value}"')))
        for kind, value in [('integer', '1.0'), ('integer', 'true'), ('boolean', '1'),
                            ('boolean', 'True'), ('null', 'false')]:
            tools = copy.deepcopy(TOOLS); tools[0]['function']['parameters']['properties']['offset'] = {'type': kind}
            self.rejected(batch(record(f'file_path="x" offset="{value}"')), tools)

    def test_whole_batch_still_obeys_required_and_constraints(self):
        self.rejected(batch(record('offset="192"')))
        self.rejected(batch(record('file_path="x" file_path="y"')))
        self.rejected(batch(record('file_path="x" surprise="y"')))
        for field, constraint in [('file_path', {'minLength': 100}), ('file_path', {'pattern': '^NO$'}),
                                  ('offset', {'maximum': 191}), ('limit', {'enum': [1]})]:
            tools = copy.deepcopy(TOOLS); tools[0]['function']['parameters']['properties'][field].update(constraint)
            self.rejected(batch(), tools)
        self.rejected(batch(record(), record('file_path="x" offset="wrong"')))

    def test_never_mix_records_or_repair_framing(self):
        base = batch()
        for text in [base.replace('>\n\n</', '/>\n\n</'),
                     base.replace('>\n\n</', '>ignored</'),
                     base.replace('</｜DSML｜ invoke>', '</｜DSML｜>'),
                     base.replace('</｜DSML｜ invoke>', ''),
                     base.replace('</｜DSML｜ calls>', ''), base + '\ntrailing',
                     base.replace('offset="192"', 'offset=192'),
                     base.replace(' limit=', 'limit='),
                     base.replace('file_path="', 'file_path=\''),
                     base.replace('PLAN.md"', 'PLAN.md\\"'),
                     base.replace('PLAN.md', '<｜DSML｜ invoke>'),
                     base.replace('>\n\n</', '>\n<｜DSML｜ file_path="second">\n</'),
                     base.replace('>\n\n</', '>\n<｜DSML｜ parameter name="limit" string="false">1</｜DSML｜ parameter>\n</'),
                     base.replace('<｜DSML｜ file_path=', '<｜DSML｜ parameter name="limit" string="false">1</｜DSML｜ parameter>\n<｜DSML｜ file_path=')]:
            self.rejected(text)

    def test_provider_finish_opt_in_choice_and_quoted_examples(self):
        for validator in (validate_reply, validate_new):
            with self.assertRaises(ToolCallError): validator(batch(), TOOLS)
        with self.assertRaises(ValueError): decode(batch(), _tool_json, allow_wrapper_tolerances=True)
        for options in ({'provider': 'qwen'}, {'finish_reason': 'length'}, {'finish_reason': None},
                        {'tool_choice': 'none'}, {'tool_choice': {'type': 'function', 'function': {'name': 'write'}}}):
            self.rejected(batch(), **options)
        for text in ['```xml\n' + batch() + '\n```', '> ' + batch().replace('\n', '\n> ')]:
            for content, calls in self.parse(text):
                self.assertIsNone(calls); self.assertEqual(content, text)

    def test_api_contracts_no_partial_delivery_or_regeneration(self):
        req = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=TOOLS)
        for validator, module in [(api._validate_tool_reply, api), (api_new.validate_tool_reply, api_new)]:
            with patch('server.config.DEEPSEEK_TOOL_PROTOCOL', 'dsml'), \
                    patch.object(module, 'save_tool_failure') as saved, \
                    patch.object(api, '_chat_with_retry', side_effect=AssertionError('No generation')):
                _, _, calls = validator(Reply(batch(), 'fixture:34', 'stop'), req, allow_continuation=False)
                self.assertEqual(len(calls), 1); saved.assert_not_called()
                with self.assertRaises(ToolCallError):
                    validator(Reply(batch(record(), record('offset="1"')), 'fixture:34', 'stop'), req, allow_continuation=False)
                saved.assert_called_once()
