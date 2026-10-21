"""Explicit, local-only tool catalog upgrades for one-use history links.

Catalogs are compatibility evidence, never new continuation tickets. Lookups
always claim the original links row; its used bit and reservation own recovery.
"""
import argparse
import hashlib
import json

from jsonschema.validators import validator_for

from .config import canonical_model


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def catalog_key(scope, model, tools, system=None):
    return hashlib.sha256(encoded([scope, canonical_model(model) or model, tools, system]).encode()).hexdigest()


def validate_catalog(tools):
    if not isinstance(tools, list) or not tools or len(tools) > 256:
        raise ValueError('Expected a nonempty OpenAI function catalog (at most 256 tools).')
    if len(encoded(tools).encode()) > 1048576:
        raise ValueError('Tool catalog exceeds the local size limit.')
    names = set()
    for tool in tools:
        fn = tool.get('function') if isinstance(tool, dict) and tool.get('type') == 'function' else None
        if (not isinstance(fn, dict) or not isinstance(fn.get('name'), str)
                or not fn['name'] or fn['name'] in names
                or not isinstance(fn.get('parameters'), dict)):
            raise ValueError('Invalid or duplicate function declaration.')
        names.add(fn['name'])
        validator_for(fn['parameters']).check_schema(fn['parameters'])


def ensure_table(db):
    db.execute('CREATE TABLE IF NOT EXISTS schema_upgrades '
               '(target TEXT NOT NULL, source TEXT NOT NULL, catalog TEXT NOT NULL, system TEXT, '
               'PRIMARY KEY (target, source))')


def register(cache, *, scope, model, old, current, old_system=None, current_system=None):
    """Authorize an exact catalog pair in one account/model namespace offline.

    Existing links, claims, migration journals and budgets are never modified.
    Registration neither proves a history exists nor authorizes its replay.
    """
    if not isinstance(scope, str) or not scope or not isinstance(model, str) or not model:
        raise ValueError('An explicit account scope and model are required.')
    validate_catalog(old)
    validate_catalog(current)
    if (old_system is None) != (current_system is None):
        raise ValueError('Both exact leading system instructions are required.')
    for system in (old_system, current_system):
        if system is not None and (not isinstance(system, str) or not system
                                   or len(system.encode()) > 262144):
            raise ValueError('Invalid or oversized leading system instruction.')
    source = catalog_key(scope, model, old, old_system)
    target = catalog_key(scope, model, current, current_system)
    if source == target:
        raise ValueError('Catalogs are identical; no upgrade is needed.')
    with cache.connection() as db:
        ensure_table(db)
        db.execute('INSERT OR IGNORE INTO schema_upgrades VALUES (?, ?, ?, ?)',
                   (target, source, encoded(old), old_system))


def candidates(db, req, scope):
    # No chains or recursive aliases. Every candidate is an exact historical
    # catalog explicitly authorized for this request's current catalog.
    ensure_table(db)
    targets = [catalog_key(scope, req.model, req.tools)]
    if req.messages and req.messages[0].role == 'system' and isinstance(req.messages[0].content, str):
        targets.append(catalog_key(scope, req.model, req.tools, req.messages[0].content))
    rows = db.execute('SELECT source, catalog, system FROM schema_upgrades WHERE target IN (?, ?)',
                      (targets[0], targets[-1])).fetchall()
    for source, catalog, system in rows:
        tools = json.loads(catalog)
        if catalog_key(scope, req.model, tools, system) != source:
            raise ValueError('Stored schema upgrade is corrupt.')
        messages = req.messages
        if system is not None:
            messages = [messages[0].model_copy(update={'content': system}), *messages[1:]]
        yield req.model_copy(update={'tools': tools, 'messages': messages})


def main():
    from pathlib import Path
    from deepseek.auth import DEFAULT_SESSION_FILE, Session
    from .conversation_cache import ConversationCache, conversation_cache

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalogs', required=True, type=Path,
                        help='Private JSON with exact OpenAI catalogs: old and current')
    parser.add_argument('--model', required=True)
    parser.add_argument('--database', type=Path, default=conversation_cache.path)
    parser.add_argument('--session-file', type=Path, default=DEFAULT_SESSION_FILE)
    args = parser.parse_args()
    session = Session.load(args.session_file)
    if session is None:
        parser.error('No local session; registration cannot infer the account namespace.')
    catalogs = json.loads(args.catalogs.read_text())
    scope = 'session-v2:' + (session.continuation_scope or session.identity())
    register(ConversationCache(args.database), scope=scope, model=args.model,
             old=catalogs['old'], current=catalogs['current'],
             old_system=catalogs.get('old_system'), current_system=catalogs.get('current_system'))
    print('Exact catalog upgrade registered locally. Histories and counters unchanged; no request sent.')


if __name__ == '__main__':
    main()
