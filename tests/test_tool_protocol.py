"""Public, synthetic regression corpus; no account or private source fixtures."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.openai_format import ToolCallError, completion_response, sse_frames
from server.tool_protocol import validate_reply as validate_legacy
from server.tool_protocol_new import demote_mistagged_tool_reply, validate_reply
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
        # Main-era DeepSeek tolerances (TOOLCALL_LEGACY=1) live in tool_protocol.
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
                        content, calls = validate_legacy(
                            text, [tool(name)], provider='deepseek')
                        self.assertEqual(content, '')
                        self.assertEqual(len(calls), 2 if index == 2 else 1)
                        for call in calls:
                            self.assertEqual(call['function']['name'], name)
                            self.assertEqual(json.loads(call['function']['arguments']), args)
                        for finish in ('length', 'error', 'content_filter'):
                            with self.assertRaises(ToolCallError):
                                validate_legacy(
                                    text, [tool(name)], provider='deepseek',
                                    finish_reason=finish)

    def test_rework_path_runs_mid_message_blocks_for_any_provider(self):
        block = fence('[{"name":"write","arguments":{"file_path":"/x","content":"y"}}]')
        text = 'First.\n\n' + block + '\n\nSecond.'
        for provider in ('deepseek', 'claude', 'qwen'):
            with self.subTest(provider=provider):
                content, calls = validate_reply(text, [tool('write')], provider=provider)
                self.assertEqual(content, 'First.\n\nSecond.')
                self.assertEqual(calls[0]['function']['name'], 'write')

    def test_rework_preserves_ordered_parts_for_streaming_frames(self):
        call = '[{"name":"write","arguments":{"file_path":"/x","content":"y"}}]'
        text = 'text1\n' + fence(call) + '\ntext2\n' + fence(call) + '\ntext3'
        content, calls, parts = validate_reply(text, [tool('write')], with_parts=True)
        self.assertEqual(content, 'text1\n\ntext2\n\ntext3')
        self.assertEqual([part['type'] for part in parts],
                         ['text', 'tool_call', 'text', 'tool_call', 'text'])
        self.assertEqual([part['text'] for part in parts if part['type'] == 'text'],
                         ['text1', 'text2', 'text3'])
        frames = list(sse_frames('deepseek-chat', content, calls, None, parts=parts))
        events = []
        for frame in frames:
            if not frame.startswith('data: {'):
                continue
            delta = json.loads(frame[6:])['choices'][0]['delta']
            if delta.get('content'):
                events.append(('text', delta['content']))
            elif delta.get('tool_calls'):
                events.append(('call', delta['tool_calls'][0]['index']))
        self.assertEqual(events, [
            ('text', 'text1\n\n'), ('call', 0), ('text', 'text2\n\n'),
            ('call', 1), ('text', 'text3')])
        # The client concatenates deltas verbatim; the result must equal the
        # same content the cache/non-streaming path uses.
        assembled = ''.join(value for kind, value in events if kind == 'text')
        self.assertEqual(assembled, content)
        final = json.loads(frames[-2][6:])
        self.assertEqual(final['choices'][0]['finish_reason'], 'tool_calls')

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
            with self.subTest(bad=bad[:48]), self.assertRaises(ToolCallError):
                validate_reply(fence('[' + good + ',' + bad + ']'), [tool('edit')])
        # Rework: valid block + fake tool-result suffix → call + content.
        # Main-era (legacy): whole reply must be one block → error.
        suffix = fence('[' + good + ']') + '\nTool (edit): success'
        content, calls = validate_reply(suffix, [tool('edit')])
        self.assertEqual(len(calls), 1)
        self.assertIn('Tool (edit): success', content)
        with self.assertRaises(ToolCallError):
            validate_legacy(suffix, [tool('edit')], provider='deepseek')

    def test_calls_fence_alias_is_not_a_browser_tool_block(self):
        # Browser only recognizes top-level ```tool_calls; ```calls stays prose.
        text = 'Shape:\n```calls\n[ { "name": "<tool>", "arguments": {} } ]\n```'
        for provider in ('claude', 'gemini', 'grok'):
            with self.subTest(provider=provider):
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

    def test_invalid_tool_blocks_fail_validate_after_demote_layer(self):
        # Advice (no name / unknown+prose) is demoted in the API layer. Remaining
        # top-level ```tool_calls must parse: syntax and no_name are errors here.
        cases = [
            ('Shape:\n```tool_calls\n[ { ... }, { ... } ]\n```\nUse it.', 'syntax'),
            ('Example:\n```tool_calls\n[{"name": "tool_name", "arguments": {...}}]\n```', 'syntax'),
            ('Missing comma:\n```tool_calls\n[{"name": "write" "arguments": {}}]\n```', 'syntax'),
            ('No name:\n```tool_calls\n[{"arguments": {"file_path": "/x"}}]\n```', 'no_name'),
            ('Not a call:\n```tool_calls\n{"hello": "world"}\n```', 'no_name'),
            ('Empty:\n```tool_calls\n[]\n```', 'no_name'),
        ]
        for provider in self.BROWSER:
            for text, kind in cases:
                with self.subTest(provider=provider, kind=kind, text=text[:24]):
                    with self.assertRaises(ToolCallError) as ctx:
                        validate_reply(text, [tool('write')], provider=provider)
                    self.assertEqual(ctx.exception.kind, kind)
        # tool_choice none: never an error from block parse.
        content, calls = validate_reply(cases[0][0], [tool('write')], 'none', provider='claude')
        self.assertIsNone(calls)
        self.assertEqual(content, cases[0][0])

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
        # LEGACY=0: DeepSeek uses the same validate as browser (unknown → error).
        with self.assertRaises(ToolCallError) as ctx:
            validate_reply(demo, [tool('write')], provider='deepseek')
        self.assertEqual(ctx.exception.kind, 'unknown_tool')

    def test_demote_mistagged_advice_rewrites_fence_to_text(self):
        tools = [tool('write')]
        no_name = 'Advice:\n```tool_calls\n[{"arguments": {"file_path": "/x"}}]\n```\nDone.'
        broken_no_name = 'Shape:\n```tool_calls\n[ { ... }, { ... } ]\n```\nUse it.'
        unknown_prose = (
            'Example:\n```tool_calls\n[{"name": "functions.my_fake_tool", '
            '"arguments": {"someString": "x"}}]\n```\nDone.')
        placeholder = (
            'Placeholder:\n```tool_calls\n'
            '[{"name": "tool_name", "arguments": {...}}]\n```')
        for text in (no_name, broken_no_name, unknown_prose, placeholder):
            with self.subTest(text=text[:28]):
                demoted = demote_mistagged_tool_reply(text, tools)
                self.assertIsNotNone(demoted)
                self.assertIn('```text', demoted)
                self.assertNotIn('```tool_calls', demoted)
                self.assertIn(text.split('```tool_calls', 1)[0], demoted)

    def test_demote_leaves_real_tool_attempts_alone(self):
        tools = [tool('write')]
        valid = '```tool_calls\n' + self.CALL + '\n```'
        unknown_alone = (
            '```tool_calls\n[{"name": "functions.my_fake_tool", '
            '"arguments": {"someString": "x"}}]\n```')
        schema = (
            '```tool_calls\n[{"name": "write", "arguments": '
            '{"file_path": "/x"}}]\n```')
        known_broken = (
            '```tool_calls\n[{"name": "write" "arguments": '
            '{"file_path": "/x", "content": "y"}}]\n```')
        mixed = 'Sample:\n```tool_calls\n[ { ... } ]\n```\n' + valid
        for text in (valid, unknown_alone, schema, known_broken, mixed):
            with self.subTest(text=text[:40]):
                self.assertIsNone(demote_mistagged_tool_reply(text, tools))

    def test_quoted_blocks_stay_text_for_browser_providers(self):
        block = '```tool_calls\n' + self.CALL + '\n```'
        for text in ('```markdown\n' + block + '\n```',
                     '\n'.join('> ' + line for line in block.splitlines())):
            with self.subTest(text=text[:20]):
                content, calls = validate_reply(text, [tool('write')], provider='claude')
                self.assertIsNone(calls)

    def test_legacy_keeps_strict_single_block_for_http_providers(self):
        block = '```tool_calls\n' + self.CALL + '\n```'
        with self.assertRaises(ToolCallError):
            validate_legacy(
                'Intro.\n' + block + '\nDone.', [tool('write')], provider='qwen')
        self.assertIsNone(
            validate_legacy(self.CALL, [tool('write')], provider='qwen')[1])

    def test_quoted_code_never_becomes_an_action(self):
        action = fence('[{"name":"edit","arguments":{"file_path":"x","content":"x"}}]')
        # find_tool_blocks only nests under ``` fences (not ~~~).
        for text in ('\n'.join('> ' + s for s in action.splitlines()),
                     '```markdown\n' + action + '\n```'):
            with self.subTest(text=text[:24]):
                content, calls = validate_reply(text, [tool('edit')])
                self.assertIsNone(calls)
                self.assertEqual(content, text)

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
