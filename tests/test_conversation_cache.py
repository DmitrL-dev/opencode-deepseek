import json
from pathlib import Path
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import httpx
import unittest
from unittest.mock import Mock, patch

from chat_protocol import Reply
from providers.access import AccessGuard, ProviderRejected
from server import api
from server.conversation_cache import ConversationCache, ConversationStateError
from server.schemas import ChatCompletionRequest, ChatMessage
from tests.support import isolated_access
from tests.test_tools import block, FLAT_TOOLS


class ConversationTests(unittest.TestCase):
    def setUp(self):
        isolated_access(self)
        self.cache = api.conversation_cache
        self.req = ChatCompletionRequest(model='deepseek-chat', tools=FLAT_TOOLS,
            messages=[ChatMessage(role='user', content='Explore owned fixture')])
        self.calls = [{'id': 'call_owned', 'type': 'function', 'function': {
            'name': 'read', 'arguments': '{"file_path":"fixture"}'}}]
        self.reply = Reply('unused', 'owned:2')

    def history(self):
        return [*self.req.messages, ChatMessage(role='assistant', tool_calls=self.calls),
                ChatMessage(role='tool', tool_call_id='call_owned', content='fixture result')]

    def test_restart_reuses_chat_and_only_new_messages_once(self):
        self.cache.remember(self.req, self.reply, '', self.calls, 0)
        req = self.req.model_copy(update={'messages': self.history()})
        restarted = ConversationCache(self.cache.path)
        continuation, steps = restarted.prepare(req)
        self.assertEqual(continuation.conversation_id, 'owned:2')
        self.assertEqual([m.role for m in continuation.messages], ['assistant', 'tool'])
        self.assertEqual(steps, 1)
        with self.assertRaises(ConversationStateError):
            restarted.prepare(req)
        self.assertNotIn('fixture result', self.cache.path.read_bytes().decode(errors='ignore'))
        self.assertEqual(self.cache.path.stat().st_mode & 0o777, 0o600)

    def test_unknown_changed_scope_and_edited_histories_never_start_chat(self):
        self.cache.remember(self.req, self.reply, '', self.calls, 0)
        for update in ({'model': 'deepseek-expert'}, {'user': 'other'}, {'tools': []},
                       {'thinking': True}, {'search': True}):
            req = self.req.model_copy(update={'messages': self.history(), **update})
            with self.assertRaises(ConversationStateError):
                self.cache.prepare(req)
        changed = self.history(); changed[0] = ChatMessage(role='user', content='other project')
        with self.assertRaises(ConversationStateError):
            self.cache.prepare(self.req.model_copy(update={'messages': changed}))

    def test_corrupt_state_fails_closed(self):
        self.cache.path.write_bytes(b'not sqlite')
        with self.assertRaises(ConversationStateError):
            self.cache.prepare(self.req.model_copy(update={'messages': self.history()}))

    def test_tool_round_trip_sends_delta_to_same_provider_chat(self):
        fake = Mock()
        fake.chat.side_effect = [Reply(block([{'name':'read','arguments':{'file_path':'fixture'}}]), 'owned:2'),
                                 Reply('Exploration complete', 'owned:4')]
        with patch.object(api, 'get_client', return_value=fake):
            first = api._conversation_tool_chat('initial fixture context', self.req, 'default')
            history = [*self.req.messages, ChatMessage(role='assistant', content=first[1], tool_calls=first[2]),
                       ChatMessage(role='tool', tool_call_id=first[2][0]['id'], content='fixture result')]
            req = self.req.model_copy(update={'messages': history})
            second = api._conversation_tool_chat('must not resend this history', req, 'default')
            self.assertEqual(second[1], 'Exploration complete')
            args = fake.chat.call_args_list[1].args
            self.assertEqual(args[1:3], ('owned:2', None))
            self.assertIn('fixture result', args[0])
            self.assertNotIn('Explore owned fixture', args[0])
            with self.assertRaises(api.ToolCallError):
                api._conversation_tool_chat('duplicate', req, 'default')
        self.assertEqual(fake.chat.call_count, 2)

    def test_conversation_step_cap(self):
        self.cache.remember(self.req, self.reply, '', self.calls, 19)
        with self.assertRaises(ConversationStateError):
            self.cache.prepare(self.req.model_copy(update={'messages': self.history()}))

    def test_ambiguous_identical_histories_are_not_merged(self):
        self.cache.remember(self.req, self.reply, '', self.calls, 0)
        self.cache.remember(self.req, Reply('', 'another:2'), '', self.calls, 0)
        with self.assertRaises(ConversationStateError):
            self.cache.prepare(self.req.model_copy(update={'messages': self.history()}))

    def test_transport_creates_one_chat_for_two_agent_steps(self):
        from deepseek.client import DeepSeekClient
        from tests.test_stream import snapshot
        paths, bodies = [], []
        def handler(request):
            paths.append(request.url.path)
            if request.url.path.endswith('/create'):
                return httpx.Response(200, json={'code': 0, 'data': {'biz_code': 0, 'biz_data': {'id': 'owned'}}})
            if request.url.path.endswith('/create_pow_challenge'):
                return httpx.Response(200, json={'code': 0, 'data': {'biz_code': 0, 'biz_data': {'challenge': {}}}})
            bodies.append(json.loads(request.content))
            answer = block([{'name': 'read', 'arguments': {'file_path': 'fixture'}}]) if len(bodies) == 1 else 'Done'
            return httpx.Response(200, text=snapshot(answer, 'FINISHED').replace('\"message_id\": 2', '\"message_id\": ' + str(len(bodies) * 2)), headers={'content-type':'text/event-stream'})
        client = DeepSeekClient.__new__(DeepSeekClient)
        client.access_guard = None
        client._request_lock, client._pow_lock = threading.Lock(), threading.Lock()
        client._pow = Mock(); client._pow.make_header.return_value = 'owned-fixture'
        client._http = httpx.Client(base_url='https://offline.invalid', transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        with patch.object(api, 'get_client', return_value=client):
            first = api._conversation_tool_chat('initial context', self.req, 'default')
            req = self.req.model_copy(update={'messages': [*self.req.messages,
                ChatMessage(role='assistant', content=first[1], tool_calls=first[2]),
                ChatMessage(role='tool', tool_call_id=first[2][0]['id'], content='owned result')]})
            api._conversation_tool_chat('unused full history', req, 'default')
        self.assertEqual(sum(path.endswith('/create') for path in paths), 1)
        self.assertEqual(len(paths), 5)
        self.assertEqual(bodies[1]['chat_session_id'], bodies[0]['chat_session_id'])
        self.assertEqual(bodies[1]['parent_message_id'], 2)
        self.assertNotIn('initial context', bodies[1]['prompt'])
        self.assertEqual([k for _, k in api.guard._memory['deepseek']['usage']],
                         ['chat', 'http', 'generation', 'http', 'generation'])

    def test_simultaneous_continuations_cannot_claim_one_parent_twice(self):
        self.cache.remember(self.req, self.reply, '', self.calls, 0)
        req = self.req.model_copy(update={'messages': self.history()})
        def claim():
            try:
                return ConversationCache(self.cache.path).prepare(req)[0].conversation_id
            except ConversationStateError:
                return None
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: claim(), range(2)))
        self.assertEqual(results.count('owned:2'), 1)
        self.assertEqual(results.count(None), 1)

    def test_account_change_does_not_reuse_previous_chat(self):
        account = self.cache.path.parent / 'synthetic-account.json'
        account.write_text('synthetic account A')
        cache = ConversationCache(self.cache.path, account)
        cache.remember(self.req, self.reply, '', self.calls, 0)
        account.write_text('synthetic account B')
        with self.assertRaises(ConversationStateError):
            cache.prepare(self.req.model_copy(update={'messages': self.history()}))

    def test_oversized_input_does_not_build_client(self):
        with patch.object(api, 'get_client') as factory, self.assertRaises(ProviderRejected):
            api._chat_with_retry('x' * 131073, self.req, 'default')
        factory.assert_not_called()



