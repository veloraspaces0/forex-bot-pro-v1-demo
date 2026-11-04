"""Forex Bot Pro V2: motor de paper trading EUR/USD com candles reais (sem corretora)."""
from __future__ import annotations

import copy
import math
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
INTERVAL = timedelta(minutes=15)
SPREAD = 0.00012  # spread HIPOTETICO de 1,2 pip, nao spread da corretora
RISK = 0.005
DAILY_LOSS = 0.02
LEVERAGE = 20.0


def iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def dtparse(value):
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def initial_state():
    return {
        "version": 2, "pair": "EUR/USD", "timeframe": "15min",
        "mode": "PAPER", "source": "Twelve Data",
        "initial_balance": 100.0, "balance": 100.0,
        "last_candle": None, "last_check_utc": None,
        "day": None, "day_start_equity": 100.0,
        "position": None, "pending": None,
        "candles": [], "trades": [], "events": [],
        "warmup_remaining": 0, "missed_intervals": 0,
        "status": "AGUARDANDO_API", "error": None,
    }


def normalize_values(values, now):
    """Normaliza apenas candles FECHADOS. O timestamp e o inicio da vela (UTC)."""
    now = dtparse(now)
    by_time = {}
    for item in values:
        try:
            stamp = dtparse(item["datetime"])
            if stamp.minute % 15 or stamp.second or stamp.microsecond:
                continue
            if stamp + INTERVAL + timedelta(seconds=30) > now:
                continue  # nunca usar candle ainda aberto
            o, h, l, c = (float(item[k]) for k in ("open", "high", "low", "close"))
            if not all(math.isfinite(v) for v in (o, h, l, c)):
                continue
            if not (0.2 <= l <= o <= h <= 5.0 and l <= c <= h):
                continue
            by_time[iso(stamp)] = {
                "time": iso(stamp), "open": o, "high": h, "low": l, "close": c
            }
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    return [by_time[t] for t in sorted(by_time)]


def ema(values, period):
    if len(values) < period:
        return None
    result = sum(values[:period]) / period
    alpha = 2.0 / (period + 1)
    for v in values[period:]:
        result = alpha * v + (1 - alpha) * result
    return result


def rsi(values, period=14):
    if len(values) <= period:
        return None
    gains = losses = 0.0
    for i in range(len(values) - period, len(values)):
        diff = values[i] - values[i - 1]
        gains += max(0.0, diff)
        losses += max(0.0, -diff)
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100.0 - 100.0 / (1 + gains / losses)


def atr(candles, period=14):
    if len(candles) <= period:
        return None
    tr = []
    for i in range(len(candles) - period, len(candles)):
        c, prev = candles[i], candles[i - 1]
        tr.append(max(c["high"] - c["low"], abs(c["high"] - prev["close"]), abs(c["low"] - prev["close"])))
    return sum(tr) / period


def equity(state, mid=None):
    value = state["balance"]
    p = state["position"]
    if p:
        if mid is None:
            mid = state["candles"][-1]["close"]
        exit_price = mid + (-SPREAD / 2 if p["side"] == "BUY" else SPREAD / 2)
        direction = 1 if p["side"] == "BUY" else -1
        value += (exit_price - p["entry"]) * p["units"] * direction
    return value


def event(state, msg):
    state["events"].append({"at": state["last_candle"], "message": msg})
    state["events"] = state["events"][-40:]


def close_position(state, candle, price, reason):
    p = state["position"]
    direction = 1 if p["side"] == "BUY" else -1
    pnl = round((price - p["entry"]) * p["units"] * direction, 6)
    state["balance"] = round(state["balance"] + pnl, 6)
    state["trades"].append({
        **p, "exit_time": candle["time"], "exit": round(price, 6),
        "pnl": pnl, "reason": reason
    })
    state["trades"] = state["trades"][-250:]
    state["position"] = None
    event(state, p["side"] + " encerrada: " + reason + " (USD " + str(round(pnl, 2)) + ")")


def evaluate_exit(state, candle):
    p = state["position"]
    if not p:
        return False
    side = p["side"]
    if side == "BUY":
        op = candle["open"] - SPREAD / 2
        lo = candle["low"] - SPREAD / 2
        hi = candle["high"] - SPREAD / 2
        if op <= p["stop"]:
            close_position(state, candle, op, "STOP_GAP")
        elif op >= p["target"]:
            close_position(state, candle, p["target"], "TAKE_PROFIT")
        elif lo <= p["stop"]:
            close_position(state, candle, p["stop"], "STOP")
        elif hi >= p["target"]:
            close_position(state, candle, p["target"], "TAKE_PROFIT")
    else:
        op = candle["open"] + SPREAD / 2
        hi = candle["high"] + SPREAD / 2
        lo = candle["low"] + SPREAD / 2
        if op >= p["stop"]:
            close_position(state, candle, op, "STOP_GAP")
        elif op <= p["target"]:
            close_position(state, candle, p["target"], "TAKE_PROFIT")
        elif hi >= p["stop"]:
            close_position(state, candle, p["stop"], "STOP")
        elif lo <= p["target"]:
            close_position(state, candle, p["target"], "TAKE_PROFIT")
    return state["position"] is None


