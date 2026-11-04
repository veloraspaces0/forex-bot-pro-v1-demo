#!/usr/bin/env python3
"""V2: paper trades from EUR/USD 15min candles; NEVER places real orders."""
import os, json, math, urllib.parse, urllib.request
from pathlib import Path
from datetime import datetime, timedelta, timezone
P=Path(__file__).parent/'state.json'
UTC=timezone.utc
STEP=timedelta(minutes=15)
SPREAD=.00012

def date(x):
    return datetime.fromisoformat(x.replace('Z','+00:00')).replace(tzinfo=UTC) if len(x)==19 else datetime.fromisoformat(x.replace('Z','+00:00')).astimezone(UTC)

def stamp(x): return x.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
def initial():
    return dict(version=2,mode='PAPER_ONLY',source='Twelve Data',pair='EUR/USD',interval='15min',
      initial_balance=100,balance=100,risk_per_trade=.005,daily_loss_limit=.02,
      max_leverage=20,spread=SPREAD,position=None,trades=[],candles=[],events=[],
      last_bar=None,last_quote=None,day=None,day_start_equity=100,
      updated_at=None,last_checked_at=None,status='awaiting_api_key',message='Configure TWELVE_DATA_API_KEY no GitHub Secrets.')
def load():
    s=json.loads(P.read_text()) if P.exists() else initial()
    if s.get('version')!=2 or s.get('mode')!='PAPER_ONLY': raise ValueError('Incompatible state')
    return s