class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'guard.json'

    def charge(self, guard, kind):
        with guard.attempt('deepseek') as attempt:
            attempt.dispatch(kind=kind)

    def test_chat_budget_survives_restart_resume_and_expires(self):
        with patch('providers.access.time.time', return_value=100000):
            guard = AccessGuard(self.path, interval=0)
            for _ in range(3):
                self.charge(guard, 'chat')
            guard = AccessGuard(self.path, interval=0)
            guard.resume('deepseek')
            with self.assertRaises(ProviderRejected) as error:
                self.charge(guard, 'chat')
            self.assertEqual(error.exception.code, 'local_budget_exceeded')
            self.assertEqual(guard.status(), {})
        with patch('providers.access.time.time', return_value=103601):
            for _ in range(3):
                self.charge(guard, 'chat')
        with patch('providers.access.time.time', return_value=107202):
            with self.assertRaises(ProviderRejected):
                self.charge(guard, 'chat')
        with patch('providers.access.time.time', return_value=190000):
            self.charge(guard, 'chat')

    def test_generation_cap_and_all_http_dispatches_charged(self):
        guard = AccessGuard(self.path, interval=0)
        for _ in range(12):
            self.charge(guard, 'generation')
        with self.assertRaises(ProviderRejected) as error:
            self.charge(guard, 'generation')
        self.assertEqual(error.exception.code, 'local_budget_exceeded')
        with guard.attempt('deepseek') as attempt:
            attempt.dispatch(kind='chat')
            attempt.dispatch()
        self.assertEqual(len(json.loads(self.path.read_text())['deepseek']['usage']), 14)

    def test_failed_calls_are_not_refunded(self):
        guard = AccessGuard(self.path, interval=0)
        with self.assertRaises(RuntimeError):
            with guard.attempt('deepseek') as attempt:
                attempt.dispatch(kind='chat')
                raise RuntimeError('offline failure')
        guard.resume('deepseek')
        self.assertEqual(len(json.loads(self.path.read_text())['deepseek']['usage']), 1)

    def test_malformed_usage_fails_closed(self):
        self.path.write_text('{"deepseek":{"usage":[[0,"unknown"]]}}')
        with self.assertRaises(ProviderRejected):
            AccessGuard(self.path, interval=0).check('deepseek')

    def test_preflight_rejects_before_wasting_chat_or_challenge(self):
        guard = AccessGuard(self.path, interval=0)
        for _ in range(12):
            self.charge(guard, 'generation')
        with self.assertRaises(ProviderRejected):
            with guard.attempt('deepseek') as attempt:
                attempt.preflight_generation(True)
        self.assertEqual(len(json.loads(self.path.read_text())['deepseek']['usage']), 12)
        self.assertEqual(guard.status(), {})

    def test_http_budget_preflight_requires_room_for_whole_sequence(self):
        guard = AccessGuard(self.path, interval=0)
        for _ in range(39):
            self.charge(guard, 'http')
        with self.assertRaises(ProviderRejected):
            with guard.attempt('deepseek') as attempt:
                attempt.preflight_generation(False)
        self.assertEqual(len(json.loads(self.path.read_text())['deepseek']['usage']), 39)

    def test_http_pacing_is_persisted_inside_one_operation(self):
        guard = AccessGuard(self.path, interval=30)
        with patch('providers.access.time.time', return_value=100000):
            with guard.attempt('deepseek') as attempt:
                attempt.dispatch(kind='chat')
                record = json.loads(self.path.read_text())['deepseek']
                self.assertEqual(record['next_at'], 100030)
                self.assertEqual(record['next_http_at'], 100003)
                with patch('providers.access.time.time', return_value=100003):
                    attempt.dispatch()
                self.assertEqual(json.loads(self.path.read_text())['deepseek']['next_http_at'], 100006)
