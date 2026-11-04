#!/usr/bin/env python3
"""EUR/USD 15-minute PAPER-ONLY monitor. Never sends brokerage orders."""
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

P = Path(__file__).parent / 'state.private.json'
UTC = timezone.utc
NY = ZoneInfo('America/New_York')
STEP = timedelta(minutes=15)
CLOSE_GRACE = timedelta(seconds=90)
SPREAD = .00012              # hypothetical, constant 1.2 pips
MAX_LEVERAGE = 20
RISK_FRACTION = .005
DAILY_FRACTION = .02
RETRY_STATUS = {429, 500, 502, 503, 504}

class DataFeedError(Exception):
    """Public-safe error; never embeds URLs, credentials, or raw provider payloads."""

def date(value):
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)

def stamp(value):
    return date(value).strftime('%Y-%m-%dT%H:%M:%SZ')

def initial():
    return dict(version=2, mode='PAPER_ONLY', source='Twelve Data', pair='EUR/USD', interval='15min',
        initial_balance=100, balance=100, risk_per_trade=RISK_FRACTION,
        daily_loss_limit=DAILY_FRACTION, max_leverage=MAX_LEVERAGE,
        spread=SPREAD, position=None, trades=[], candles=[], events=[],
        last_bar=None, last_quote=None, day=None, day_start_equity=100,
        gap_halt=False, updated_at=None, last_checked_at=None,
        status='awaiting_api_key', message='Configure TWELVE_DATA_API_KEY no GitHub Secrets.')

def validate_state(s):
    if not isinstance(s, dict) or s.get('version') != 2 or s.get('mode') != 'PAPER_ONLY':
        raise ValueError('Invalid paper state version/mode')
    if not isinstance(s.get('trades'), list) or not isinstance(s.get('candles'), list):
        raise ValueError('Invalid paper state history')
    if not math.isfinite(s.get('balance', float('nan'))):
        raise ValueError('Invalid paper state balance')
    if s.get('pair') != 'EUR/USD' or s.get('interval') != '15min':
        raise ValueError('Unexpected trading pair/interval')
    if s.get('last_bar') is not None: date(s['last_bar'])
    if s.get('position') is not None and not isinstance(s['position'], dict):
        raise ValueError('Invalid paper state position')
    s.setdefault('gap_halt', False)  # non-disruptive migration from V2
    return s

def load():
    return validate_state(json.loads(P.read_text(encoding='utf-8'))) if P.exists() else initial()

def save(s):
    # This is temporary PRIVATE state on an ephemeral CI runner. runner.py
    # performs the durable, atomic encrypted/public writes.
    P.parent.mkdir(parents=True, exist_ok=True)
    P.write_text(json.dumps(validate_state(s), indent=2, ensure_ascii=False, allow_nan=False)+'\n', encoding='utf-8')

def note(s, at, message):
    s['events'] = (s['events'] + [dict(at=at, message=message)])[-100:]

def _parse_payload(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('values'), list):
        raise DataFeedError('provider_invalid_payload')
    if not isinstance(payload.get('meta', {}), dict) or payload.get('meta', {}).get('symbol', 'EUR/USD') != 'EUR/USD':
        raise DataFeedError('provider_symbol_mismatch')
    result = {}
    for item in payload['values']:
        try:
            when = date(item['datetime'])
            if when.second or when.microsecond or when.minute % 15:
                raise ValueError('unaligned timestamp')
            ts = stamp(when)
            p = {k: float(item[k]) for k in ('open', 'high', 'low', 'close')}
            if not all(math.isfinite(n) and .1 <= n <= 10 for n in p.values()):
                raise ValueError('invalid price')
            if p['high'] < max(p['open'], p['close']) or p['low'] > min(p['open'], p['close']):
                raise ValueError('invalid candle range')
            candle = dict(time=ts, **p)
            if ts in result and result[ts] != candle:
                raise ValueError('conflicting duplicate timestamp')
            result[ts] = candle
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise DataFeedError('provider_invalid_candle') from None
    if len(result) < 40:
        raise DataFeedError('provider_insufficient_history')
    return [result[k] for k in sorted(result)]

def api(key, retries=3, sleep=time.sleep):
    if not key:
        raise DataFeedError('provider_key_missing')
    query = urllib.parse.urlencode(dict(symbol='EUR/USD', interval='15min', timezone='UTC',
                                        outputsize='260', apikey=key))
    url = 'https://api.twelvedata.com/time_series?' + query
    req = urllib.request.Request(url, headers={'User-Agent': 'ForexBotProV2-paper'})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                payload = json.loads(response.read(1_000_000))
            return _parse_payload(payload)
        except urllib.error.HTTPError as exc:
            err = 'provider_http_' + str(exc.code)
            transient = exc.code in RETRY_STATUS
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            err, transient = 'provider_network_error', True
        except (ValueError, UnicodeError) as exc:
            # Malformed JSON is treated as transient but never used as market data.
            err, transient = 'provider_invalid_json', True
        if not transient or attempt == retries - 1:
            raise DataFeedError(err) from None
        sleep(min(8, 2 ** (attempt + 1)))
    raise DataFeedError('provider_unavailable')

