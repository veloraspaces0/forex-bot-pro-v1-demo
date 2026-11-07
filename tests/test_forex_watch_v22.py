"""Offline regression tests for the read-only Forex Watch V2.2 prototype."""
import copy
import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

WATCHER_PATH = Path(__file__).resolve().parents[1] / "experimentos" / "forex_watch_v22.py"
spec = importlib.util.spec_from_file_location("forex_watch_v22", WATCHER_PATH)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

NOW = datetime(2026, 11, 4, 15, 52, tzinfo=timezone.utc)

def stamp(delta_minutes):
    return (NOW - timedelta(minutes=delta_minutes)).isoformat().replace("+00:00", "Z")

def state(minutes=13, open_position=False, status="monitoring", gap=False):
    return dict(last_checked_at=stamp(minutes), updated_at=stamp(minutes), status=status,
                gap_halt=gap, open_position=open_position,
                position_side="SELL" if open_position else None,
                balance=99.5, estimated_equity=99.45, closed_trades=1, risk_violations=0)

def runs(minute_gaps=(13, 28, 43), conclusion="success"):
    return [dict(name=watch.WORKFLOW_NAME, head_branch="main", event="schedule",
                 created_at=stamp(minutes), status="completed", conclusion=conclusion if i == 0 else "success")
            for i, minutes in enumerate(minute_gaps)]

class WatchV22Tests(unittest.TestCase):
    def test_healthy_15min_schedule(self):
        data = watch.assess(state(), runs(), NOW)
        self.assertEqual(data["status"], "OK")
        self.assertEqual(data["recent_schedule_intervals_min"], [15.0, 15.0])

    def test_realistic_55min_schedule_triggers_attention(self):
        data = watch.assess(state(open_position=True), runs((13, 68, 123)), NOW)
        self.assertEqual(data["status"], "ATENCAO")
        self.assertTrue(any("Intervalos" in x for x in data["reasons"]))
        self.assertEqual(data["recent_schedule_intervals_min"], [55.0, 55.0])

    def test_open_position_with_stale_status_is_critical(self):
        report = watch.assess(state(minutes=36, open_position=True), runs((13, 28, 43)), NOW)
        self.assertEqual(report["status"], "CRITICO")
        self.assertTrue(any("Posição paper" in x for x in report["reasons"]))

    def test_gap_halt_is_critical(self):
        report = watch.assess(state(gap=True), runs(), NOW)
        self.assertEqual(report["status"], "CRITICO")
        self.assertTrue(any("lacuna" in x for x in report["reasons"]))

    def test_latest_run_failure_is_critical(self):
        report = watch.assess(state(), runs(conclusion="failure"), NOW)
        self.assertEqual(report["status"], "CRITICO")

    def test_missing_runs_is_critical(self):
        report = watch.assess(state(), [], NOW)
        self.assertEqual(report["status"], "CRITICO")

    def test_api_error_with_open_position_is_critical(self):
        report = watch.assess(state(status="api_error", open_position=True), runs(), NOW)
        self.assertEqual(report["status"], "CRITICO")

    def test_no_data_mutation(self):
        a, b = state(open_position=True), runs((13, 68, 123))
        old_a, old_b = copy.deepcopy(a), copy.deepcopy(b)
        watch.assess(a, b, NOW)
        self.assertEqual(a, old_a)
        self.assertEqual(b, old_b)

    def test_future_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            watch.age_minutes(stamp(-10), NOW)

    def test_different_workflow_ignored(self):
        r = runs()
        r.insert(0, dict(name="pages build and deployment", head_branch="main",
                         event="dynamic", created_at=stamp(1), status="completed", conclusion="failure"))
        self.assertEqual(watch.assess(state(), r, NOW)["status"], "OK")

if __name__ == "__main__":
    unittest.main()
