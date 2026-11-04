import datetime as dt
import unittest
from unittest.mock import patch
from experimentos.forex_v211_schedule_diagnostics import diagnose, fetch_workflow_runs

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


    def test_future_state_timestamp_is_flagged(self):
        s = state(-20)
        result = diagnose([run(5)], s, NOW)
        self.assertIn("future_timestamp", {a["type"] for a in result["anomalies"]})
        self.assertIsNone(result["state_age_minutes"])

    def test_missing_timestamp_open_position_is_critical(self):
        s = state(5)
        del s["last_checked_at"]
        result = diagnose([run(5)], s, NOW)
        self.assertIn("critical_open_position_unchecked", {a["type"] for a in result["anomalies"]})

    def test_hundred_runs_are_not_automatically_incomplete(self):
        result = diagnose([run(i * 15 + 1) for i in range(100)], state(5), NOW)
        self.assertNotIn("history_truncated", {a["type"] for a in result["anomalies"]})

    def test_inconsistent_run_start_is_flagged(self):
        r = run(5)
        r["run_started_at"] = (NOW - dt.timedelta(minutes=10)).isoformat()
        result = diagnose([r], state(5), NOW)
        self.assertIn("inconsistent_run_timestamps", {a["type"] for a in result["anomalies"]})


    def test_future_timestamp_open_position_is_critical(self):
        result = diagnose([run(5)], state(-20), NOW)
        self.assertIn("critical_open_position_unchecked", {a["type"] for a in result["anomalies"]})

    def test_timed_out_run_is_failure(self):
        result = diagnose([run(5, "timed_out")], state(5), NOW)
        self.assertIn("workflow_failure", {a["type"] for a in result["anomalies"]})

    def test_queued_run_is_not_success(self):
        r = dict(run(5), status="queued", conclusion=None)
        result = diagnose([r], state(5), NOW)
        self.assertIn("run_not_completed", {a["type"] for a in result["anomalies"]})

    def test_no_matching_schedule_is_explicit(self):
        r = dict(run(5), event="push")
        result = diagnose([r], state(5), NOW)
        self.assertIn("no_matching_scheduled_runs", {a["type"] for a in result["anomalies"]})


    def test_pagination_fetches_second_page(self):
        pages = [
            {"total_count": 101, "workflow_runs": [run(i) for i in range(100)]},
            {"total_count": 101, "workflow_runs": [run(101)]},
        ]
        with patch("experimentos.forex_v211_schedule_diagnostics.fetch_json", side_effect=pages) as mocked:
            result = fetch_workflow_runs("https://api.github.com/repos/example/repo")
        self.assertEqual(len(result["workflow_runs"]), 101)
        self.assertFalse(result["history_incomplete"])
        self.assertIn("page=2", mocked.call_args_list[1].args[0])

    def test_pagination_limit_flags_incomplete(self):
        with patch("experimentos.forex_v211_schedule_diagnostics.fetch_json",
                   return_value={"total_count": 400, "workflow_runs": [run(i) for i in range(100)]}):
            result = fetch_workflow_runs("https://api.github.com/repos/example/repo", max_pages=2)
        self.assertEqual(len(result["workflow_runs"]), 200)
        self.assertTrue(result["history_incomplete"])

    def test_malformed_api_payload_rejected(self):
        with patch("experimentos.forex_v211_schedule_diagnostics.fetch_json",
                   return_value={"workflow_runs": []}):
            with self.assertRaises(ValueError):
                fetch_workflow_runs("https://api.github.com/repos/example/repo")


    def test_latest_run_not_equal_last_success(self):
        failed = run(5, "failure")
        passed = run(50, "success")
        result = diagnose([failed, passed], state(5), NOW)
        types = {a["type"] for a in result["anomalies"]}
        self.assertIn("last_success_stale", types)
        self.assertEqual(result["last_success_age_minutes"], 50)

    def test_no_successful_runs(self):
        result = diagnose([run(5, "failure")], state(5), NOW)
        self.assertIn("no_successful_scheduled_runs", {a["type"] for a in result["anomalies"]})

    def test_duplicate_pages_deduplicated(self):
        page = {"total_count": 150, "workflow_runs": [dict(run(i), id=i + 1) for i in range(100)]}
        with patch("experimentos.forex_v211_schedule_diagnostics.fetch_json", return_value=page):
            result = fetch_workflow_runs("https://api.github.com/repos/example/repo", max_pages=2)
        self.assertEqual(len(result["workflow_runs"]), 100)
        self.assertTrue(result["history_incomplete"])

    def test_invalid_pagination_limit(self):
        with self.assertRaises(ValueError):
            fetch_workflow_runs("https://api.github.com/repos/example/repo", max_pages=0)

    def test_timezone_offsets_sorted_correctly(self):
        newer = run(5)
        older = run(15)
        newer["created_at"] = "2026-11-04T18:15:00-03:00"
        older["created_at"] = "2026-11-04T21:05:00+00:00"
        result = diagnose([older, newer], state(5), NOW)
        self.assertEqual(result["last_run_age_minutes"], 5)

    def test_live_mode_and_risk_violation_alerts(self):
        s = state(5)
        s.update(mode="LIVE", pair="GBP/USD", risk_violations=1)
        result = diagnose([run(5)], s, NOW)
        types = {a["type"] for a in result["anomalies"]}
        self.assertTrue({"unexpected_mode", "unexpected_pair", "risk_violations"} <= types)

    def test_invalid_run_type_rejected(self):
        with self.assertRaises(ValueError):
            diagnose([None], state(5), NOW)


    def test_pagination_empty_second_page_detected(self):
        pages = [
            {"total_count": 150, "workflow_runs": [dict(run(i), id=i + 1) for i in range(100)]},
            {"total_count": 150, "workflow_runs": []},
        ]
        with patch("experimentos.forex_v211_schedule_diagnostics.fetch_json", side_effect=pages):
            result = fetch_workflow_runs("https://api.github.com/repos/example/repo")
        self.assertTrue(result["history_incomplete"])
        self.assertIn("unexpected_empty_page", result["pagination_anomalies"])

    def test_pagination_total_count_changed_detected(self):
        pages = [
            {"total_count": 150, "workflow_runs": [dict(run(i), id=i + 1) for i in range(100)]},
            {"total_count": 160, "workflow_runs": [dict(run(i), id=i + 1) for i in range(100, 160)]},
        ]
        with patch("experimentos.forex_v211_schedule_diagnostics.fetch_json", side_effect=pages):
            result = fetch_workflow_runs("https://api.github.com/repos/example/repo")
        self.assertIn("total_count_changed", result["pagination_anomalies"])

    def test_skipped_run_flagged(self):
        result = diagnose([run(5, "skipped")], state(5), NOW)
        self.assertIn("workflow_failure", {a["type"] for a in result["anomalies"]})

    def test_unfinished_success_not_counted(self):
        r = dict(run(5), status="in_progress")
        result = diagnose([r], state(5), NOW)
        self.assertIn("no_successful_scheduled_runs", {a["type"] for a in result["anomalies"]})

    def test_stale_candle_detected(self):
        s = state(5)
        s["last_candle_at"] = (NOW - dt.timedelta(minutes=60)).isoformat()
        result = diagnose([run(5)], s, NOW)
        self.assertIn("candle_stale", {a["type"] for a in result["anomalies"]})

    def test_missing_candle_detected(self):
        result = diagnose([run(5)], state(5), NOW)
        self.assertIn("missing_last_candle_timestamp", {a["type"] for a in result["anomalies"]})

    def test_missing_safety_mode_and_pair(self):
        result = diagnose([run(5)], state(5), NOW)
        types = {a["type"] for a in result["anomalies"]}
        self.assertTrue({"missing_mode", "missing_pair"} <= types)

    def test_invalid_risk_counter_detected(self):
        s = state(5)
        s["risk_violations"] = -1
        result = diagnose([run(5)], s, NOW)
        self.assertIn("invalid_risk_violations", {a["type"] for a in result["anomalies"]})


    def test_invalid_run_id_rejected(self):
        with patch("experimentos.forex_v211_schedule_diagnostics.fetch_json",
                   return_value={"total_count": 1, "workflow_runs": [dict(run(5), id=-1)]}):
            with self.assertRaises(ValueError):
                fetch_workflow_runs("https://api.github.com/repos/example/repo")

    def test_more_unique_runs_than_total_detected(self):
        with patch("experimentos.forex_v211_schedule_diagnostics.fetch_json",
                   return_value={"total_count": 1, "workflow_runs": [dict(run(5), id=1), dict(run(6), id=2)]}):
            result = fetch_workflow_runs("https://api.github.com/repos/example/repo")
        self.assertIn("more_unique_runs_than_total_count", result["pagination_anomalies"])

    def test_numeric_position_flag_invalid(self):
        s = state(5)
        s["open_position"] = 1
        types = {a["type"] for a in diagnose([run(5)], s, NOW)["anomalies"]}
        self.assertIn("invalid_open_position_flag", types)
        self.assertIn("position_state_unknown", types)

    def test_inconsistent_success_status(self):
        r = dict(run(5), status="queued")
        types = {a["type"] for a in diagnose([r], state(5), NOW)["anomalies"]}
        self.assertIn("inconsistent_run_status", types)

    def test_completed_without_conclusion(self):
        r = dict(run(5), status="completed", conclusion=None)
        types = {a["type"] for a in diagnose([r], state(5), NOW)["anomalies"]}
        self.assertIn("missing_run_conclusion", types)

    def test_missing_risk_counter(self):
        types = {a["type"] for a in diagnose([run(5)], state(5), NOW)["anomalies"]}
        self.assertIn("missing_risk_violations", types)

    def test_invalid_position_side(self):
        s = state(5)
        s["position_side"] = "HOLD"
        types = {a["type"] for a in diagnose([run(5)], s, NOW)["anomalies"]}
        self.assertIn("invalid_position_side", types)

    def test_candle_ahead_of_state(self):
        s = state(15)
        s["last_candle_at"] = (NOW - dt.timedelta(minutes=5)).isoformat()
        types = {a["type"] for a in diagnose([run(5)], s, NOW)["anomalies"]}
        self.assertIn("candle_ahead_of_state", types)

    def test_check_ahead_of_update(self):
        s = state(5)
        s["updated_at"] = (NOW - dt.timedelta(minutes=15)).isoformat()
        types = {a["type"] for a in diagnose([run(5)], s, NOW)["anomalies"]}
        self.assertIn("check_ahead_of_update", types)

    def test_fractional_risk_counter_invalid(self):
        s = state(5)
        s["risk_violations"] = 0.5
        types = {a["type"] for a in diagnose([run(5)], s, NOW)["anomalies"]}
        self.assertIn("invalid_risk_violations", types)


    def test_workflow_name_change_does_not_hide_schedule(self):
        r = dict(run(5), name="Renamed paper workflow")
        result = diagnose([r], state(5), NOW)
        self.assertEqual(result["scheduled_runs"], 1)

    def test_invalid_timestamp_none_is_value_error(self):
        r = dict(run(5), created_at=None)
        with self.assertRaises(ValueError):
            diagnose([r], state(5), NOW)

    def test_boolean_pagination_limit_rejected(self):
        with self.assertRaises(ValueError):
            fetch_workflow_runs("https://api.github.com/repos/example/repo", max_pages=True)

if __name__ == "__main__":
    unittest.main()
