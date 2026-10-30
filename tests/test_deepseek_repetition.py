"""No completed call can be recovered from a looping/incomplete live response."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from deepseek import stream_diagnostics
from deepseek.client import DeepSeekStreamError
from deepseek.repetition import ToolRepetitionGuard, RepeatedToolFraming, MAX_REDUNDANT_CLOSES
from providers.access import AccessGuard, ProviderRejected
from tests.test_stream import mock_client, snapshot, event
from tests.test_prompt_contract import TOOLS
from server import config
from server.openai_format import messages_to_prompt
from server.schemas import ChatMessage


ROOT = '<｜DSML｜ calls>\n'
INVOKE = '<｜DSML｜ invoke name="bash">\n'
END = '</｜DSML｜ invoke>\n</｜DSML｜ calls>'


def parameter(key, value):
    return f'<｜DSML｜ parameter name="{key}" string="true">{value}</｜DSML｜ parameter>\n'


def broken():
    return ROOT + INVOKE + parameter('command', 'echo safe') + parameter('description', 'Inspect') + \
        '</｜DSML｜ parameter>\n' * 8 + parameter('description', 'Inspect')


class RepetitionTests(unittest.TestCase):
    def test_duplicate_fields_stop_before_tail_for_every_chunk_boundary(self):
        text = broken()
        for width in (1, 2, 7, 31, 563, len(text)):
            for alias in (text, text.replace('｜DSML｜', '｜｜DSML｜｜')):
                guard = ToolRepetitionGuard()
                with self.subTest(width=width), self.assertRaises(RepeatedToolFraming):
                    for i in range(0, len(alias), width): guard.feed(alias[i:i+width])

    def test_unbounded_closing_run_stops_but_small_compatibility_run_survives(self):
        prefix = ROOT + INVOKE + parameter('command', 'x')
        guard = ToolRepetitionGuard()
        guard.feed(prefix + '</｜DSML｜ parameter>\n' * MAX_REDUNDANT_CLOSES)
        with self.assertRaises(RepeatedToolFraming): guard.feed('</｜DSML｜ parameter>')
        ToolRepetitionGuard().feed(prefix + '</｜DSML｜ parameter>\n' * 2 + END)

    def test_values_and_prose_are_not_repetition_templates(self):
        examples = ['same\n' * 20000, '```xml\n' + broken() + '\n```',
                    '> ' + broken().replace('\n', '\n> '),
                    ROOT + INVOKE + parameter('command', 'line\n' * 20000) + END,
                    ROOT + INVOKE + parameter('command', 'z' * 200000) + END]
        for text in examples:
            guard = ToolRepetitionGuard()
            for i in range(0, len(text), 31):
                guard.feed(text[i:i+31]); self.assertLessEqual(len(guard.pending), 560)

    def test_fields_reset_per_invocation_and_supported_outer_closers(self):
        for closer in ('</｜DSML｜ invoke>', '</｜｜DSML｜｜ invoke>', '</DSML｜ invoke>', '</｜DSML｜>'):
            text = ROOT + (INVOKE + parameter('description', 'same') + closer + '\n') * 40 + '</DSML｜ calls>'
            guard = ToolRepetitionGuard()
            for char in text: guard.feed(char)

    def test_stream_boundary_does_not_change_redundant_close_count(self):
        text = ROOT + INVOKE + parameter('command', 'x') + '</｜DSML｜ parameter>\n' * 17
        for width in (1, 23, 1024):
            guard = ToolRepetitionGuard()
            with self.subTest(width=width), self.assertRaises(RepeatedToolFraming):
                for i in range(0, len(text), width): guard.feed(text[i:i+width])

    def test_prompt_has_one_canonical_form_and_no_repair_example(self):
        with patch.object(config, 'DEEPSEEK_TOOL_PROTOCOL', 'dsml'):
            prompt = messages_to_prompt([ChatMessage(role='user', content='Continue')],
                TOOLS, provider='deepseek', conversation_continuation=True, tool_instructions='full')
        self.assertEqual(prompt.count('<｜DSML｜ calls>'), 1)
        self.assertIn('each argument name exactly once', prompt)
        self.assertIn('close calls exactly once and stop', prompt)
        self.assertNotIn('an alternative is', prompt)
        self.assertIn('not an alternative output format', prompt)
        for mode in ('medium', 'light'):
            with patch.object(config, 'DEEPSEEK_TOOL_PROTOCOL', 'dsml'):
                follow = messages_to_prompt([ChatMessage(role='user', content='Continue')],
                    TOOLS, provider='deepseek', conversation_continuation=True, tool_instructions=mode)
            self.assertNotIn('<｜DSML｜ calls>', follow)
            self.assertIn('Tools remain available from earlier in this chat', follow)

    def test_live_client_closes_stream_saves_prefix_and_never_retries(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(stream_diagnostics, 'DIRECTORY', Path(directory)/'captures'), \
                patch.dict(os.environ, DEEPSEEK_STREAM_DIAGNOSTICS='1'):
            guard_path = Path(directory)/'guard.json'
            client = mock_client('unused'); self.addCleanup(client.close)
            client.access_guard = AccessGuard(path=guard_path, interval=0)
            chunks = [snapshot(ROOT + INVOKE + parameter('command', 'PRIVATE_VALUE')).encode(),
                      event({'p':'response/fragments/0/content','o':'APPEND','v':parameter('description','Inspect')}).encode(),
                      event({'v':parameter('description','Inspect')}).encode()]
            class Bytes(httpx.SyncByteStream):
                closed = False
                def __iter__(self):
                    yield from chunks
                    raise AssertionError('Must stop before reading any more stream data')
                def close(self): self.closed = True
            stream = Bytes(); requests = []
            def transport(request):
                requests.append(request)
                return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=stream)
            client._http.close(); client._http = httpx.Client(base_url='https://chat.deepseek.com',transport=httpx.MockTransport(transport))
            with self.assertLogs(level='WARNING') as logs, self.assertRaises(DeepSeekStreamError) as error:
                client.chat('PRIVATE_PROMPT',conversation_id='fixture:1')
            self.assertEqual(error.exception.diagnostic_code,'stream_repeated_tool_framing')
            self.assertTrue(stream.closed); self.assertEqual(len(requests),1)
            self.assertNotIn('PRIVATE_', '\n'.join(logs.output))
            data = json.loads(next((Path(directory)/'captures').glob('*.json')).read_text())
            self.assertEqual(base64.b64decode(data['body_base64']), b''.join(chunks))
            self.assertFalse(data['eof']); self.assertFalse(data['truncated'])
            self.assertEqual(data['reason'],'stream_repeated_tool_framing')
            with self.assertRaises(ValueError): stream_diagnostics.reconstruct(next((Path(directory)/'captures').glob('*.json')))
            client.access_guard = AccessGuard(path=guard_path, interval=0)
            with self.assertRaises(ProviderRejected): client.chat('No retry',conversation_id='fixture:1')
            self.assertEqual(len(requests),1)

    def test_repeated_snapshot_is_not_repeated_output(self):
        text = ROOT + INVOKE + parameter('command','x') + END
        client = mock_client(snapshot(text) + snapshot(text, 'FINISHED'))
        self.addCleanup(client.close)
        self.assertEqual(client.chat('fixture').text,text)
