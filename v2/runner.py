#!/usr/bin/env python3
"""Encrypted state runner. Only derived performance data is made public."""
import base64
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import paper_bot as bot

ROOT=Path(__file__).parent
ENC=ROOT/'state.enc'
PUB=ROOT/'state.json'
AAD=b'forex-bot-pro-v2-paper-only'
HEADER=b'FX2'

def cipher(key_text):
    salt=os.getenv('V2_STATE_ENCRYPTION_KEY','').strip() or key_text
    key=hashlib.pbkdf2_hmac('sha256',salt.encode('utf-8'),b'fxbot-v2-v1',180000,32)
    return AESGCM(key)

def decrypt(key):
    raw=base64.b64decode(ENC.read_bytes())
    if raw[:3]!=HEADER or len(raw)<32: raise ValueError('Invalid encrypted state header')
    return cipher(key).decrypt(raw[3:15],raw[15:],AAD)

def encrypt(key,plaintext):
    nonce=os.urandom(12)
    sealed=HEADER+nonce+cipher(key).encrypt(nonce,plaintext,AAD)
    ENC.write_bytes(base64.b64encode(sealed)+b'\n')

def summary(s):
    trades=s['trades']
    wins=sum(t['pnl']>0 for t in trades)
    curve=[dict(n=0,balance=round(s['initial_balance'],2))]
    cash=s['initial_balance'];peak=cash;max_dd=0
    for i,t in enumerate(trades,1):
        cash+=t['pnl']
        peak=max(peak,cash)
        if peak>0: max_dd=max(max_dd,(peak-cash)/peak*100)
        if i>len(trades)-120:
            curve.append(dict(n=i,balance=round(cash,2)))
    if not trades:
        curve=[dict(n=0,balance=round(s['initial_balance'],2))]
    elif len(trades)>120:
        curve.insert(0,dict(n=len(trades)-120,balance=round(s['initial_balance']+sum(t['pnl'] for t in trades[:-120]),2)))
    e=round(bot.equity(s),2)
    result=dict(version=2,mode='PAPER_ONLY',source='Twelve Data',
      pair='EUR/USD',interval='15min',
      initial_balance=s['initial_balance'],balance=round(s['balance'],2),
      estimated_equity=e,pnl_total=round(e-s['initial_balance'],2),
      closed_trades=len(trades),wins=wins,losses=len(trades)-wins,
      win_rate_pct=round(100*wins/len(trades),1) if trades else 0,
      max_closed_drawdown_pct=round(max_dd,2),max_risk_pct=.5,
      daily_loss_limit_pct=2,open_position=bool(s['position']),
      position_side=s['position']['side'] if s['position'] else None,
      status=s['status'],message=s['message'],
      updated_at=s.get('updated_at'),last_checked_at=s.get('last_checked_at'),
      last_candle_at=s.get('last_bar'),
      equity_curve=curve,
      closed_results=[dict(at=t['exit_at'],side=t['side'],pnl=round(t['pnl'],2),
                          reason=t['reason']) for t in trades[-20:]][::-1],
      events=s['events'][-12:][::-1],
      notice='Paper-only estimates. No orders, no real fills. Published metrics exclude raw market candles.')
    return result

def write_public(s):
    PUB.write_text(json.dumps(summary(s),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def main():
    key=os.getenv('TWELVE_DATA_API_KEY','').strip()
    if not key:
        print('API credential missing; no API call and no trades.')
        if not PUB.exists():
            write_public(bot.initial())
        return
    try:
        if ENC.exists():
            data=decrypt(key)
            bot.P.write_bytes(data)
        else:
            bot.P.write_text(json.dumps(bot.initial()),encoding='utf-8')
        os.chmod(bot.P,0o600)
        bot.main()
        data=bot.P.read_bytes()
        s=json.loads(data)
        encrypt(key,data)
        write_public(s)
        print('Only encrypted candles and derived dashboard data persisted.')
    finally:
        bot.P.unlink(missing_ok=True)

if __name__=='__main__': main()
