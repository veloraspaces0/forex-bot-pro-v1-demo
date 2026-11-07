"""Offline deterministic regression tests. No network calls or real trades."""
import base64
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v2'))
import paper_bot as b
import runner

UTC = timezone.utc

def bars(count=80,start=datetime(2026,11,2,9,0,tzinfo=UTC)):
    result=[]
    for i in range(count):
        op=1.084+i*.000005
        result.append(dict(time=b.stamp(start+timedelta(minutes=15*i)),
                           open=op,high=op+.0004,low=op-.0004,close=op+.00005))
    return result

def valid_payload(data):
    return {'meta':{'symbol':'EUR/USD'},'values':[
        dict(datetime=x['time'],open=str(x['open']),high=str(x['high']),
             low=str(x['low']),close=str(x['close'])) for x in reversed(data)]}

class PaperIntegrity(unittest.TestCase):
    def test_no_duplicate_candle_or_trade_on_rerun(self):
        data=bars()
        s=b.initial()
        now=b.date(data[-1]['time'])+timedelta(minutes=20)
        b.process(s,data,now)
        last_bar=s['last_bar']
        events=len(s['events'])
        b.process(s,data,now)
        self.assertEqual(s['last_bar'],last_bar)
        self.assertEqual(len(s['trades']),0)
        self.assertEqual(len(s['events']),events)

    def test_twelve_data_open_timestamp_respects_15min_close_and_90s_grace(self):
        # 81 synthetic timestamps from Nov 4 04:45 to Nov 5 00:45 UTC.
        # At 01:00:34 the 00:45 candle closed 34 seconds ago; the
        # 90-second close grace deliberately excludes it.
        start=datetime(2026,11,4,4,45,tzinfo=UTC)
        feed=bars(81,start=start)
        self.assertEqual(feed[-1]['time'],'2026-11-05T00:45:00Z')
        early=datetime(2026,11,5,1,0,34,tzinfo=UTC)
        s=b.process(b.initial(),feed,early)
        self.assertEqual(s['last_bar'],'2026-11-05T00:30:00Z')
        self.assertEqual(s['status'],'market_closed_or_stale')
        self.assertIsNone(s['position'])

        # After the grace period, 00:45 is closed/eligible, but a fresh
        # quote still requires a distinct current 01:00 opening candle.
        later=datetime(2026,11,5,1,2,0,tzinfo=UTC)
        s=b.process(s,feed,later)
        self.assertEqual(s['last_bar'],'2026-11-05T00:45:00Z')
        self.assertEqual(s['status'],'market_closed_or_stale')
        self.assertIsNone(s['position'])

        next_quote=bars(82,start=start)[-1]
        self.assertEqual(next_quote['time'],'2026-11-05T01:00:00Z')
        s=b.process(s,feed+[next_quote],later)
        self.assertEqual(s['last_bar'],'2026-11-05T00:45:00Z')
        self.assertEqual(s['status'],'monitoring')
        self.assertIsNone(s['position'])

    def test_current_forming_quote_does_not_hide_missing_previous_closed_bar(self):
        # 00:45 is absent while the 01:00 quote is present at 01:02.
        # Old >= comparison incorrectly declared this feed fresh and could
        # enter a new position based on indicators only through 00:30.
        start=datetime(2026,11,4,4,45,tzinfo=UTC)
        complete=bars(82,start=start)
        missing=complete[:-2]   # through 00:30; omit 00:45
        current=complete[-1]    # 01:00 forming quote
        self.assertEqual(missing[-1]['time'],'2026-11-05T00:30:00Z')
        self.assertEqual(current['time'],'2026-11-05T01:00:00Z')
        now=datetime(2026,11,5,1,2,tzinfo=UTC)
        s=b.initial()
        s['last_bar']=missing[-2]['time']
        s['candles']=missing[:-1]
        with patch.object(b,'signal',return_value='BUY') as proposed:
            b.process(s,missing+[current],now)
        self.assertFalse(proposed.called)
        self.assertIsNone(s['position'])
        self.assertFalse(s['gap_halt'])
        self.assertEqual(s['status'],'market_closed_or_stale')
        self.assertEqual(s['last_bar'],missing[-1]['time'])

        # Supplying the missing, fully closed 00:45 candle restores a
        # continuous feed and permits the normal entry rules.
        with patch.object(b,'signal',return_value='BUY') as proposed:
            b.process(s,complete,now)
        self.assertTrue(proposed.called)
        self.assertEqual(s['status'],'monitoring')
        self.assertIsNotNone(s['position'])
        self.assertEqual(s['position']['signal_bar'],'2026-11-05T00:45:00Z')

    def test_sell_position_stale_three_cycles_then_one_closed_bar_exit(self):
        # Adversarial regression: never use the forming bar's OHLC for a
        # fictitious stop, and never close the same paper trade twice.
        data=bars(80)
        last_open=b.date(data[-1]['time'])
        s=b.initial()
        s['last_bar']=data[-2]['time']
        s['candles']=data[:-1]
        s['day']=data[-2]['time'][:10]
        s['position']=dict(side='SELL',entry=1.085,stop=1.086,target=1.083,
            first_exit_bar=b.stamp(last_open+b.STEP),
            units=100,planned_risk_usd=.1)
        initial_position=s['position'].copy()
        for minutes in (7,22,37):
            b.process(s,data,last_open+timedelta(minutes=minutes))
            self.assertEqual(s['position'],initial_position)
            self.assertEqual(s['trades'],[])
            self.assertEqual(s['balance'],100)
            # A forming quote can be current at +7m without being eligible
            # for a stop/target exit; later the same quote becomes stale.
            self.assertEqual(s['status'],
                'monitoring' if minutes==7 else 'market_closed_or_stale')
        self.assertEqual(s['last_bar'],data[-1]['time'])

        # The first eligible exit bar is evaluated only AFTER it closes
        # (including CLOSE_GRACE), even when its full OHLC is available.
        exit_bar=dict(time=b.stamp(last_open+b.STEP),open=1.085,
            high=1.088,low=1.084,close=1.087)
        with_next=data+[exit_bar]
        b.process(s,with_next,last_open+timedelta(minutes=29))
        self.assertEqual(s['position'],initial_position)
        self.assertEqual(s['trades'],[])

        after_close=last_open+timedelta(minutes=32)
        b.process(s,with_next,after_close)
        self.assertIsNone(s['position'])
        self.assertEqual(len(s['trades']),1)
        self.assertEqual(s['trades'][0]['reason'],'STOP')
        closed_balance=s['balance']
        b.process(s,with_next,after_close+timedelta(seconds=30))
        self.assertEqual(len(s['trades']),1)
        self.assertEqual(s['balance'],closed_balance)

    def test_closed_bar_excludes_last_incomplete(self):
        data=bars(80)
        current=b.date(data[-1]['time'])+timedelta(minutes=7)
        s=b.process(b.initial(),data,current)
        self.assertEqual(s['last_bar'],data[-2]['time'])
        self.assertEqual(s['candles'][-1]['time'],data[-2]['time'])
        self.assertIsNone(s['position'])

    def test_stale_quote_blocks_entry(self):
        data=bars()
        s=b.initial()
        s['last_bar']=data[-2]['time']
        s['candles']=data[:-2]
        now=b.date(data[-1]['time'])+timedelta(minutes=22)
        with patch.object(b,'signal',return_value='BUY') as patched:
            b.process(s,data,now)
        self.assertFalse(patched.called)
        self.assertIsNone(s['position'])
        self.assertEqual(s['status'],'market_closed_or_stale')

    def test_enter_only_after_new_closed_candle_and_fresh_quote(self):
        data=bars(80)
        next_start=b.date(data[-1]['time'])+b.STEP
        quote=dict(time=b.stamp(next_start),open=data[-1]['close'],high=data[-1]['close']+.0001,
                   low=data[-1]['close']-.0001,close=data[-1]['close']+.00004)
        s=b.initial()
        s['candles']=data[:-1]
        s['last_bar']=data[-2]['time']
        with patch.object(b,'signal',return_value='BUY'):
            b.process(s,data+[quote],next_start+timedelta(minutes=7))
        self.assertIsNotNone(s['position'])
        self.assertEqual(s['position']['signal_bar'],data[-1]['time'])
        self.assertEqual(s['position']['first_exit_bar'],b.stamp(next_start+b.STEP))
        self.assertLessEqual(s['position']['planned_risk_usd'],.5+.0001)
        self.assertLessEqual(s['position']['units']*s['position']['entry'],2000+.01)

    def test_no_exit_from_opening_candle_full_ohlc(self):
        s=b.initial()
        s['position']=dict(side='BUY',entry=1.085,stop=1.084,target=1.087,
            first_exit_bar='2026-11-02T10:15:00Z',units=100,planned_risk_usd=.1)
        b.check_exit(s,dict(time='2026-11-02T10:00:00Z',open=1.085,low=1.07,high=1.09))
        self.assertIsNotNone(s['position'])
        self.assertFalse(s['trades'])

    def test_gap_exit_buy_marks_exceeded_risk(self):
        s=b.initial()
        s['position']=dict(side='BUY',entry=1.085,stop=1.084,target=1.088,
            first_exit_bar='2026-11-02T10:15:00Z',units=333,planned_risk_usd=.333)
        b.check_exit(s,dict(time='2026-11-02T10:15:00Z',open=1.082,low=1.081,high=1.086))
        self.assertEqual(s['trades'][0]['reason'],'STOP')
        self.assertLess(s['trades'][0]['pnl'],-.333)
        self.assertTrue(any('RISK_EXCEEDED' in e['message'] for e in s['events']))
        self.assertEqual(runner.summary(s)['risk_violations'],1)

    def test_gap_exit_sell_marks_exceeded_risk(self):
        s=b.initial()
        s['position']=dict(side='SELL',entry=1.085,stop=1.086,target=1.082,
            first_exit_bar='2026-11-02T10:15:00Z',units=333,planned_risk_usd=.333)
        b.check_exit(s,dict(time='2026-11-02T10:15:00Z',open=1.090,low=1.084,high=1.091))
        self.assertEqual(s['trades'][0]['reason'],'STOP')
        self.assertLess(s['trades'][0]['pnl'],-.333)

    def test_both_levels_touch_stop_first(self):
        s=b.initial()
        s['position']=dict(side='SELL',entry=1.085,stop=1.086,target=1.083,
            first_exit_bar='2026-11-02T10:15:00Z',units=100,planned_risk_usd=.1)
        b.check_exit(s,dict(time='2026-11-02T10:15:00Z',open=1.085,low=1.081,high=1.088))
        self.assertEqual(s['trades'][0]['reason'],'STOP')

    def test_bootstrap_fails_closed_on_initial_gap(self):
        data=bars(81)
        # Remove an active-market bar from the first window.
        incomplete=data[:45]+data[46:]
        s=b.process(b.initial(),incomplete,b.date(data[-1]['time'])+timedelta(minutes=20))
        self.assertTrue(s['gap_halt'])
        self.assertEqual(s['status'],'data_gap')
        self.assertIsNone(s['last_bar'])
        self.assertFalse(s['trades'])

    def test_internal_history_gap_halts_before_processing(self):
        data=bars(81)
        s=b.initial()
        s['last_bar']=data[-3]['time']
        s['candles']=data[:-3]
        s['status']='monitoring'
        b.process(s,data[:-2]+data[-1:],b.date(data[-1]['time'])+timedelta(minutes=20))
        self.assertEqual(s['status'],'data_gap')
        self.assertTrue(s['gap_halt'])
        self.assertEqual(s['last_bar'],data[-3]['time'])
        self.assertFalse(s['trades'])

    def test_gap_halt_is_latched_despite_status_overwrite(self):
        data=bars()
        s=b.initial()
        s['last_bar']=data[-4]['time']
        s['gap_halt']=True
        s['status']='api_error'
        b.process(s,data,b.date(data[-1]['time'])+timedelta(minutes=20))
        self.assertEqual(s['status'],'data_gap')
        self.assertEqual(s['last_bar'],data[-4]['time'])

    def test_missing_weekend_bars_not_flagged_as_gap(self):
        fri='2026-11-06T21:45:00Z'   # Friday 16:45 EST
        sun='2026-11-08T22:00:00Z'   # Sunday 17:00 EST
        self.assertFalse(b.missing_trading_bar(fri,sun))
        self.assertTrue(b.missing_trading_bar('2026-11-02T10:00:00Z','2026-11-02T10:30:00Z'))

    def test_risk_precision_on_stop_and_lots_both_sides(self):
        for side in ('BUY','SELL'):
            s=b.initial();s['candles']=bars();s['last_bar']=s['candles'][-1]['time']
            b.enter(s,side,1.085,b.date(s['last_bar'])+timedelta(minutes=7))
            self.assertLessEqual(s['position']['planned_risk_usd'],.5+.00001)
            self.assertLessEqual(s['position']['units']*s['position']['entry'],2000+.01)
            stop=s['position']['stop']
            b.close(s,dict(time='2026-11-02T21:00:00Z'),stop,'STOP')
            self.assertLessEqual(abs(s['trades'][0]['pnl']),.5005)

    def test_utc_day_limit_blocks_entry(self):
        data=bars(81)
        s=b.initial();s['balance']=97;s['day_start_equity']=100
        s['day']=data[-2]['time'][:10]
        s['last_bar']=data[-3]['time'];s['candles']=data[:-3]
        next_start=b.date(data[-1]['time'])+b.STEP
        quote=dict(time=b.stamp(next_start),open=1.08,high=1.081,low=1.079,close=1.08)
        with patch.object(b,'signal',return_value='BUY') as patched:
            b.process(s,data+[quote],next_start+timedelta(minutes=7))
        self.assertFalse(patched.called)
        self.assertIsNone(s['position'])

