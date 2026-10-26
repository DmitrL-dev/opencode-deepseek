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
                        content, calls = validate_reply(
                            text, [tool(name)], provider='deepseek')
                        self.assertEqual(content, '')
                        self.assertEqual(len(calls), 2 if index == 2 else 1)
                        for call in calls:
                            self.assertEqual(call['function']['name'], name)
                            self.assertEqual(json.loads(call['function']['arguments']), args)
                        for finish in ('length', 'error', 'content_filter'):
                            with self.assertRaises(ToolCallError):
                                validate_reply(
                                    text, [tool(name)], provider='deepseek',
                                    finish_reason=finish)

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
        # A fabricated tool-result suffix makes the whole reply invalid.
        suffix = fence('[' + good + ']') + '\nTool (edit): success'
        for provider in (None, 'deepseek', 'claude'):
            with self.subTest(provider=provider), self.assertRaises(ToolCallError):
                validate_reply(suffix, [tool('edit')], provider=provider)

    BROWSER = ('claude', 'grok', 'mistral', 'chatgpt', 'glm')
    CALL = '[{"name": "write", "arguments": {"file_path": "/x", "content": "y"}}]'

    def test_whole_reply_call_works_for_every_browser_provider(self):
        for provider in self.BROWSER:
            with self.subTest(provider=provider):
                content, calls = validate_reply(
                    '```tool_calls\n' + self.CALL + '\n```', [tool('write')], provider=provider)
                self.assertEqual(content, '')
                self.assertEqual(calls[0]['function']['name'], 'write')

    def test_legacy_keeps_strict_single_block_for_http_providers(self):
        block = '```tool_calls\n' + self.CALL + '\n```'
        with self.assertRaises(ToolCallError):
            validate_reply(
                'Intro.\n' + block + '\nDone.', [tool('write')], provider='qwen')
        self.assertIsNone(
            validate_reply(self.CALL, [tool('write')], provider='qwen')[1])

    def test_quoted_code_never_becomes_an_action(self):
        action = fence('[{"name":"edit","arguments":{"file_path":"x","content":"x"}}]')
        for text in ('\n'.join('> ' + s for s in action.splitlines()),
                     '```markdown\n' + action + '\n```',
                     '~~~markdown\n' + action + '\n~~~',
                     '~~~~markdown\n' + action + '\n~~~~',
                     '````markdown\n' + action + '\n````',
                     '`````markdown\n' + action + '\n`````',
                     '\n'.join('    ' + s for s in action.splitlines()),
                     '\n'.join('\t' + s for s in action.splitlines())):
            with self.subTest(text=text[:24]):
                try:
                    _, calls = validate_reply(text, [tool('edit')])
                except ToolCallError:
                    continue
                self.assertIsNone(calls)

    def test_outer_fence_closes_only_with_its_kind_and_at_least_its_length(self):
        from server.openai_format import find_tool_blocks, looks_truncated
        action = fence(self.CALL)
        for opening, short in (('````markdown', '```'), ('~~~~markdown', '~~~')):
            for closer in (short, '~~~' if opening.startswith('`') else '```'):
                text = opening + '\n' + closer + '\n' + action
                with self.subTest(opening=opening, closer=closer):
                    self.assertEqual(find_tool_blocks(text), [])
                    self.assertFalse(looks_truncated(text))

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
