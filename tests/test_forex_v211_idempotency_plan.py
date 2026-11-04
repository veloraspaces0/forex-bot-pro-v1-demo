import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experimentos"))
from forex_v211_idempotency_plan import (
    candle_idempotency_key, normalize_candle_timestamp, plan_candle_claim,
)

NOW = dt.datetime(2026, 11, 4, 22, 30, tzinfo=dt.timezone.utc)
CANDLE = "2026-11-04T22:15:00Z"

class IdempotencyPlanTests(unittest.TestCase):
    def test_same_candle_same_key(self):
        self.assertEqual(candle_idempotency_key(CANDLE), candle_idempotency_key(CANDLE))

    def test_timezone_normalization(self):
        self.assertEqual(candle_idempotency_key(CANDLE),
                         candle_idempotency_key("2026-11-04T19:15:00-03:00"))

    def test_different_candles_different_keys(self):
        self.assertNotEqual(candle_idempotency_key(CANDLE),
                            candle_idempotency_key("2026-11-04T22:00:00Z"))

    def test_sha256_hex_length(self):
        self.assertEqual(len(candle_idempotency_key(CANDLE)), 64)

    def test_claimed_key_blocks(self):
        key = candle_idempotency_key(CANDLE)
        result = plan_candle_claim(CANDLE, {key}, now=NOW)
        self.assertEqual(result["decision"], "BLOCK")
        self.assertEqual(result["reason"], "already_claimed")

    def test_unclaimed_key_never_dispatches(self):
        result = plan_candle_claim(CANDLE, set(), now=NOW)
        self.assertEqual(result["decision"], "SIMULATE_ONLY")
        self.assertIn("durable_atomic_claim", result["required_controls"])

    def test_future_candle_blocks(self):
        result = plan_candle_claim("2026-11-04T22:45:00Z", set(), now=NOW)
        self.assertEqual(result["reason"], "future_candle")

    def test_stale_candle_blocks(self):
        result = plan_candle_claim("2026-11-03T22:15:00Z", set(), now=NOW)
        self.assertEqual(result["reason"], "stale_candle")

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            candle_idempotency_key("2026-11-04T22:15:00")

    def test_nonboundary_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            candle_idempotency_key("2026-11-04T22:16:00Z")

    def test_wrong_pair_rejected(self):
        with self.assertRaises(ValueError):
            candle_idempotency_key(CANDLE, pair="GBP/USD")

    def test_wrong_repository_rejected(self):
        with self.assertRaises(ValueError):
            candle_idempotency_key(CANDLE, repository="someone/else")

    def test_invalid_claimed_key_rejected(self):
        with self.assertRaises(ValueError):
            plan_candle_claim(CANDLE, {"not-a-hash"}, now=NOW)

    def test_claimed_set_not_mutated(self):
        claims = set()
        plan_candle_claim(CANDLE, claims, now=NOW)
        self.assertEqual(claims, set())

    def test_same_key_for_one_hundred_replays(self):
        key = candle_idempotency_key(CANDLE)
        for replay in range(100):
            with self.subTest(replay=replay):
                result = plan_candle_claim(CANDLE, {key}, now=NOW)
                self.assertEqual(result["reason"], "already_claimed")

    def test_no_side_effects_in_source(self):
        import inspect
        import forex_v211_idempotency_plan as mod
        source = inspect.getsource(mod)
        for forbidden in ("urlopen", "subprocess", "git push", "workflow_dispatch", "open(", "GITHUB_TOKEN"):
            self.assertNotIn(forbidden, source)

if __name__ == "__main__":
    unittest.main()
