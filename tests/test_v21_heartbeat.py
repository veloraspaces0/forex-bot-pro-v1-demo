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
        for run, attempt in [('oops', '1'), ('123', '0'), ('123', '-1'), ('１２３', '1'), ('123', '١')]:
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


    def test_publish_then_diagnose_integration_offline(self):
        from cadence_diagnostics import diagnose
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'state.json'
            target = Path(tmp) / 'heartbeat.json'
            state = {'mode': 'PAPER_ONLY', 'pair': 'EUR/USD',
                     'last_checked_at': '2026-11-04T23:59:06Z',
                     'last_candle_at': '2026-11-04T23:45:00Z',
                     'open_position': True}
            source.write_text(json.dumps(state), encoding='utf-8')
            heartbeat.publish(source, target, '123', '1',
                              lambda path, content: path.write_bytes(content))
            observed = json.loads(target.read_text(encoding='utf-8'))
            result = diagnose(state, observed, now=datetime.now(timezone.utc))
            self.assertNotIn('HEARTBEAT_STATE_MISMATCH', result['flags'])
            self.assertTrue(result['open_position'])
            self.assertEqual(json.loads(source.read_text()), state)

    def test_publish_does_not_write_when_state_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'state.json'
            target = Path(tmp) / 'heartbeat.json'
            source.write_text(json.dumps(dict(self.state, mode='LIVE')), encoding='utf-8')
            with self.assertRaises(ValueError):
                heartbeat.publish(source, target, '123', '1',
                                  lambda path, content: path.write_bytes(content))
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
