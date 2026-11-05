"""Offline heartbeat tests: no network, secrets, trades, or repository writes."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v2'))
import heartbeat


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.state = {'mode': 'PAPER_ONLY', 'pair': 'EUR/USD',
                      'last_checked_at': '2026-11-04T23:59:06Z',
                      'position': {'secret': 'must-not-leak'}}

    def test_sanitized_schema_and_utc(self):
        at = datetime(2026, 11, 5, 0, 1, tzinfo=timezone.utc)
        result = heartbeat.build_heartbeat(self.state, '123', '2', now=at)
        self.assertEqual(result['completed_at'], '2026-11-05T00:01:00Z')
        self.assertEqual(result['run_id'], '123')
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertNotIn('position', result)
        self.assertNotIn('secret', json.dumps(result))

    def test_rejects_non_paper_or_missing_check(self):
        for state in [dict(self.state, mode='LIVE'), dict(self.state, last_checked_at=None)]:
            with self.assertRaises(ValueError):
                heartbeat.build_heartbeat(state, '123', '1')

    def test_rejects_bad_run_identifiers(self):
        for run, attempt in [('oops', '1'), ('123', '0'), ('123', '-1')]:
            with self.assertRaises(ValueError):
                heartbeat.build_heartbeat(self.state, run, attempt)

    def test_publish_writes_only_target_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'state.json'
            target = Path(tmp) / 'heartbeat.json'
            source.write_text(json.dumps(self.state), encoding='utf-8')
            writes = []
            def writer(path, content):
                writes.append(path)
                path.write_bytes(content)
            heartbeat.publish(source, target, '123', '1', writer)
            self.assertEqual(writes, [target])
            self.assertEqual(json.loads(target.read_text())['status'], 'SUCCESS')
            self.assertEqual(json.loads(source.read_text()), self.state)


if __name__ == '__main__':
    unittest.main()
