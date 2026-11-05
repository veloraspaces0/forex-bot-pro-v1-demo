"""Read-only schedule health analysis. No provider, secrets or state writes."""
from datetime import datetime, timezone

EXPECTED_MINUTES = (7, 22, 37, 52)

def parse_utc(value):
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return dt.astimezone(timezone.utc)

def analyze_runs(runs, now, max_state_age_minutes=30):
    """Analyze only main's Forex workflow scheduled runs; never infer dropped slots as facts."""
    now = parse_utc(now) if isinstance(now, str) else now
    if now.tzinfo is None:
        raise ValueError("timezone required")
    selected = []
    for r in runs:
        if r.get("head_branch") != "main" or r.get("event") != "schedule":
            continue
        if not r.get("run_started_at") and not r.get("created_at"):
            continue
        started = parse_utc(r.get("run_started_at") or r["created_at"])
        created = parse_utc(r["created_at"]) if r.get("created_at") else started
        if started > now or created > now:
            raise ValueError("future workflow timestamp")
        selected.append((started, created, r))
    selected.sort(key=lambda item: item[0], reverse=True)
    intervals = [round((selected[i][0]-selected[i+1][0]).total_seconds()/60, 2)
                 for i in range(len(selected)-1)]
    age = round((now-selected[0][0]).total_seconds()/60, 2) if selected else None
    # created_at to run_started_at measures queue/runner delay, NOT schedule-to-create delay.
    queue = [round((started-created).total_seconds()/60, 2)
             for started, created, _ in selected]
    if any(v < 0 for v in queue):
        raise ValueError("negative queue delay")
    return {
        "scheduled_runs_observed": len(selected),
        "last_scheduled_start_utc": selected[0][0].isoformat() if selected else None,
        "minutes_since_last_scheduled_start": age,
        "intervals_minutes": intervals,
        "intervals_over_30": [v for v in intervals if v > 30],
        "queue_delay_minutes": queue,
        "failed_or_cancelled_ids": [r.get("id") for _, _, r in selected
                                     if r.get("conclusion") in ("failure", "cancelled")],
        "schedule_over_30": age is not None and age > 30,
        "schedule_over_75": age is not None and age > 75,
        "note": "GitHub schedule is best-effort; missing cron slots cannot be attributed without more evidence."
    }
