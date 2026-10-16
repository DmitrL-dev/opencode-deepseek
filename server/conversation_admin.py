"""Administrative commands for the local conversation cache."""

import argparse
import json

from .conversation_cache import conversation_cache


def clear_all(cache=conversation_cache):
    """Delete cached conversation links and associated local metadata."""
    with cache.connection() as db:
        counts = {}
        for table in ('links', 'trace_links', 'rollovers'):
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists:
                cursor = db.execute(f'DELETE FROM {table}')
                counts[table] = cursor.rowcount
            else:
                counts[table] = 0
        return counts


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Manage local cached conversation links and trace associations.'
    )
    parser.add_argument(
        '-clear', '--clear', action='store_true', dest='clear',
        help='delete all cached chats, trace associations, and rollover records',
    )
    args = parser.parse_args(argv)
    if not args.clear:
        parser.print_help()
        return 0
    counts = clear_all()
    print(json.dumps({'deleted': counts}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
