import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from chat_protocol import Reply
from server import diagnostics
from server.schemas import ChatCompletionRequest, ChatMessage


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private' / 'last.json'
        self.reply = Reply('PRIVATE_INVALID_REPLY', 'PRIVATE_CONVERSATION')
        self.request = ChatCompletionRequest(model='deepseek-chat', messages=[
            ChatMessage(role='user', content='PRIVATE_PROMPT')])

    def test_capture_is_opt_in_private_deduplicated_and_bounded(self):
        with patch.object(diagnostics, 'FAILURE_FILE', self.path), patch.dict(os.environ, TOOLCALL_DIAGNOSTICS='0'):
            diagnostics.save_tool_failure(self.reply, self.request)
            self.assertFalse(self.path.exists())
        with patch.object(diagnostics, 'FAILURE_FILE', self.path), patch.dict(os.environ, TOOLCALL_DIAGNOSTICS='1'):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                diagnostics.save_tool_failure(self.reply, self.request)
            self.assertEqual(output.getvalue(), '')
            value = self.path.read_text()
            self.assertIn('PRIVATE_INVALID_REPLY', value)
            self.assertNotIn('PRIVATE_PROMPT', value)
            self.assertNotIn('PRIVATE_CONVERSATION', value)
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
            diagnostics.save_tool_failure(Reply('replacement', None), self.request)
            self.assertEqual(json.loads(self.path.read_text())['reply'], 'replacement')
            archive = self.path.parent / 'toolcall-failures'
            self.assertEqual(len(list(archive.iterdir())), 2)
            for entry in archive.iterdir():
                self.assertEqual(stat.S_IMODE(entry.stat().st_mode), 0o600)
            diagnostics.save_tool_failure(Reply('replacement', None), self.request)
            self.assertEqual(len(list(archive.iterdir())), 2)
            with patch.object(diagnostics, 'MAX_ARCHIVED', 2):
                diagnostics.save_tool_failure(Reply('new failure', None), self.request)
            self.assertEqual(len(list(archive.iterdir())), 2)
            self.assertEqual(json.loads(self.path.read_text())['reply'], 'new failure')

    def test_capture_failure_never_leaks_payload_or_replaces_original_error(self):
        with patch.dict(os.environ, TOOLCALL_DIAGNOSTICS='1'), \
                patch.object(diagnostics, 'write_private_json', side_effect=OSError('PRIVATE_PATH')) as writer, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            diagnostics.save_tool_failure(self.reply, self.request)
        writer.assert_called_once()
        self.assertNotIn('PRIVATE', output.getvalue())
        with patch.dict(os.environ, TOOLCALL_DIAGNOSTICS='1'), \
                patch.object(diagnostics, 'MAX_BYTES', 1), \
                patch.object(diagnostics, 'write_private_json') as writer, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            diagnostics.save_tool_failure(self.reply, self.request)
        writer.assert_not_called()
        self.assertNotIn('PRIVATE', output.getvalue())

    def test_foreign_or_invalid_identity_does_not_create_a_recovery_binding(self):
        for model, identity in [('qwen3.8-max', 'fixture:298'),
                                ('deepseek-chat', 'fixture'),
                                ('deepseek-chat', 'fixture:0'),
                                ('deepseek-chat', 'private\nidentity:298'),
                                ('deepseek-chat', None)]:
            with self.subTest(model=model, identity=identity), \
                    patch.object(diagnostics, 'FAILURE_FILE', self.path), \
                    patch.dict(os.environ, TOOLCALL_DIAGNOSTICS='1'):
                req = self.request.model_copy(update={'model': model})
                diagnostics.save_tool_failure(Reply('fixture', identity), req)
                self.assertNotIn('recovery_binding', json.loads(self.path.read_text()))
    def test_full_tool_stream_directory_preserves_evidence_and_original_validation_error(self):
        from tests.test_stream import mock_client, snapshot
        from deepseek import stream_diagnostics
        from server import api
        from server.openai_format import ToolCallError
        directory = self.path.parent / 'failed-tool-streams'
        directory.mkdir(parents=True)
        old = directory / 'old.json'
        old.write_bytes(b'earlier evidence')
        client = mock_client(snapshot('```tool_calls\n{broken\n```', 'FINISHED'))
        self.addCleanup(client.close)
        req = self.request.model_copy(update={'tools': [{'function': {'name': 'read',
            'parameters': {'type': 'object'}}}]})
        with patch.dict(os.environ, TOOLCALL_DIAGNOSTICS='1', DEEPSEEK_STREAM_DIAGNOSTICS='1'), \
                patch.object(diagnostics, 'FAILURE_FILE', self.path), \
                patch.object(stream_diagnostics, 'MAX_FILES', 1):
            reply = client.chat('PRIVATE_PROMPT', conversation_id='fixture:1')
            with self.assertLogs(level='WARNING'), self.assertRaises(ToolCallError):
                api._validate_tool_reply(reply, req)
        self.assertEqual(old.read_bytes(), b'earlier evidence')
        self.assertEqual(len(list(directory.iterdir())), 1)
        data = json.loads(self.path.read_text())
        self.assertEqual(data['recovery_binding']['conversation_id'], 'fixture:2')
        self.assertNotIn('stream_capture', data)
        self.assertEqual(data['stream_capture_status'], 'storage_full')

    def test_exact_deepseek_binding_is_private_metadata_not_a_delivery_permission(self):
        from server.conversation_cache import digest
        reply = Reply('PRIVATE_INVALID_REPLY', 'fixture:298')
        with patch.object(diagnostics, 'FAILURE_FILE', self.path), \
                patch.dict(os.environ, TOOLCALL_DIAGNOSTICS='1'), \
                patch('httpx.Client.send', side_effect=AssertionError('Network forbidden')):
            diagnostics.save_tool_failure(reply, self.request)
        data = json.loads(self.path.read_text())
        self.assertEqual(data['recovery_binding'], {
            'conversation_id': 'fixture:298',
            'request_digest': digest(self.request, self.request.messages),
            'delivery_authorized': False})
        self.assertNotIn('PRIVATE_PROMPT', self.path.read_text())
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_complete_stream_is_kept_only_after_tool_failure_without_new_request(self):
        from tests.test_stream import mock_client, snapshot
        from deepseek import stream_diagnostics
        from deepseek.stream_diagnostics import reconstruct
        raw = snapshot('PRIVATE_INVALID_REPLY', 'FINISHED')
        client = mock_client(raw)
        self.addCleanup(client.close)
        transport_captures = self.path.parent / 'transport-only'
        with patch.dict(os.environ, TOOLCALL_DIAGNOSTICS='1', DEEPSEEK_STREAM_DIAGNOSTICS='1'), \
                patch.object(diagnostics, 'FAILURE_FILE', self.path), \
                patch.object(stream_diagnostics, 'DIRECTORY', transport_captures):
            reply = client.chat('PRIVATE_PROMPT', conversation_id='fixture:296')
            self.assertFalse(self.path.parent.exists())
            with patch.object(client._http, 'send', side_effect=AssertionError('No new request')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                diagnostics.save_tool_failure(reply, self.request)
                diagnostics.save_tool_failure(reply, self.request)
        data = json.loads(self.path.read_text())
        captures = list((self.path.parent / 'failed-tool-streams').glob('*.json'))
        self.assertEqual(len(captures), 1)
        self.assertFalse(transport_captures.exists())
        self.assertEqual(data['stream_capture'], captures[0].name)
        self.assertEqual(data['stream_capture_status'], 'saved')
        self.assertEqual(reconstruct(captures[0])['conversation_id'], 'fixture:2')
        self.assertEqual(reconstruct(captures[0])['text'], 'PRIVATE_INVALID_REPLY')
        self.assertNotIn('PRIVATE_PROMPT', captures[0].read_text())
        self.assertNotIn('PRIVATE', output.getvalue())
