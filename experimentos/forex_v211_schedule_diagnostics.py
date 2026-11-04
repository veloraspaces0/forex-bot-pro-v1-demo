#!/usr/bin/env python3
"""Read-only Forex V2.1 schedule diagnostic. No orders, no state writes."""
import argparse
import datetime as dt
import json
import os
import time
import sys
import urllib.request
import urllib.error
import urllib.parse
from zoneinfo import ZoneInfo

REPO = "veloraspaces0/forex-bot-pro-v1-demo"
WORKFLOW = "forex-v2.yml"
UTC = dt.timezone.utc
NY = ZoneInfo("America/New_York")

def forex_market_open(now):
    """Approximate FX weekly session; Sunday 17:00 through Friday 17:00 NY."""
    local = now.astimezone(NY)
    weekday = local.weekday()
    return weekday < 4 or (weekday == 4 and local.hour < 17) or (weekday == 6 and local.hour >= 17)

def fetch_json(url, attempts=3):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "forex-v211-readonly-diagnostics",
    }
    parsed_url = urllib.parse.urlsplit(url)
    allowed_path = f"/repos/{REPO}/"
    if (parsed_url.scheme == "https" and parsed_url.netloc == "api.github.com"
            and parsed_url.path.startswith(allowed_path) and os.getenv("GITHUB_TOKEN")):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    request = urllib.request.Request(url, headers=headers)
    if type(attempts) is not int or not 1 <= attempts <= 5:
        raise ValueError("attempts must be between 1 and 5")
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            error_headers = exc.headers or {}
            remaining = error_headers.get("x-ratelimit-remaining")
            retry_after = error_headers.get("retry-after")
            transient = exc.code in (429, 500, 502, 503, 504) or (exc.code == 403 and (remaining == "0" or retry_after is not None))
            if not transient or attempt == attempts - 1:
                raise
            delay = min(8, 2 ** attempt)
            if retry_after is not None:
                try:
                    value = float(retry_after)
                    if 0 <= value < float("inf"):
                        delay = min(8, max(delay, value))
                except (ValueError, OverflowError):
                    pass
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
            time.sleep(min(8, 2 ** attempt))

