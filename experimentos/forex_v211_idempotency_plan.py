#!/usr/bin/env python3
"""Pure, offline idempotency plan for Forex PAPER contingency.

No network calls, file writes, state mutations, dispatches, or real orders.
This module is a design guard only, NOT a distributed lock. Production use
requires a durable atomic compare-and-swap and serialization with the paper job.
"""
import datetime as dt
import hashlib
import re

UTC = dt.timezone.utc
CANDLE_MINUTES = 15
REPO = "veloraspaces0/forex-bot-pro-v1-demo"
PAIR = "EUR/USD"
_SAFE_KEY = re.compile(r"^[a-f0-9]{64}$")


def normalize_candle_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("candle timestamp must be a nonempty ISO-8601 string")
    try:
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid candle timestamp") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("candle timestamp requires timezone")
    stamp = stamp.astimezone(UTC)
    if stamp.second or stamp.microsecond or stamp.minute % CANDLE_MINUTES:
        raise ValueError("candle timestamp must align to a 15-minute UTC boundary")
    return stamp


def candle_idempotency_key(candle_timestamp, *, repository=REPO, pair=PAIR):
    """Deterministic SHA-256 identity, independent of worker invocation time."""
    if repository != REPO or pair != PAIR:
        raise ValueError("unsupported repository or pair")
    stamp = normalize_candle_timestamp(candle_timestamp)
    canonical = f"forex-paper-v2|{repository}|{pair}|{stamp.isoformat()}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_candle_claim(candle_timestamp, claimed_keys, *, now):
    """Offline advisory; never treats an in-memory claim as a production lock."""
    if not isinstance(now, dt.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not isinstance(claimed_keys, (set, frozenset)) or any(
        not isinstance(k, str) or not _SAFE_KEY.fullmatch(k) for k in claimed_keys
    ):
        raise ValueError("claimed_keys must be a set of SHA-256 hex keys")
    stamp = normalize_candle_timestamp(candle_timestamp)
    now = now.astimezone(UTC)
    key = candle_idempotency_key(candle_timestamp)
    if stamp > now:
        return {"decision": "BLOCK", "reason": "future_candle", "key": key}
    if key in claimed_keys:
        return {"decision": "BLOCK", "reason": "already_claimed", "key": key}
    if now - stamp > dt.timedelta(hours=24):
        return {"decision": "BLOCK", "reason": "stale_candle", "key": key}
    return {
        "decision": "SIMULATE_ONLY",
        "reason": "unclaimed_candle_requires_atomic_production_lock",
        "key": key,
        "required_controls": [
            "durable_atomic_claim",
            "shared_concurrency_group_with_paper_runner",
            "state_version_compare_and_swap",
            "paper_only_assertion",
            "audit_trail",
        ],
    }