class DataFeedIntegrity(unittest.TestCase):
    def test_parse_provider_normalizes_reverse_chronology(self):
        data=b._parse_payload(valid_payload(bars(45)))
        self.assertEqual(len(data),45)
        self.assertLess(data[0]['time'],data[-1]['time'])

    def test_reject_conflicting_duplicate(self):
        obj=valid_payload(bars(45));d=obj['values'][0].copy();d['close']=str(float(d['close'])+.0001)
        obj['values'].append(d)
        with self.assertRaises(b.DataFeedError):b._parse_payload(obj)

    def test_reject_nonfinite_and_invalid_range(self):
        for key,value in [('open','NaN'),('low','5')]:
            obj=valid_payload(bars(45));obj['values'][0][key]=value
            with self.assertRaises(b.DataFeedError):b._parse_payload(obj)

    def test_http_429_retry_no_key_exposure(self):
        payload=json.dumps(valid_payload(bars(45))).encode('utf-8')
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        err=urllib.error.HTTPError('https://somewhere/?apikey=HIDDEN',429,'quota',{},None)
        pauses=[]
        with patch.object(b.urllib.request,'urlopen',side_effect=[err,Response(payload)]) as network:
            result=b.api('DO_NOT_SHARE_THIS_KEY',retries=3,sleep=pauses.append)
        self.assertEqual(len(result),45)
        self.assertEqual(len(pauses),1)
        self.assertEqual(network.call_count,2)

    def test_nonretryable_http_403(self):
        err=urllib.error.HTTPError('https://somewhere/?apikey=HIDDEN',403,'bad',{},None)
        with patch.object(b.urllib.request,'urlopen',side_effect=err):
            with self.assertRaisesRegex(b.DataFeedError,'provider_http_403') as c:
                b.api('PRIVATE_KEY',sleep=lambda _:None)
        self.assertNotIn('PRIVATE_KEY',str(c.exception))

    def test_invalid_json_retries_and_fails_closed(self):
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        with patch.object(b.urllib.request,'urlopen',side_effect=[Response(b'{'),Response(b'{')]):
            with self.assertRaisesRegex(b.DataFeedError,'provider_invalid_json'):
                b.api('PRIVATE_KEY',retries=2,sleep=lambda _:None)

    def test_state_migrates_missing_gap_halt(self):
        s=b.initial();del s['gap_halt']
        b.validate_state(s)
        self.assertFalse(s['gap_halt'])

class EncryptedStateIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.original_enc,self.original_pub=runner.ENC,runner.PUB
        runner.ENC=Path(self.tmp.name)/'state.enc'
        runner.PUB=Path(self.tmp.name)/'state.json'
        self.env=patch.dict(os.environ,{'V2_STATE_ENCRYPTION_KEY':''})
        self.env.start()

    def tearDown(self):
        runner.ENC,runner.PUB=self.original_enc,self.original_pub
        self.env.stop()
        self.tmp.cleanup()

    def test_reject_empty_key_material_before_encrypting(self):
        data=json.dumps(b.initial()).encode('utf-8')
        with patch.dict(os.environ,{'V2_STATE_ENCRYPTION_KEY':''}):
            with self.assertRaisesRegex(ValueError,'Missing encrypted-state key material'):
                runner.encrypt('',data)
            with self.assertRaisesRegex(ValueError,'Missing encrypted-state key material'):
                runner.cipher('   ')
        self.assertFalse(runner.ENC.exists())

    def test_explicit_encryption_secret_remains_backwards_compatible(self):
        data=json.dumps(b.initial()).encode('utf-8')
        with patch.dict(os.environ,{'V2_STATE_ENCRYPTION_KEY':'LONG_SECRET_FOR_OFFLINE_TESTS'}):
            runner.encrypt('',data)
            self.assertEqual(json.loads(runner.decrypt('')),b.initial())

    def test_legacy_encryption_format_roundtrip(self):
        data=json.dumps(b.initial()).encode('utf-8')
        runner.encrypt('API_PRIVATE_KEY',data)
        self.assertEqual(json.loads(runner.decrypt('API_PRIVATE_KEY')),b.initial())
        self.assertTrue(base64.b64decode(runner.ENC.read_bytes()).startswith(runner.HEADER))

    def test_truncation_fails_closed_not_reinitialized(self):
        runner.encrypt('API_PRIVATE_KEY',json.dumps(b.initial()).encode())
        before=runner.ENC.read_bytes()
        runner.ENC.write_bytes(before[:len(before)//2])
        with self.assertRaises(Exception):runner.decrypt('API_PRIVATE_KEY')
        self.assertTrue(runner.ENC.exists())

    def test_atomic_replace_failure_preserves_previous_state(self):
        file=Path(self.tmp.name)/'state.enc'
        file.write_bytes(b'ORIGINAL')
        with patch.object(runner.os,'replace',side_effect=OSError('simulated crash')):
            with self.assertRaises(OSError):runner.atomic_write(file,b'NEW STATE')
        self.assertEqual(file.read_bytes(),b'ORIGINAL')
        self.assertEqual(list(Path(self.tmp.name).glob('*.tmp')),[])

    def test_public_summary_omits_private_fields(self):
        s=b.initial();s['status']='monitoring'
        runner.write_public(s)
        published=json.loads(runner.PUB.read_text())
        self.assertFalse({'candles','position','last_quote','trades'} & published.keys())
        self.assertEqual(published['mode'],'PAPER_ONLY')
        self.assertEqual(published['gap_halt'],False)

    def test_corrupt_state_raises_not_cleared(self):
        runner.ENC.write_bytes(b'invalid')
        with self.assertRaises(Exception):runner.decrypt('KEY')
        self.assertEqual(runner.ENC.read_bytes(),b'invalid')

if __name__=='__main__':unittest.main()
