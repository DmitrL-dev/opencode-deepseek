import tempfile
import unittest
from pathlib import Path

from server.conversation_admin import clear_all
from server.conversation_cache import ConversationCache


class ConversationAdminTests(unittest.TestCase):
    def test_clear_removes_links_trace_associations_and_rollovers(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ConversationCache(Path(directory) / 'conversations.sqlite3')
            with cache.connection() as db:
                db.execute("INSERT INTO links (key,cid,used,steps,reservation) VALUES ('k','cid',1,2,NULL)")
                db.execute("INSERT INTO trace_links (cid,trace_path) VALUES ('cid','trace.log')")
                db.execute('CREATE TABLE rollovers (key TEXT PRIMARY KEY, previous_cid TEXT NOT NULL, archive TEXT NOT NULL)')
                db.execute("INSERT INTO rollovers VALUES ('k','cid','archive')")

            counts = clear_all(cache)

            self.assertEqual(counts, {'links': 1, 'trace_links': 1, 'rollovers': 1})
            with cache.connection() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM links').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM trace_links').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM rollovers').fetchone()[0], 0)

    def test_clear_handles_database_without_rollovers_table(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ConversationCache(Path(directory) / 'conversations.sqlite3')
            self.assertEqual(clear_all(cache), {'links': 0, 'trace_links': 0, 'rollovers': 0})


if __name__ == '__main__':
    unittest.main()
