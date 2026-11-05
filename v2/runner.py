#!/usr/bin/env python3
"""Encrypted paper-only state runner. Public output is derived metrics only."""
import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import paper_bot as bot

ROOT = Path(__file__).parent
ENC = ROOT / 'state.enc'
PUB = ROOT / 'state.json'
AAD = b'forex-bot-pro-v2-paper-only'
HEADER = b'FX2'

# Do not change existing key derivation: already-encrypted V2 state must
# remain decryptable. A future key rotation requires an explicit migration.
def cipher(key_text):
    secret = os.getenv('V2_STATE_ENCRYPTION_KEY','').strip() or key_text
    # Fail closed without changing legacy encrypted-state derivation.
    if not isinstance(secret, str) or not secret.strip():
        raise ValueError('Missing encrypted-state key material')
    key = hashlib.pbkdf2_hmac('sha256',secret.encode('utf-8'),b'fxbot-v2-v1',180000,32)
    return AESGCM(key)

def decrypt(key, path=None):
    raw = base64.b64decode((Path(path) if path is not None else ENC).read_bytes().strip(),validate=True)
    if raw[:3] != HEADER or len(raw)<32: raise ValueError('Invalid encrypted state header')
    plain = cipher(key).decrypt(raw[3:15],raw[15:],AAD)
    bot.validate_state(json.loads(plain))  # wrong-key / corruption fails closed
    return plain

def atomic_write(path, content):
    """Temp file+fsync+replace on same filesystem; never expose partial state."""
    path.parent.mkdir(parents=True,exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb',prefix='.forex-state-',suffix='.tmp',
                                         dir=path.parent,delete=False) as temporary:
            name = temporary.name
            os.chmod(name,0o600)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(name,path)
        name = None
        try:
            fd = os.open(str(path.parent),os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        except OSError:
            pass  # Filesystem may not support directory fsync.
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)

def encrypt(key, plaintext, path=None):
    bot.validate_state(json.loads(plaintext))
    nonce = os.urandom(12)
    sealed = HEADER+nonce+cipher(key).encrypt(nonce,plaintext,AAD)
    atomic_write(Path(path) if path is not None else ENC,base64.b64encode(sealed)+b'\n')

def summary(s):
    bot.validate_state(s)
    trades=s['trades']
    wins=sum(t['pnl']>0 for t in trades)
    curve=[dict(n=0,balance=round(s['initial_balance'],2))]
    cash=s['initial_balance'];peak=cash;max_dd=0
    for i,t in enumerate(trades,1):
        cash+=t['pnl'];peak=max(peak,cash)
        if peak>0:max_dd=max(max_dd,(peak-cash)/peak*100)
        if i>len(trades)-120:
            curve.append(dict(n=i,balance=round(cash,2)))
    if len(trades)>120:
        curve.insert(0,dict(n=len(trades)-120,
            balance=round(s['initial_balance']+sum(t['pnl'] for t in trades[:-120]),2)))
    e=round(bot.equity(s),2)
    risk_violations=sum(t['pnl'] < -t.get('planned_risk_usd',float('inf'))-.000001 for t in trades)
    return dict(version=2,mode='PAPER_ONLY',source='Twelve Data',pair='EUR/USD',interval='15min',
        initial_balance=s['initial_balance'],balance=round(s['balance'],2),
        estimated_equity=e,pnl_total=round(e-s['initial_balance'],2),
        closed_trades=len(trades),wins=wins,losses=len(trades)-wins,
        win_rate_pct=round(100*wins/len(trades),1) if trades else 0,
        max_closed_drawdown_pct=round(max_dd,2),max_risk_pct=.5,daily_loss_limit_pct=2,
        risk_violations=risk_violations,gap_halt=bool(s.get('gap_halt')),
        open_position=bool(s['position']),position_side=s['position']['side'] if s['position'] else None,
        status=s['status'],message=s['message'],updated_at=s.get('updated_at'),
        last_checked_at=s.get('last_checked_at'),last_candle_at=s.get('last_bar'),
        equity_curve=curve,
        closed_results=[dict(at=t['exit_at'],side=t['side'],pnl=round(t['pnl'],2),reason=t['reason'],
            planned_risk_usd=round(t['planned_risk_usd'],4) if t.get('planned_risk_usd') is not None else None,
            risk_exceeded=(t['pnl'] < -t['planned_risk_usd']-.000001) if t.get('planned_risk_usd') is not None else None)
                        for t in trades[-20:]][::-1],
        events=s['events'][-12:][::-1],
        notice='Paper estimates only. Gaps and variable costs may exceed planned risk; no real orders.')

def write_public(s):
    data=json.dumps(summary(s),ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    atomic_write(PUB,data.encode('utf-8'))

def main():
    key = os.getenv('TWELVE_DATA_API_KEY','').strip()
    if not key:
        print('Missing API credential; no paper operations.')
        if not PUB.exists(): write_public(bot.initial())
        return
    try:
        if ENC.exists():
            data=decrypt(key)
        else:
            # Bootstrapping is only allowed if there is no existing public
            # operational state, otherwise a missing ENC is a possible data loss.
            if PUB.exists():
                old=json.loads(PUB.read_text(encoding='utf-8'))
                if old.get('closed_trades',0) or old.get('open_position') or old.get('last_candle_at'):
                    raise RuntimeError('Encrypted state missing; refusing to reset paper history')
            data=json.dumps(bot.initial()).encode('utf-8')
        atomic_write(bot.P,data)
        bot.main()
        updated=bot.P.read_bytes()
        updated_state=bot.validate_state(json.loads(updated))
        # The encrypted blob is authoritative. A later public write failure
        # is repaired by publishing from ENC on the next successful run.
        encrypt(key,updated)
        write_public(updated_state)
        print('Only encrypted state and derived public summary persisted.')
    finally:
        bot.P.unlink(missing_ok=True)

if __name__=='__main__': main()
