import json
import unittest
from unittest.mock import patch

from server.openai_format import parse_tool_calls, ToolCallError
from tests.test_tools import FLAT_TOOLS, block


SUFFIX = '</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'
SAVED_REPLY = ('I\'ll start by orienting myself in the workspace to see what\'s there '
               'and what "продолжи" refers to.\n\n```tool_calls\n'
               '[{"name": "bash", "command": "pwd && ls -la", '
               '"description": "Show working directory and list files"}]' + SUFFIX)


def parse(text, tools=FLAT_TOOLS):
    names = {t.get('function', t)['name'] for t in tools}
    return parse_tool_calls(text, names, allow_unclosed_fence=True,
                            allow_closing_artifacts=True, flat_argument_tools=tools,
                            allow_tool_preamble=True)


class SchemaProtocolTests(unittest.TestCase):
    def test_saved_reply_values_preserved(self):
        _, calls = parse(SAVED_REPLY)
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0]['function']['arguments']), {
            'command': 'pwd && ls -la', 'description': 'Show working directory and list files'})
        self.assertIsNone(parse_tool_calls(SAVED_REPLY, {'bash'})[1])
        self.assertIsNone(parse_tool_calls(SAVED_REPLY, {'bash'}, allow_tool_preamble=True)[1])

    def test_general_batch_and_empty_arguments(self):
        tools = FLAT_TOOLS + [{'name': 'arbitrary_reader', 'parameters': {
            'type': 'object', 'properties': {}, 'additionalProperties': False}}]
        items = [{'name': 'arbitrary_reader'}, {'name': 'read', 'file_path': '/fixture'},
                 {'name': 'bash', 'arguments': {'command': 'printf "hello"', 'description': 'test'}}]
        for text in (block(items), 'Next steps.\n' + block(items),
                     'Next steps.\n```tool_calls\n' + json.dumps(items) + SUFFIX):
            _, calls = parse(text, tools)
            self.assertEqual([c['function']['name'] for c in calls], ['arbitrary_reader', 'read', 'bash'])
            self.assertEqual(json.loads(calls[0]['function']['arguments']), {})

    def test_invalid_batch_never_returns_partial_calls(self):
        valid = {'name': 'read', 'arguments': {'file_path': '/fixture'}}
        for bad in ({'name': 'bash'}, {'name': 'read', 'file_path': 7},
                    {'name': 'read', 'arguments': {'file_path': False}},
                    {'name': 'read', 'file_path': '/fixture', 'unknown': 1},
                    {'name': 'read', 'arguments': {}, 'file_path': '/fixture'},
                    {'name': 'unknown', 'arguments': {}}):
            with self.subTest(bad=bad), self.assertRaises(ToolCallError):
                parse('Next steps.\n' + block([valid, bad]))

    def test_ambiguous_framing_and_suffix_are_not_executed(self):
        for text in ('```text\n' + SAVED_REPLY, '> Quoted example\n' + SAVED_REPLY,
                     '<pre>\n' + SAVED_REPLY,
                     SAVED_REPLY + '\n' + block([{'name': 'read', 'file_path': '/fixture'}]),
                     SAVED_REPLY.replace('```tool_calls', '```json')):
            self.assertIsNone(parse(text)[1])
        for text in (SAVED_REPLY + ' trailing text', SAVED_REPLY.replace('}]', '}'),
                     SAVED_REPLY.replace('"name": "bash"', '"name": "bash", "name": "read"'),
                     SAVED_REPLY.replace('</｜｜DSML｜｜ parameter>', '<｜｜DSML｜｜ parameter>')):
            with self.subTest(text=text), self.assertRaises(ToolCallError):
                parse(text)

    def test_nested_schema_constraints_and_local_refs(self):
        schema = {'type': 'object', 'properties': {'items': {'type': 'array', 'minItems': 1,
                  'items': {'$ref': '#/$defs/entry'}}}, 'required': ['items'],
                  'additionalProperties': False, '$defs': {'entry': {'type': 'object',
                  'properties': {'mode': {'enum': ['read']}, 'limit': {'type': 'integer', 'minimum': 1}},
                  'required': ['mode', 'limit'], 'additionalProperties': False}}}
        tools = [{'name': 'inspect', 'parameters': schema}]
        good = {'items': [{'mode': 'read', 'limit': 2}]}
        self.assertIsNotNone(parse(block([{'name': 'inspect', 'arguments': good}]), tools)[1])
        for args in ({}, {'items': []}, {'items': [{'mode': 'write', 'limit': 2}]},
                     {'items': [{'mode': 'read', 'limit': True}]},
                     {'items': [{'mode': 'read', 'limit': 0}]}, {**good, 'extra': 1}):
            with self.subTest(args=args), self.assertRaises(ToolCallError):
                parse(block([{'name': 'inspect', 'arguments': args}]), tools)

    def test_remote_invalid_and_duplicate_schemas_fail_closed(self):
        for schema in ({'$ref': 'https://example.invalid/schema'}, {'type': 'invalid'},
                       {'$schema': 'https://example.invalid/dialect'}, False):
            with self.subTest(schema=schema), patch('urllib.request.urlopen', side_effect=AssertionError('network')) as network:
                with self.assertRaises(ToolCallError):
                    parse(block([{'name': 'inspect', 'arguments': {}}]), [{'name': 'inspect', 'parameters': schema}])
                network.assert_not_called()
        with self.assertRaises(ToolCallError):
            parse(block([{'name': 'read', 'arguments': {'file_path': '/fixture'}}]), FLAT_TOOLS * 2)
