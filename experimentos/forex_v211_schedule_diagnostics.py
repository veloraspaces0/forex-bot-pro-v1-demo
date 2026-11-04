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

def fetch_workflow_runs(base, max_pages=5):
    """Fetch bounded run history; fail closed if the API omits expected runs."""
    if not isinstance(max_pages, int) or not 1 <= max_pages <= 20:
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
        if not batch and len(all_runs) < expected:
            pagination_anomalies.append("unexpected_empty_page")
        for item in batch:
            if not isinstance(item, dict):
                raise ValueError("Invalid workflow run entry")
            identifier = item.get("id")
            if identifier is not None:
                if identifier in seen_ids:
                    continue
                seen_ids.add(identifier)
            all_runs.append(item)
        if len(batch) < 100 or len(all_runs) >= expected:
            break
    return {"workflow_runs": all_runs, "total_count": expected,
            "history_incomplete": len(all_runs) < expected,
            "pagination_anomalies": sorted(set(pagination_anomalies))}

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
    if not isinstance(runs, list) or not isinstance(state, dict):
        raise ValueError("Invalid diagnostic input types")
    if not isinstance(now, dt.datetime) or now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    if any(not isinstance(r, dict) for r in runs):
        raise ValueError("Invalid workflow run item")
    scheduled = sorted(
        (r for r in runs if r.get("head_branch") == "main"
         and r.get("name") == "Forex Bot Pro V2 - Paper EURUSD"
         and r.get("event") == "schedule"),
        key=lambda r: parse_time(r["created_at"]), reverse=True,
    )
    report = {"checked_at_utc": now.isoformat(), "scheduled_runs": len(scheduled),
              "intervals_minutes": [], "anomalies": []}
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
        if r.get("conclusion") in ("failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale", "skipped"):
            report["anomalies"].append({"type": "workflow_failure", "run": r["html_url"],
                                        "conclusion": r["conclusion"]})
    successful = [r for r in scheduled if r.get("conclusion") == "success"
                  and r.get("status", "completed") == "completed"]
    if scheduled and not successful:
        report["anomalies"].append({"type": "no_successful_scheduled_runs"})
    if successful:
        success_age = valid_age(now, successful[0]["created_at"], report, "last_success")
        report["last_success_age_minutes"] = success_age
        if success_age is not None and success_age > 30:
            report["anomalies"].append({"type": "last_success_stale", "minutes": success_age})
    if scheduled:
        age = valid_age(now, scheduled[0]["created_at"], report, "last_run")
        report["last_run_age_minutes"] = age
        if age is not None and age > 30:
            report["anomalies"].append({"type": "run_late", "minutes": round(age, 2)})
        if age is not None and age > 75:
            report["anomalies"].append({"type": "run_missing", "minutes": round(age, 2)})
    else:
        report["anomalies"].append({"type": "no_scheduled_runs_in_response"})
    if state.get("open_position") not in (True, False):
        report["anomalies"].append({"type": "invalid_open_position_flag"})
    if state.get("mode") is None:
        report["anomalies"].append({"type": "missing_mode"})
    if state.get("pair") is None:
        report["anomalies"].append({"type": "missing_pair"})
    if state.get("mode") not in (None, "PAPER_ONLY"):
        report["anomalies"].append({"type": "unexpected_mode", "mode": state.get("mode")})
    if state.get("pair") not in (None, "EUR/USD"):
        report["anomalies"].append({"type": "unexpected_pair", "pair": state.get("pair")})
    if state.get("risk_violations") is not None and (
        type(state["risk_violations"]) not in (int, float)
        or state["risk_violations"] < 0
        or state["risk_violations"] != state["risk_violations"]
    ):
        report["anomalies"].append({"type": "invalid_risk_violations"})
    if type(state.get("risk_violations")) in (int, float) and state["risk_violations"] > 0:
        report["anomalies"].append({"type": "risk_violations", "count": state["risk_violations"]})
    candle_at = state.get("last_candle_at")
    if candle_at:
        candle_age = valid_age(now, candle_at, report, "last_candle")
        report["last_candle_age_minutes"] = candle_age
        if candle_age is not None and candle_age > 45:
            report["anomalies"].append({"type": "candle_stale", "minutes": candle_age})
    else:
        report["anomalies"].append({"type": "missing_last_candle_timestamp"})
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
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"VERIFICATION_FAILED: {exc}", file=sys.stderr)
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
