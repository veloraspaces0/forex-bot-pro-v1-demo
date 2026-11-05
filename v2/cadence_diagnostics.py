"""Read-only diagnostics for public Forex PAPER_ONLY telemetry."""
import json
from datetime import datetime, timezone
from pathlib import Path


def parse_utc(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be text")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return result.astimezone(timezone.utc)


def diagnose(state, heartbeat=None, now=None, expected_minutes=15, grace_minutes=15):
    if state.get("mode") != "PAPER_ONLY" or state.get("pair") != "EUR/USD":
        raise ValueError("Unexpected state identity")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Current time must be timezone aware")
    current = current.astimezone(timezone.utc)
    checked = parse_utc(state["last_checked_at"])
    candle = parse_utc(state["last_candle_at"])
    if heartbeat is not None:
        if heartbeat.get("mode") != "PAPER_ONLY" or heartbeat.get("status") != "SUCCESS":
            raise ValueError("Unexpected heartbeat identity")
        completed = parse_utc(heartbeat["completed_at"])
        hb_checked = parse_utc(heartbeat["state_last_checked_at"])
    else:
        completed = hb_checked = None
    age = (current - checked).total_seconds() / 60
    candle_age = (current - candle).total_seconds() / 60
    hb_age = (current - completed).total_seconds() / 60 if completed else None
    flags = []
    if age < -1 or candle_age < -1 or (hb_age is not None and hb_age < -1):
        flags.append("FUTURE_TIMESTAMP")
    if age > expected_minutes + grace_minutes:
        flags.append("STATE_UPDATE_DELAY")
    if candle_age > expected_minutes * 2 + grace_minutes:
        flags.append("CANDLE_AGE_HIGH")
    if completed is None:
        flags.append("HEARTBEAT_UNAVAILABLE")
    else:
        if hb_age > expected_minutes + grace_minutes:
            flags.append("HEARTBEAT_DELAY")
        if hb_checked != checked:
            flags.append("HEARTBEAT_STATE_MISMATCH")
    return {
        "mode": "PAPER_ONLY",
        "observed_at": current.isoformat().replace("+00:00", "Z"),
        "state_age_minutes": round(age, 2),
        "candle_age_minutes": round(candle_age, 2),
        "heartbeat_age_minutes": round(hb_age, 2) if hb_age is not None else None,
        "open_position": state.get("open_position") is True,
        "flags": flags,
        "note": "Ages alone cannot establish whether GitHub scheduling, provider data or publication failed.",
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="v2/state.json")
    parser.add_argument("--heartbeat", default="v2/heartbeat.json")
    args = parser.parse_args()
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    path = Path(args.heartbeat)
    heartbeat = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    print(json.dumps(diagnose(state, heartbeat), indent=2))


if __name__ == "__main__":
    main()
