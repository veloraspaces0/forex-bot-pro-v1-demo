#!/usr/bin/env python3
"""Read-only contingency decision engine. NEVER dispatches or writes state."""
import datetime as dt

UTC = dt.timezone.utc

def assess_contingency(report, now, cooldown_minutes=30, grace_minutes=5):
    """Return an advisory only; no network, file writes, orders, or dispatches.

    Fail closed when evidence is stale, incomplete, or malformed. A separate,
    explicitly approved production implementation would require a repository
    concurrency lock and an idempotency key per candle before any dispatch.
    """
    if not isinstance(report, dict):
        raise ValueError("report must be an object")
    if not isinstance(now, dt.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone aware")
    if type(cooldown_minutes) is not int or cooldown_minutes < 15:
        raise ValueError("cooldown_minutes must be >= 15")
    if type(grace_minutes) is not int or not 0 <= grace_minutes <= 15:
        raise ValueError("grace_minutes must be 0..15")
    now = now.astimezone(UTC)
    evidence = report.get("cron_slot_evidence")
    if not isinstance(evidence, dict) or evidence.get("tolerance_minutes") != 3:
        return {"advisory": "NO_ACTION", "reason": "missing_or_untrusted_slot_evidence"}
    checked = report.get("checked_at_utc")
    if not isinstance(checked, str):
        return {"advisory": "NO_ACTION", "reason": "missing_check_timestamp"}
    try:
        stamp = dt.datetime.fromisoformat(checked.replace("Z", "+00:00"))
    except ValueError:
        return {"advisory": "NO_ACTION", "reason": "invalid_check_timestamp"}
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return {"advisory": "NO_ACTION", "reason": "naive_check_timestamp"}
    check_age = (now - stamp.astimezone(UTC)).total_seconds() / 60
    if not 0 <= check_age <= grace_minutes:
        return {"advisory": "NO_ACTION", "reason": "stale_or_future_report"}
    if report.get("required_evidence_complete") is not True:
        return {"advisory": "NO_ACTION", "reason": "incomplete_state_evidence"}
    if report.get("operational_health") != "degraded":
        return {"advisory": "NO_ACTION", "reason": "no_degraded_health_signal"}
    slots = evidence.get("unmatched_slots")
    if not isinstance(slots, list) or not slots:
        return {"advisory": "NO_ACTION", "reason": "no_unmatched_slots"}
    if any(not isinstance(x, str) for x in slots):
        return {"advisory": "NO_ACTION", "reason": "invalid_slot_evidence"}
    last_run_age = report.get("last_run_age_minutes")
    if type(last_run_age) not in (float, int) or not cooldown_minutes <= last_run_age <= 1440:
        return {"advisory": "NO_ACTION", "reason": "last_run_not_overdue_or_unknown"}
    return {
        "advisory": "REVIEW_ONLY",
        "reason": "overdue_schedule_and_unmatched_slots",
        "unmatched_slot_count": len(slots),
        "note": "Never automatically dispatch from this advisory. Requires manual approval and idempotency.",
    }