def save(s):
    P.parent.mkdir(parents=True,exist_ok=True)
    P.write_text(json.dumps(s,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def note(s,at,msg):
    s['events']=(s['events']+[dict(at=at,message=msg)])[-100:]
def api(key):
    query=urllib.parse.urlencode(dict(symbol='EUR/USD',interval='15min',timezone='UTC',outputsize='260',apikey=key))
    req=urllib.request.Request('https://api.twelvedata.com/time_series?'+query,headers={'User-Agent':'ForexBotProV2-paper'})
    with urllib.request.urlopen(req,timeout=25) as response:
        payload=json.loads(response.read(1000000))
    if not isinstance(payload,dict) or 'values' not in payload: raise ValueError('Twelve Data API error '+str(payload.get('code','?')))
    if payload.get('meta',{}).get('symbol','EUR/USD')!='EUR/USD': raise ValueError('Unexpected symbol')
    out={}
    for a in payload['values']:
        t=stamp(date(a['datetime']))
        p={k:float(a[k]) for k in ('open','high','low','close')}
        if not all(math.isfinite(v) and .1<=v<=10 for v in p.values()): raise ValueError('Bad quote')
        if p['high']<max(p['open'],p['close']) or p['low']>min(p['open'],p['close']): raise ValueError('Bad OHLC')
        out[t]=dict(time=t,**p)
    if len(out)<40: raise ValueError('Not enough history')
    return [out[t] for t in sorted(out)]
def ema(vals,n):
    if len(vals)<n: return None
    r=sum(vals[:n])/n
    for p in vals[n:]: r=p*(2/(n+1))+r*(1-2/(n+1))
    return r
def rsi(vals):
    if len(vals)<15: return None
    d=[vals[i]-vals[i-1] for i in range(len(vals)-14,len(vals))]
    up=sum(max(0,x) for x in d); down=sum(max(0,-x) for x in d)
    return 100 if down==0 and up>0 else 50 if down==0 else 100-100/(1+up/down)
def atr(bars):
    if len(bars)<15: return None
    z=[]
    for i in range(len(bars)-14,len(bars)):
        c=bars[i]; prev=bars[i-1]['close']
        z.append(max(c['high']-c['low'],abs(c['high']-prev),abs(c['low']-prev)))
    return sum(z)/len(z)
def equity(s,mid=None):
    p=s['position']; value=s['balance']
    if p and (mid is not None or s['last_quote'] is not None):
        q=mid if mid is not None else s['last_quote']
        fill=q-SPREAD/2 if p['side']=='BUY' else q+SPREAD/2
        value+=(fill-p['entry'])*p['units']*(1 if p['side']=='BUY' else -1)
    return value
def close(s,b,fill,why):
    p=s['position']; pnl=(fill-p['entry'])*p['units']*(1 if p['side']=='BUY' else -1)
    s['balance']=round(s['balance']+pnl,6)
    s['trades']=(s['trades']+[dict(**p,exit_at=b['time'],exit=round(fill,6),pnl=round(pnl,6),reason=why)])[-2000:]
    s['position']=None
    note(s,b['time'],p['side']+' closed '+why+' P/L '+format(pnl,'+.2f'))
def check_exit(s,b):
    p=s['position']
    if not p or b['time']<p['first_exit_bar']: return
    if p['side']=='BUY':
        op=b['open']-SPREAD/2
        if b['low']-SPREAD/2<=p['stop']: close(s,b,min(op,p['stop']),'STOP')
        elif b['high']-SPREAD/2>=p['target']: close(s,b,max(op,p['target']),'TAKE_PROFIT')
    else:
        op=b['open']+SPREAD/2
        if b['high']+SPREAD/2>=p['stop']: close(s,b,max(op,p['stop']),'STOP')
        elif b['low']+SPREAD/2<=p['target']: close(s,b,min(op,p['target']),'TAKE_PROFIT')
def signal(bars):
    if len(bars)<35: return None
    closes=[b['close'] for b in bars]
    f=ema(closes,9); l=ema(closes,21)
    pf=ema(closes[:-1],9); pl=ema(closes[:-1],21); r=rsi(closes)
    if pf<=pl and f>l and 45<=r<=75: return 'BUY'
    if pf>=pl and f<l and 25<=r<=55: return 'SELL'
    return None
def enter(s,side,quote,clock):
    vol=atr(s['candles'])
    if not vol or equity(s,quote)<=0: return
    dist=max(vol*1.5,.00035)
    entry=quote+(SPREAD/2 if side=='BUY' else -SPREAD/2)
    budget=max(s['balance'],0)*.005
    units=math.floor(min(budget/dist,max(0,equity(s,quote))*20/entry))
    if units<1: return
    stop=entry+(-dist if side=='BUY' else dist)
    target=entry+(2*dist if side=='BUY' else -2*dist)
    start=clock.replace(minute=clock.minute//15*15,second=0,microsecond=0)
    if start<clock: start+=STEP
    s['position']=dict(side=side,entry_at=stamp(clock),signal_bar=s['last_bar'],first_exit_bar=stamp(start),
      entry=round(entry,6),stop=round(stop,6),target=round(target,6),
      units=units,planned_risk_usd=round(units*dist,4))
    note(s,stamp(clock),side+' simulated '+str(units)+' EUR, approx risk $'+format(units*dist,'.2f'))
def process(s,bars,now):
    closed=[b for b in bars if date(b['time'])+STEP+timedelta(seconds=90)<=now]
    if not closed: raise ValueError('No completed candles')
    last=closed[-1]; quoted=bars[-1]
    q_age=(now-date(quoted['time'])).total_seconds()
    fresh=-60<=q_age<=1200
    s['last_checked_at']=stamp(now)
    s['last_quote']=quoted['close']; s['quote_at']=quoted['time']
    previous=s['last_bar']
    if previous is None:
        s['candles']=closed[-220:]
        s['last_bar']=last['time']
        note(s,stamp(now),'Initialized indicators; no historical trades.')
        s['message']='Histórico inicial carregado. Aguardando próximo candle.'
    else:
        if date(closed[0]['time'])>date(previous)+STEP or s['status']=='data_gap':
            s['status']='data_gap'; s['message']='Historical gap. Manual review required; orders halted.'
            note(s,stamp(now),'DATA GAP: trading halted')
            s['updated_at']=stamp(now)
            return s
        new=[b for b in closed if b['time']>previous]
        for b in new:
            if s['day']!=b['time'][:10]:
                s['day']=b['time'][:10]
                s['day_start_equity']=equity(s,b['open'])
            check_exit(s,b)
            s['candles']=(s['candles']+[b])[-220:]
            s['last_bar']=b['time']
        if new and s['position'] is None and fresh:
            age=(now-date(new[-1]['time'])-STEP).total_seconds()
            allowed=0<=age<=1800 and equity(s,quoted['close'])>0
            allowed=allowed and equity(s,quoted['close'])>s['day_start_equity']*.98
            if allowed:
                side=signal(s['candles'])
                if side: enter(s,side,quoted['close'],now)
        s['message']=str(len(new))+' candles novos processados; simulação.'
    if not s['day']:
        s['day']=last['time'][:10]
        s['day_start_equity']=s['balance']
    s['status']='monitoring' if fresh else 'market_closed_or_stale'
    s['updated_at']=stamp(now)
    return s
def main():
    s=load()
    key=os.getenv('TWELVE_DATA_API_KEY','').strip()
    if not key:
        if s['status']!='awaiting_api_key':
            s['status']='awaiting_api_key';s['message']='Configure TWELVE_DATA_API_KEY no GitHub Secrets.';save(s)
        print('API key absent: no operation.');return
    try:
        process(s,api(key),datetime.now(UTC))
    except Exception as exc:
        s['status']='api_error';s['message']=type(exc).__name__+': '+str(exc)[:110]
        s['last_checked_at']=stamp(datetime.now(UTC));s['updated_at']=s['last_checked_at']
        note(s,s['last_checked_at'],'Data error, no new trade')
        save(s); print(s['message']);return
    save(s);print('V2',s['status'],'trades',len(s['trades']),'balance',s['balance'])
if __name__=='__main__': main()
