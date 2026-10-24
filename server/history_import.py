"""Explicit, exact, single-use API-history imports. Authorization is local only.

This does not repair or reopen continuation links. An import ticket is consumed
before dispatch and stays consumed on every failure, including process death.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time

from .config import model_provider
from .conversation_cache import ConversationStateError
from .context_rollover import checkpoint
from .openai_format import messages_to_prompt
from .schemas import ChatMessage, ChatCompletionRequest
from .tool_history import validate_tool_history


MAX_PROMPT = 131072
MAX_ARCHIVE = 16 * 1024 * 1024


def request_key(req, scope):
    # Streaming changes delivery only. Unlike a continuation link, authorization
    # also binds tool_choice, sampling parameters and the output token limit.
    raw = json.dumps([scope, req.model_dump(exclude={'stream'}, exclude_none=True)],
                     sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def _eligible(req):
    if (model_provider(req.model) != 'deepseek' or req.conversation_id
            or not any(m.role == 'assistant' for m in req.messages)):
        raise ConversationStateError('Import requires external assistant history and no provider conversation ID.')
    validate_tool_history(req.messages)
    if req.messages[-1].role not in ('tool', 'user'):
        raise ConversationStateError('Import requires an unperformed continuation after the recorded replies.')


def _table(db):
    db.execute('CREATE TABLE IF NOT EXISTS history_imports ('
               'key TEXT PRIMARY KEY, consumed INTEGER NOT NULL, created REAL NOT NULL, '
               'expires REAL NOT NULL, handoff_path TEXT NOT NULL, handoff_hash TEXT NOT NULL, '
               'archive_path TEXT NOT NULL, archive_hash TEXT NOT NULL)')


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _read_private(path, expected_hash, limit):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    try:
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size > limit
                    or (os.name != 'nt' and info.st_mode & 0o077)):
                raise ValueError('Invalid private import file')
            data = stream.read(limit + 1)
        if len(data) > limit or _hash(data) != expected_hash:
            raise ValueError('Import file integrity mismatch')
        return data
    except (OSError, ValueError) as exc:
        raise ConversationStateError('Private import archive is unavailable or changed; no request sent.') from exc


def authorize(cache, req, scope, root, *, source_provider, ttl_seconds=600):
    """Archive and authorize this exact request without any account request."""
    _eligible(req)
    if source_provider != 'deepseek-official' or not 1 <= ttl_seconds <= 600:
        raise ValueError('Explicit deepseek-official source and a 1..600 second lifetime are required')
    key = request_key(req, scope)
    tools_size = len(messages_to_prompt([], req.tools, req.tool_choice,
                    tools_continuation=False, provider='deepseek').encode())
    with cache.connection() as db:
        _table(db)
        if db.execute('SELECT 1 FROM history_imports WHERE key=?', (key,)).fetchone():
            raise ConversationStateError('This history import was already authorized; it cannot be rearmed.')
        handoff, archive = checkpoint(req, None, root, external_provider=source_provider,
                                     max_text_bytes=MAX_PROMPT - tools_size - 1024)
        migrated = req.model_copy(update={'messages': [ChatMessage(role='user', content=handoff)]})
        prompt = messages_to_prompt(migrated.messages, migrated.tools, migrated.tool_choice,
                                    tools_continuation=False, provider='deepseek')
        if len(prompt.encode()) > MAX_PROMPT:
            raise ConversationStateError('Import prompt exceeds the local limit; no request sent.')
        fd, filename = tempfile.mkstemp(prefix='import-', suffix='.txt', dir=Path(archive).parent)
        data = handoff.encode()
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        now = time.time()
        db.execute('INSERT INTO history_imports VALUES (?,0,?,?,?,?,?,?)',
                   (key, now, now + ttl_seconds, filename, _hash(data), archive,
                    _hash(Path(archive).read_bytes())))
    return {'key': key, 'archive': archive, 'expires_at': now + ttl_seconds,
            'prompt_bytes': len(prompt.encode()), 'network_requests': 0}


def claim(cache, req, scope):
    """Consume an exact ticket atomically; return None for unapproved histories."""
    key = request_key(req, scope)
    with cache.connection() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='history_imports'").fetchone():
            return None
        row = db.execute('SELECT consumed,created,expires,handoff_path,handoff_hash,archive_path,archive_hash '
                         'FROM history_imports WHERE key=?', (key,)).fetchone()
        if row is None:
            return None
        now = time.time()
        if row[0] or not row[1] <= now < row[2]:
            raise ConversationStateError('History import is consumed or expired; no automatic replay.')
        _eligible(req)
        _read_private(row[5], row[6], MAX_ARCHIVE)
        text = _read_private(row[3], row[4], MAX_PROMPT).decode('utf-8')
        migrated = req.model_copy(update={'messages': [ChatMessage(role='user', content=text)]})
        prompt = messages_to_prompt(migrated.messages, migrated.tools, migrated.tool_choice,
                                    tools_continuation=False, provider='deepseek')
        if len(prompt.encode()) > MAX_PROMPT:
            raise ConversationStateError('Import prompt exceeds the local limit; no request sent.')
        db.execute('UPDATE history_imports SET consumed=1 WHERE key=? AND consumed=0', (key,))
    return migrated, prompt


def validate_new_calls(original, reply, calls):
    """The first imported step cannot reissue any exact observed action."""
    from .api_new import canonical_call
    from .diagnostics import save_tool_failure
    from .openai_format import ToolCallError
    observed = {m.tool_call_id for m in original.messages if m.role == 'tool'}
    # get_goal is a read of the current native lifecycle state. Its empty
    # arguments are identical after every resume, while the state has changed.
    # Re-reading it is required before a goal update, not replaying completed work.
    prior = {canonical_call(c['function']['name'], c['function']['arguments'])
             for m in original.messages for c in (m.tool_calls or [])
             if c['id'] in observed and c['function']['name'] != 'get_goal'}
    if any(canonical_call(c['function']['name'], c['function']['arguments']) in prior
           for c in (calls or [])):
        save_tool_failure(reply, original)
        error = ToolCallError('Imported step repeats a recorded tool call; no calls returned.', kind='repeat')
        error.reply = reply
        raise error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True, help='Exact reviewed bridge request JSON')
    parser.add_argument('--archive-root', type=Path, required=True,
                        help='Private git-ignored directory readable by the current Harness workspace')
    parser.add_argument('--source-provider', choices=['deepseek-official'], required=True)
    args = parser.parse_args(argv)
    try:
        if args.request.stat().st_size > MAX_ARCHIVE:
            raise ValueError('Request file exceeds the local archive limit')
        req = ChatCompletionRequest.model_validate_json(args.request.read_bytes())
        # get_client loads a local session only; it never signs in or probes the account.
        from . import api
        api.guard.check('deepseek')
        scope = api._conversation_scope('deepseek', api.get_client())
        result = authorize(api.conversation_cache, req, scope, args.archive_root,
                           source_provider=args.source_provider)
    except Exception:
        # Validation errors can embed private request values. Keep CLI output
        # free of raw history, credentials and exception tracebacks.
        parser.exit(1, 'History import not authorized. Check the private request, local session, '
                       'pause, archive access and existing import ticket. No account request sent.\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
