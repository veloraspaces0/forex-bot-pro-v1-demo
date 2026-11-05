"""Forex V2.2 isolated PAPER-ONLY risk experiments; no live trading or state writes.

Run in parallel with the baseline on identical closed candles. Never use this
module to mutate the production V2.1 state.
"""
import math

def adaptive_risk_fraction(closed_trades, base=0.005, floor=0.00125):
    """Reduce risk after consecutive losing closed trades; never increase it."""
    if not 0 < floor <= base <= 0.005:
        raise ValueError("invalid risk limits")
    streak = 0
    for trade in reversed(closed_trades):
        pnl = float(trade["pnl"])
        if not math.isfinite(pnl):
            raise ValueError("invalid pnl")
        if pnl >= 0:
            break
        streak += 1
    return max(floor, base * (0.5 ** min(streak, 8)))

def trailing_stop(position, closed_bar, atr_value, multiple=1.5):
    """Compute next stop from a COMPLETED bar, for use on subsequent bars only.

    Caller MUST evaluate stop/target exits for this bar using the PREVIOUS
    stop before calling this function; otherwise intrabar look-ahead occurs.
    Does not modify the input position or infer intrabar price order.
    """
    if position["side"] not in ("BUY", "SELL"):
        raise ValueError("invalid side")
    if not math.isfinite(atr_value) or atr_value <= 0 or not math.isfinite(multiple) or multiple <= 0:
        raise ValueError("invalid trailing parameters")
    for key in ("high", "low", "close"):
        if not math.isfinite(float(closed_bar[key])):
            raise ValueError("invalid candle")
    stop = float(position["stop"])
    entry = float(position["entry"])
    if not math.isfinite(stop) or not math.isfinite(entry):
        raise ValueError("invalid position")
    distance = atr_value * multiple
    if position["side"] == "BUY":
        # No loosening, and only trail after a favorable completed close.
        return max(stop, float(closed_bar["close"]) - distance) if float(closed_bar["close"]) > entry else stop
    return min(stop, float(closed_bar["close"]) + distance) if float(closed_bar["close"]) < entry else stop

def exposure_allowed(position, max_positions=1):
    """Single-position guard. This experiment never authorizes an order."""
    if max_positions != 1:
        raise ValueError("only one simulated position is supported")
    return position is None
