"""Bounded live replay: no real repositories, history corpus, or worker launches."""
from argparse import Namespace
import json
from pathlib import Path
import sqlite3
import tempfile

import unittest
from unittest.mock import patch
from z0int.fsm import supervise as S

class OperationalWindow(unittest.TestCase):
    def test_primary_key_window_and_no_repository_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dbpath=root/'state.db';db=sqlite3.connect(dbpath)
            db.executescript('''create table messages(id integer primary key, session_id text, role text, content text, tool_call_id text, tool_calls text, timestamp real);
            create table sessions(id text, source text, title text, started_at real, ended_at real, end_reason text, parent_session_id text, last_activity_at real);
            create table async_delegations(delegation_id text,state text,dispatched_at real,completed_at real,task_json text,result_json text);''')
            call=json.dumps([{'id':'c','function':{'name':'terminal','arguments':json.dumps({'command':'true'})}}])
            db.executemany('insert into messages values(?,?,?,?,?,?,?)',[
                (1,'s','assistant','',None,call,1),(2,'s','tool','old','c',None,2),
                (3,'s','assistant','',None,call,3),(4,'s','tool','fresh','c',None,4)])
            db.commit();db.close()
            for name in ('delegations','fleet','live'): (root/name).mkdir()
            prior={'generated_at':'prior','workers':[{'id':'old','phase':'Overdue','meta':{'worktrees':['/protected']}}],
                   'needs_kevin':{'items':[{'worker':'old','phase':'Overdue','worktrees':[],'recommended':'abandon'}]}}
            (root/'live/state.json').write_text(json.dumps(prior))
            args=Namespace(now='2026-10-22T18:00:00Z',limit_hours=2,operational_window=2,
                live_dir=root/'live',delegation_dir=root/'delegations',state_db=dbpath,
                fleet_runs=root/'fleet',processes=root/'processes.json',roots=['/MUST-NOT-SCAN'],
                decisions=root/'decisions.jsonl',loop1_report=root/'no-old-report.json')
            with patch.object(S,'find_repos',side_effect=AssertionError('broad repository scan')), \
                 patch.object(S,'worktree_inventory',side_effect=AssertionError('broad inventory')), \
                 patch.object(S,'worktree_state',side_effect=AssertionError('git status scan')):
                rep=S.build(args)
            self.assertEqual(rep['sources']['tool_calls'],1)
            self.assertEqual(rep['sources']['evidence_window']['min_message_id_exclusive'],2)
            self.assertFalse(rep['sources']['evidence_window']['repository_scans'])
            self.assertEqual(rep['needs_kevin']['items'][0]['recommended'],'hold')
            self.assertEqual(rep['needs_kevin']['items'][0]['decision_line'],'')
            self.assertTrue(S.dispatch_check(rep,'/protected',False,None))
            self.assertIn('BOUNDED OPERATIONAL WINDOW',rep['scope'])

    def test_recent_logs_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'deleg_old/task-0.log';p.parent.mkdir();p.write_text('goal: old\nstarted: 2026-01-01 00:00:00\n')
            import os
            os.utime(p,(1,1))
            self.assertEqual(S.read_tasks(Path(tmp),changed_since=2),[])
            self.assertEqual(len(S.read_tasks(Path(tmp))),1)

if __name__=='__main__':unittest.main()
