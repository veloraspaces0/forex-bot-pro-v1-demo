"""Adversarial, no-network regressions for Forex Watch V2.3."""
import base64
import copy
import importlib.util
import io
import json
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "forex_watch_v23", Path(__file__).resolve().parents[1] / "experimentos" / "forex_watch_v23.py")
watch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watch)
NOW = datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc)

def stamp(age, now=NOW):
    return (now - timedelta(minutes=age)).isoformat().replace("+00:00", "Z")

def state(age=4, candle_age=20, opened=False, now=NOW, **updates):
    result = {
        "mode": "PAPER_ONLY", "pair": "EUR/USD", "interval": "15min",
        "risk_violations": 0, "last_checked_at": stamp(age, now), "updated_at": stamp(age, now),
        "last_candle_at": stamp(candle_age, now),
        "open_position": opened, "position_side": "SELL" if opened else None,
        "status": "monitoring", "gap_halt": False, "balance": 99.5, "estimated_equity": 99.45,
        "initial_balance": 100, "pnl_total": -0.55,
        "closed_trades": 1, "wins": 0, "losses": 1,
        "closed_results": [{"at": "2026-11-04T07:30:00Z", "side": "SELL", "pnl": -0.5}],
        "equity_curve": [{"n": 0, "balance": 100}, {"n": 1, "balance": 99.5}]
    }
    result.update(updates)
    return result

def runs(ages=(4, 19, 34, 49, 64), now=NOW):
    return [{
        "name": watch.WORKFLOW_NAME, "head_branch": "main", "event": "schedule",
        "status": "completed", "conclusion": "success", "created_at": stamp(age, now),
        "run_started_at": stamp(age - 1, now), "id": k + 1
    } for k, age in enumerate(ages)]

def reasons(report):
    return {entry["code"] for entry in report["issues"]}

