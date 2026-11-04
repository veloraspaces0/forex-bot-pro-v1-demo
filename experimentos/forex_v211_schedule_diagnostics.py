#!/usr/bin/env python3
"""Read-only Forex V2.1 schedule diagnostic. No secrets, no writes."""
import argparse
import datetime as dt
import json
import sys
import urllib.request

REPO = "veloraspaces0/forex-bot-pro-v1-demo"
WORKFLOW = "forex-v2.yml"
UTC = dt.timezone.utc

def fetch_json(url):
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "forex-v211-readonly-diagnostics",
    })
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)

def parse_time(value):
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
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    scheduled = sorted(
        (r for r in runs if r.get("head_branch") == "main"
         and r.get("name") == "Forex Bot Pro V2 - Paper EURUSD"
         and r.get("event") == "schedule"),
        key=lambda r: r["created_at"], reverse=True,
    )
    report = {"checked_at_utc": now.isoformat(), "scheduled_runs": len(scheduled),
              "intervals_minutes": [], "anomalies": []}
    if len(runs) >= 100:
        report["anomalies"].append({"type": "history_truncated", "note": "First page only; older gaps may be missing"})
    for a, b in zip(scheduled, scheduled[1:]):
        gap = round((parse_time(a["created_at"]) - parse_time(b["created_at"])).total_seconds()/60, 2)
        report["intervals_minutes"].append(gap)
        if gap > 30:
            report["anomalies"].append({"type": "schedule_interval", "minutes": gap,
                                        "newer_run": a["html_url"]})
    for r in scheduled:
        created = parse_time(r["created_at"])
        started = parse_time(r["run_started_at"]) if r.get("run_started_at") else None
        if started is not None and started < created:
            report["anomalies"].append({"type": "inconsistent_run_timestamps", "run": r.get("html_url")})
        if started is not None and started >= created:
            queue_delay = round((started - created).total_seconds() / 60, 2)
            if queue_delay > 10:
                report["anomalies"].append({"type": "runner_queue_delay",
                                            "minutes": queue_delay, "run": r.get("html_url")})
        if r.get("status") in ("queued", "in_progress", "waiting", "pending", "requested"):
            report["anomalies"].append({"type": "run_not_completed", "run": r.get("html_url"),
                                        "status": r["status"]})
        if r.get("conclusion") in ("failure", "cancelled", "timed_out", "action_required", "startup_failure"):
            report["anomalies"].append({"type": "workflow_failure", "run": r["html_url"],
                                        "conclusion": r["conclusion"]})
    if scheduled:
        age = valid_age(now, scheduled[0]["created_at"], report, "last_run")
        report["last_run_age_minutes"] = age
        if age is not None and age > 30:
            report["anomalies"].append({"type": "run_late", "minutes": round(age, 2)})
        if age is not None and age > 75:
            report["anomalies"].append({"type": "run_missing", "minutes": round(age, 2)})
    else:
        report["anomalies"].append({"type": "no_scheduled_runs_in_response"})
    updated = state.get("last_checked_at") or state.get("updated_at")
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
    if state.get("gap_halt") or state.get("status") in ("data_gap", "gap_halt"):
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
                fetch_json(f"{base}/actions/workflows/{WORKFLOW}/runs?branch=main&per_page=100"))
        state = (json.load(open(args.state_file, encoding="utf-8")) if args.state_file else
                 fetch_json(f"https://raw.githubusercontent.com/{REPO}/main/v2/state.json"))
        if not isinstance(runs, dict) or not isinstance(runs.get("workflow_runs"), list):
            raise ValueError("Invalid GitHub workflow_runs payload")
        if not isinstance(state, dict):
            raise ValueError("Invalid state payload")
        if runs.get("total_count", len(runs["workflow_runs"])) > len(runs["workflow_runs"]):
            # The API response is incomplete: fail closed instead of implying full coverage.
            print("WARNING: workflow history is paginated; only first page was inspected", file=sys.stderr)
        report = diagnose(runs["workflow_runs"], state, dt.datetime.now(UTC))
        if runs.get("total_count", len(runs["workflow_runs"])) > len(runs["workflow_runs"]):
            report["anomalies"].append({"type": "history_truncated", "reason": "api_total_count_exceeds_page"})
        print(json.dumps(report, indent=2, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"VERIFICATION_FAILED: {exc}", file=sys.stderr)
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
