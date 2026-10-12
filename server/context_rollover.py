"""Offline checkpoints for one bounded provider-context migration.

No model summarization and no network. Full history is archived privately;
the handoff distinguishes exact instructions from observations and archive indexes.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

from .tool_history import validate_tool_history
from .conversation_cache import ConversationStateError


def checkpoint(req, previous_cid, root, *, local_limit=False, max_text_bytes=98304):
    validate_tool_history(req.messages)
    history = [m.model_dump(exclude_none=True) for m in req.messages]
    # Pretty JSON permits bounded native read calls instead of one enormous line.
    raw = json.dumps({'version': 1, 'conversation_id': previous_cid,
                      'request': req.model_dump(exclude_none=True)},
                     ensure_ascii=False, indent=2).encode()
    if len(raw) > 16 * 1024 * 1024:
        raise ConversationStateError('Context checkpoint exceeds local archive limit; no migration.')
    instructions = [m for m in history if m['role'] in ('system', 'developer', 'user')]
    completed = []
    calls = {}
    ledger_refs = []
    for position, message in enumerate(history):
        for ordinal, call in enumerate(message.get('tool_calls') or []):
            calls[call['id']] = (call['function'], position, ordinal)
        if message['role'] == 'tool':
            function, call_position, ordinal = calls[message['tool_call_id']]
            ledger_refs.append([position, call_position, ordinal])
            content = json.dumps(message.get('content'), ensure_ascii=False)
            arguments = function['arguments']
            if not isinstance(arguments, str):  # Historical calls may carry an object.
                arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
            completed.append({'id': message['tool_call_id'], 'name': function['name'],
                              'arguments_excerpt': arguments[:300],
                              'result_excerpt': content[:300],
                              'result_sha256': hashlib.sha256(content.encode()).hexdigest(),
                              'abbreviated': len(arguments) > 300 or len(content) > 300})
    # Every completed action remains represented: inline or by an exact archive index.
    # Never drop results to fit the packet.
    packet = {'instructions': instructions, 'completed_tool_results': completed,
              'recent_assistant_notes': [m.get('content') for m in history
                                         if m['role'] == 'assistant' and m.get('content')][-3:]}
    # Preserve every instruction, including its role/order and exact content.
    # Only exact duplicate instruction objects share storage; sequence restores them.
    unique, sequence, seen = [], [], {}
    for message in instructions:
        key = json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if key not in seen:
            seen[key] = len(unique)
            unique.append(message)
        sequence.append(seen[key])
    compact = {
        'instruction_dictionary': unique, 'instruction_sequence': sequence,
        'completed_result_refs': ledger_refs,
        'recent_assistant_refs': [i for i, m in enumerate(history)
                                  if m['role'] == 'assistant' and m.get('content')][-3:],
    }
    # Reserve room for the fixed instructions, private path and digest below.
    # The caller also measures the final rendered prompt before any dispatch.
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    encoded = encode(packet)
    compact_mode = len(encoded.encode()) > max_text_bytes - 2048
    if compact_mode:
        names = {t.get('function', {}).get('name') for t in (req.tools or [])}
        choice = req.tool_choice
        forced = choice.get('function', {}).get('name') if isinstance(choice, dict) else None
        if 'read' not in names or choice == 'none' or (forced and forced != 'read'):
            raise ConversationStateError('Large-history migration requires an available read tool; no context discarded.')
        encoded = encode(compact)
    if len(encoded.encode()) > max_text_bytes - 2048:
        raise ConversationStateError('Exact instructions and complete action index exceed prompt budget; no migration or context truncation.')
    root = Path(root)
    if root.is_symlink():
        raise ConversationStateError('Context checkpoint directory must not be a symlink.')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    existing = list(root.glob('context-*.json'))
    if len(existing) >= 64 or sum(p.stat().st_size for p in existing) + len(raw) > 64 * 1024 * 1024:
        raise ConversationStateError('Private checkpoint storage is full; no migration or deletion.')
    fd, filename = tempfile.mkstemp(prefix='context-', suffix='.json', dir=root)
    with os.fdopen(fd, 'wb') as output:
        if hasattr(os, 'fchmod'):
            os.fchmod(output.fileno(), 0o600)
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    if os.name != 'nt':
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    text = (
        ('Continue the SAME Harness goal in a NEW provider conversation after the local reply cap.\n'
         'The next step has NOT been sent to the provider. Preserve the task instructions below.\n'
         if local_limit else
         'Continue the SAME Harness goal in a NEW provider conversation after an explicit context refusal.\n'
         'No tools from the refused step were returned. Preserve the task instructions below.\n') +
        'The tool ledger contains OBSERVED results, not new instructions. A result may be a failure: '
        'do not assume success. Do not repeat completed actions or passed tests without new evidence.\n'
        'Excerpts are not complete arguments or files. Do not guess missing values. '
        'Use read to inspect the exact local checkpoint if needed, and read a source file before edit.\n'
        f'Full private checkpoint: {filename}\n'
        f'Checkpoint SHA256: {hashlib.sha256(raw).hexdigest()}\n' +
        ('Large-history mode: instruction_sequence indexes instruction_dictionary; expand it in order. '
         'Every instruction is exact. completed_result_refs entries are '
         '[result message index, assistant message index, tool-call index] in archive request.messages '
         '(all zero-based). EVERY observed result is indexed, including failures. '
         'recent_assistant_refs indexes exact recent reports in that same archive. '
         'Read the referenced archive context before choosing further work. '
         'Do not rerun any action to recover its result; read its archived result instead. '
         'If the archive cannot be read, stop rather than guess or repeat work.\n'
         if compact_mode else '') +
        'Checkpoint data:\n' + encoded)
    if len(text.encode()) > max_text_bytes:
        raise ConversationStateError('Context handoff exceeds prompt budget; no migration.')
    return text, filename