def ema(vals, n):
    if len(vals) < n: return None
    result = sum(vals[:n]) / n
    for price in vals[n:]: result = price * (2/(n+1)) + result * (1 - 2/(n+1))
    return result

def rsi(vals):
    if len(vals) < 15: return None
    changes = [vals[i]-vals[i-1] for i in range(len(vals)-14, len(vals))]
    up = sum(max(0, x) for x in changes)
    down = sum(max(0, -x) for x in changes)
    return (100 if up else 50) if down == 0 else 100-100/(1+up/down)

def atr(bars):
    if len(bars) < 15: return None
    ranges = []
    for i in range(len(bars)-14, len(bars)):
        bar, prev = bars[i], bars[i-1]['close']
        ranges.append(max(bar['high']-bar['low'], abs(bar['high']-prev), abs(bar['low']-prev)))
    return sum(ranges)/len(ranges)

def equity(s, mid=None):
    position = s['position']
    value = s['balance']
    if position and (mid is not None or s.get('last_quote') is not None):
        quote = mid if mid is not None else s['last_quote']
        fill = quote-SPREAD/2 if position['side']=='BUY' else quote+SPREAD/2
        value += (fill-position['entry'])*position['units']*(1 if position['side']=='BUY' else -1)
    return value

def close(s, bar, fill, reason):
    pos = s['position']
    pnl = (fill-pos['entry'])*pos['units']*(1 if pos['side']=='BUY' else -1)
    s['balance'] = round(s['balance']+pnl, 6)
    s['trades'] = (s['trades']+[dict(**pos, exit_at=bar['time'], exit=round(fill,6),
                                       pnl=round(pnl,6), reason=reason)])[-2000:]
    s['position'] = None
    if pnl < -pos.get('planned_risk_usd', 0) - .000001:
        note(s, bar['time'], 'RISK_EXCEEDED: modeled loss above planned risk')
    note(s, bar['time'], pos['side']+' closed '+reason+' P/L '+format(pnl,'+.2f'))

def check_exit(s, bar):
    pos = s['position']
    if not pos or bar['time'] < pos['first_exit_bar']: return
    if pos['side'] == 'BUY':
        opening = bar['open']-SPREAD/2
        if bar['low']-SPREAD/2 <= pos['stop']:
            close(s, bar, min(opening, pos['stop']), 'STOP')
        elif bar['high']-SPREAD/2 >= pos['target']:
            close(s, bar, max(opening, pos['target']), 'TAKE_PROFIT')
    else:
        opening = bar['open']+SPREAD/2
        if bar['high']+SPREAD/2 >= pos['stop']:
            close(s, bar, max(opening, pos['stop']), 'STOP')
        elif bar['low']+SPREAD/2 <= pos['target']:
            close(s, bar, min(opening, pos['target']), 'TAKE_PROFIT')

def signal(bars):
    if len(bars) < 35: return None
    closes = [bar['close'] for bar in bars]
    fast, slow = ema(closes,9), ema(closes,21)
    prev_fast, prev_slow = ema(closes[:-1],9), ema(closes[:-1],21)
    momentum = rsi(closes)
    if prev_fast <= prev_slow and fast > slow and 45 <= momentum <= 75: return 'BUY'
    if prev_fast >= prev_slow and fast < slow and 25 <= momentum <= 55: return 'SELL'
    return None

