"""Private, durable one-use continuation links for stateless OpenAI clients.

Only hashes of the exact delivered history are retained, never source text.
Keep the original digest format, including system instructions and assistant
commentary, so existing links retain their exact one-use ownership.
Unknown or already consumed histories fail closed instead of creating chats.
"""
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from settings import ROOT
from .config import canonical_model, model_provider
from .schemas import ChatMessage
from .openai_format import _text_of
from .recovery_status import RECOVERY_STOP_TEXT
from . import conversation_schemas
from .tool_notice import notice_of, provider_tool_error_followup
from .toolcall_logging import (enabled as toolcall_logging_enabled,
                               new_chat as new_toolcall_log,
                               record as record_toolcall_log)


class ConversationStateError(RuntimeError):
    pass


def normalized(message):
    value = message.model_dump(exclude_none=True) if hasattr(message, 'model_dump') else dict(message)
    content = value.get('content')
    if isinstance(content, list) and all(p.get('type') == 'text' for p in content):
        content = _text_of(content)
    value['content'] = content or ''
    if not value.get('tool_calls'):
        value.pop('tool_calls', None)
    for call in value.get('tool_calls', []):
        fn = call.get('function', {})
        if isinstance(fn.get('arguments'), str):
            try:
                fn['arguments'] = json.loads(fn['arguments'])
            except ValueError:
                pass
    return value


def _digest_payload(req, messages):
    """The original persistent digest contract; never silently omit history."""
    model = canonical_model(req.model) or req.model
    stable = [normalized(m) for m in messages]
    return [model, req.user, req.thinking, req.search, req.tools, stable]


