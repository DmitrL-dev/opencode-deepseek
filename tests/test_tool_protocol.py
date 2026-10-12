"""Public, synthetic regression corpus; no account or private source fixtures."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.openai_format import ToolCallError, completion_response, sse_frames
from server.tool_protocol import validate_reply
from server.recovery_status import recovery_stopped
from server.offline_corpus import replay


def tool(name):
    return {"type": "function", "function": {"name": name, "parameters": {
        "type": "object", "properties": {
            "file_path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["file_path", "content"], "additionalProperties": False}}}


def fence(payload):
    return '```tool_calls\n' + payload + '\n```'


class ProtocolCorpusTests(unittest.TestCase):
    def test_general_envelope_matrix_preserves_every_argument(self):
        # Names are deliberately arbitrary: normalization cannot depend on a
        # special-cased read/write/edit implementation.
        for name in ('read', 'write', 'edit', 'arbitrary_custom_tool'):
            for value in ('', 'quote " slash \\', '\n> text\n```tool_calls',
                          '},{"name":"not_an_action"}', 'кириллица 🚀'):
                args = {'file_path': '/fixture', 'content': value}
                entry = json.dumps({'name': name, 'arguments': args})
                raw = '[' + entry + ']'
                variants = [fence(raw), fence(raw[:-1]),
                            fence('[' + entry[:-1] + ',' + entry[:-1] + ']'),
                            fence('[' + entry[:-1] + ']}'),
                            'Prose\n\n> Quoted prose\n\n' + fence(raw),
                            fence(json.dumps([{'name': name, **args}]))]
                for index, text in enumerate(variants):
                    with self.subTest(name=name, value=value, variant=index):
                        content, calls = validate_reply(text, [tool(name)])
                        self.assertEqual(content, '')
                        self.assertEqual(len(calls), 2 if index == 2 else 1)
                        for call in calls:
                            self.assertEqual(call['function']['name'], name)
                            self.assertEqual(json.loads(call['function']['arguments']), args)
                        for finish in ('length', 'error', 'content_filter'):
                            with self.assertRaises(ToolCallError):
                                validate_reply(text, [tool(name)], finish_reason=finish)

    def test_invalid_arguments_or_fabricated_receipts_never_return_partial_batch(self):
        good = json.dumps({'name': 'edit', 'arguments': {'file_path': '/x', 'content': 'x'}})
        invalid = [
            '{"name":"edit","arguments":{"content":"x"}}',
            '{"name":"edit","arguments":{"file_path":9,"content":"x"}}',
            '{"name":"edit","arguments":{"file_path":"x","content":"a","content":"b"}}',
            '{"name":"edit","arguments":{"file_path":"x","content":NaN}}',
            '{"name":"edit","arguments":{"file_path":"x","content":"unterminated}}',
            '{"name":"edit","arguments":{"file_path":"x","content":"x"',
            '{"name":"unknown","arguments":{"file_path":"x","content":"x"}}',
        ]
        for bad in invalid:
            for text in (fence('[' + good + ',' + bad + ']'),
                         fence('[' + good + ']') + '\nTool (edit): success'):
                with self.subTest(text=text), self.assertRaises(ToolCallError):
                    validate_reply(text, [tool('edit')])

    def test_abstract_tool_example_is_text_not_a_failed_call(self):
        # An explanation that shows the protocol shape names no advertised tool:
        # it is an answer, never an action and never a "Unsupported markup" error.
        examples = [
            'The format looks like this:\n```tool_calls\n[ { ... }, { ... } ]\n```\nUse it to call tools.',
            'Example:\n```tool_calls\n[{"name": "tool_name", "arguments": {...}}]\n```',
            'Shape:\n```calls\n[ { "name": "<tool>", "arguments": {} } ]\n```',
        ]
        # DeepSeek keeps its own preamble parser and repair flow (unchanged here).
        for provider in ('claude', 'gemini', 'grok'):
            for text in examples:
                with self.subTest(provider=provider, text=text[:30]):
                    content, calls = validate_reply(text, [tool('write')], provider=provider)
                    self.assertIsNone(calls)
                    self.assertEqual(content, text)

    BROWSER = ('claude', 'grok', 'mistral', 'chatgpt', 'glm')
    CALL = '[{"name": "write", "arguments": {"file_path": "/x", "content": "y"}}]'

    def test_whole_reply_call_works_for_every_browser_provider(self):
        for provider in self.BROWSER:
            with self.subTest(provider=provider):
                content, calls = validate_reply(
                    '```tool_calls\n' + self.CALL + '\n```', [tool('write')], provider=provider)
                self.assertEqual(content, '')
                self.assertEqual(calls[0]['function']['name'], 'write')

    def test_valid_call_blocks_run_wherever_they_stand_and_the_rest_stays_content(self):
        block = '```tool_calls\n' + self.CALL + '\n```'
        sample = '```text\nsome sample\n```'
        cases = [
            ('I will write the file now.\n' + block, 'I will write the file now.', 1),
            (block + '\nThat was it.', 'That was it.', 1),
            ('First.\n\n' + block + '\n\nSecond.', 'First.\n\nSecond.', 1),
            ('A\n' + block + '\nB\n' + block + '\nC', 'A\n\nB\n\nC', 2),
            ('Sample:\n' + sample + '\n' + block, 'Sample:\n' + sample, 1),
            # A closing fence may be missing after a normal stop.
            ('Writing.\n```tool_calls\n' + self.CALL, 'Writing.', 1),
        ]
        for provider in self.BROWSER:
            for text, expected, count in cases:
                with self.subTest(provider=provider, text=text[:24]):
                    content, calls = validate_reply(text, [tool('write')], provider=provider)
                    self.assertEqual(content, expected)
                    self.assertEqual(len(calls), count)
                    self.assertTrue(all(c['function']['name'] == 'write' for c in calls))
            with self.subTest(provider=provider, case='unfinished'), self.assertRaises(ToolCallError):
                validate_reply(cases[0][0], [tool('write')], provider=provider, finish_reason='length')

    def test_arrays_without_the_tool_calls_fence_are_plain_text(self):
        replies = [self.CALL, '```json\n' + self.CALL + '\n```', '```\n' + self.CALL + '\n```',
                   'Here is a call:\n\n' + self.CALL, self.CALL + '\nThat was it.',
                   '[{"name": "write", "arguments": {"bad": 1}}]',
                   '```calls\n' + self.CALL + '\n```']
        for provider in self.BROWSER:
            for reply in replies:
                with self.subTest(provider=provider, reply=reply[:14]):
                    content, calls = validate_reply(reply, [tool('write')], provider=provider)
                    self.assertIsNone(calls)
                    self.assertEqual(content, reply)

    def test_abstract_blocks_are_text_not_a_failed_call(self):
        # Broken JSON syntax (an ellipsis, a placeholder) or no tool name at all.
        texts = [
            'Shape:\n```tool_calls\n[ { ... }, { ... } ]\n```\nUse it.',
            'Example:\n```tool_calls\n[{"name": "tool_name", "arguments": {...}}]\n```',
            'Missing comma:\n```tool_calls\n[{"name": "write" "arguments": {}}]\n```',
            'No name:\n```tool_calls\n[{"arguments": {"file_path": "/x"}}]\n```',
            'Not a call:\n```tool_calls\n{"hello": "world"}\n```',
            'Empty:\n```tool_calls\n[]\n```',
        ]
        for provider in self.BROWSER:
            for text in texts:
                with self.subTest(provider=provider, text=text[:24]):
                    content, calls = validate_reply(text, [tool('write')], provider=provider)
                    self.assertIsNone(calls)
                    self.assertEqual(content, text)
        # tool_choice stays a contract.
        with self.assertRaises(ToolCallError):
            validate_reply(texts[0], [tool('write')], 'required', provider='claude')

    def test_unknown_tool_and_wrong_arguments_are_errors_with_a_kind(self):
        demo = ('```tool_calls\n[{"name": "functions.my_fake_tool", "arguments": '
                '{"someString": "x"}}]\n```')
        wrong = '```tool_calls\n[{"name": "write", "arguments": {"file_path": "/x"}}]\n```'
        valid = '```tool_calls\n' + self.CALL + '\n```'
        for provider in self.BROWSER:
            with self.subTest(provider=provider, case='unknown'):
                with self.assertRaises(ToolCallError) as ctx:
                    validate_reply('Example:\n' + demo + '\nDone.', [tool('write')], provider=provider)
                self.assertEqual(ctx.exception.kind, 'unknown_tool')
                self.assertEqual(ctx.exception.tool, 'functions.my_fake_tool')
            with self.subTest(provider=provider, case='schema'):
                with self.assertRaises(ToolCallError) as ctx:
                    validate_reply('Now:\n' + wrong, [tool('write')], provider=provider)
                self.assertEqual(ctx.exception.kind, 'schema')
                self.assertEqual(ctx.exception.tool, 'write')
                self.assertIn('file_path: string', ctx.exception.guidance)
                self.assertNotIn('/x', ctx.exception.guidance)
            with self.subTest(provider=provider, case='mixed'):
                # One bad call stops the whole message; nothing runs.
                for text in (valid + '\nAnd:\n' + demo, wrong + '\n' + valid):
                    with self.assertRaises(ToolCallError):
                        validate_reply(text, [tool('write')], provider=provider)
            with self.subTest(provider=provider, case='none'):
                # tool_choice none: an attempted call is shown, never an error.
                content, calls = validate_reply(demo, [tool('write')], 'none', provider=provider)
                self.assertIsNone(calls)
        # DeepSeek keeps its own strict flow.
        with self.assertRaisesRegex(ToolCallError, 'unadvertised'):
            validate_reply(demo, [tool('write')], provider='deepseek')

    def test_quoted_blocks_stay_text_for_browser_providers(self):
        block = '```tool_calls\n' + self.CALL + '\n```'
        for text in ('```markdown\n' + block + '\n```',
                     '\n'.join('> ' + line for line in block.splitlines())):
            with self.subTest(text=text[:20]):
                content, calls = validate_reply(text, [tool('write')], provider='claude')
                self.assertIsNone(calls)

    def test_non_browser_providers_keep_the_original_strict_contract(self):
        block = '```tool_calls\n' + self.CALL + '\n```'
        with patch('server.tool_protocol.provider_uses_browser', return_value=False):
            with self.assertRaises(ToolCallError):
                validate_reply('Intro.\n' + block + '\nDone.', [tool('write')], provider='qwen')
            self.assertIsNone(validate_reply(self.CALL, [tool('write')], provider='qwen')[1])

    def test_quoted_code_never_becomes_an_action(self):
        action = fence('[{"name":"edit","arguments":{"file_path":"x","content":"x"}}]')
        for text in ('\n'.join('> ' + s for s in action.splitlines()),
                     '```markdown\n' + action + '\n```',
                     '~~~markdown\n' + action + '\n~~~'):
            try:
                _, calls = validate_reply(text, [tool('edit')])
            except ToolCallError:
                continue
            self.assertIsNone(calls)

    def test_recovery_stop_is_normal_completion_and_never_claims_goal_success(self):
        with patch('socket.socket', side_effect=AssertionError('network forbidden')):
            reply, content, calls = recovery_stopped()
            self.assertIsNone(reply.conversation_id)
            self.assertIsNone(calls)
            result = completion_response('deepseek-chat', content, '', None)
            self.assertEqual(result['choices'][0]['finish_reason'], 'stop')
            frames = list(sse_frames('deepseek-chat', content, calls, None))
            self.assertFalse(any('"error"' in frame for frame in frames))
            self.assertIn('Цель не объявлена выполненной', content)

    def test_private_corpus_detects_changed_source_before_parsing(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            (p/'reply.json').write_text('{}')
            (p/'manifest.json').write_text(json.dumps([
                {'file': 'reply.json', 'sha256': 'wrong', 'expected': {'kind': 'reject'}}]))
            with patch('socket.socket', side_effect=AssertionError('network forbidden')):
                self.assertEqual(replay(p/'manifest.json')['failed_indices'], [0])
