import asyncio
import copy
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import Mock, patch

from chat_protocol import Reply
from providers.access import current_attempt, ProviderRejected
from server import api
from server.conversation_cache import ConversationStateError
from server.history_import import authorize, claim, request_key, validate_new_calls, main
from server.schemas import ChatCompletionRequest, ChatMessage
from tests.support import isolated_access
from tests.test_tools import block, FLAT_TOOLS


class HistoryImportTests(unittest.TestCase):
    def setUp(self):
        isolated_access(self)
        self.cache = api.conversation_cache
        self.root = self.cache.path.parent / 'checkpoints'
        self.req = ChatCompletionRequest(model='deepseek-expert', tools=FLAT_TOOLS, messages=[
            ChatMessage(role='system', content='Preserve the existing task and do not delegate.'),
            ChatMessage(role='user', content='Investigate the next unperformed action.'),
            ChatMessage(role='assistant', tool_calls=[{'id': 'already_done', 'type': 'function',
                'function': {'name': 'read', 'arguments': '{"file_path":"previous.txt"}'}}]),
            ChatMessage(role='tool', tool_call_id='already_done', content='Observed: 12 tests passed.')])
        self.client = Mock()
        self.client.chat.side_effect = self.answer
        # An unrelated old link and usage must survive every import.
        seed = ChatCompletionRequest(model='deepseek-chat', messages=[ChatMessage(role='user', content='Other task')])
        self.cache.remember(seed, Reply('Old response', 'old:100'), 'Old response', None, 17)
        with self.cache.connection() as db:
            self.old_rows = list(db.execute('SELECT * FROM links'))
        attempt = api.guard.begin('deepseek')
        attempt.dispatch(kind='generation')
        attempt.complete()
        self.old_usage = copy.deepcopy(api.guard._memory['deepseek']['usage'])

    def authorize(self, req=None, scope='isolated'):
        return authorize(self.cache, req or self.req, scope, self.root, source_provider='deepseek-official')

    def run_request(self, req=None):
        with patch.object(api, 'get_client', return_value=self.client):
            return api._conversation_tool_chat('unused full history', req or self.req, None)

    def answer(self, prompt, cid, *args):
        self.assertIsNone(cid)
        self.assertIn('explicitly authorized import from deepseek-official', prompt)
        self.assertIn('already_done', prompt)
        self.assertIn('Do not repeat completed actions', prompt)
        current_attempt('deepseek').dispatch(kind='generation')
        return Reply(block([{'name': 'read', 'arguments': {'file_path': 'next.txt'}}]), 'new:2')

    def assert_old_state(self):
        with self.cache.connection() as db:
            for row in self.old_rows:
                self.assertEqual(db.execute('SELECT * FROM links WHERE key=?', (row[0],)).fetchone(), row)
        self.assertEqual(api.guard._memory['deepseek']['usage'][:len(self.old_usage)], self.old_usage)

    def test_unknown_history_stays_closed_without_ticket(self):
        with self.assertRaisesRegex(api.ToolCallError, 'History is unknown'):
            self.run_request()
        self.client.chat.assert_not_called()
        self.assert_old_state()

    def test_exact_import_binds_original_history_only_and_continues_normally(self):
        self.authorize()
        result = self.run_request()
        self.assertEqual(self.client.chat.call_count, 1)
        self.assertEqual(json.loads(result[2][0]['function']['arguments']), {'file_path': 'next.txt'})
        self.assert_old_state()
        with self.cache.connection() as db:
            self.assertEqual(db.execute('SELECT consumed FROM history_imports').fetchone(), (1,))
            self.assertEqual(db.execute('SELECT count(*) FROM links').fetchone(), (2,))
            self.assertEqual(db.execute('SELECT used,steps FROM links WHERE cid=?', ('new:2',)).fetchone(), (0, 1))
        following = self.req.model_copy(update={'messages': [*self.req.messages,
            ChatMessage(role='assistant', content=result[1] or '', tool_calls=result[2]),
            ChatMessage(role='tool', tool_call_id=result[2][0]['id'], content='Next file content')]})
        prepared, steps = self.cache.prepare(following, scope='isolated')
        self.assertEqual((prepared.conversation_id, steps), ('new:2', 1))
        with self.assertRaisesRegex(api.ToolCallError, 'consumed'):
            self.run_request()
        self.assertEqual(self.client.chat.call_count, 1)

    def test_authorization_binds_every_semantic_field_and_account(self):
        self.authorize()
        variants = [self.req.model_copy(update={k: v}) for k, v in {
            'model': 'deepseek-chat', 'tool_choice': 'none', 'temperature': 0.3,
            'max_tokens': 77, 'search': True, 'thinking': True, 'user': 'different',
            'tools': None, 'messages': [*self.req.messages, ChatMessage(role='user', content='Changed task')],
        }.items()]
        for req in variants:
            with self.subTest(key=request_key(req, 'isolated')):
                self.assertIsNone(claim(self.cache, req, 'isolated'))
        self.assertIsNone(claim(self.cache, self.req, 'different-account'))
        self.assertIsNotNone(claim(self.cache, self.req.model_copy(update={'stream': True}), 'isolated'))

    def test_new_validator_preserves_schema_checks_and_binds_valid_reply(self):
        self.authorize()
        with patch.object(api, '_LEGACY', False):
            result = self.run_request()
        self.assertEqual(result[2][0]['function']['name'], 'read')
        self.assertEqual(self.client.chat.call_count, 1)

    def test_repeated_action_rejects_whole_batch_without_new_binding(self):
        self.authorize()
        def repeat(*args):
            current_attempt('deepseek').dispatch(kind='generation')
            return Reply(block([
                {'name': 'read', 'arguments': {'file_path': 'next.txt'}},
                {'name': 'read', 'arguments': {'file_path': 'previous.txt'}},
            ]), 'new:2')
        self.client.chat.side_effect = repeat
        with patch('server.diagnostics.save_tool_failure') as save:
            with self.assertRaisesRegex(api.ToolCallError, 'repeats a recorded tool call'):
                self.run_request()
            save.assert_called_once()
            self.assertIs(save.call_args.args[1], self.req)
        with self.cache.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM links').fetchone(), (1,))
        with self.assertRaisesRegex(api.ToolCallError, 'consumed'):
            self.run_request()
        self.assertEqual(self.client.chat.call_count, 1)

    def test_current_goal_read_is_not_a_replayed_action(self):
        tools = [*FLAT_TOOLS, {'type': 'function', 'function': {'name': 'get_goal',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}}]
        req = self.req.model_copy(update={'tools': tools, 'messages': [*self.req.messages,
            ChatMessage(role='assistant', tool_calls=[{'id': 'old-goal', 'type': 'function',
                'function': {'name': 'get_goal', 'arguments': '{}'}}]),
            ChatMessage(role='tool', tool_call_id='old-goal', content='Old goal state before resume')]})
        self.authorize(req)
        def current(*args):
            current_attempt('deepseek').dispatch(kind='generation')
            return Reply(block([{'name': 'read', 'arguments': {'file_path': 'new-archive.json'}},
                                {'name': 'get_goal', 'arguments': {}}]), 'new:2')
        self.client.chat.side_effect = current
        result = self.run_request(req)
        self.assertEqual([c['function']['name'] for c in result[2]], ['read', 'get_goal'])
        self.assertEqual(self.client.chat.call_count, 1)

    def test_goal_mutation_remains_protected_from_repetition(self):
        call = {'id': 'old-update', 'type': 'function', 'function': {
            'name': 'update_goal', 'arguments': '{"status":"complete"}'}}
        req = self.req.model_copy(update={'messages': [*self.req.messages,
            ChatMessage(role='assistant', tool_calls=[call]),
            ChatMessage(role='tool', tool_call_id=call['id'], content='Complete')]})
        with patch('server.diagnostics.save_tool_failure'):
            with self.assertRaises(api.ToolCallError):
                validate_new_calls(req, Reply('fixture', 'new:2'), [call])

    def test_schema_failure_is_not_repaired_in_either_parser(self):
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                req = self.req.model_copy(update={'user': str(legacy)})
                self.authorize(req)
                self.client.chat.side_effect = None
                self.client.chat.return_value = Reply(block([
                    {'name': 'read', 'arguments': {'wrong_path': 'next.txt'}}]), 'new:2')
                before = self.client.chat.call_count
                with patch.object(api, '_LEGACY', legacy), patch.object(api, '_FORMAT_REPAIR', True):
                    with self.assertRaises(api.ToolCallError):
                        self.run_request(req)
                self.assertEqual(self.client.chat.call_count, before + 1)

    def test_duplicate_authorization_restart_and_concurrent_claim_never_rearm(self):
        self.authorize()
        with self.assertRaisesRegex(ConversationStateError, 'cannot be rearmed'):
            self.authorize()
        results = []
        def contender():
            try:
                results.append(claim(type(self.cache)(self.cache.path), self.req, 'isolated'))
            except ConversationStateError:
                results.append('stopped')
        threads = [threading.Thread(target=contender) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(results), 2)
        self.assertEqual(results.count('stopped'), 1)
        with self.assertRaisesRegex(ConversationStateError, 'cannot be rearmed'):
            self.authorize()

    def test_expired_or_clock_rollback_ticket_does_not_dispatch(self):
        with patch('server.history_import.time.time', return_value=1000):
            self.authorize()
        for now in (999, 1600, 1700):
            with patch('server.history_import.time.time', return_value=now):
                with self.assertRaisesRegex(ConversationStateError, 'expired'):
                    claim(self.cache, self.req, 'isolated')

    def test_tampered_missing_or_public_archive_stops_before_consuming(self):
        proof = self.authorize()
        path = Path(proof['archive']); original = path.read_bytes()
        path.write_bytes(original + b' ')
        with self.assertRaisesRegex(ConversationStateError, 'unavailable or changed'):
            claim(self.cache, self.req, 'isolated')
        path.write_bytes(original)
        if os.name != 'nt':
            path.chmod(0o644)
            with self.assertRaises(ConversationStateError):
                claim(self.cache, self.req, 'isolated')
            path.chmod(0o600)
        path.unlink()
        with self.assertRaises(ConversationStateError):
            claim(self.cache, self.req, 'isolated')
        with self.cache.connection() as db:
            self.assertEqual(db.execute('SELECT consumed FROM history_imports').fetchone(), (0,))

    def test_existing_used_link_cannot_be_imported_to_bypass_replay_guard(self):
        base = self.req.model_copy(update={'messages': self.req.messages[:2]})
        self.cache.remember(base, Reply('', 'known:2'), '', self.req.messages[2].tool_calls, 3)
        self.cache.prepare(self.req, scope='isolated')
        self.authorize()
        with self.assertRaisesRegex(api.ToolCallError, 'already consumed'):
            self.run_request()
        self.client.chat.assert_not_called()

    def test_pause_prevents_provider_dispatch(self):
        self.authorize()
        with patch.object(api.guard, 'check', side_effect=ProviderRejected('account_restricted')):
            with self.assertRaises(ProviderRejected): self.run_request()
        self.client.chat.assert_not_called()

    def test_cancel_after_claim_never_rearms_ticket(self):
        self.authorize()
        with patch.object(api, '_check_cancelled', side_effect=[None, asyncio.CancelledError()]):
            with self.assertRaises(asyncio.CancelledError):
                self.run_request()
        self.client.chat.assert_not_called()
        with self.assertRaisesRegex(ConversationStateError, 'consumed'):
            claim(self.cache, self.req, 'isolated')

    def test_invalid_cli_request_does_not_print_private_values(self):
        file = self.cache.path.parent / 'bad.json'
        file.write_text('{"model":{"PRIVATE_SENTINEL":"secret"}}')
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaises(SystemExit) as error:
            main(['--request', str(file), '--archive-root', str(self.root),
                  '--source-provider', 'deepseek-official'])
        self.assertEqual(error.exception.code, 1)
        self.assertNotIn('PRIVATE_SENTINEL', output.getvalue())
        self.assertNotIn('secret', output.getvalue())

    def test_failed_dispatch_keeps_ticket_consumed_without_retries(self):
        self.authorize()
        def fail(*args):
            current_attempt('deepseek').dispatch(kind='generation')
            raise RuntimeError('Owned uncertain transport failure')
        self.client.chat.side_effect = fail
        with self.assertRaises(ProviderRejected): self.run_request()
        self.assertEqual(self.client.chat.call_count, 1)
        with self.cache.connection() as db:
            self.assertEqual(db.execute('SELECT consumed FROM history_imports').fetchone(), (1,))
        with self.assertRaises(ConversationStateError):
            claim(self.cache, self.req, 'isolated')
        self.assert_old_state()

    def test_malformed_reply_never_repairs_or_delivers_partial_calls(self):
        self.authorize()
        def malformed(*args):
            current_attempt('deepseek').dispatch(kind='generation')
            return Reply('```tool_calls\n[{"name":"read","arguments":', 'new:2', finish_reason='length')
        self.client.chat.side_effect = malformed
        with patch.object(api, '_MAX_CONTINUATIONS', 3), patch.object(api, '_FORMAT_REPAIR', True):
            with self.assertRaises(api.ToolCallError): self.run_request()
        self.assertEqual(self.client.chat.call_count, 1)
        with self.cache.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM links').fetchone(), (1,))
        self.assert_old_state()

    def test_unfinished_tool_history_and_oversized_instructions_fail_offline(self):
        with self.assertRaises(Exception):
            self.authorize(self.req.model_copy(update={'messages': self.req.messages[:-1]}))
        huge = self.req.model_copy(deep=True)
        huge.messages[0].content = 'X' * 150000
        with self.assertRaisesRegex(ConversationStateError, 'exceed prompt budget'):
            self.authorize(huge)
        self.client.chat.assert_not_called()

    def test_full_archive_and_completed_action_index_are_preserved(self):
        req = self.req.model_copy(deep=True)
        for i in range(500):
            call = {'id': f'done_{i}', 'type': 'function', 'function': {
                'name': 'read', 'arguments': json.dumps({'file_path': 'f' + str(i) + 'x' * 400})}}
            req.messages.extend([ChatMessage(role='assistant', tool_calls=[call]),
                                 ChatMessage(role='tool', tool_call_id=call['id'], content='x' * 500)])
        proof = self.authorize(req)
        archive = json.loads(Path(proof['archive']).read_text())
        self.assertEqual(archive['request'], req.model_dump(exclude_none=True))
        migrated, prompt = claim(self.cache, req, 'isolated')
        data = json.loads(migrated.messages[0].content.split('Checkpoint data:\n')[1])
        self.assertEqual(len(data['completed_result_refs']), 501)
        self.assertIn('Read the referenced archive context', prompt)
        self.assertLessEqual(len(prompt.encode()), 131072)


if __name__ == '__main__':
    unittest.main()
