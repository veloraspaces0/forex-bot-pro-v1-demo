"""Real SQLite contention regression for Cloudflare DO's atomic reservation SQL.

Runs offline with a temporary local SQLite database. The SQL statement and
40-minute hold interval are imported from the JavaScript module via Node.
No Cloudflare credentials, GitHub API, or trading operations are involved.
"""
import concurrent.futures
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT="""
import {CREATE_DISPATCH_TABLE,ATOMIC_RESERVATION_SQL,DISPATCH_HOLD_MS}
from './experimentos/forex_backup_guard_sql.mjs';
console.log(JSON.stringify({schema:CREATE_DISPATCH_TABLE,
  query:ATOMIC_RESERVATION_SQL,hold:DISPATCH_HOLD_MS}));
"""
SOURCE=json.loads(subprocess.check_output(
    ['node','--input-type=module','-e',SCRIPT], cwd=ROOT, text=True))

class AtomicReservation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.database=str(Path(self.temp.name)/'coordinator.sqlite')
        with sqlite3.connect(self.database) as db:
            db.execute(SOURCE['schema'])
        self.now=1770000000000

    def tearDown(self):
        self.temp.cleanup()

    def claim(self,now):
        with sqlite3.connect(self.database,timeout=8.0,isolation_level=None) as db:
            db.execute('PRAGMA busy_timeout=8000')
            return bool(db.execute(SOURCE['query'],(
                now,now-SOURCE['hold'])).fetchall())

    def test_32_parallel_requests_have_one_winner(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            granted=list(pool.map(self.claim,[self.now]*32))
        self.assertEqual(sum(granted),1)
        self.assertEqual(granted.count(False),31)

    def test_persistent_cooldown_and_expiry(self):
        self.assertTrue(self.claim(self.now))
        self.assertFalse(self.claim(self.now+1))
        self.assertFalse(self.claim(self.now+SOURCE['hold']-1))
        self.assertTrue(self.claim(self.now+SOURCE['hold']))
        self.assertFalse(self.claim(self.now+SOURCE['hold']+1))

    def test_retry_after_ambiguous_dispatch_is_not_released(self):
        self.assertTrue(self.claim(self.now))
        # Simulate lost HTTP 204 response, process restart, and a second tick;
        # reservation remains in persistent SQLite despite lost in-memory state.
        self.assertFalse(self.claim(self.now+10*60*1000))

    def test_one_slot_per_logical_paper_workflow(self):
        with sqlite3.connect(self.database) as db:
            self.assertTrue(self.claim(self.now))
            entries=db.execute('SELECT slot,reserved_at FROM backup_dispatch_guard').fetchall()
        self.assertEqual(entries,[(1,self.now)])

if __name__=='__main__':
    unittest.main()
