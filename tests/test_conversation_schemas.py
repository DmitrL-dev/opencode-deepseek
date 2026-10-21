"""Catalog upgrades must consume the original durable continuation exactly once."""
import copy
import json
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import Mock, patch

from chat_protocol import Reply
from server.conversation_cache import ConversationCache, ConversationStateError
from server.conversation_schemas import register
from server.schemas import ChatCompletionRequest, ChatMessage


def catalog(description):
    return [{'type': 'function', 'function': {'name': 'read', 'description': description,
            'parameters': {'type': 'object', 'properties': {'file_path': {'type': 'string'}},
                           'required': ['file_path'], 'additionalProperties': False}}}]


class SchemaUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = ConversationCache(Path(self.temp.name) / 'links.sqlite3')
        self.scope = 'session-v2:owned-account'
        self.old = catalog('Old Harness description')
        self.current = catalog('New Harness description')
        self.current.append({'type': 'function', 'function': {'name': 'working_directory',
                            'parameters': {'type': 'object', 'properties': {}}}})
        self.initial = ChatCompletionRequest(model='deepseek-expert', tools=self.old,
                           messages=[ChatMessage(role='user', content='Explore fixture')])
        self.calls = [{'id': 'read_once', 'type': 'function', 'function': {
            'name': 'read', 'arguments': '{"file_path":"owned.txt"}'}}]
        self.cache.remember(self.initial, Reply('unused', 'owned:298'), '', self.calls, 199,
                            scope=self.scope)
        self.history = [*self.initial.messages, ChatMessage(role='assistant', tool_calls=self.calls),
                        ChatMessage(role='tool', tool_call_id='read_once', content='Completed read')]
        self.req = self.initial.model_copy(update={'tools': self.current, 'messages': self.history})

    def authorize(self, old=None, current=None, scope=None, model='deepseek-expert'):
        register(self.cache, scope=scope or self.scope, model=model,
                 old=old or self.old, current=current or self.current)

    def prepare(self, req=None, **kwargs):
        return self.cache.prepare(req or self.req, scope=self.scope,
                                  allow_local_rollover=True, **kwargs)

    def test_explicit_upgrade_claims_old_row_without_new_ticket_or_changed_request_schema(self):
        with self.assertRaises(ConversationStateError):
            self.prepare(claim={})
        self.authorize()
        claim = {}
        prepared, steps = self.prepare(claim=claim)
        self.assertEqual((prepared.conversation_id, steps), ('owned:298', 200))
        self.assertEqual(prepared.tools, self.current)
        self.assertEqual(claim['key'], self.cache.key(self.initial, self.history[:-1], self.scope))
        with self.cache.connection() as db:
            self.assertEqual(db.execute('SELECT used, steps, reservation FROM links').fetchall(),
                             [(1, 200, claim['reservation'])])
        restarted = ConversationCache(self.cache.path)
        for tools in (self.old, self.current):
            with self.assertRaises(ConversationStateError):
                restarted.prepare(self.req.model_copy(update={'tools': tools}), scope=self.scope,
                                  claim={}, allow_local_rollover=True)

    def test_authorization_does_not_change_existing_links_and_is_idempotent(self):
        with self.cache.connection() as db:
            before = db.execute('SELECT * FROM links').fetchall()
        self.authorize()
        self.authorize()
        with self.cache.connection() as db:
            self.assertEqual(db.execute('SELECT * FROM links').fetchall(), before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM schema_upgrades').fetchone(), (1,))

    def test_unknown_or_consumed_history_cannot_be_resurrected(self):
        self.cache.prepare(self.req.model_copy(update={'tools': self.old}), scope=self.scope,
                           claim={}, allow_local_rollover=True)
        self.authorize()
        for cache in (self.cache, ConversationCache(self.cache.path)):
            with self.assertRaises(ConversationStateError):
                cache.prepare(self.req, scope=self.scope, claim={}, allow_local_rollover=True)

    def test_scope_model_flags_messages_arguments_and_catalog_remain_exact(self):
        self.authorize()
        for update in ({'model': 'deepseek-chat'}, {'user': 'other'}, {'thinking': True},
                       {'search': True}, {'tools': catalog('Unregistered')},
                       {'messages': [ChatMessage(role='user', content='Edited'), *self.history[1:]]}):
            with self.subTest(update=tuple(update)), self.assertRaises(ConversationStateError):
                self.prepare(self.req.model_copy(update=update), claim={})
        changed = copy.deepcopy(self.history)
        changed[1].tool_calls[0]['function']['arguments'] = '{"file_path":"other.txt"}'
        with self.assertRaises(ConversationStateError):
            self.prepare(self.req.model_copy(update={'messages': changed}), claim={})
        with self.assertRaises(ConversationStateError):
            self.cache.prepare(self.req, scope='session-v2:other-account', claim={}, allow_local_rollover=True)

    def test_missing_duplicate_or_changed_results_still_rejected(self):
        self.authorize()
        for tail in ([], [self.history[-1], self.history[-1]],
                     [ChatMessage(role='tool', tool_call_id='other', content='result')]):
            with self.assertRaises(ConversationStateError):
                self.prepare(self.req.model_copy(update={'messages': [*self.history[:-1], *tail]}), claim={})

    def test_concurrent_old_and_new_catalogs_share_one_claim(self):
        self.authorize()
        def run(index):
            try:
                req = self.req if index % 2 else self.req.model_copy(update={'tools': self.old})
                self.prepare(req, claim={})
                return True
            except ConversationStateError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(run, range(16))), 1)

    def test_pre_dispatch_release_requires_exact_owner_and_keeps_reply_count(self):
        self.authorize()
        claim = {}
        self.prepare(claim=claim)
        self.cache.release({**claim, 'reservation': 'wrong-owner'})
        with self.assertRaises(ConversationStateError):
            self.prepare(claim={})
        self.cache.release(claim)
        self.assertEqual(self.prepare(claim={})[1], 200)

    def test_rollover_journal_and_limit_use_original_claim(self):
        self.authorize()
        self.cache.max_replies = 200
        with self.assertRaisesRegex(ConversationStateError, '200 accepted replies'):
            self.cache.prepare(self.req, scope=self.scope)
        claim = {}
        self.prepare(claim=claim)
        self.cache.claim_rollover(claim, 'owned:298', 'private-checkpoint')
        self.cache.release(claim)  # A proven pre-dispatch failure may release, never re-migrate.
        next_claim = {}
        self.prepare(claim=next_claim)
        with self.assertRaisesRegex(ConversationStateError, 'already attempted'):
            self.cache.claim_rollover(next_claim, 'owned:298', 'another-checkpoint')

    def test_new_reply_is_bound_to_current_schema_and_validated_against_it(self):
        from server.openai_format import ToolCallError
        from server import api
        from tests.test_tools import block
        self.authorize()
        prepared, steps = self.prepare(claim={})
        self.cache.remember(self.req, Reply('done', 'owned:300'), 'done', None, steps, scope=self.scope)
        next_req = self.req.model_copy(update={'messages': [*self.history,
            ChatMessage(role='assistant', content='done'), ChatMessage(role='user', content='Continue')]})
        self.assertEqual(self.prepare(next_req, claim={})[0].conversation_id, 'owned:300')
        with self.assertRaises(ToolCallError):
            api._validate_tool_reply(Reply(block([{'name': 'read', 'arguments': {}}]), 'owned:300'), prepared)

    def test_ambiguous_parents_including_used_or_exact_current_fail_closed(self):
        other = catalog('Third schema')
        self.authorize(old=other)
        self.authorize()
        for tools in (other, self.current):
            with self.subTest(catalog=tools[0]['function']['description']):
                self.cache.remember(self.initial.model_copy(update={'tools': tools}),
                                    Reply('unused', 'other:2'), '', self.calls, 3, scope=self.scope)
                if tools == other:
                    key = self.cache.key(self.initial.model_copy(update={'tools': tools}),
                                         self.history[:-1], self.scope)
                    with self.cache.connection() as db:
                        db.execute('UPDATE links SET used=1 WHERE key=?', (key,))
                with self.assertRaisesRegex(ConversationStateError, 'Ambiguous schema'):
                    self.prepare(claim={})

    def test_catalogs_are_directional_nontransitive_and_validated(self):
        third = catalog('Third schema')
        self.authorize()
        self.authorize(old=self.current, current=third)
        with self.assertRaises(ConversationStateError):
            self.prepare(self.req.model_copy(update={'tools': third}), claim={})
        for bad in ([], [self.old[0], self.old[0]], [{'type': 'function'}],
                    catalog('bad')[:-1] + [{'type': 'function', 'function': {
                        'name': 'bad', 'parameters': {'type': 'nonexistent'}}}]):
            from jsonschema.exceptions import SchemaError
            with self.assertRaises((ValueError, SchemaError)):
                register(self.cache, scope=self.scope, model=self.req.model,
                         old=bad, current=self.current)

    def test_corrupt_registration_fails_without_consuming_link(self):
        self.authorize()
        with self.cache.connection() as db:
            db.execute('UPDATE schema_upgrades SET catalog=?', (json.dumps(catalog('Tampered')),))
        with self.assertRaisesRegex(ConversationStateError, 'schema upgrade unavailable'):
            self.prepare(claim={})
        with self.cache.connection() as db:
            self.assertEqual(db.execute('SELECT used FROM links').fetchall(), [(0,)])

    def test_system_instruction_upgrade_requires_exact_explicit_pair(self):
        old_system = ChatMessage(role='system', content='Old Harness instruction')
        new_system = ChatMessage(role='system', content='New Harness instruction')
        original = self.initial.model_copy(update={'messages': [old_system, *self.initial.messages]})
        self.cache.remember(original, Reply('', 'with-system:298'), '', self.calls, 199, scope=self.scope)
        current = self.req.model_copy(update={'messages': [new_system, *self.history]})
        self.authorize()
        with self.assertRaises(ConversationStateError):
            self.prepare(current, claim={})
        register(self.cache, scope=self.scope, model=self.req.model, old=self.old, current=self.current,
                 old_system=old_system.content, current_system=new_system.content)
        edited = current.model_copy(update={'messages': [
            ChatMessage(role='system', content='Unregistered instruction'), *self.history]})
        with self.assertRaises(ConversationStateError):
            self.prepare(edited, claim={})
        claim = {}
        prepared, steps = self.prepare(current, claim=claim)
        self.assertEqual((prepared.conversation_id, steps), ('with-system:298', 200))
        self.assertEqual(prepared.tools, self.current)
        self.assertEqual(current.messages[0], new_system)
        old_request = current.model_copy(update={'tools': self.old,
            'messages': [old_system, *self.history]})
        with self.assertRaises(ConversationStateError):
            self.prepare(old_request, claim={})

    def test_incomplete_system_pairs_cannot_authorize_changes(self):
        for kwargs in ({'old_system': 'old'}, {'current_system': 'new'},
                       {'old_system': '', 'current_system': 'new'},
                       {'old_system': {}, 'current_system': 'new'}):
            with self.assertRaises(ValueError):
                register(self.cache, scope=self.scope, model=self.req.model,
                         old=self.old, current=self.current, **kwargs)

    def test_uncertain_dispatch_remains_consumed_across_catalogs(self):
        from server import api
        from providers.access import AccessGuard, ProviderRejected
        from tests.support import isolated_access
        import httpx
        isolated_access(self)
        self.authorize()
        self.cache.max_replies = 1000
        guard = AccessGuard(Path(self.temp.name) / 'pauses.json', interval=0)
        client = Mock()
        def uncertain(*args):
            from providers.access import current_attempt
            current_attempt('deepseek').dispatch()
            raise httpx.ReadTimeout('Owned offline uncertain response')
        client.chat.side_effect = uncertain
        with patch.object(api, 'conversation_cache', self.cache), patch.object(api, 'guard', guard), \
                patch.object(api, '_conversation_scope', return_value=self.scope), \
                patch.object(api, 'get_client', return_value=client):
            with self.assertRaises(ProviderRejected):
                api._conversation_tool_chat('fixture', self.req, None)
            self.assertEqual(client.chat.call_count, 1)
        with self.assertRaisesRegex(ConversationStateError, 'already consumed'):
            self.prepare(claim={})
        with self.cache.connection() as db:
            self.assertEqual(db.execute('SELECT used, steps FROM links').fetchall(), [(1, 200)])

    def test_cli_reads_local_session_without_network_or_secret_output(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from server.conversation_schemas import main
        root = Path(self.temp.name)
        session = root / 'session.json'
        session.write_text(json.dumps({'token': 'PRIVATE_OFFLINE_TOKEN', 'cookies': [],
            'captured_at': 0, 'user_agent': 'fixture', 'continuation_scope': 'a' * 64}))
        catalogs = root / 'catalogs.json'
        catalogs.write_text(json.dumps({'old': self.old, 'current': self.current}))
        before = session.read_bytes()
        output = StringIO()
        with patch('sys.argv', ['conversation_schemas', '--catalogs', str(catalogs),
                    '--model', self.req.model, '--database', str(self.cache.path),
                    '--session-file', str(session)]), redirect_stdout(output), \
                patch('socket.socket.connect', side_effect=AssertionError('No network allowed')):
            main()
        self.assertNotIn('PRIVATE_OFFLINE_TOKEN', output.getvalue())
        self.assertEqual(session.read_bytes(), before)
        with self.cache.connection() as db:
            self.assertEqual(db.execute('SELECT used, steps FROM links').fetchall(), [(0, 200)])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM schema_upgrades').fetchone(), (1,))