def fetch_workflow_runs(base, max_pages=5):
    """Fetch bounded run history; fail closed if the API omits expected runs."""
    if type(max_pages) is not int or not 1 <= max_pages <= 20:
        raise ValueError("max_pages must be between 1 and 20")
    all_runs = []
    seen_ids = set()
    expected = None
    pagination_anomalies = []
    for page in range(1, max_pages + 1):
        payload = fetch_json(
            f"{base}/actions/workflows/{WORKFLOW}/runs?branch=main&per_page=100&page={page}"
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
            raise ValueError("Invalid workflow_runs API payload")
        if type(payload.get("total_count")) is not int:
            raise ValueError("Missing total_count in workflow_runs API payload")
        if payload["total_count"] < 0:
            raise ValueError("Invalid negative total_count")
        if expected is None:
            expected = payload["total_count"]
        batch = payload["workflow_runs"]
        if len(batch) > 100:
            raise ValueError("Workflow page exceeds per_page=100")
        if payload["total_count"] != expected:
            pagination_anomalies.append("total_count_changed")
        if expected == 0 and batch:
            pagination_anomalies.append("nonempty_page_with_zero_total")
        if not batch and len(all_runs) < expected:
            pagination_anomalies.append("unexpected_empty_page")
        if len(batch) == 100 and all(isinstance(item, dict) and item.get("id") in seen_ids for item in batch):
            pagination_anomalies.append("duplicate_page")
        for item in batch:
            if not isinstance(item, dict):
                raise ValueError("Invalid workflow run entry")
            identifier = item.get("id")
            if type(identifier) is not int or identifier <= 0:
                raise ValueError("Missing or invalid workflow run ID")
            if identifier in seen_ids:
                continue
            seen_ids.add(identifier)
            all_runs.append(item)
        if len(all_runs) > expected:
            pagination_anomalies.append("more_unique_runs_than_total_count")
        if len(batch) < 100 or len(all_runs) >= expected:
            break
    return {"workflow_runs": all_runs, "total_count": expected,
            "history_incomplete": len(all_runs) < expected,
            "pagination_anomalies": sorted(set(pagination_anomalies))}

def parse_time(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Timestamp must be a nonempty ISO string")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone missing in timestamp")
    return parsed.astimezone(UTC)

def valid_age(now, stamp, report, field):
    age = round((now - parse_time(stamp)).total_seconds() / 60, 2)
    if age < -2:
        report["anomalies"].append({"type": "future_timestamp", "field": field, "minutes": age})
        return None
    return max(0.0, age)

def diagnose(runs, state, now):
    if not isinstance(runs, list) or not isinstance(state, dict):
        raise ValueError("Invalid diagnostic input types")
    if not isinstance(now, dt.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    if any(not isinstance(r, dict) for r in runs):
        raise ValueError("Invalid workflow run item")
    if any(r.get("event") == "schedule" and r.get("head_branch") == "main"
           and not isinstance(r.get("created_at"), str) for r in runs):
        raise ValueError("Missing scheduled run creation timestamp")
    scheduled = sorted(
        (r for r in runs if r.get("head_branch") == "main"
         and r.get("event") == "schedule"),
        key=lambda r: parse_time(r["created_at"]), reverse=True,
    )
    report = {"checked_at_utc": now.isoformat(), "scheduled_runs": len(scheduled),
              "intervals_minutes": [], "anomalies": [], "historical_anomalies": [],
              "recent_schedule_gaps": []}
    recent_cutoff = now - dt.timedelta(hours=2)
    report["recent_scheduled_runs"] = sum(parse_time(r["created_at"]) >= recent_cutoff for r in scheduled)
    report["historical_scheduled_runs"] = len(scheduled) - report["recent_scheduled_runs"]
    report["queue_delays_minutes"] = []
    report["schedule_dispatch_offsets_minutes"] = []
    # Expected cron minutes are 07, 22, 37, 52 UTC. Created_at is
    # a GitHub run-creation timestamp, not proof of the actual trigger time.
    for r in scheduled:
        stamp = parse_time(r["created_at"])
        seconds_after_slot = ((stamp.minute - 7) % 15) * 60 + stamp.second + stamp.microsecond / 1_000_000
        report["schedule_dispatch_offsets_minutes"].append(round(seconds_after_slot / 60, 2))
    report["max_dispatch_offset_minutes"] = max(report["schedule_dispatch_offsets_minutes"], default=None)
    report["recent_dispatch_offsets_minutes"] = [
        offset for r, offset in zip(scheduled, report["schedule_dispatch_offsets_minutes"])
        if recent_cutoff <= parse_time(r["created_at"]) <= now + dt.timedelta(minutes=2)
    ]
    report["max_recent_dispatch_offset_minutes"] = max(report["recent_dispatch_offsets_minutes"], default=None)
    for a, b in zip(scheduled, scheduled[1:]):
        gap = round((parse_time(a["created_at"]) - parse_time(b["created_at"])).total_seconds()/60, 2)
        report["intervals_minutes"].append(gap)
        if gap > 30:
            target = "anomalies" if parse_time(a["created_at"]) >= recent_cutoff else "historical_anomalies"
            if target == "anomalies":
                report["recent_schedule_gaps"].append({
                    "newer_created_at": a["created_at"],
                    "older_created_at": b["created_at"],
                    "gap_minutes": gap,
                    "newer_run": a.get("html_url"),
                    "older_run": b.get("html_url"),
                })
            report[target].append({"type": "schedule_interval", "minutes": gap,
                                   "newer_run": a.get("html_url")})
    for r in scheduled:
        created = parse_time(r["created_at"])
        if created > now + dt.timedelta(minutes=2):
            report["anomalies"].append({"type": "future_run_created_at", "run": r.get("html_url")})
        started = parse_time(r["run_started_at"]) if r.get("run_started_at") else None
        if started is not None and started < created:
            target = "anomalies" if created >= recent_cutoff else "historical_anomalies"
            report[target].append({"type": "inconsistent_run_timestamps", "run": r.get("html_url")})
        if started is not None and started >= created:
            queue_delay = round((started - created).total_seconds() / 60, 2)
            report["queue_delays_minutes"].append(queue_delay)
            if queue_delay > 10:
                target = "anomalies" if created >= recent_cutoff else "historical_anomalies"
                report[target].append({"type": "runner_queue_delay",
                                       "minutes": queue_delay, "run": r.get("html_url")})
        if r.get("conclusion") == "success" and r.get("status") not in (None, "completed"):
            target = "anomalies" if created >= recent_cutoff else "historical_anomalies"
            report[target].append({"type": "inconsistent_run_status", "run": r.get("html_url")})
        if r.get("status") in ("queued", "in_progress", "waiting", "pending", "requested"):
            if (now - created).total_seconds() > 20 * 60:
                target = "anomalies" if created >= recent_cutoff else "historical_anomalies"
                report[target].append({"type": "run_not_completed", "run": r.get("html_url"),
                                       "status": r["status"]})
        if r.get("status") == "completed" and r.get("conclusion") is None:
            target = "anomalies" if created >= recent_cutoff else "historical_anomalies"
            report[target].append({"type": "missing_run_conclusion", "run": r.get("html_url")})
        if r.get("conclusion") in ("failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale", "skipped"):
            target = "anomalies" if created >= recent_cutoff else "historical_anomalies"
            report[target].append({"type": "workflow_failure", "run": r.get("html_url"),
                                   "conclusion": r["conclusion"]})
    report["max_queue_delay_minutes"] = max(report["queue_delays_minutes"], default=None)
    successful = [r for r in scheduled if parse_time(r["created_at"]) <= now + dt.timedelta(minutes=2)
                  and r.get("conclusion") == "success"
                  and r.get("status", "completed") == "completed"]
    if scheduled and not successful:
        report["anomalies"].append({"type": "no_successful_scheduled_runs"})
    if successful:
        success_age = valid_age(now, successful[0]["created_at"], report, "last_success")
        report["last_success_age_minutes"] = success_age
        if success_age is not None and success_age > 30:
            report["anomalies"].append({"type": "last_success_stale", "minutes": success_age})
    valid_scheduled = [r for r in scheduled if parse_time(r["created_at"]) <= now + dt.timedelta(minutes=2)]
    if valid_scheduled:
        age = valid_age(now, valid_scheduled[0]["created_at"], report, "last_run")
        report["last_run_age_minutes"] = age
        if age is not None and age > 30:
            report["anomalies"].append({"type": "run_late", "minutes": round(age, 2)})
        if age is not None and age > 75:
            report["anomalies"].append({"type": "run_missing", "minutes": round(age, 2)})
    else:
        report["anomalies"].append({"type": "no_valid_scheduled_runs" if scheduled else "no_scheduled_runs_in_response"})
    if type(state.get("open_position")) is not bool:
        report["anomalies"].append({"type": "invalid_open_position_flag"})
        report["anomalies"].append({"type": "position_state_unknown"})
    if state.get("mode") is None:
        report["anomalies"].append({"type": "missing_mode"})
    if state.get("pair") is None:
        report["anomalies"].append({"type": "missing_pair"})
    if state.get("mode") not in (None, "PAPER_ONLY"):
        report["anomalies"].append({"type": "unexpected_mode", "mode": state.get("mode")})
    if state.get("pair") not in (None, "EUR/USD"):
        report["anomalies"].append({"type": "unexpected_pair", "pair": state.get("pair")})
    if state.get("risk_violations") is None:
        report["anomalies"].append({"type": "missing_risk_violations"})
    if state.get("risk_violations") is not None and (
        type(state["risk_violations"]) is not int
        or state["risk_violations"] < 0
    ):
        report["anomalies"].append({"type": "invalid_risk_violations"})
    if type(state.get("risk_violations")) is int and state["risk_violations"] > 0:
        report["anomalies"].append({"type": "risk_violations", "count": state["risk_violations"]})
    if state.get("open_position") is True and state.get("position_side") not in ("BUY", "SELL"):
        report["anomalies"].append({"type": "invalid_position_side"})
    candle_at = state.get("last_candle_at")
    if candle_at:
        candle_age = valid_age(now, candle_at, report, "last_candle")
        report["last_candle_age_minutes"] = candle_age
        if candle_age is not None and candle_age > 45:
            if forex_market_open(now):
                report["anomalies"].append({"type": "candle_stale", "minutes": candle_age})
            else:
                report["market_closed_candle_age_minutes"] = candle_age
    else:
        report["anomalies"].append({"type": "missing_last_candle_timestamp"})
    updated = state.get("last_checked_at") or state.get("updated_at")
    if candle_at and updated and parse_time(candle_at) > parse_time(updated) + dt.timedelta(minutes=2):
        report["anomalies"].append({"type": "candle_ahead_of_state"})
    if state.get("last_checked_at") and state.get("updated_at") and parse_time(state["last_checked_at"]) > parse_time(state["updated_at"]) + dt.timedelta(minutes=2):
        report["anomalies"].append({"type": "check_ahead_of_update"})
    if updated:
        age = valid_age(now, updated, report, "state")
        report["state_age_minutes"] = age
        if age is not None and age > 30:
            report["anomalies"].append({"type": "state_stale", "minutes": round(age, 2)})
        if state.get("open_position") and age is None:
            report["anomalies"].append({"type": "critical_open_position_unchecked",
                                        "reason": "invalid_future_timestamp"})
        if state.get("open_position") and age is not None and age > 35:
            report["anomalies"].append({"type": "critical_open_position_unchecked",
                                        "minutes": round(age, 2)})
    if not updated:
        report["anomalies"].append({"type": "missing_state_timestamp"})
        if state.get("open_position"):
            report["anomalies"].append({"type": "critical_open_position_unchecked", "reason": "missing_timestamp"})
    if state.get("status") == "api_error":
        report["anomalies"].append({"type": "api_error"})
    if type(state.get("gap_halt")) is not bool:
        report["anomalies"].append({"type": "invalid_gap_halt_flag"})
    if state.get("gap_halt") is True or state.get("status") in ("data_gap", "gap_halt"):
        report["anomalies"].append({"type": "gap_halt_or_data_gap"})
    if not scheduled:
        report["anomalies"].append({"type": "history_unavailable_or_empty" if not runs else "no_matching_scheduled_runs"})
    report["diagnostic_note"] = ("Schedule gaps do not prove whether GitHub omitted triggers, "
                                 "delayed dispatch, or queued jobs. Compare API run history "
                                 "and queue delays before attributing root cause.")
    report["position_side"] = state.get("position_side") if state.get("open_position") else None
    report["balance"] = state.get("balance")
    report["estimated_equity"] = state.get("estimated_equity")
    report["pnl_total"] = state.get("pnl_total")
    return report

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-file", help="Offline GitHub API fixture")
    parser.add_argument("--state-file", help="Offline state fixture")
    args = parser.parse_args()
    base = f"https://api.github.com/repos/{REPO}"
    try:
        runs = (json.load(open(args.runs_file, encoding="utf-8")) if args.runs_file else
                fetch_workflow_runs(base))
        state = (json.load(open(args.state_file, encoding="utf-8")) if args.state_file else
                 fetch_json(f"https://raw.githubusercontent.com/{REPO}/main/v2/state.json"))
        if not isinstance(runs, dict) or not isinstance(runs.get("workflow_runs"), list):
            raise ValueError("Invalid GitHub workflow_runs payload")
        if not isinstance(state, dict):
            raise ValueError("Invalid state payload")
        report = diagnose(runs["workflow_runs"], state, dt.datetime.now(UTC))
        if runs.get("history_incomplete") or runs.get("total_count", len(runs["workflow_runs"])) > len(runs["workflow_runs"]):
            report["anomalies"].append({"type": "history_truncated", "reason": "bounded_history_limit"})
        for anomaly in runs.get("pagination_anomalies", []):
            report["anomalies"].append({"type": "pagination_inconsistency", "reason": anomaly})
        print(json.dumps(report, indent=2, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as exc:
        print(f"VERIFICATION_FAILED: {exc}", file=sys.stderr)
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
