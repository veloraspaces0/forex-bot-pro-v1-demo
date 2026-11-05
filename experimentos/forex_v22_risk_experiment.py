"""Isolated Forex V2.2 PAPER-ONLY research functions. No state writes or orders."""
import math

MAX_RISK = 0.005

def _finite_number(value, name):
    if isinstance(value, bool):
        raise ValueError("invalid " + name)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid " + name) from None
    if not math.isfinite(result):
        raise ValueError("invalid " + name)
    return result

def adaptive_risk_fraction(closed_trades, base=MAX_RISK, floor=0.00125):
    """Reduce risk after consecutive losing CLOSED trades; never increase it."""
    base = _finite_number(base, "base risk")
    floor = _finite_number(floor, "minimum risk")
    if not 0 < floor <= base <= MAX_RISK:
        raise ValueError("invalid risk limits")
    if not isinstance(closed_trades, (tuple, list)):
        raise ValueError("invalid closed trades")
    streak = 0
    for trade in reversed(closed_trades):
        if not isinstance(trade, dict) or "pnl" not in trade:
            raise ValueError("invalid trade")
        pnl = _finite_number(trade["pnl"], "pnl")
        if pnl >= 0:
            break
        streak += 1
    return max(floor, base * (0.5 ** min(streak, 8)))

def trailing_stop(position, closed_bar, atr_value, multiple=1.5):
    """Return stop for FUTURE bars, after checking exits on the current closed bar.

    Never apply this value to the candle used to compute it. Caller must check
    old stop/target FIRST and only then update for the next candle.
    """
    if not isinstance(position, dict) or position.get("side") not in ("BUY", "SELL"):
        raise ValueError("invalid side")
    if not isinstance(closed_bar, dict):
        raise ValueError("invalid candle")
    atr_value = _finite_number(atr_value, "ATR")
    multiple = _finite_number(multiple, "multiple")
    if atr_value <= 0 or multiple <= 0:
        raise ValueError("invalid trailing parameters")
    high = _finite_number(closed_bar.get("high"), "high")
    low = _finite_number(closed_bar.get("low"), "low")
    close = _finite_number(closed_bar.get("close"), "close")
    if not low <= close <= high:
        raise ValueError("invalid candle range")
    stop = _finite_number(position.get("stop"), "stop")
    entry = _finite_number(position.get("entry"), "entry")
    distance = atr_value * multiple
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("invalid trailing distance")
    if position["side"] == "BUY":
        return max(stop, close - distance) if close > entry else stop
    return min(stop, close + distance) if close < entry else stop

def exposure_allowed(position, max_positions=1):
    """Only one simulated position; does not authorize an order."""
    if type(max_positions) is not int or max_positions != 1:
        raise ValueError("only one simulated position is supported")
    if position is not None and not isinstance(position, dict):
        raise ValueError("invalid position")
    return position is None
