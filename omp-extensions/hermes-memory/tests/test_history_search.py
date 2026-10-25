import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import history


class SearchEvidenceTest(unittest.TestCase):
    def test_ln_keeps_tool_calls_but_ranks_user_gold_first(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript('''
            CREATE TABLE sessions (
                id TEXT, source TEXT, cwd TEXT, git_repo_root TEXT, title TEXT
            );
            CREATE TABLE messages (
                id INTEGER, session_id TEXT, role TEXT, timestamp REAL,
                content TEXT, tool_name TEXT, tool_calls TEXT
            );
            CREATE VIRTUAL TABLE messages_fts USING fts5(content, tool_name, tool_calls);
        ''')
        rows = [
            (229772, 'user', 'recover work from ln and coordinate with omp', ''),
            (231946, 'user', 'ln is last night - when did i say ln', ''),
            (231961, 'assistant', '', '{"command":"search ln in Hermes history"}'),
        ]
        for message_id, role, content, tool_calls in rows:
            db.execute("INSERT INTO sessions VALUES (?, 'clean', '', '', 'ln test')",
                       (str(message_id),))
            db.execute('INSERT INTO messages VALUES (?, ?, ?, 1, ?, ?, ?)',
                       (message_id, str(message_id), role, content, '', tool_calls))
            db.execute('INSERT INTO messages_fts(rowid, content, tool_name, tool_calls) VALUES (?, ?, ?, ?)',
                       (message_id, content, '', tool_calls))

        matches = history.search(db, {'query': 'ln', 'limit': 5})['matches']
        ids = [row['message_id'] for row in matches]
        self.assertIn(229772, ids)
        self.assertIn(231946, ids)
        self.assertIn(231961, ids)
        self.assertLess(ids.index(229772), ids.index(231961))
        self.assertLess(ids.index(231946), ids.index(231961))
        tool_hit = next(row for row in matches if row['message_id'] == 231961)
        self.assertIn('ln', tool_hit['excerpt'].lower())
        self.assertTrue(tool_hit['excerpt'])


if __name__ == '__main__':
    unittest.main()
