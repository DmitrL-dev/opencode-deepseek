"""Offline wire-format regressions; no accounts, tools or private transcripts."""
import json
import unittest
from unittest.mock import patch

from chat_protocol import Reply
from server import api, api_new, config
from server.openai_format import ToolCallError, messages_to_prompt
from server.schemas import ChatCompletionRequest, ChatMessage
from tests.test_dsml import batch, invoke
from tests.test_tools import FLAT_TOOLS


class ToolDialectTests(unittest.TestCase):
    def prompt(self, *, protocol='dsml', provider='deepseek', mode='full', choice=None,
               continuation=False, messages=None):
        with patch.object(config, 'DEEPSEEK_TOOL_PROTOCOL', protocol, create=True):
            return messages_to_prompt(messages or [ChatMessage(role='user', content='Inspect project')],
                FLAT_TOOLS, choice, provider=provider, tool_instructions=mode,
                tools_continuation=continuation)

    def test_native_full_prompt_has_one_dialect_and_keeps_safety_contracts(self):
        prompt = self.prompt()
        for marker in ('<｜DSML｜ calls>', '</｜DSML｜ calls>',
                       'string="true"', 'string="false"', 'Required top-level argument fields',
                       'Verification reuse contract:', 'Shell search contract:',
                       'Only tool results establish what actually happened',
                       'Wait for the mutation tool result before running its verifier'):
            self.assertIn(marker, prompt)
        for mixed_rule in ('```tool_calls', 'Never use XML/DSML', 'closing tool_calls fence',
                           'all input fields inside `arguments`', '｜｜DSML｜｜'):
            self.assertNotIn(mixed_rule, prompt)
        self.assertIn('Never mix', prompt)
        self.assertIn('Never append a `Tool (...):` line', prompt)

    def test_native_followup_and_forced_choice_never_switch_back_to_json(self):
        for mode in ('full', 'medium', 'light'):
            for choice in (None, 'required', {'type': 'function', 'function': {'name': 'read'}}):
                with self.subTest(mode=mode, choice=choice):
                    prompt = self.prompt(mode=mode, choice=choice, continuation=True)
                    self.assertIn('DSML', prompt)
                    self.assertIn('<｜DSML｜ calls>', prompt)
                    self.assertNotIn('｜｜DSML｜｜', prompt)
                    self.assertNotIn('```tool_calls', prompt)
                    self.assertNotIn('ONLY the fenced', prompt)
                    if choice == 'required':
                        self.assertIn('MUST call one of the tools', prompt)
                    elif isinstance(choice, dict):
                        self.assertIn('MUST call the tool `read`', prompt)
        self.assertIn('Do NOT call any tools', self.prompt(choice='none'))

    def test_native_history_keeps_ids_and_values_as_inert_data(self):
        arguments = {'command': 'printf "two\\nlines"', 'description': 'Inspect fixture'}
        history = [ChatMessage(role='assistant', tool_calls=[{'id': 'call_fixture',
            'type': 'function', 'function': {'name': 'bash', 'arguments': json.dumps(arguments)}}]),
            ChatMessage(role='tool', name='bash', tool_call_id='call_fixture', content='two\nlines')]
        prompt = self.prompt(messages=history)
        self.assertNotIn('```tool_calls', prompt)
        self.assertIn('Historical tool calls (JSON data):', prompt)
        self.assertIn('"id": "call_fixture"', prompt)
        self.assertIn(json.dumps(arguments, ensure_ascii=False), prompt)
        self.assertIn('Tool (bash, call_id=call_fixture): two\nlines', prompt)

    def test_other_providers_and_default_json_are_unchanged(self):
        self.assertIn('```tool_calls', self.prompt(protocol='json'))
        self.assertIn('Never use XML/DSML', self.prompt(protocol='json'))
        for provider in ('qwen', 'kimi', 'grok', 'gemini'):
            with self.subTest(provider=provider):
                prompt = self.prompt(provider=provider)
                self.assertIn('```tool_calls', prompt)
                self.assertNotIn('<｜DSML｜ calls>', prompt)
                self.assertNotIn('<｜｜DSML｜｜ calls>', prompt)

    def test_config_defaults_to_json_and_rejects_unknown_dialects(self):
        with patch.dict('os.environ', DEEPSEEK_TOOL_PROTOCOL=''):
            self.assertEqual(config._transport('DEEPSEEK_TOOL_PROTOCOL', 'json', ('json', 'dsml')), 'json')
        with patch.dict('os.environ', DEEPSEEK_TOOL_PROTOCOL='Dsml'):
            self.assertEqual(config._transport('DEEPSEEK_TOOL_PROTOCOL', 'json', ('json', 'dsml')), 'dsml')
        with patch.dict('os.environ', DEEPSEEK_TOOL_PROTOCOL='mixed'), self.assertRaises(ValueError):
            config._transport('DEEPSEEK_TOOL_PROTOCOL', 'json', ('json', 'dsml'))

    def test_native_goal_and_inspection_batch_is_complete_and_typed(self):
        goal = {'function': {'name': 'create_goal', 'parameters': {
            'type': 'object', 'properties': {'objective': {'type': 'string'},
                'max_goal_rounds': {'type': 'integer', 'minimum': 1}},
            'required': ['objective'], 'additionalProperties': False}}}
        objective = 'Исследовать проект; завершить только после проверки результата.'
        text = batch(invoke('create_goal', [('objective', objective, True),
                                           ('max_goal_rounds', '8', False)]),
                     invoke('bash', [('command', 'git status --short', True),
                                     ('description', 'Inspect fixture', True)]))
        req = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=[goal, *FLAT_TOOLS])
        for validator in (api._validate_tool_reply, api_new.validate_tool_reply):
            with self.subTest(validator=validator.__module__):
                _, _, calls = validator(Reply(text, 'fixture:2'), req)
                self.assertEqual([c['function']['name'] for c in calls], ['create_goal', 'bash'])
                self.assertEqual(json.loads(calls[0]['function']['arguments']),
                                 {'objective': objective, 'max_goal_rounds': 8})

    def test_native_values_roundtrip_and_same_schema_policy_rejects_missing_targets(self):
        command = 'printf "quoted\\ntext"\n# Literal <｜｜DSML｜｜ calls> &amp;'
        # JSON string encoding is available when a value contains protocol markers.
        encoded = json.dumps(command).replace('<', '\\u003c').replace('>', '\\u003e')
        text = batch(invoke('bash', [('command', encoded, False),
                                      ('description', 'Inspect fixture', True)]))
        req = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=FLAT_TOOLS)
        for validator in (api._validate_tool_reply, api_new.validate_tool_reply):
            with self.subTest(validator=validator.__module__), \
                    patch.object(api, '_chat_with_retry', side_effect=AssertionError('No generation')), \
                    patch('server.api.save_tool_failure'), patch('server.api_new.save_tool_failure'):
                _, _, calls = validator(Reply(text, 'fixture:2'), req)
                self.assertEqual(json.loads(calls[0]['function']['arguments'])['command'], command)
                with self.assertRaises(ToolCallError):
                    validator(Reply(batch(invoke('read', [])), 'fixture:2'), req)

    def test_mixed_json_and_native_tail_cannot_drop_or_partially_execute_a_call(self):
        # A sanitized shape of the real first long-goal failure. The second
        # invocation has no opening native batch, so manufacturing one is unsafe.
        text = ('Inspecting the project.\n```tool_calls\n'
                '[{"name":"read","file_path":"owned.txt"}]\n'
                '</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>\n'
                + invoke('bash', [('command', 'pwd', True), ('description', 'Inspect', True)])
                + '\n</｜｜DSML｜｜ calls>')
        req = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=FLAT_TOOLS)
        for entry in (api._tool_chat_with_retry, api_new.tool_chat_with_retry):
            with self.subTest(entry=entry.__module__), \
                    patch.object(api, '_chat_with_retry', return_value=Reply(text, 'fixture:2')) as upstream, \
                    patch.object(api, '_FORMAT_REPAIR', False), \
                    patch('server.api.save_tool_failure'), patch('server.api_new.save_tool_failure'):
                with self.assertRaises(ToolCallError):
                    entry('fixture prompt', req, 'expert')
                self.assertEqual(upstream.call_count, 1)
