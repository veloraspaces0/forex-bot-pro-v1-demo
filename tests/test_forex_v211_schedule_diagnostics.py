import datetime as dt
import unittest
from experimentos.forex_v211_schedule_diagnostics import diagnose

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 4, 21, 20, tzinfo=UTC)

def run(minute, conclusion="success"):
    t = NOW - dt.timedelta(minutes=minute)
    return {"head_branch": "main", "name": "Forex Bot Pro V2 - Paper EURUSD",
            "event": "schedule", "created_at": t.isoformat(),
            "html_url": f"https://github.com/example/actions/runs/{minute}",
            "conclusion": conclusion}

def state(minute, opened=True):
    return {"last_checked_at": (NOW - dt.timedelta(minutes=minute)).isoformat(),
            "open_position": opened, "position_side": "SELL",
            "status": "monitoring", "gap_halt": False, "balance": 99.5}

class ScheduleDiagnosticTests(unittest.TestCase):
    def test_critical_position_and_long_intervals(self):
        result = diagnose([run(44), run(104)], state(44), NOW)
        types = {a["type"] for a in result["anomalies"]}
        self.assertIn("critical_open_position_unchecked", types)
        self.assertIn("schedule_interval", types)
        self.assertIn("run_late", types)

    def test_no_false_critical_when_position_closed(self):
        result = diagnose([run(5), run(20)], state(40, False), NOW)
        types = {a["type"] for a in result["anomalies"]}
        self.assertNotIn("critical_open_position_unchecked", types)
        self.assertIn("state_stale", types)

    def test_api_and_gap(self):
        s = state(5)
        s.update(status="api_error", gap_halt=True)
        types = {a["type"] for a in diagnose([run(5)], s, NOW)["anomalies"]}
        self.assertIn("api_error", types)
        self.assertIn("gap_halt_or_data_gap", types)

    def test_branch_and_event_filter(self):
        other = dict(run(1), head_branch="feature")
        result = diagnose([other, run(10)], state(10), NOW)
        self.assertEqual(result["scheduled_runs"], 1)


    def test_missing_state_timestamp_is_reported(self):
        s = state(5)
        del s["last_checked_at"]
        result = diagnose([run(5)], s, NOW)
        self.assertIn("missing_state_timestamp", {a["type"] for a in result["anomalies"]})

    def test_queue_delay_is_separate_from_scheduler_gap(self):
        r = run(20)
        r["run_started_at"] = (NOW - dt.timedelta(minutes=5)).isoformat()
        result = diagnose([r], state(5), NOW)
        types = {a["type"] for a in result["anomalies"]}
        self.assertIn("runner_queue_delay", types)
        self.assertNotIn("schedule_interval", types)

    def test_timezone_naive_now_is_rejected(self):
        with self.assertRaises(ValueError):
            diagnose([run(5)], state(5), NOW.replace(tzinfo=None))

    def test_missing_runs_does_not_claim_no_position(self):
        result = diagnose([], state(40), NOW)
        types = {a["type"] for a in result["anomalies"]}
        self.assertIn("no_scheduled_runs_in_response", types)
        self.assertIn("critical_open_position_unchecked", types)

if __name__ == "__main__":
    unittest.main()
