"""Testes offline. Dados inventados APENAS nos testes, nunca na producao."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import (
    initial_state, normalize_values, update_state, process_candle,
    enter_pending, evaluate_exit, summary, iso, dtparse
)

UTC = timezone.utc
BASE = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


def bar(i, price=1.08, high=None, low=None):
    return {
        "time": iso(BASE + timedelta(minutes=15 * i)),
        "open": price, "high": high if high is not None else price + .0002,
        "low": low if low is not None else price - .0002, "close": price
    }


class EngineTests(unittest.TestCase):
    def test_normalize_ignores_open_invalid_and_duplicates(self):
        rows = [
            {"datetime": "2026-11-03 10:00:00", "open": "1.08", "high": "1.081",
             "low": "1.079", "close": "1.08"},
            {"datetime": "2026-11-03 10:15:00", "open": "1.08", "high": "1.081",
             "low": "1.079", "close": "1.08"},
            {"datetime": "2026-11-03 09:45:00", "open": "1.08", "high": "0",
             "low": "1.079", "close": "1.08"},
        ]
        now = BASE + timedelta(minutes=30, seconds=45)
        candles = normalize_values(rows, now)
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0]["time"], iso(BASE))

    def test_bootstrap_does_not_backtrade(self):
        state = update_state(initial_state(), [bar(i) for i in range(50)],
                             BASE + timedelta(hours=13))
        self.assertEqual(state["balance"], 100)
        self.assertEqual(state["trades"], [])
        self.assertIsNone(state["position"])
        self.assertIsNone(state["pending"])
        self.assertEqual(state["last_candle"], bar(49)["time"])

    def test_idempotent_same_candles(self):
        first = update_state(initial_state(), [bar(i) for i in range(40)],
                             BASE + timedelta(hours=11))
        second = update_state(first, [bar(i) for i in range(40)],
                              BASE + timedelta(hours=11))
        self.assertEqual(second["candles"], first["candles"])
        self.assertEqual(second["trades"], first["trades"])

    def test_entry_waits_until_next_candle_and_limits_risk(self):
        s = initial_state()
        s["last_candle"] = bar(0)["time"]
        s["day"] = bar(0)["time"][:10]
        s["candles"] = [bar(0)]
        s["pending"] = {"side": "BUY", "distance": .001, "signal_time": bar(0)["time"]}
        self.assertIsNone(s["position"])
        process_candle(s, bar(1))
        self.assertIsNotNone(s["position"])
        self.assertLessEqual(s["position"]["planned_risk_usd"], .5 + 1e-6)
        self.assertEqual(s["position"]["entry_time"], bar(1)["time"])

    def test_stop_has_priority_when_both_touched(self):
        s = initial_state()
        s["position"] = {"side": "BUY", "entry": 1.08, "units": 400,
                         "stop": 1.079, "target": 1.082, "entry_time": bar(0)["time"]}
        c = bar(1, high=1.083, low=1.078)
        self.assertTrue(evaluate_exit(s, c))
        self.assertEqual(s["trades"][-1]["reason"], "STOP")
        self.assertLess(s["trades"][-1]["pnl"], 0)

    def test_gap_uses_worse_open_for_stop(self):
        s = initial_state()
        s["position"] = {"side": "BUY", "entry": 1.08, "units": 400,
                         "stop": 1.079, "target": 1.082, "entry_time": bar(0)["time"]}
        c = bar(1, price=1.077, high=1.078, low=1.076)
        evaluate_exit(s, c)
        self.assertEqual(s["trades"][-1]["reason"], "STOP_GAP")
        self.assertLess(s["trades"][-1]["exit"], 1.079)

    def test_gap_pauses_new_entries(self):
        s = initial_state()
        s["last_candle"] = bar(0)["time"]
        s["candles"] = [bar(0)]
        s["day"] = bar(0)["time"][:10]
        s["pending"] = {"side": "BUY", "distance": .001, "signal_time": bar(0)["time"]}
        process_candle(s, bar(8))
        self.assertIsNone(s["position"])
        self.assertIsNone(s["pending"])
        self.assertEqual(s["missed_intervals"], 7)
        self.assertGreater(s["warmup_remaining"], 0)

    def test_daily_limit_blocks_entry(self):
        s = initial_state()
        s["balance"] = 97.9
        s["pending"] = {"side": "BUY", "distance": .001, "signal_time": bar(0)["time"]}
        enter_pending(s, bar(1))
        self.assertIsNone(s["position"])

    def test_summary_pnl(self):
        s = initial_state()
        s["balance"] = 101.25
        self.assertEqual(summary(s)["pnl"], 1.25)

    def test_stale_market_status(self):
        s = update_state(initial_state(), [bar(i) for i in range(40)],
                         BASE + timedelta(hours=11))
        s = update_state(s, [bar(i) for i in range(40)],
                         BASE + timedelta(days=3))
        self.assertEqual(s["status"], "DADOS_ATRASADOS_OU_MERCADO_FECHADO")


if __name__ == "__main__":
    unittest.main()