def daily_ok(state, price):
    return equity(state, price) > 0 and equity(state, price) > state["day_start_equity"] * (1 - DAILY_LOSS)


def enter_pending(state, candle):
    pending = state["pending"]
    state["pending"] = None
    if not pending or not daily_ok(state, candle["open"]):
        return
    side, distance = pending["side"], pending["distance"]
    entry = candle["open"] + (SPREAD / 2 if side == "BUY" else -SPREAD / 2)
    risk_budget = max(0.0, state["balance"]) * RISK
    units = math.floor(min(risk_budget / distance, max(0.0, equity(state, candle["open"])) * LEVERAGE / entry))
    if units < 1:
        event(state, "Sinal ignorado: capital insuficiente")
        return
    state["position"] = {
        "side": side, "entry_time": candle["time"],
        "entry": round(entry, 6), "units": units,
        "stop": round(entry + (-distance if side == "BUY" else distance), 6),
        "target": round(entry + (2 * distance if side == "BUY" else -2 * distance), 6),
        "planned_risk_usd": round(units * distance, 4),
    }
    event(state, side + " simulada: " + str(units) + " EUR")


def signal(candles):
    if len(candles) < 23:
        return None
    closes = [c["close"] for c in candles]
    prev_fast, prev_slow = ema(closes[:-1], 9), ema(closes[:-1], 21)
    fast, slow = ema(closes, 9), ema(closes, 21)
    momentum, volatility = rsi(closes), atr(candles)
    if None in (prev_fast, prev_slow, fast, slow, momentum, volatility):
        return None
    side = None
    if prev_fast <= prev_slow and fast > slow and 45 <= momentum <= 75:
        side = "BUY"
    elif prev_fast >= prev_slow and fast < slow and 25 <= momentum <= 55:
        side = "SELL"
    return {"side": side, "distance": max(1.5 * volatility, 0.00035)} if side else None


def process_candle(state, candle):
    last = state["last_candle"]
    if last and candle["time"] <= last:
        return
    gap = bool(last and dtparse(candle["time"]) - dtparse(last) > INTERVAL)
    if gap:
        missing = int((dtparse(candle["time"]) - dtparse(last)) / INTERVAL) - 1
        state["missed_intervals"] += missing
        state["warmup_remaining"] = 22
        state["pending"] = None
        event(state, "Intervalo sem dados: " + str(missing) + " velas; novas entradas pausadas")
    day = candle["time"][:10]
    if day != state["day"]:
        state["day"] = day
        state["day_start_equity"] = round(equity(state, candle["open"]), 6)
    if not gap and state["warmup_remaining"] == 0:
        enter_pending(state, candle)
    else:
        state["pending"] = None
    closed = evaluate_exit(state, candle)
    state["candles"].append(candle)
    state["candles"] = state["candles"][-250:]
    state["last_candle"] = candle["time"]
    if state["warmup_remaining"] > 0:
        state["warmup_remaining"] -= 1
    if not state["position"] and not closed and state["warmup_remaining"] == 0 and daily_ok(state, candle["close"]):
        s = signal(state["candles"])
        if s:
            state["pending"] = {**s, "signal_time": candle["time"]}
            event(state, "Sinal " + s["side"] + " para proxima vela (sem ordem real)")


def update_state(state, candles, now):
    state = copy.deepcopy(state)
    now = dtparse(now)
    state["last_check_utc"] = iso(now)
    state["error"] = None
    if not candles:
        state["status"] = "SEM_DADOS"
        return state
    if not state["last_candle"]:
        # Inicializa indicadores, SEM criar operacoes retroativas.
        state["candles"] = candles[-200:]
        state["last_candle"] = candles[-1]["time"]
        state["day"] = candles[-1]["time"][:10]
        state["day_start_equity"] = state["balance"]
        state["status"] = "MONITORANDO"
        event(state, "Indicadores inicializados; sem trades historicos")
        return state
    fresh = [c for c in candles if c["time"] > state["last_candle"]]
    for candle in fresh:
        process_candle(state, candle)
    age = now - (dtparse(state["last_candle"]) + INTERVAL)
    state["status"] = "DADOS_ATRASADOS_OU_MERCADO_FECHADO" if age > timedelta(hours=3) else "MONITORANDO"
    return state


def summary(state):
    last = state["candles"][-1]["close"] if state["candles"] else None
    eq = equity(state, last) if last is not None else state["balance"]
    trades = state["trades"]
    wins = sum(1 for t in trades if t["pnl"] > 0)
    return {
        "equity": round(eq, 4),
        "pnl": round(eq - state["initial_balance"], 4),
        "closed_trades": len(trades),
        "win_rate": round(100 * wins / len(trades), 2) if trades else 0.0,
        "last_price": last,
        "risk_per_trade_usd": round(max(0.0, state["balance"]) * RISK, 4),
    }
