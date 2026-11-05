import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2"))
from cadence_diagnostics import diagnose


class CadenceDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 4, 21, 30, tzinfo=timezone.utc)
        self.state = {
            "mode": "PAPER_ONLY", "pair": "EUR/USD",
            "last_checked_at": "2026-11-04T21:15:00Z",
            "last_candle_at": "2026-11-04T21:00:00Z",
            "open_position": True,
        }
        self.heartbeat = {
            "mode": "PAPER_ONLY", "status": "SUCCESS",
            "completed_at": "2026-11-04T21:16:00Z",
            "state_last_checked_at": "2026-11-04T21:15:00Z",
        }

    def test_healthy(self):
        self.assertEqual(diagnose(self.state, self.heartbeat, self.now)["flags"], [])

    def test_delayed_state_and_heartbeat(self):
        self.state["last_checked_at"] = "2026-11-04T20:30:00Z"
        self.heartbeat["completed_at"] = "2026-11-04T20:31:00Z"
        self.heartbeat["state_last_checked_at"] = "2026-11-04T20:30:00Z"
        flags = diagnose(self.state, self.heartbeat, self.now)["flags"]
        self.assertIn("STATE_UPDATE_DELAY", flags)
        self.assertIn("HEARTBEAT_DELAY", flags)

    def test_missing_heartbeat_is_not_claimed_success(self):
        self.assertIn("HEARTBEAT_UNAVAILABLE", diagnose(self.state, None, self.now)["flags"])

    def test_mismatch(self):
        self.heartbeat["state_last_checked_at"] = "2026-11-04T21:00:00Z"
        self.assertIn("HEARTBEAT_STATE_MISMATCH", diagnose(self.state, self.heartbeat, self.now)["flags"])

    def test_reject_live_state(self):
        self.state["mode"] = "LIVE"
        with self.assertRaises(ValueError):
            diagnose(self.state, self.heartbeat, self.now)

    def test_future_timestamp(self):
        self.state["last_checked_at"] = "2026-11-04T22:00:00Z"
        self.heartbeat["state_last_checked_at"] = "2026-11-04T22:00:00Z"
        self.assertIn("FUTURE_TIMESTAMP", diagnose(self.state, self.heartbeat, self.now)["flags"])


    def test_boundary_at_thirty_minutes_is_not_delayed(self):
        self.state["last_checked_at"] = "2026-11-04T21:00:00Z"
        self.heartbeat["state_last_checked_at"] = "2026-11-04T21:00:00Z"
        self.heartbeat["completed_at"] = "2026-11-04T21:00:00Z"
        flags = diagnose(self.state, self.heartbeat, self.now)["flags"]
        self.assertNotIn("STATE_UPDATE_DELAY", flags)
        self.assertNotIn("HEARTBEAT_DELAY", flags)

    def test_reject_timestamp_without_timezone(self):
        self.state["last_checked_at"] = "2026-11-04T21:15:00"
        with self.assertRaises(ValueError):
            diagnose(self.state, self.heartbeat, self.now)

    def test_reject_heartbeat_that_claims_live(self):
        self.heartbeat["mode"] = "LIVE"
        with self.assertRaises(ValueError):
            diagnose(self.state, self.heartbeat, self.now)

    def test_stale_candle_can_coexist_with_recent_state(self):
        self.state["last_candle_at"] = "2026-11-04T20:00:00Z"
        flags = diagnose(self.state, self.heartbeat, self.now)["flags"]
        self.assertIn("CANDLE_AGE_HIGH", flags)
        self.assertNotIn("STATE_UPDATE_DELAY", flags)


if __name__ == "__main__":
    unittest.main()