def enter(s, side, quote, clock):
    if s['position'] is not None or side not in ('BUY','SELL') or s.get('gap_halt'): return
    volatility = atr(s['candles'])
    available = min(s['balance'], equity(s, quote))
    if not volatility or not math.isfinite(quote) or quote <= 0 or available <= 0: return
    distance = max(volatility * 1.5, .00035)
    entry = quote + (SPREAD/2 if side=='BUY' else -SPREAD/2)
    budget = max(available,0)*RISK_FRACTION
    units = math.floor(min(budget/distance, max(0,equity(s,quote))*MAX_LEVERAGE/entry))
    if units < 1: return
    stop = entry+(-distance if side=='BUY' else distance)
    target = entry+(2*distance if side=='BUY' else -2*distance)
    start = date(clock).replace(minute=clock.minute//15*15,second=0,microsecond=0)
    if start < clock: start += STEP
    s['position'] = dict(side=side, entry_at=stamp(clock),signal_bar=s['last_bar'],
        first_exit_bar=stamp(start), entry=round(entry,6),stop=round(stop,6),
        target=round(target,6),units=units,planned_risk_usd=round(units*distance,4))
    note(s,stamp(clock),side+' simulated '+str(units)+' EUR, approx risk $'+format(units*distance,'.2f'))

def _market_expected(bar_time):
    """Conservative 24x5 session: Sunday 17:00 to Friday 17:00 New York time."""
    ny = date(bar_time).astimezone(NY)
    day = ny.weekday()
    return (day < 4 or (day == 4 and ny.hour < 17)
            or (day == 6 and ny.hour >= 17))

def missing_trading_bar(previous, current):
    expected = date(previous)+STEP
    target = date(current)
    checks = 0
    while expected < target:
        checks += 1
        if checks > 3000: return True
        if _market_expected(expected): return True
        expected += STEP
    return False

def process(s, bars, now):
    now = date(now)
    if not bars: raise DataFeedError('no_candles')
    if any(date(bars[i]['time']) > date(bars[i+1]['time']) for i in range(len(bars)-1)):
        raise DataFeedError('out_of_order_candles')
    closed = [bar for bar in bars if date(bar['time'])+STEP+CLOSE_GRACE <= now]
    if not closed: raise DataFeedError('no_completed_candles')
    last, quoted = closed[-1], bars[-1]
    current_start = now.replace(minute=now.minute//15*15,second=0,microsecond=0)
    # Estimate a paper fill only from the current (possibly still-forming) bar,
    # never from a previous candle falsely interpreted as a live quote.
    fresh = date(quoted['time']) == current_start and date(quoted['time']) >= date(last['time'])+STEP
    s['last_checked_at'] = stamp(now)
    s['last_quote'] = quoted['close']
    s['quote_at'] = quoted['time']
    previous = s['last_bar']
    if s.get('gap_halt'):
        s['status'] = 'data_gap'
        s['message'] = 'Trading halted: historical gap requires manual review.'
        s['updated_at'] = stamp(now)
        return s
    if previous is None:
        history = closed[-220:]
        if any(missing_trading_bar(a['time'], z['time']) for a,z in zip(history,history[1:])):
            s['gap_halt'] = True
            s['status'] = 'data_gap'
            s['message'] = 'Initial historical candles contain a trading-session gap; manual review required.'
            note(s, stamp(now), 'DATA GAP during initialization: trading halted')
            s['updated_at'] = stamp(now)
            return s
        s['candles'] = history
        s['last_bar'] = last['time']
        note(s, stamp(now), 'Initialized indicators; no historical trades.')
        s['message'] = 'Histórico inicial carregado. Aguardando próximo candle.'
    else:
        new = [bar for bar in closed if bar['time'] > previous]
        before = previous
        for bar in new:
            if missing_trading_bar(before, bar['time']):
                s['gap_halt'] = True
                s['status'] = 'data_gap'
                s['message'] = 'Missing completed 15min candle during trading hours; manual review required.'
                note(s, stamp(now), 'DATA GAP: trading halted')
                s['updated_at'] = stamp(now)
                return s  # Never partially apply the batch.
            before = bar['time']
        for bar in new:
            if s['day'] != bar['time'][:10]:
                s['day'] = bar['time'][:10]  # explicitly UTC, unchanged from V2
                s['day_start_equity'] = equity(s,bar['open'])
            check_exit(s,bar)
            s['candles'] = (s['candles']+[bar])[-220:]
            s['last_bar'] = bar['time']
        if new and s['position'] is None and fresh:
            age = (now-date(new[-1]['time'])-STEP).total_seconds()
            allowed = 0 <= age <= 1800 and equity(s,quoted['close']) > 0
            allowed = allowed and equity(s,quoted['close']) > s['day_start_equity']*(1-DAILY_FRACTION)
            if allowed:
                side = signal(s['candles'])
                if side: enter(s,side,quoted['close'],now)
        s['message'] = str(len(new))+' candles novos processados; simulação.'
    if not s['day']:
        s['day'] = last['time'][:10]
        s['day_start_equity'] = s['balance']
    s['status'] = 'monitoring' if fresh else 'market_closed_or_stale'
    s['updated_at'] = stamp(now)
    return s

def main():
    state = load()  # Invalid/corrupted state must fail closed; never reset it.
    key = os.getenv('TWELVE_DATA_API_KEY','').strip()
    if not key:
        state['status'] = 'data_gap' if state.get('gap_halt') else 'awaiting_api_key'
        state['message'] = 'Configure TWELVE_DATA_API_KEY no GitHub Secrets.'
        state['last_checked_at'] = stamp(datetime.now(UTC))
        state['updated_at'] = state['last_checked_at']
        save(state)
        print('API key missing; no paper operations.')
        return
    try:
        process(state,api(key),datetime.now(UTC))
    except DataFeedError as exc:
        state['status'] = 'data_gap' if state.get('gap_halt') else 'api_error'
        state['message'] = 'Data feed: '+str(exc)[:90]  # sanitized error codes only
        state['last_checked_at'] = stamp(datetime.now(UTC))
        state['updated_at'] = state['last_checked_at']
        note(state,state['last_checked_at'],'Data error, no new paper trade')
        save(state)
        print(state['message'])
        return
    save(state)
    print('V2.1',state['status'],'closed_trades',len(state['trades']),'balance',state['balance'])

if __name__ == '__main__': main()