class V23(unittest.TestCase):
    def test_healthy(self):
        out = watch.assess(state(), runs(), NOW)
        self.assertEqual(out["status"], "OK")
        self.assertEqual(out["recent_schedule_intervals_min"], [15, 15, 15, 15])

    def test_candle_stale_while_check_fresh(self):
        out = watch.assess(state(age=2, candle_age=120), runs(), NOW)
        self.assertEqual(out["status"], "CRITICO")
        self.assertIn("CANDLE_STALE", reasons(out))

    def test_position_candle_stale(self):
        out = watch.assess(state(candle_age=37, opened=True), runs(), NOW)
        self.assertIn("POSITION_CANDLE_STALE", reasons(out))

    def test_position_last_check_stale(self):
        out = watch.assess(state(age=36, opened=True), runs(), NOW)
        self.assertIn("POSITION_UNCHECKED", reasons(out))

    def test_stuck_running_accurate_reason(self):
        rr = runs()
        rr[0].update(status="in_progress", conclusion=None, run_started_at=stamp(26))
        out = watch.assess(state(), rr, NOW)
        self.assertIn("RUN_STUCK", reasons(out))
        self.assertNotIn("SCHEDULE_MISSING", reasons(out))

    def test_queue_stuck_accurate_reason(self):
        rr = runs((24, 39, 54, 69, 84))
        rr[0].update(status="queued", conclusion=None)
        out = watch.assess(state(), rr, NOW)
        self.assertIn("QUEUE_STUCK", reasons(out))
        self.assertNotIn("SCHEDULE_MISSING", reasons(out))

    def test_api_error_without_position_is_reported(self):
        out = watch.assess(state(status="api_error"), runs(), NOW)
        self.assertIn("API_ERROR", reasons(out))
        self.assertEqual(out["status"], "ATENCAO")

    def test_api_error_persistent_is_critical(self):
        out = watch.assess(state(age=40, status="api_error"), runs(), NOW)
        self.assertEqual(out["status"], "CRITICO")
        self.assertIn("API_ERROR", reasons(out))

    def test_risk_violation_in_public_state_is_critical(self):
        out = watch.assess(state(risk_violations=1), runs(), NOW)
        self.assertEqual(out["status"], "CRITICO")
        self.assertIn("RISK_VIOLATIONS", reasons(out))

    def test_public_state_schema_requires_valid_paper_fields(self):
        for updates in ({"mode": None}, {"pair": "GBP/USD"}, {"interval": None},
                        {"open_position": "false"}, {"risk_violations": -1},
                        {"balance": float("nan")}, {"closed_trades": -2}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                watch.assess(state(**updates), runs(), NOW)

    def test_public_history_consistency_alarm(self):
        broken_cases = (
            {"closed_trades": 2},
            {"wins": 2},
            {"losses": 2},
            {"closed_results": []},
            {"equity_curve": [{"n": 0, "balance": 100}]},
            {"equity_curve": [{"n": 1, "balance": 95}]},
            {"pnl_total": -9.0},
        )
        for update in broken_cases:
            with self.subTest(update=update):
                out=watch.assess(state(**update),runs(),NOW)
                self.assertEqual(out["status"],"CRITICO")
                self.assertIn("PUBLIC_HISTORY_INCONSISTENT",reasons(out))

    def test_gap_halt(self):
        out = watch.assess(state(gap_halt=True), runs(), NOW)
        self.assertIn("DATA_GAP", reasons(out))

    def test_sched_55min_gap(self):
        out = watch.assess(state(), runs((4, 59, 114)), NOW)
        self.assertIn("CADENCE_DELAY", reasons(out))
        self.assertEqual(out["recent_schedule_intervals_min"], [55, 55])

    def test_one_schedule_avoids_false_green(self):
        out = watch.assess(state(), runs((4,)), NOW)
        self.assertIn("INSUFFICIENT_SCHEDULE_HISTORY", reasons(out))

    def test_previous_failed_run_not_hidden_by_latest_success(self):
        rr = runs()
        rr[1]["conclusion"] = "failure"
        out = watch.assess(state(), rr, NOW)
        self.assertIn("RECENT_FAILURE", reasons(out))

    def test_unrelated_pages_run_filtered(self):
        rr = runs()
        rr.insert(0, {"name": "pages build and deployment", "head_branch": "main",
                      "created_at": stamp(1), "status": "completed", "conclusion": "failure"})
        self.assertEqual(watch.assess(state(), rr, NOW)["status"], "OK")

    def test_weekend_candle_stale_ignored(self):
        sat = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
        out = watch.assess(state(now=sat, candle_age=1200), runs(now=sat), sat)
        self.assertTrue(out["market_closed_weekend"])
        self.assertNotIn("CANDLE_STALE", reasons(out))

    def test_friday_after_new_york_closure(self):
        # 2026-07-03 Friday 21:00Z = 17:00 NY (EDT).
        self.assertTrue(watch.market_closed(datetime(2026, 7, 3, 21, 1, tzinfo=timezone.utc)))
        self.assertFalse(watch.market_closed(datetime(2026, 7, 3, 20, 59, tzinfo=timezone.utc)))

    def test_sunday_open_grace(self):
        sunday = datetime(2026, 11, 8, 22, 20, tzinfo=timezone.utc) # 17:20 NY EST
        out = watch.assess(state(now=sunday, candle_age=1200), runs(now=sunday), sunday)
        self.assertTrue(watch.sunday_open_grace(sunday))
        self.assertNotIn("CANDLE_STALE", reasons(out))

    def test_unclosed_sunday_after_grace(self):
        sunday = datetime(2026, 11, 8, 23, 0, tzinfo=timezone.utc)
        out = watch.assess(state(now=sunday, candle_age=1200), runs(now=sunday), sunday)
        self.assertIn("CANDLE_STALE", reasons(out))

    def test_timezone_naive_rejected(self):
        with self.assertRaises(ValueError):
            watch.utc(datetime(2026, 11, 4, 12))
        with self.assertRaises(ValueError):
            watch.utc("2026-11-04T10:00:00")

    def test_future_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            watch.assess(state(last_checked_at=stamp(-10)), runs(), NOW)

    def test_bad_mode_fails_closed(self):
        with self.assertRaises(ValueError):
            watch.assess(state(mode="LIVE"), runs(), NOW)

    def test_no_state_mutation(self):
        a, b = state(opened=True), runs()
        before = copy.deepcopy((a,b))
        watch.assess(a,b,NOW)
        self.assertEqual((a,b),before)

    def test_live_check_decodes_base64_and_reuses_assessor(self):
        s = state()
        payloads = {
            watch.STATE_ENDPOINT: {"encoding":"base64","content":base64.b64encode(json.dumps(s).encode()).decode()},
            watch.RUNS_ENDPOINT: {"workflow_runs":runs()}
        }
        self.assertEqual(watch.live_check(fetcher=lambda p: payloads[p],now=NOW)["status"],"OK")

    def test_live_check_bad_api_response_is_error(self):
        with self.assertRaises(ValueError):
            watch.live_check(fetcher=lambda p: {"status":"unexpected"}, now=NOW)

    def test_retry_after_transient_http_503(self):
        responses = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size): return b'{"ok":true}'
        def opener(request,timeout):
            responses.append(timeout)
            if len(responses)<3:
                raise urllib.error.HTTPError(request.full_url,503,"Service Unavailable",None,None)
            return Response()
        sleeps=[]
        data=watch.github_json(watch.RUNS_ENDPOINT,opener=opener,sleeper=sleeps.append)
        self.assertEqual(data, {"ok": True})
        self.assertEqual(sleeps,[1,2])
        self.assertEqual(len(responses),3)

    def test_no_retry_on_http_403(self):
        calls=[]
        def opener(request,timeout):
            calls.append(1)
            raise urllib.error.HTTPError(request.full_url,403,"Forbidden",None,None)
        with self.assertRaises(urllib.error.HTTPError):
            watch.github_json(watch.RUNS_ENDPOINT,opener=opener,sleeper=lambda _:None)
        self.assertEqual(len(calls),1)

    def test_unsafe_url_path_rejected(self):
        with self.assertRaises(ValueError):
            watch.github_json("repos/private/secret")
    def test_completed_workflow_unknown_result(self):
        rr=runs()
        rr[0]["conclusion"]=None
        self.assertIn("RUN_UNKNOWN",reasons(watch.assess(state(),rr,NOW)))

