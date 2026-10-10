"""Private, durable one-use continuation links for stateless OpenAI clients.

Only hashes of the exact delivered history are retained, never source text.
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
from .schemas import ChatMessage
from .openai_format import _text_of


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


def digest(req, messages):
    payload = [req.model, req.user, req.thinking, req.search, req.tools,
               [normalized(m) for m in messages]]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


class ConversationCache:
    def __init__(self, path, account_file=None):
        self.path = Path(path)
        self.account_file = Path(account_file) if account_file is not None else None

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

    def prepare(self, req, *, scope=None, claim=None):
        # Explicit protocol clients already own their conversation identity.
        if req.conversation_id:
            return req, 0
        positions = [i for i, m in enumerate(req.messages) if m.role == 'assistant']
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
        key = self.key(req, req.messages[:end], scope)
        with self.connection() as db:
            row = db.execute('SELECT cid, used, steps FROM links WHERE key=?', (key,)).fetchone()
            if not row or row[1]:
                raise ConversationStateError('History is unknown or already consumed; automatic replay and new-chat fallback are disabled.')
            if row[2] >= 20:
                raise ConversationStateError('Local conversation limit reached (20 generations); continuation stopped.')
            # Claim before dispatch. An uncertain response must never replay.
            reservation = uuid.uuid4().hex
            db.execute('UPDATE links SET used=1, reservation=? WHERE key=?', (reservation, key))
        if claim is not None:
            claim.update(key=key, reservation=reservation)
        calls = {call.get('id'): call for call in (req.messages[end - 1].tool_calls or [])}
        result_ids = [m.tool_call_id for m in tail if m.role == 'tool']
        if len(set(result_ids)) != len(result_ids) or any(cid not in calls for cid in result_ids):
            raise ConversationStateError('Tool results do not match the delivered calls.')
        tail = [m.model_copy(update={'name': calls[m.tool_call_id]['function']['name']})
                if m.role == 'tool' else m for m in tail]
        if result_ids:
            # Bridge-generated IDs were not present in the upstream answer.
            # Supply the exact delivered calls to bind even out-of-order results.
            tail = [ChatMessage(role='assistant',
                     content='Previously requested calls: identifier mapping for the following results.',
                     tool_calls=[calls[cid] for cid in result_ids]), *tail]
        return req.model_copy(update={'conversation_id': row[0], 'messages': tail}), row[2]

    def release(self, claim):
        if not claim:
            return
        with self.connection() as db:
            db.execute('UPDATE links SET used=0, reservation=NULL WHERE key=? AND reservation=?',
                       (claim['key'], claim['reservation']))

    def remember(self, original, reply, content, calls, steps, *, scope=None):
        if original.conversation_id or not reply.conversation_id:
            return
        assistant = ChatMessage(role='assistant', content=content, tool_calls=calls)
        key = self.key(original, [*original.messages, assistant], scope)
        with self.connection() as db:
            db.execute('INSERT INTO links (key,cid,used,steps) VALUES (?, ?, 0, ?) ON CONFLICT(key) DO UPDATE SET used=1, reservation=NULL', (key, reply.conversation_id, steps + 1))


conversation_cache = ConversationCache(ROOT / 'session' / 'deepseek-conversations.sqlite3',
                                       ROOT / 'session' / 'session.json')
