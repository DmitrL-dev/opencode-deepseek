import json
import copy
import asyncio
import os
import sqlite3
import unittest
import httpx
from unittest.mock import Mock, patch

from chat_protocol import Reply
from deepseek.client import _parse_sse
from providers.access import ProviderRejected, current_attempt
from server import api
from server.context_rollover import checkpoint
from server.schemas import ChatCompletionRequest, ChatMessage
from tests.support import isolated_access
from tests.test_stream import event, snapshot


class ContextRolloverTests(unittest.TestCase):
    def setUp(self):
        isolated_access(self)
        self.base = ChatCompletionRequest(model='deepseek-chat', messages=[
            ChatMessage(role='user', content='Investigate fixture. Preserve existing changes.')])
        self.call = {'id': 'done_1', 'type': 'function', 'function': {
            'name': 'read', 'arguments': '{"file_path":"fixture.py"}'}}
        api.conversation_cache.remember(self.base, Reply('', 'old:2'), '', [self.call], 7)
        self.req = self.base.model_copy(update={'messages': [*self.base.messages,
            ChatMessage(role='assistant', tool_calls=[self.call]),
            ChatMessage(role='tool', tool_call_id='done_1', content='observed result')]})
        self.client = Mock()
        self.enabled = patch.dict(os.environ, {'DEEPSEEK_CONTEXT_ROLLOVER': '1'})
        self.enabled.start()
        self.addCleanup(self.enabled.stop)

    def response(self, prompt, cid, model, thinking, search):
        attempt = current_attempt('deepseek')
        attempt.dispatch(kind='generation')
        if cid == 'old:2':
            attempt.empty_context_refusal = True
            raise ProviderRejected('conversation_context_exceeded')
        self.assertIsNone(cid)
        self.assertIn('done_1', prompt)
        self.assertIn('Investigate fixture', prompt)
        self.assertIn('Do not repeat completed actions', prompt)
        return Reply('Continue with the next unperformed step.', 'new:2')

    def run_request(self):
        with patch.object(api, 'get_client', return_value=self.client):
            return api._conversation_tool_chat('unused full prompt', self.req, 'default')

    def capped(self):
        api.conversation_cache.max_replies = 200
        with api.conversation_cache.connection() as db:
            db.execute('UPDATE links SET steps=200')

    def test_local_cap_migrates_once_without_sending_to_old_chat(self):
        self.capped()
        prior = api.guard.begin('deepseek')
        prior.dispatch(kind='generation')
        prior.complete()
        before = copy.deepcopy(api.guard._memory['deepseek']['usage'])
        def answer(prompt, cid, *args):
            self.assertIsNone(cid)
            self.assertIn('next step has NOT been sent', prompt)
            self.assertIn('done_1', prompt)
            self.assertIn('observed result', prompt)
            self.assertIn('Do not repeat completed actions', prompt)
            current_attempt('deepseek').dispatch(kind='generation')
            return Reply('Next unperformed step.', 'new:2')
        self.client.chat.side_effect = answer
        result = self.run_request()
        self.assertEqual(self.client.chat.call_count, 1)
        self.assertEqual(api.guard._memory['deepseek']['usage'][:-1], before)
        with api.conversation_cache.connection() as db:
            self.assertEqual(db.execute('SELECT used, steps FROM links WHERE cid=?', ('old:2',)).fetchone(), (1, 200))
            self.assertEqual(db.execute('SELECT used, steps FROM links WHERE cid=?', ('new:2',)).fetchone(), (0, 1))
        archive = next((api.conversation_cache.path.parent / 'context-checkpoints').glob('*.json'))
        self.assertEqual(json.loads(archive.read_text())['request'], self.req.model_dump(exclude_none=True))
        with self.assertRaises(api.ToolCallError):
            self.run_request()
        self.assertEqual(self.client.chat.call_count, 1)
        following = self.req.model_copy(update={'messages': [*self.req.messages,
            ChatMessage(role='assistant', content=result[1]), ChatMessage(role='user', content='Continue')]})
        prepared, steps = api.conversation_cache.prepare(following)
        self.assertEqual((prepared.conversation_id, steps), ('new:2', 1))

    def test_local_cap_disabled_does_not_dispatch_or_consume(self):
        self.capped()
        with patch.dict(os.environ, {'DEEPSEEK_CONTEXT_ROLLOVER': '0'}):
            with self.assertRaises(api.ToolCallError):
                self.run_request()
        self.client.chat.assert_not_called()
        with api.conversation_cache.connection() as db:
            self.assertEqual(db.execute('SELECT used, steps FROM links').fetchone(), (0, 200))

    def test_local_cap_new_refusal_does_not_trigger_another_rollover(self):
        self.capped()
        def refuse(*args):
            attempt = current_attempt('deepseek')
            attempt.dispatch(kind='generation')
            attempt.empty_context_refusal = True
            raise ProviderRejected('conversation_context_exceeded')
        self.client.chat.side_effect = refuse
        with self.assertRaises(ProviderRejected):
            self.run_request()
        with self.assertRaises(ProviderRejected):
            self.run_request()
        self.assertEqual(self.client.chat.call_count, 1)

    def test_below_local_cap_continues_existing_chat(self):
        self.capped()
        with api.conversation_cache.connection() as db:
            db.execute('UPDATE links SET steps=199')
        def answer(prompt, cid, *args):
            self.assertEqual(cid, 'old:2')
            current_attempt('deepseek').dispatch(kind='generation')
            return Reply('Next step.', 'old:4')
        self.client.chat.side_effect = answer
        self.run_request()
        self.assertEqual(self.client.chat.call_count, 1)
        self.assertFalse((api.conversation_cache.path.parent / 'context-checkpoints').exists())

    def test_local_cap_truncated_tools_never_trigger_repair(self):
        from tests.test_tools import FLAT_TOOLS
        self.req = self.req.model_copy(update={'tools': FLAT_TOOLS})
        base = self.base.model_copy(update={'tools': FLAT_TOOLS})
        api.conversation_cache.remember(base, Reply('', 'old:2'), '', [self.call], 199)
        self.capped()
        def answer(prompt, cid, *args):
            self.assertIsNone(cid)
            current_attempt('deepseek').dispatch(kind='generation')
            return Reply('```tool_calls\n[{"name":"read","arguments":', 'new:2', finish_reason='length')
        self.client.chat.side_effect = answer
        with patch.object(api, '_MAX_CONTINUATIONS', 3), patch.object(api, '_FORMAT_REPAIR', True):
            with self.assertRaises(api.ToolCallError):
                self.run_request()
        self.assertEqual(self.client.chat.call_count, 1)

    def test_local_cap_checkpoint_failure_releases_without_dispatch(self):
        self.capped()
        with patch('server.context_rollover.checkpoint', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.run_request()
        self.client.chat.assert_not_called()
        with api.conversation_cache.connection() as db:
            self.assertEqual(db.execute('SELECT used, steps FROM links').fetchone(), (0, 200))

    def test_one_migration_preserves_history_steps_and_usage(self):
        self.client.chat.side_effect = self.response
        result = self.run_request()
        self.assertEqual(result[0].conversation_id, 'new:2')
        self.assertEqual(self.client.chat.call_count, 2)
        usage = api.guard._memory['deepseek']['usage']
        self.assertEqual([entry[1] for entry in usage], ['generation', 'generation'])
        archives = list((api.conversation_cache.path.parent / 'context-checkpoints').glob('*.json'))
        self.assertEqual(len(archives), 1)
        self.assertEqual(json.loads(archives[0].read_bytes())['request']['messages'],
                         [m.model_dump(exclude_none=True) for m in self.req.messages])
        if os.name != 'nt':
            self.assertEqual(archives[0].stat().st_mode & 0o777, 0o600)
        next_request = self.req.model_copy(update={'messages': [*self.req.messages,
            ChatMessage(role='assistant', content=result[1]), ChatMessage(role='user', content='Continue')]})
        next_req, steps = api.conversation_cache.prepare(next_request)
        self.assertEqual((next_req.conversation_id, steps), ('new:2', 9))
        with self.assertRaises(api.ToolCallError):
            self.run_request()
        self.assertEqual(self.client.chat.call_count, 2)
        with sqlite3.connect(api.conversation_cache.path) as db:
            self.assertEqual(db.execute('SELECT cid, used, steps FROM links WHERE cid=?', ('old:2',)).fetchone(),
                             ('old:2', 1, 8))

    def test_new_failure_never_starts_a_third_request(self):
        def reject(prompt, cid, *args):
            if cid:
                return self.response(prompt, cid, *args)
            current_attempt('deepseek').dispatch(kind='generation')
            raise ProviderRejected('upstream_failure')
        self.client.chat.side_effect = reject
        with self.assertRaises(ProviderRejected):
            self.run_request()
        with self.assertRaises(ProviderRejected):
            self.run_request()
        self.assertEqual(self.client.chat.call_count, 2)
        self.assertEqual(api.guard.status(), {'deepseek': 'upstream_failure'})

    def test_no_migration_without_exact_empty_refusal(self):
        for code in ('upstream_failure', 'session_expired', 'conversation_context_exceeded'):
            with self.subTest(code=code):
                # Each subcase uses independent state via a temporary test instance.
                case = ContextRolloverTests()
                case.setUp()
                try:
                    def reject(*args):
                        current_attempt('deepseek').dispatch(kind='generation')
                        raise ProviderRejected(code)
                    case.client.chat.side_effect = reject
                    with self.assertRaises(ProviderRejected):
                        case.run_request()
                    self.assertEqual(case.client.chat.call_count, 1)
                    self.assertEqual(api.guard.status(), {'deepseek': code})
                finally:
                    case.doCleanups()

    def test_checkpoint_failure_keeps_refusal_and_budget(self):
        self.client.chat.side_effect = self.response
        with patch('server.context_rollover.checkpoint', side_effect=OSError('fixture disk full')):
            with self.assertRaises(OSError):
                self.run_request()
        self.assertEqual(self.client.chat.call_count, 1)
        self.assertEqual(api.guard.status(), {'deepseek': 'conversation_context_exceeded'})
        self.assertEqual(len(api.guard._memory['deepseek']['usage']), 1)

    def test_disabled_mode_retains_manual_stop(self):
        self.client.chat.side_effect = self.response
        with patch.dict(os.environ, {'DEEPSEEK_CONTEXT_ROLLOVER': '0'}):
            with self.assertRaises(ProviderRejected):
                self.run_request()
        self.assertEqual(self.client.chat.call_count, 1)
        self.assertEqual(api.guard.status(), {'deepseek': 'conversation_context_exceeded'})

    def test_harness_sse_receives_new_reply_without_failed_turn(self):
        self.client.chat.side_effect = self.response
        async def request():
            with patch.object(api, '_request_gate', asyncio.Lock()), \
                    patch.object(api, 'get_client', return_value=self.client):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(
                        app=api.app, client=(self.id(), 123)), base_url='http://fixture.local') as http:
                    return await http.post('/v1/chat/completions', json=self.req.model_copy(
                        update={'stream': True}).model_dump(exclude_none=True))
        response = asyncio.run(request())
        self.assertEqual(response.status_code, 200)
        self.assertIn('data: [DONE]', response.text)
        self.assertIn('Continue with the next unperformed step.', response.text)
        self.assertNotIn('"error"', response.text)
        self.assertEqual(self.client.chat.call_count, 2)

    def test_retirement_changes_only_ownership_and_context_pause(self):
        attempt = api.guard.begin('deepseek')
        attempt.dispatch(kind='generation')
        attempt.empty_context_refusal = True
        attempt.abort(ProviderRejected('conversation_context_exceeded'))
        before = copy.deepcopy(api.guard._memory['deepseek'])
        api.guard.retire_context_refusal(attempt)
        for field in ('pending', 'dispatched', 'pause'):
            before.pop(field)
        self.assertEqual(api.guard._memory['deepseek'], before)
        with self.assertRaises(ProviderRejected):
            api.guard.retire_context_refusal(attempt)

    def test_new_truncated_tool_reply_does_not_use_enabled_continuations(self):
        from tests.test_tools import FLAT_TOOLS
        api.conversation_cache.remember(self.base.model_copy(update={'tools': FLAT_TOOLS}),
                                       Reply('', 'old:2'), '', [self.call], 7)
        self.req = self.req.model_copy(update={'tools': FLAT_TOOLS})
        def truncated(prompt, cid, *args):
            if cid:
                return self.response(prompt, cid, *args)
            current_attempt('deepseek').dispatch(kind='generation')
            return Reply('```tool_calls\n[{"name":"read","arguments":', 'new:2', finish_reason='length')
        self.client.chat.side_effect = truncated
        with patch.object(api, '_MAX_CONTINUATIONS', 3), patch.object(api, '_FORMAT_REPAIR', True):
            with self.assertRaises(api.ToolCallError):
                self.run_request()
        self.assertEqual(self.client.chat.call_count, 2)

    def test_real_stream_refusal_records_proof_on_owned_attempt(self):
        from tests.test_stream import mock_client
        client = mock_client(event({'type': 'error', 'clear_response': True,
                                   'finish_reason': 'context_length_exceeded'}, 'hint'))
        self.addCleanup(client.close)
        attempt = api.guard.begin('deepseek')
        with attempt.bind(), self.assertRaises(ProviderRejected):
            client.chat('fixture', conversation_id='old:2')
        self.assertTrue(attempt.empty_context_refusal)
        self.assertEqual(api.guard.status(), {'deepseek': 'conversation_context_exceeded'})

    def test_parser_only_proves_empty_cleared_context_refusal(self):
        hint = event({'type': 'error', 'clear_response': True,
                      'finish_reason': 'context_length_exceeded'}, 'hint')
        for prefix, safe in [('', True), (snapshot('partial'), False), (snapshot(status='FINISHED'), False)]:
            with self.subTest(safe=safe), self.assertRaises(ProviderRejected) as error:
                list(_parse_sse((prefix + hint).splitlines()))
            self.assertEqual(error.exception.empty_context_refusal, safe)
        with self.assertRaises(ProviderRejected) as error:
            list(_parse_sse(event({'type': 'error', 'finish_reason': 'context_length_exceeded'}, 'hint').splitlines()))
        self.assertFalse(error.exception.empty_context_refusal)

    def test_missing_tool_result_and_unrepresentable_instructions_refuse_checkpoint(self):
        root = api.conversation_cache.path.parent / 'checkpoints'
        for messages in (self.req.messages[:-1], [ChatMessage(role='user', content='x' * 200000)]):
            with self.subTest(length=len(messages)), self.assertRaises((ValueError, RuntimeError)):
                checkpoint(self.req.model_copy(update={'messages': messages}), 'old:2', root)
        self.assertFalse(root.exists())