class V23Hardening(unittest.TestCase):
    def test_schedule_missing_90_minutes_critical(self):
        out=watch.assess(state(age=90,candle_age=90),runs(ages=(90,105,120)),NOW)
        self.assertEqual(out["status"],"CRITICO")
        self.assertIn("SCHEDULE_MISSING",reasons(out))

    def test_workflow_specific_runs_endpoint(self):
        self.assertIn("actions/workflows/forex-v2.yml/runs?",watch.RUNS_ENDPOINT)
        self.assertNotEqual(watch.RUNS_ENDPOINT,"actions/runs?branch=main&per_page=100")

    def test_running_without_start_time_does_not_claim_stuck(self):
        rr=runs(ages=(65,80,95,110))
        rr[0]["status"]="in_progress"
        rr[0]["conclusion"]=None
        rr[0].pop("run_started_at",None)
        out=watch.assess(state(),rr,NOW)
        self.assertIn("RUN_START_UNKNOWN",reasons(out))
        self.assertNotIn("RUN_STUCK",reasons(out))

    def test_http_429_retry_and_recovery(self):
        count=[]
        sleeps=[]
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,size): return b'{"workflow_runs":[]}'
        def opener(request,timeout):
            count.append(timeout)
            if len(count)==1:
                raise urllib.error.HTTPError(request.full_url,429,"Too Many Requests",None,None)
            return Response()
        out=watch.github_json(watch.RUNS_ENDPOINT,opener=opener,sleeper=sleeps.append)
        self.assertEqual(out,{"workflow_runs":[]})
        self.assertEqual(len(count),2)
        self.assertEqual(sleeps,[1])

class V23EngineStatuses(unittest.TestCase):
    def test_missing_api_key_is_critical_even_with_fresh_heartbeat(self):
        result = watch.assess(state(status="awaiting_api_key"), runs(), NOW)
        self.assertEqual(result["status"], "CRITICO")
        self.assertIn("API_CREDENTIAL_MISSING", reasons(result))

    def test_open_position_with_stale_provider_status(self):
        result = watch.assess(state(opened=True, status="market_closed_or_stale"), runs(), NOW)
        self.assertEqual(result["status"], "CRITICO")
        self.assertIn("MARKET_DATA_NOT_FRESH", reasons(result))

    def test_weekend_stale_provider_status_not_an_error(self):
        sat = datetime(2026, 11, 7, 16, tzinfo=timezone.utc)
        result = watch.assess(state(now=sat,candle_age=1000,status="market_closed_or_stale"),
                              runs(now=sat), sat)
        self.assertNotIn("MARKET_DATA_NOT_FRESH", reasons(result))

    def test_unknown_engine_status_not_silently_green(self):
        result = watch.assess(state(status="internal_error"), runs(), NOW)
        self.assertEqual(result["status"],"ATENCAO")
        self.assertIn("UNRECOGNIZED_BOT_STATUS",reasons(result))

if __name__ == "__main__":
    unittest.main()