def digest_payload_json(req, messages):
    """Exact canonical JSON input to digest(), shared with safe diagnostic logging."""
    return json.dumps(_digest_payload(req, messages), sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'))


def digest(req, messages):
    return hashlib.sha256(digest_payload_json(req, messages).encode()).hexdigest()


class ConversationCache:
    def __init__(self, path, account_file=None):
        raw = os.getenv('CONVERSATION_MAX_REPLIES', '20')
        if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 1000:
            raise ValueError('CONVERSATION_MAX_REPLIES must be an integer between 1 and 1000')
        self.max_replies = int(raw)
        self.path = Path(path)
        self.account_file = Path(account_file) if account_file is not None else None

    def clear_used_links_on_startup(self):
        raw = os.getenv('CONVERSATION_CLEAR_USED_ON_STARTUP', '0').strip()
        if raw not in ('0', '1'):
            raise ValueError('CONVERSATION_CLEAR_USED_ON_STARTUP must be 0 or 1')
        if raw == '0':
            return 0
        with self.connection() as db:
            # This deliberately permits repeating an initial prompt in a new chat,
            # but identical histories can then be matched to the wrong upstream chat.
            cursor = db.execute('DELETE FROM links WHERE used=1')
            return cursor.rowcount

    def account_identity(self):
        scope = 'isolated'
        if self.account_file is not None:
            try:
                scope = hashlib.sha256(self.account_file.read_bytes()).hexdigest()
            except OSError as exc:
                raise ConversationStateError('Local account identity unavailable; no request sent.') from exc
        return scope

    def key(self, req, messages, scope=None):
        identity = self.account_identity() if scope is None else scope
        return hashlib.sha256((identity + digest(req, messages)).encode()).hexdigest()

    @contextmanager
    def connection(self):
        db = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            os.chmod(self.path, 0o600)
            db = sqlite3.connect(str(self.path), timeout=5)
            db.execute('CREATE TABLE IF NOT EXISTS links (key TEXT PRIMARY KEY, cid TEXT NOT NULL, used INTEGER NOT NULL, steps INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS trace_links (cid TEXT PRIMARY KEY, trace_path TEXT NOT NULL)')

            db.execute('BEGIN IMMEDIATE')
            if 'reservation' not in [row[1] for row in db.execute('PRAGMA table_info(links)')]:
                db.execute('ALTER TABLE links ADD COLUMN reservation TEXT')
            yield db
            db.commit()
        except (OSError, sqlite3.Error) as exc:
            raise ConversationStateError('Local conversation state unavailable; no new chat was created.') from exc
        finally:
            if db is not None:
                db.close()

    def prepare(self, req, *, scope=None, claim=None, compact_results=False,
                legacy_scopes=(), allow_local_rollover=False, trace_path=None):
        # Explicit protocol clients already own their conversation identity.
        if req.conversation_id:
            return req, 0
        positions = [i for i, m in enumerate(req.messages) if m.role == 'assistant']
        # A local recovery notice has no upstream reply ID. Resolve the original
        # delivered action instead, retaining ALL tool results for the next
        # real continuation. The original exact history hash, result IDs and
        # one-use claim are still checked below; never create an alias claim.
        if positions:
            last = positions[-1]
            message = req.messages[last]
            if not message.tool_calls and _text_of(message.content) == RECOVERY_STOP_TEXT:
                if (not any(m.role == 'user' for m in req.messages[last + 1:])
                        or any(m.role != 'user' for m in req.messages[last + 1:])):
                    raise ConversationStateError('Recovery is stopped; explicit continuation is required.')
                req = req.model_copy(update={'messages': [*req.messages[:last], *req.messages[last + 1:]]})
                positions = [i for i, m in enumerate(req.messages) if m.role == 'assistant']
                if not positions:
                    raise ConversationStateError('Recovery parent unavailable; no request sent.')
        if not positions:
            if any(m.role == 'tool' for m in req.messages):
                raise ConversationStateError('Tool history has no known assistant; no new chat was created.')
            # Verify durable storage before the first upstream dispatch too.
            self.key(req, req.messages, scope)
            with self.connection():
                pass
            return req, 0
        end = positions[-1] + 1
        tail = req.messages[end:]
        if not tail or any(m.role not in ('tool', 'user') for m in tail):
            raise ConversationStateError('Ambiguous continuation; no new chat was created.')
        calls = {call.get('id'): call for call in (req.messages[end - 1].tool_calls or [])}
        result_ids = [m.tool_call_id for m in tail if m.role == 'tool']
        # OpenAI-compatible clients may omit tool_call_id when there was only
        # one delivered tool call. Bind that single result to the only call.
        # TODO: We intentionally canonicalize the single-call case where
        # tool_call_id is omitted. Histories that differ only by that ID are
        # treated as the same continuation because real provider chats are not
        # expected to diverge only on a synthetic tool identifier.
        if result_ids == [None] and len(calls) == 1:
            bound_id = next(iter(calls))
            result_ids = [bound_id]
            normalized_messages = [
                m.model_copy(update={'tool_call_id': bound_id})
                if m.role == 'tool' and m.tool_call_id is None else m
                for m in req.messages
            ]
            req = req.model_copy(update={'messages': normalized_messages})
            tail = req.messages[end:]
        if len(set(result_ids)) != len(result_ids) or set(result_ids) != set(calls):
            raise ConversationStateError('Tool results do not match the delivered calls.')
        key = self.key(req, req.messages[:end], scope)
        identity = self.account_identity() if scope is None else scope
        search = {
            'key': key,
            'scope': scope if scope is not None else self.account_identity(),
            'history': self._history_metadata(req, req.messages[:end]),
        }
        if trace_path is None and toolcall_logging_enabled():
            trace_path = new_toolcall_log(model_provider(req.model))
        with self.connection() as db:
            record_toolcall_log(trace_path, 'search_chat', search)
            prior_contract = None
            row = db.execute('SELECT cid, used, steps FROM links WHERE key=?', (key,)).fetchone()
            # A namespace upgrade must not orphan histories stored before it.
            # The legacy link is claimed exactly like a current one.
            for legacy in () if row else legacy_scopes:
                legacy_key = self.key(req, req.messages[:end], legacy)
                legacy_search = {
                    'key': legacy_key,
                    'scope': legacy,
                    'history': self._history_metadata(req, req.messages[:end]),
                }
                record_toolcall_log(trace_path, 'search_chat', legacy_search)
                row = db.execute('SELECT cid, used, steps FROM links WHERE key=?', (legacy_key,)).fetchone()
                if row:
                    key = legacy_key
                    break
            if row is None or not row[1]:
                # An administrator must register the exact old/current catalogs
                # locally. A changed leading Harness instruction also requires
                # an explicit exact pair. Account, model, flags and every other
                # message/argument/call ID stay identical under the normal
                # digest. Claim the OLD row, never mint an alias.
                matches = {key: (row, None)} if row is not None else {}
                try:
                    for old in conversation_schemas.candidates(db, req, identity):
                        old_key = self.key(old, old.messages[:end], identity)
                        old_row = db.execute('SELECT cid, used, steps FROM links WHERE key=?',
                                             (old_key,)).fetchone()
                        if old_row is not None:
                            matches[old_key] = (old_row, old)
                except (ValueError, TypeError) as exc:
                    raise ConversationStateError('Local schema upgrade unavailable; no request sent.') from exc
                if len(matches) > 1:
                    raise ConversationStateError('Ambiguous schema upgrade history; no request sent.')
                if matches:
                    key, (row, prior_contract) = next(iter(matches.items()))
            if not row:
                raise ConversationStateError('History is unknown; automatic replay and new-chat fallback are disabled.')
            if row[1]:
                raise ConversationStateError(
                    'History already consumed (previously used chat); automatic replay and new-chat fallback are disabled.')
            if row[2] >= self.max_replies and not (allow_local_rollover and claim is not None):
                raise ConversationStateError(f'Local conversation limit reached ({self.max_replies} accepted replies); continuation stopped. This per-conversation cap is separate from hourly/daily budgets and is not reset by /bridge-budget-reset.')
            # Claim before dispatch. An uncertain response must never replay.
            reservation = uuid.uuid4().hex
            consume_event = {
                'key': key,
                'conversation_id': row[0],
                'previous_used': row[1],
                'used': 1,
                'previous_steps': row[2],
                'reservation': reservation,
                'reason': 'continuation_claimed_before_provider_dispatch_to_prevent_replay_after_uncertain_response',
                'history': self._history_metadata(req, req.messages[:end]),
            }
            # Keep the claim update parameterized so the database mutation and its
            # audit event stay adjacent under the same immediate transaction.
            record_toolcall_log(trace_path, 'consume_chat', consume_event)
            db.execute('UPDATE links SET used=1, reservation=? WHERE key=?', (reservation, key))
        if claim is not None:
            claim.update(key=key, reservation=reservation)
        tail = [m.model_copy(update={'name': calls[m.tool_call_id]['function']['name']})
                if m.role == 'tool' else m for m in tail]
        # A bridge notice about a rejected tool reply is a delivered assistant turn
        # the provider chat never saw; hand it to the model with the user's next
        # message as a failed Tool result so the provider loop matches a tool error.
        notice = notice_of(_text_of(req.messages[end - 1].content))
        if notice and not calls and tail and tail[0].role == 'user':
            tail = [tail[0].model_copy(update={
                'content': provider_tool_error_followup(
                    notice, _text_of(tail[0].content))}), *tail[1:]]
        # Multiple results of the same tool name need a call-ID mapping block;
        # compact rendering still preserves each result's own tool_call_id.
        names = [calls[cid]['function']['name'] for cid in result_ids]
        unambiguous = compact_results and len(set(names)) == len(names)
        if result_ids and not unambiguous:
            # Bridge-generated IDs were not present in the upstream answer.
            # Supply the exact delivered calls to bind even out-of-order results.
            tail = [ChatMessage(role='assistant',
                     content='Previously requested calls: identifier mapping for the following results.',
                     tool_calls=[calls[cid] for cid in result_ids]), *tail]
        if (prior_contract is not None and req.messages[0].role == 'system'
                and req.messages[0] != prior_contract.messages[0]):
            # The exact pair was authorized for history matching. The provider
            # must also see the NEW host instruction, not silently retain the old
            # one. Send it once with the tail; never replay the preceding work.
            tail = [req.messages[0], *tail]
        prepared = req.model_copy(update={'conversation_id': row[0], 'messages': tail})
        prepared._tool_contract_refresh = prior_contract is not None
        return prepared, row[2]

    def release(self, claim):
        if not claim:
            return
        with self.connection() as db:
            db.execute('UPDATE links SET used=0, reservation=NULL WHERE key=? AND reservation=?',
                       (claim['key'], claim['reservation']))

    def claim_rollover(self, claim, previous_cid, archive):
        """Journal migration before clearing a refusal; never reopen an old link."""
        if not claim:
            raise ConversationStateError('Migration requires an owned history reservation.')
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS rollovers '
                       '(key TEXT PRIMARY KEY, previous_cid TEXT NOT NULL, archive TEXT NOT NULL)')
            owned = db.execute('SELECT used, reservation, cid FROM links WHERE key=?',
                               (claim['key'],)).fetchone()
            if owned != (1, claim['reservation'], previous_cid):
                raise ConversationStateError('Migration history reservation changed.')
            if db.execute('SELECT 1 FROM rollovers WHERE key=?', (claim['key'],)).fetchone():
                raise ConversationStateError('Migration already attempted; automatic replay disabled.')
            db.execute('INSERT INTO rollovers VALUES (?, ?, ?)',
                       (claim['key'], previous_cid, str(archive)))

    @staticmethod
    def _history_metadata(req, messages):
        """Return exactly the canonical JSON hashed by digest()."""
        return digest_payload_json(req, messages)

    def remember(self, original, reply, content, calls, steps, *, scope=None):
        if original.conversation_id or not reply.conversation_id:
            return
        assistant = ChatMessage(role='assistant', content=content, tool_calls=calls)
        messages = [*original.messages, assistant]
        key = self.key(original, messages, scope)
        entry = {
            'key': key,
            'conversation_id': reply.conversation_id,
            'used': 0,
            'steps': steps + 1,
            'scope': scope if scope is not None else self.account_identity(),
            'history': self._history_metadata(original, messages),
        }
        trace = new_toolcall_log(model_provider(original.model))
        record_toolcall_log(trace, 'add_chat', entry)
        with self.connection() as db:
            existing = db.execute('SELECT cid, used, steps FROM links WHERE key=?', (key,)).fetchone()
            if existing:
                # A pre-existing key is updated and consumed to avoid reusing the old link.
                record_toolcall_log(trace, 'consume_chat', {
                    'key': key,
                    'conversation_id': existing[0],
                    'previous_used': existing[1],
                    'used': 1,
                    'previous_steps': existing[2],
                    'reservation': None,
                    'reason': 'remember_insert_conflict_updates_existing_link',
                    'history': self._history_metadata(original, messages),
                })
                db.execute(
                    'UPDATE links SET cid=?, used=1, steps=?, reservation=NULL WHERE key=?',
                    (reply.conversation_id, steps + 1, key))
            else:
                db.execute('INSERT INTO links (key,cid,used,steps) VALUES (?, ?, 0, ?)',
                           (key, reply.conversation_id, steps + 1))

    def trace_path(self, conversation_id):
        if not conversation_id:
            return None
        try:
            with self.connection() as db:
                row = db.execute('SELECT trace_path FROM trace_links WHERE cid=?',
                                 (conversation_id,)).fetchone()
            return row[0] if row else None
        except ConversationStateError:
            # Existing conversations should not fail just because tracing state is unavailable.
            return None

    def remember_trace(self, conversation_id, trace_path):
        if not conversation_id or not trace_path:
            return
        try:
            with self.connection() as db:
                db.execute('INSERT INTO trace_links (cid,trace_path) VALUES (?, ?) '
                           'ON CONFLICT(cid) DO UPDATE SET trace_path=excluded.trace_path',
                           (conversation_id, str(trace_path)))
        except ConversationStateError:
            # Tracing is observational; preserve the provider response if unavailable.
            return



conversation_cache = ConversationCache(ROOT / 'session' / 'deepseek-conversations.sqlite3',
                                       ROOT / 'session' / 'session.json')
