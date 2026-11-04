import datetime as dt
import unittest
import io
import urllib.error
from unittest.mock import patch
from experimentos.forex_v211_schedule_diagnostics import diagnose, fetch_workflow_runs, fetch_json, forex_market_open

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 4, 21, 20, tzinfo=UTC)

def run(minute, conclusion="success"):
    t = NOW - dt.timedelta(minutes=minute)
    return {"head_branch": "main", "name": "Forex Bot Pro V2 - Paper EURUSD",
            "event": "schedule", "id": minute + 100000, "created_at": t.isoformat(),
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
        r = dict(run(25), status="queued", conclusion=None)
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
        pages = [
            {"total_count": 400, "workflow_runs": [run(i) for i in range(100)]},
            {"total_count": 400, "workflow_runs": [run(i) for i in range(100, 200)]},
        ]
        with patch("experimentos.forex_v211_schedule_diagnostics.fetch_json", side_effect=pages):
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


    def test_weekend_candle_staleness_is_informational(self):
        now = dt.datetime(2026, 11, 7, 18, tzinfo=UTC)
        s = state(5)
        s["last_candle_at"] = (now - dt.timedelta(hours=20)).isoformat()
        result = diagnose([run(5)], s, now)
        self.assertNotIn("candle_stale", {a["type"] for a in result["anomalies"]})
        self.assertGreater(result["market_closed_candle_age_minutes"], 45)

    def test_weekday_candle_staleness_is_alert(self):
        s = state(5)
        s["last_candle_at"] = (NOW - dt.timedelta(hours=2)).isoformat()
        result = diagnose([run(5)], s, NOW)
        self.assertIn("candle_stale", {a["type"] for a in result["anomalies"]})

    def test_forex_session_boundaries(self):
        self.assertFalse(forex_market_open(dt.datetime(2026, 11, 7, 12, tzinfo=UTC)))
        self.assertTrue(forex_market_open(dt.datetime(2026, 11, 8, 22, tzinfo=UTC)))
        self.assertFalse(forex_market_open(dt.datetime(2026, 11, 6, 22, tzinfo=UTC)))


    def test_recent_queued_run_has_grace_period(self):
        r = dict(run(5), status="queued", conclusion=None)
        result = diagnose([r], state(5), NOW)
        self.assertNotIn("run_not_completed", {a["type"] for a in result["anomalies"]})

    def test_historical_schedule_gaps_not_current_alerts(self):
        result = diagnose([run(200), run(260)], state(5), NOW)
        self.assertNotIn("schedule_interval", {a["type"] for a in result["anomalies"]})
        self.assertIn("schedule_interval", {a["type"] for a in result["historical_anomalies"]})

    def test_recent_schedule_gap_still_alerts(self):
        result = diagnose([run(10), run(70)], state(5), NOW)
        self.assertIn("schedule_interval", {a["type"] for a in result["anomalies"]})

    def test_historical_failure_is_retained(self):
        result = diagnose([run(5), run(180, "failure")], state(5), NOW)
        self.assertNotIn("workflow_failure", {a["type"] for a in result["anomalies"]})
        self.assertIn("workflow_failure", {a["type"] for a in result["historical_anomalies"]})


    def test_missing_run_id_fails_closed(self):
        item = run(5)
        del item["id"]
        with patch("experimentos.forex_v211_schedule_diagnostics.fetch_json",
                   return_value={"total_count": 1, "workflow_runs": [item]}):
            with self.assertRaises(ValueError):
                fetch_workflow_runs("https://api.github.com/repos/example/repo")

    def test_old_queue_delay_is_historical(self):
        r = run(180)
        r["run_started_at"] = (NOW - dt.timedelta(minutes=160)).isoformat()
        result = diagnose([run(5), r], state(5), NOW)
        self.assertNotIn("runner_queue_delay", {a["type"] for a in result["anomalies"]})
        self.assertIn("runner_queue_delay", {a["type"] for a in result["historical_anomalies"]})

    def test_recent_queue_delay_still_alerts(self):
        r = run(40)
        r["run_started_at"] = (NOW - dt.timedelta(minutes=20)).isoformat()
        result = diagnose([r], state(5), NOW)
        self.assertIn("runner_queue_delay", {a["type"] for a in result["anomalies"]})


    def test_http_429_retries_then_succeeds(self):
        err = urllib.error.HTTPError("https://api.github.com/test", 429, "rate limit", {}, None)
        response = io.BytesIO(b'{"ok": true}')
        with patch("experimentos.forex_v211_schedule_diagnostics.urllib.request.urlopen",
                   side_effect=[err, response]) as opened, patch(
                   "experimentos.forex_v211_schedule_diagnostics.time.sleep") as sleeping:
            self.assertEqual(fetch_json("https://api.github.com/test"), {"ok": True})
        self.assertEqual(opened.call_count, 2)
        sleeping.assert_called_once()

    def test_http_403_without_rate_limit_does_not_retry(self):
        err = urllib.error.HTTPError("https://api.github.com/test", 403, "forbidden", {}, None)
        with patch("experimentos.forex_v211_schedule_diagnostics.urllib.request.urlopen",
                   side_effect=err) as opened:
            with self.assertRaises(urllib.error.HTTPError):
                fetch_json("https://api.github.com/test")
        self.assertEqual(opened.call_count, 1)

    def test_http_503_retries_are_bounded(self):
        err = urllib.error.HTTPError("https://api.github.com/test", 503, "unavailable", {}, None)
        with patch("experimentos.forex_v211_schedule_diagnostics.urllib.request.urlopen",
                   side_effect=err) as opened, patch(
                   "experimentos.forex_v211_schedule_diagnostics.time.sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                fetch_json("https://api.github.com/test")
        self.assertEqual(opened.call_count, 3)


    def test_retry_attempt_bounds(self):
        for n in (0, -1, 6, True, 1.5):
            with self.subTest(n=n), self.assertRaises(ValueError):
                fetch_json("https://api.github.com/test", attempts=n)

    def test_retry_after_nan_is_ignored(self):
        err = urllib.error.HTTPError("https://api.github.com/test", 429, "rate", {"Retry-After": "NaN"}, None)
        with patch("experimentos.forex_v211_schedule_diagnostics.urllib.request.urlopen",
                   side_effect=[err, io.BytesIO(b'{}')]), patch(
                   "experimentos.forex_v211_schedule_diagnostics.time.sleep") as sleeping:
            self.assertEqual(fetch_json("https://api.github.com/test"), {})
        self.assertEqual(sleeping.call_args.args[0], 1)

    def test_old_unfinished_run_is_historical(self):
        r = dict(run(180), status="in_progress", conclusion=None)
        result = diagnose([run(5), r], state(5), NOW)
        self.assertNotIn("run_not_completed", {a["type"] for a in result["anomalies"]})
        self.assertIn("run_not_completed", {a["type"] for a in result["historical_anomalies"]})

    def test_boolean_risk_counter_not_treated_as_positive_count(self):
        st = state(5)
        st["risk_violations"] = True
        result = diagnose([run(5)], st, NOW)
        kinds = {a["type"] for a in result["anomalies"]}
        self.assertIn("invalid_risk_violations", kinds)
        self.assertNotIn("risk_violations", kinds)


    def test_duplicate_page_is_reported(self):
        page = {"total_count": 250, "workflow_runs": [run(i) for i in range(100)]}
        with patch("experimentos.forex_v211_schedule_diagnostics.fetch_json",
                   side_effect=[page, page]):
            result = fetch_workflow_runs("https://api.github.com/repos/example/repo", max_pages=2)
        self.assertIn("duplicate_page", result["pagination_anomalies"])
        self.assertTrue(result["history_incomplete"])

    def test_malformed_gap_halt_flag(self):
        st = state(5)
        st["gap_halt"] = "false"
        result = diagnose([run(5)], st, NOW)
        kinds = {a["type"] for a in result["anomalies"]}
        self.assertIn("invalid_gap_halt_flag", kinds)
        self.assertNotIn("gap_halt_or_data_gap", kinds)

    def test_valid_gap_halt_true(self):
        st = state(5)
        st["gap_halt"] = True
        kinds = {a["type"] for a in diagnose([run(5)], st, NOW)["anomalies"]}
        self.assertIn("gap_halt_or_data_gap", kinds)


    def test_recent_and_historical_run_counts(self):
        result = diagnose([run(5), run(45), run(180)], state(5), NOW)
        self.assertEqual(result["recent_scheduled_runs"], 2)
        self.assertEqual(result["historical_scheduled_runs"], 1)

    def test_queue_delay_metrics(self):
        r = run(15)
        r["run_started_at"] = (NOW - dt.timedelta(minutes=8)).isoformat()
        result = diagnose([r], state(5), NOW)
        self.assertEqual(result["queue_delays_minutes"], [7.0])
        self.assertEqual(result["max_queue_delay_minutes"], 7.0)

    def test_queue_delay_metric_absent_without_start_time(self):
        result = diagnose([run(5)], state(5), NOW)
        self.assertEqual(result["queue_delays_minutes"], [])
        self.assertIsNone(result["max_queue_delay_minutes"])


    def test_recent_schedule_gap_has_both_timestamps(self):
        newest, older = run(5), run(50)
        result = diagnose([newest, older], state(5), NOW)
        self.assertEqual(len(result["recent_schedule_gaps"]), 1)
        gap = result["recent_schedule_gaps"][0]
        self.assertEqual(gap["newer_created_at"], newest["created_at"])
        self.assertEqual(gap["older_created_at"], older["created_at"])
        self.assertEqual(gap["gap_minutes"], 45.0)

    def test_old_schedule_gap_not_in_recent_evidence(self):
        result = diagnose([run(5), run(180), run(240)], state(5), NOW)
        self.assertEqual(len(result["recent_schedule_gaps"]), 1)
        self.assertEqual(result["recent_schedule_gaps"][0]["gap_minutes"], 175.0)


    def test_cron_slot_dispatch_offset_at_expected_minute(self):
        r = run(5)
        r["created_at"] = "2026-11-04T21:37:00Z"
        result = diagnose([r], state(5), NOW)
        self.assertEqual(result["max_dispatch_offset_minutes"], 0.0)

    def test_cron_slot_dispatch_offset_late_run(self):
        r = run(5)
        r["created_at"] = "2026-11-04T21:42:30Z"
        result = diagnose([r], state(5), NOW)
        self.assertEqual(result["max_dispatch_offset_minutes"], 5.5)


    def test_auth_not_forwarded_to_similar_host(self):
        with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}), patch(
            "experimentos.forex_v211_schedule_diagnostics.urllib.request.urlopen",
            return_value=io.BytesIO(b'{}')) as opened:
            fetch_json("https://api.github.com.evil.test/repos/veloraspaces0/forex-bot-pro-v1-demo/x")
        self.assertIsNone(opened.call_args.args[0].get_header("Authorization"))

    def test_auth_not_forwarded_to_userinfo_host(self):
        with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}), patch(
            "experimentos.forex_v211_schedule_diagnostics.urllib.request.urlopen",
            return_value=io.BytesIO(b'{}')) as opened:
            fetch_json("https://api.github.com@evil.test/repos/veloraspaces0/forex-bot-pro-v1-demo/x")
        self.assertIsNone(opened.call_args.args[0].get_header("Authorization"))

    def test_null_http_error_headers_fail_closed(self):
        err = urllib.error.HTTPError("https://api.github.com/x", 403, "forbidden", None, None)
        with patch("experimentos.forex_v211_schedule_diagnostics.urllib.request.urlopen",
                   side_effect=err) as opened:
            with self.assertRaises(urllib.error.HTTPError):
                fetch_json("https://api.github.com/x")
        self.assertEqual(opened.call_count, 1)

    def test_zero_total_with_nonempty_page_is_anomaly(self):
        with patch("experimentos.forex_v211_schedule_diagnostics.fetch_json",
                   return_value={"total_count": 0, "workflow_runs": [run(5)]}):
            result = fetch_workflow_runs("https://api.github.com/repos/example/repo")
        self.assertIn("nonempty_page_with_zero_total", result["pagination_anomalies"])

    def test_empty_page_not_duplicate_page(self):
        with patch("experimentos.forex_v211_schedule_diagnostics.fetch_json",
                   return_value={"total_count": 1, "workflow_runs": []}):
            result = fetch_workflow_runs("https://api.github.com/repos/example/repo")
        self.assertNotIn("duplicate_page", result["pagination_anomalies"])

    def test_naive_now_rejected_with_clear_error(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            diagnose([run(5)], state(5), dt.datetime(2026, 11, 4, 21, 20))

    def test_future_created_run_flagged(self):
        result = diagnose([run(-15)], state(5), NOW)
        self.assertIn("future_run_created_at", {a["type"] for a in result["anomalies"]})

    def test_old_inconsistent_status_is_historical(self):
        result = diagnose([run(5), dict(run(180), status="queued")], state(5), NOW)
        self.assertNotIn("inconsistent_run_status", {a["type"] for a in result["anomalies"]})
        self.assertIn("inconsistent_run_status", {a["type"] for a in result["historical_anomalies"]})

    def test_old_missing_conclusion_is_historical(self):
        result = diagnose([run(5), dict(run(180), status="completed", conclusion=None)], state(5), NOW)
        self.assertNotIn("missing_run_conclusion", {a["type"] for a in result["anomalies"]})
        self.assertIn("missing_run_conclusion", {a["type"] for a in result["historical_anomalies"]})

    def test_future_success_does_not_mask_missing_success(self):
        result = diagnose([run(-15)], state(5), NOW)
        self.assertIn("no_successful_scheduled_runs", {a["type"] for a in result["anomalies"]})


    def test_fifty_cron_slot_offsets_adversarial(self):
        # 50 independent input cases, with one assertion group per minute.
        # Checks rollover across hours and the exact 07/22/37/52 UTC schedule.
        for minute in range(50):
            with self.subTest(minute=minute):
                r = run(5)
                r["created_at"] = NOW.replace(hour=20, minute=minute, second=30).isoformat()
                result = diagnose([r], state(5), NOW)
                expected = round((((minute - 7) % 15) * 60 + 30) / 60, 2)
                self.assertEqual(result["max_dispatch_offset_minutes"], expected)
                self.assertEqual(result["max_recent_dispatch_offset_minutes"], expected)
                self.assertEqual(result["recent_scheduled_runs"], 1)

    def test_future_run_not_used_as_last_run(self):
        result = diagnose([run(-15), run(40)], state(5), NOW)
        self.assertEqual(result["last_run_age_minutes"], 40)
        self.assertIn("run_late", {a["type"] for a in result["anomalies"]})

    def test_future_only_run_is_not_healthy(self):
        result = diagnose([run(-15)], state(5), NOW)
        self.assertIn("no_valid_scheduled_runs", {a["type"] for a in result["anomalies"]})
        self.assertIsNone(result["max_recent_dispatch_offset_minutes"])


    def test_one_hundred_schedule_cases_with_exact_coverage(self):
        # 100 independently evaluated cases: four full quarter-hour cycles
        # plus boundaries across hours, including expected cron slots.
        for minute_offset in range(100):
            with self.subTest(case=minute_offset):
                stamp = NOW - dt.timedelta(minutes=minute_offset)
                r = run(minute_offset)
                r["created_at"] = stamp.isoformat()
                report = diagnose([r], state(5), NOW)
                expected = round((((stamp.minute - 7) % 15) * 60 + stamp.second) / 60, 2)
                self.assertEqual(report["max_dispatch_offset_minutes"], expected)
                self.assertEqual(report["schedule_dispatch_offsets_minutes"], [expected])
                self.assertEqual(report["recent_dispatch_sample_count"], 1)
                self.assertEqual(report["recent_scheduled_runs"], 1)
                self.assertEqual(report["last_run_age_minutes"], float(minute_offset))

    def test_missing_queue_start_is_not_zero_delay_evidence(self):
        report = diagnose([run(5)], state(5), NOW)
        self.assertEqual(report["queue_delay_sample_count"], 0)
        self.assertEqual(report["queue_delay_missing_count"], 1)
        self.assertIsNone(report["max_queue_delay_minutes"])

    def test_queue_coverage_counts_mixed_runs(self):
        started = run(5)
        started["run_started_at"] = started["created_at"]
        report = diagnose([started, run(20)], state(5), NOW)
        self.assertEqual(report["queue_delay_sample_count"], 1)
        self.assertEqual(report["queue_delay_missing_count"], 1)
        self.assertEqual(report["max_queue_delay_minutes"], 0.0)

if __name__ == "__main__":
    unittest.main()
