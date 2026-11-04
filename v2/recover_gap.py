#!/usr/bin/env python3
"""Manual, offline, fail-closed gap recovery for PAPER-ONLY encrypted state.

This tool DOES NOT fetch quotes, place orders or push to GitHub. It skips an
unverifiable simulation interval only after an explicit, documented review.
Never use it when a paper position was open across the missing interval.
"""
import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import paper_bot as bot
import runner

MIN_CONTIGUOUS = 40
MAX_CLOSED_AGE = timedelta(minutes=45)


def propose_recovery(state, provider_payload, now, reason):
    """Pure transformation; no changes to secrets/files or historical trades."""
    now = bot.date(now)
    if not isinstance(reason, str) or not (8 <= len(reason.strip()) <= 160):
        raise ValueError('Review reason must be between 8 and 160 characters')
    s = bot.validate_state(json.loads(json.dumps(state)))  # deep copy and validate
    if not s['gap_halt']:
        raise ValueError('State is not halted; recovery not permitted')
    if s['position'] is not None:
        raise ValueError('Open paper position: manual reconciliation required; recovery denied')
    bars = bot._parse_payload(provider_payload)  # validates price, market, UTC alignment
    closed = [bar for bar in bars if bot.date(bar['time'])+bot.STEP+bot.CLOSE_GRACE <= now]
    if len(closed) < MIN_CONTIGUOUS:
        raise ValueError('Not enough verified completed candles to recover')
    tail = closed[-MIN_CONTIGUOUS:]
    for a,z in zip(tail,tail[1:]):
        if bot.date(z['time'])-bot.date(a['time']) != bot.STEP:
            raise ValueError('The final 40 completed candles are not strictly contiguous')
    previous = s.get('last_bar')
    if previous is not None and bot.date(tail[-1]['time']) <= bot.date(previous):
        raise ValueError('No newer completed candle after the halted state')
    age = now - (bot.date(tail[-1]['time'])+bot.STEP)
    if age < bot.CLOSE_GRACE or age > MAX_CLOSED_AGE:
        raise ValueError('Reviewed snapshot is outside the permitted freshness window')
    original_trades = len(s['trades'])
    original_balance = s['balance']
    new_day = tail[-1]['time'][:10]
    if s.get('day') != new_day:
        s['day'] = new_day
        s['day_start_equity'] = s['balance']  # flat; document UTC day reference change
    s['candles'] = tail
    s['last_bar'] = tail[-1]['time']  # only FUTURE candles can create new simulated trades
    s['last_quote'] = None
    s['quote_at'] = None
    s['gap_halt'] = False
    s['status'] = 'recovery_paused'
    s['message'] = 'Reviewed gap intentionally skipped; awaiting a NEW closed candle. No historical trades simulated.'
    s['updated_at'] = bot.stamp(now)
    s['last_checked_at'] = bot.stamp(now)
    s['recovery_count'] = int(s.get('recovery_count',0)) + 1
    s['last_recovery_at'] = bot.stamp(now)
    bot.note(s, bot.stamp(now), 'RECOVERY_GAP_SKIPPED: '+reason.strip()+'; prior_bar='+str(previous)+'; resumed_from='+s['last_bar']+'; no backfill')
    bot.validate_state(s)
    if len(s['trades']) != original_trades or s['balance'] != original_balance:
        raise AssertionError('Recovery unexpectedly changed trades or balance')
    return s


def backup_bytes_exclusive(target, raw):
    """Do not clobber another backup; mode 0600 from creation."""
    target = Path(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline recovery of a paused FOREX paper state; DRY-RUN by default.')
    parser.add_argument('--state',type=Path,required=True,help='Local copy of encrypted state.enc (never commit)')
    parser.add_argument('--snapshot',type=Path,required=True,help='Manually reviewed Twelve Data time_series JSON')
    parser.add_argument('--reason',required=True,help='Why skipping the unavailable simulated interval was approved')
    parser.add_argument('--apply',action='store_true',help='Actually modify the local encrypted state')
    parser.add_argument('--acknowledge-skipped-paper-trades',action='store_true',help='Explicitly accept no reconstructed trades in the gap')
    parser.add_argument('--backup',type=Path,help='New backup file OUTSIDE the repository, required with --apply')
    parser.add_argument('--public',type=Path,help='Optional local derived summary; never upload raw quotes')
    args = parser.parse_args(argv)
    if args.apply:
        if not args.acknowledge_skipped_paper_trades or args.backup is None:
            parser.error('--apply requires --acknowledge-skipped-paper-trades and --backup')
        if args.backup.resolve() == args.state.resolve():
            parser.error('Backup must not overwrite source state')
        if args.backup.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
            parser.error('Backup must be outside the repository')
        if args.backup.exists():
            parser.error('Backup already exists; refusing to overwrite')
    key = os.getenv('TWELVE_DATA_API_KEY','').strip()
    dedicated = os.getenv('V2_STATE_ENCRYPTION_KEY','').strip()
    if not key and not dedicated:
        parser.error('Provide existing encryption secret via environment (never CLI arguments)')
    raw = args.state.read_bytes()
    state = json.loads(runner.decrypt(key,path=args.state))
    snapshot = json.loads(args.snapshot.read_text(encoding='utf-8'))
    repaired = propose_recovery(state,snapshot,datetime.now(bot.UTC),args.reason)
    print('REVIEW ONLY: old last_bar='+str(state.get('last_bar'))+', new last_bar='+str(repaired['last_bar']))
    print('Trades and balance preserved. Open positions are NOT recoverable by this tool.')
    if not args.apply:
        print('DRY RUN: no files were modified; add --apply and explicit acknowledgement after independent review.')
        return 0
    if args.state.read_bytes() != raw:
        raise RuntimeError('State changed during review; refusing to overwrite')
    backup_bytes_exclusive(args.backup,raw)
    runner.encrypt(key,json.dumps(repaired,ensure_ascii=False,allow_nan=False).encode('utf-8'),path=args.state)
    if args.public:
        runner.atomic_write(args.public,(json.dumps(runner.summary(repaired),ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8'))
    print('Applied to LOCAL COPY. Backup preserved. DO NOT push to main while Actions is active.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