class LargeCheckpointTests(unittest.TestCase):
    def request(self, size=45000, results=120):
        instruction = ChatMessage(role='user', content='Точная инструкция 😀 ' + 'x' * size)
        messages = [ChatMessage(role='system', content='Preserve restrictions.'), instruction]
        for i in range(results):
            call = {'id': f'call_{i}', 'type': 'function', 'function': {
                'name': 'read', 'arguments': json.dumps({'file_path': f'file-{i}.txt'})}}
            messages.extend([ChatMessage(role='assistant', tool_calls=[call]),
                ChatMessage(role='tool', tool_call_id=call['id'], content=('FAILED ' if i % 2 else 'OK ') + 'result' * 200)])
        messages.append(instruction.model_copy())
        return ChatCompletionRequest(model='deepseek-chat', messages=messages, tools=[{
            'type': 'function', 'function': {'name': 'read', 'parameters': {
                'type': 'object', 'properties': {'file_path': {'type': 'string'}}, 'required': ['file_path']}}}])

    def test_large_packet_restores_instructions_and_indexes_every_exact_result(self):
        import tempfile
        from pathlib import Path
        q = self.request()
        with tempfile.TemporaryDirectory() as tmp:
            text, filename = checkpoint(q, 'old:400', Path(tmp) / 'archives', max_text_bytes=60000)
            data = json.loads(text.split('Checkpoint data:\n', 1)[1])
            restored = [data['instruction_dictionary'][i] for i in data['instruction_sequence']]
            self.assertEqual(restored, [m.model_dump(exclude_none=True) for m in q.messages
                                       if m.role in ('system', 'developer', 'user')])
            saved = json.loads(Path(filename).read_text())
            self.assertEqual(saved['request'], q.model_dump(exclude_none=True))
            messages = saved['request']['messages']
            self.assertEqual(len(data['completed_result_refs']), 120)
            for result, assistant, ordinal in data['completed_result_refs']:
                self.assertEqual(messages[result]['tool_call_id'], messages[assistant]['tool_calls'][ordinal]['id'])
                self.assertEqual(messages[result], q.messages[result].model_dump(exclude_none=True))
            self.assertLessEqual(len(text.encode()), 60000)
            self.assertIn('If the archive cannot be read, stop', text)
            self.assertIn('Do not rerun any action', text)
            if os.name != 'nt':
                self.assertEqual(Path(filename).stat().st_mode & 0o777, 0o600)

    def test_unique_instructions_above_32k_are_preserved_inline(self):
        import tempfile
        from pathlib import Path
        q = self.request(size=40000, results=0)
        with tempfile.TemporaryDirectory() as tmp:
            text, _ = checkpoint(q, 'old:400', Path(tmp) / 'archive')
            data = json.loads(text.split('Checkpoint data:\n', 1)[1])
            self.assertEqual(data['instructions'][1]['content'], q.messages[1].content)

    def test_insufficient_budget_or_no_read_tool_refuses_before_writing(self):
        import tempfile
        from pathlib import Path
        q = self.request()
        for request, budget in [(q, 1000), (q.model_copy(update={'tools': None}), 60000),
                                (q.model_copy(update={'tool_choice': 'none'}), 60000)]:
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'archive'
                with self.assertRaises(RuntimeError):
                    checkpoint(request, 'old:400', root, max_text_bytes=budget)
                self.assertFalse(root.exists())
