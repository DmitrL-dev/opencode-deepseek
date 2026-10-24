"""Offline regression for a continuation that mixed old DSML and JSON framing."""
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from server import config
from server.conversation_cache import ConversationCache, ConversationStateError, digest
from server.openai_format import messages_to_prompt
from server.schemas import ChatCompletionRequest, ChatMessage


TOOLS = [{'type': 'function', 'function': {'name': 'bash', 'parameters': {
    'type': 'object', 'properties': {'command': {'type': 'string'},
                                     'description': {'type': 'string'}},
    'required': ['command', 'description'], 'additionalProperties': False}}}]
ARGUMENTS = {'command': 'printf "quoted\\ntext"\n# exact content',
             'description': 'Inspect fixture'}
CALL = {'id': 'call_saved', 'type': 'function', 'function': {
    'name': 'bash', 'arguments': json.dumps(ARGUMENTS)}}


class PromptContractTests(unittest.TestCase):
    def test_exact_resume_keeps_identity_and_claim_but_not_an_executable_history_fence(self):
        # Old provider context can still contain DSML after a setting switch.
        # Re-rendering the current tail must not teach another action example.
        request = ChatCompletionRequest(model='deepseek-expert', tools=TOOLS, messages=[
            ChatMessage(role='user', content='Continue the existing goal; never repeat work.'),
            ChatMessage(role='assistant', tool_calls=[CALL]),
            ChatMessage(role='tool', tool_call_id='call_saved', content='observed result'),
        ])
        original = request.model_dump()
        original_digest = digest(request, request.messages)
        with tempfile.TemporaryDirectory() as directory:
            cache = ConversationCache(Path(directory) / 'history.sqlite3')
            key = cache.key(request, request.messages[:-1])
            with cache.connection() as db:
                db.execute('INSERT INTO links(key,cid,used,steps) VALUES(?,?,0,4)',
                           (key, 'fixture-existing:10'))
            prepared, steps = cache.prepare(request)
            with patch.object(config, 'DEEPSEEK_TOOL_PROTOCOL', 'json'):
                prompt = messages_to_prompt(prepared.messages, prepared.tools,
                    provider='deepseek', tools_continuation=True, tool_instructions='full')
            self.assertEqual(prepared.conversation_id, 'fixture-existing:10')
            self.assertEqual(steps, 4)
            self.assertEqual(request.model_dump(), original)
            self.assertEqual(digest(request, request.messages), original_digest)
            with self.assertRaises(ConversationStateError):
                cache.prepare(request)
            with cache.connection() as db:
                self.assertEqual(db.execute('SELECT cid,used,steps FROM links').fetchall(),
                                 [('fixture-existing:10', 1, 4)])
        self.assertEqual(prompt.count('```tool_calls\n'), 1)
        self.assertIn('Historical tool calls (JSON data):\n', prompt)
        history = json.loads(prompt.split('Historical tool calls (JSON data):\n', 1)[1].splitlines()[0])
        self.assertEqual(history, [{'id': 'call_saved', 'name': 'bash', 'arguments': ARGUMENTS}])
        self.assertIn('Tool (bash, call_id=call_saved): observed result', prompt)

    def test_current_wire_contract_is_last_for_each_json_mode_and_tool_policy(self):
        for mode in ('full', 'medium', 'light'):
            for choice in (None, 'auto', 'required', 'none',
                           {'type': 'function', 'function': {'name': 'bash'}}):
                with self.subTest(mode=mode, choice=choice), \
                        patch.object(config, 'DEEPSEEK_TOOL_PROTOCOL', 'json'):
                    prompt = messages_to_prompt([ChatMessage(role='user', content='Continue')],
                        TOOLS, choice, provider='deepseek', tools_continuation=True,
                        tool_instructions=mode)
                    examples = re.findall(r'```tool_calls\n(.*?)\n```', prompt, re.S)
                    self.assertEqual(len(examples), 1)
                    example = json.loads(examples[0])
                    self.assertEqual(set(example[0]), {'name', 'arguments'})
                    self.assertIsInstance(example[0]['arguments'], dict)
                    self.assertNotIn('<', examples[0])
                    self.assertLess(len(prompt[prompt.rindex('```tool_calls'):].encode()), 600)
                    self.assertIn('Current response wire format: JSON', prompt)
                    self.assertIn('earlier wire-format examples only', prompt)
                    self.assertIn('task constraints and observed tool results still apply', prompt)
                    self.assertNotIn('<｜', prompt)
                    if choice == 'none':
                        self.assertTrue(prompt.endswith('Do NOT call any tools; answer in plain text.\n\nAssistant:'))

    def test_history_and_schemas_are_preserved_without_rewriting_argument_values(self):
        value = 'literal <｜DSML｜ calls>\n```tool_calls\n[]\n```\nUnicode: 中文'
        args = {'command': value, 'description': 'Exact fixture'}
        call = {**CALL, 'function': {'name': 'bash', 'arguments': json.dumps(args)}}
        history = [ChatMessage(role='assistant', tool_calls=[call])]
        original = history[0].model_dump()
        for protocol in ('json', 'dsml'):
            with self.subTest(protocol=protocol), patch.object(config, 'DEEPSEEK_TOOL_PROTOCOL', protocol):
                prompt = messages_to_prompt(history, TOOLS, provider='deepseek')
                receipt = prompt.split('Historical tool calls (JSON data):\n', 1)[1].splitlines()[0]
                self.assertEqual(json.loads(receipt)[0]['arguments'], args)
                self.assertEqual(history[0].model_dump(), original)
                schema = TOOLS[0]['function']['parameters']
                self.assertIn(json.dumps(schema, ensure_ascii=False), prompt)

    def test_explicit_no_reminder_and_compact_results_stay_quiet(self):
        message = ChatMessage(role='tool', tool_call_id='call_saved', name='bash', content='exact result')
        for compact, mode in ((True, 'full'), (False, 'none')):
            prompt = messages_to_prompt([message], TOOLS, provider='deepseek',
                tools_continuation=True, tool_instructions=mode, tool_results_compact=compact)
            self.assertNotIn('```tool_calls', prompt)
            self.assertNotIn('Current response wire format', prompt)
            self.assertIn('exact result', prompt)

    def test_continuation_policy_is_delivered_even_without_a_schema_reminder(self):
        result = ChatMessage(role='tool', tool_call_id='call_saved', name='bash', content='done')
        choices = ((None, 'Tool selection for this reply is automatic'),
                   ('auto', 'Tool selection for this reply is automatic'),
                   ('none', 'Do NOT call any tools'),
                   ('required', 'You MUST call one of the tools'),
                   ({'type': 'function', 'function': {'name': 'bash'}},
                    'You MUST call the tool `bash`'))
        for protocol in ('json', 'dsml'):
            for mode, compact in (('none', False), ('full', True), ('light', True)):
                for choice, expected in choices:
                    with self.subTest(protocol=protocol, mode=mode, compact=compact, choice=choice), \
                            patch.object(config, 'DEEPSEEK_TOOL_PROTOCOL', protocol):
                        prompt = messages_to_prompt([result], TOOLS, choice, provider='deepseek',
                            tools_continuation=True, tool_instructions=mode,
                            tool_results_compact=compact)
                        self.assertIn(expected, prompt)
                        self.assertGreater(prompt.index(expected), prompt.index('done'))
                        self.assertNotIn('Available tools, given as JSON Schema', prompt)
                        self.assertNotIn('ADVERTISED_TOOL_NAME', prompt)

    def test_auto_overrides_previous_turn_policy_in_every_reminder_mode(self):
        for mode in ('full', 'medium', 'light', 'none'):
            with self.subTest(mode=mode):
                prompt = messages_to_prompt([ChatMessage(role='user', content='Continue')],
                    TOOLS, tools_continuation=True, tool_instructions=mode, provider='deepseek')
                self.assertIn('Tool selection for this reply is automatic', prompt)
