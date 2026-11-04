"""Adversarial consolidation tests. Offline, fake prices, no broker/API secrets."""
import io
import json
import os
import ssl
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'v2'))
import paper_bot as b
import recover_gap as recovery
import runner
from test_v21_hardening import bars, valid_payload

UTC=timezone.utc
FIXTURES=ROOT/'tests'/'fixtures'

class DataValidationAdversarial(unittest.TestCase):
    class Response(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*args):self.close()

    def test_provider_http200_error_429_retried(self):
        good=json.dumps(valid_payload(bars(45))).encode()
        error=json.dumps({'status':'error','code':429,'message':'throttled'}).encode()
        pauses=[]
        with patch.object(b.urllib.request,'urlopen',side_effect=[self.Response(error),self.Response(good)]) as urlopen:
            parsed=b.api('TEST_ONLY',sleep=pauses.append)
        self.assertEqual(len(parsed),45)
        self.assertEqual(urlopen.call_count,2)
        self.assertEqual(pauses,[2])

    def test_provider_http200_error_401_fail_fast(self):
        error=json.dumps({'status':'error','code':401,'message':'do not leak'}).encode()
        with patch.object(b.urllib.request,'urlopen',return_value=self.Response(error)) as urlopen:
            with self.assertRaisesRegex(b.DataFeedError,'provider_status_401') as err:
                b.api('TOP_SECRET_KEY',sleep=lambda _:self.fail('Unexpected retry'))
        self.assertNotIn('TOP_SECRET_KEY',str(err.exception))
        self.assertEqual(urlopen.call_count,1)

    def test_tls_error_is_retried(self):
        good=json.dumps(valid_payload(bars(45))).encode()
        with patch.object(b.urllib.request,'urlopen',side_effect=[ssl.SSLError('transient tls'),self.Response(good)]) as call:
            parsed=b.api('TEST_KEY',sleep=lambda _:None)
        self.assertEqual(len(parsed),45)
        self.assertEqual(call.call_count,2)

    def test_retries_zero_rejected_cleanly(self):
        with self.assertRaisesRegex(ValueError,'positive integer'):
            b.api('KEY',retries=0)

    def test_status_message_migrated_and_bad_type_rejected(self):
        s=b.initial()
        del s['status'];del s['message'];del s['gap_halt']
        b.validate_state(s)
        self.assertEqual(s['status'],'unknown')
        self.assertEqual(s['message'],'')
        self.assertFalse(s['gap_halt'])
        s['gap_halt']='False'
        with self.assertRaises(ValueError):b.validate_state(s)

class StateAndRiskAdversarial(unittest.TestCase):
    def test_original_v2_encrypted_fixture_is_readable_and_migrates(self):
        frozen=json.loads((FIXTURES/'v2_legacy_synthetic_state.json').read_text())
        sealed=FIXTURES/'v2_legacy_synthetic_state.enc.txt'
        self.assertNotIn('gap_halt',frozen)
        with patch.dict(os.environ,{'V2_STATE_ENCRYPTION_KEY':''}):
            legacy_json=json.loads(runner.decrypt('V2_SYNTHETIC_TEST_KEY_DO_NOT_USE',path=sealed))
        self.assertEqual(frozen,legacy_json)
        migrated=b.validate_state(legacy_json)
        self.assertFalse(migrated['gap_halt'])
        self.assertEqual(migrated['balance'],99.5)
        self.assertEqual(len(migrated['trades']),1)
        self.assertEqual(migrated['trades'][0]['reason'],'STOP')

    def test_idempotency_when_series_grows_then_repeats(self):
        beginning=bars(65)
        s=b.process(b.initial(),beginning,b.date(beginning[-1]['time'])+timedelta(minutes=20))
        old_last=s['last_bar']
        extended=bars(81)  # final bar is still forming: use only 80 closed bars
        now=b.date(extended[-1]['time'])+timedelta(minutes=7)
        with patch.object(b,'signal',return_value='BUY') as spy:
            b.process(s,extended,now)
            self.assertGreater(s['last_bar'],old_last)
            self.assertTrue(spy.called)
            previous_trade_count=len(s['trades'])
            previous_events=len(s['events'])
            previous_position=json.dumps(s['position'],sort_keys=True)
            spy.reset_mock()
            b.process(s,extended,now)
            self.assertFalse(spy.called)
        self.assertEqual(s['last_bar'],extended[-2]['time'])
        self.assertEqual(len(s['trades']),previous_trade_count)
        self.assertEqual(len(s['events']),previous_events)
        self.assertEqual(json.dumps(s['position'],sort_keys=True),previous_position)

    def test_daily_loss_guard_fails_but_allows_identical_positive_case(self):
        data=bars(81)
        next_start=b.date(data[-1]['time'])+b.STEP
        quote=dict(time=b.stamp(next_start),open=1.08,high=1.081,low=1.079,close=1.08)
        def make_state(balance):
            s=b.initial();s['balance']=balance;s['day_start_equity']=100
            s['day']=data[-2]['time'][:10]
            s['last_bar']=data[-3]['time'];s['candles']=data[:-3]
            return s
        with patch.object(b,'signal',return_value='BUY') as spy:
            denied=make_state(97)
            b.process(denied,data+[quote],next_start+timedelta(minutes=7))
            self.assertFalse(spy.called)
            self.assertIsNone(denied['position'])
            spy.reset_mock()
            accepted=make_state(99.5)
            b.process(accepted,data+[quote],next_start+timedelta(minutes=7))
            self.assertTrue(spy.called)
            self.assertIsNotNone(accepted['position'])

    def test_risk_is_near_budget_both_directions_for_unconstrained_case(self):
        for side in ('BUY','SELL'):
            s=b.initial();s['candles']=bars(80);s['last_bar']=s['candles'][-1]['time']
            b.enter(s,side,1.085,b.date(s['last_bar'])+timedelta(minutes=7))
            pos=s['position']
            self.assertIsNotNone(pos)
            self.assertGreaterEqual(pos['planned_risk_usd'],0.49)
            self.assertLessEqual(pos['planned_risk_usd'],0.50001)
            self.assertLessEqual(pos['units']*pos['entry'],2000.01)

    def test_exact_boundary_entry_cannot_exit_on_same_bar(self):
        s=b.initial();s['candles']=bars(80);s['last_bar']=s['candles'][-1]['time']
        clock=datetime(2026,11,3,10,0,tzinfo=UTC)
        b.enter(s,'BUY',1.085,clock)
        self.assertEqual(s['position']['first_exit_bar'],'2026-11-03T10:15:00Z')
        b.check_exit(s,dict(time='2026-11-03T10:00:00Z',open=1.085,high=1.09,low=1.07))
        self.assertIsNotNone(s['position'])
        b.check_exit(s,dict(time='2026-11-03T10:15:00Z',open=1.085,high=1.09,low=1.07))
        self.assertIsNone(s['position'])

    def test_public_summary_displays_individual_risk_excess(self):
        s=b.initial();s['balance']=98.7
        s['trades']=[dict(exit_at='2026-11-02T11:00:00Z',side='BUY',pnl=-1.2,reason='STOP',planned_risk_usd=.5),
                     dict(exit_at='2026-11-02T12:00:00Z',side='SELL',pnl=-.1,reason='STOP',planned_risk_usd=.5)]
        out=runner.summary(s)
        self.assertEqual(out['risk_violations'],1)
        self.assertFalse(out['closed_results'][0]['risk_exceeded'])
        self.assertTrue(out['closed_results'][1]['risk_exceeded'])
        self.assertEqual(out['closed_results'][1]['planned_risk_usd'],.5)
        self.assertFalse({'candles','position','trades','last_quote'} & out.keys())

    def test_blocked_entry_emits_reason(self):
        s=b.initial();s['gap_halt']=True
        b.enter(s,'BUY',1.085,datetime(2026,11,2,12,0,tzinfo=UTC))
        self.assertIsNone(s['position'])
        self.assertIn('ENTRY_BLOCKED_BY_GAP_HALT',s['events'][-1]['message'])

class ManualRecoveryAdversarial(unittest.TestCase):
    def new_state(self):
        s=b.initial();s['gap_halt']=True;s['balance']=97.5
        s['last_bar']=bars(20)[-1]['time']
        s['status']='data_gap'
        return s

    def good_snapshot(self):
        src=bars(80)
        return valid_payload(src),b.date(src[-1]['time'])+timedelta(minutes=20)

    def test_proposal_preserves_balance_trades_and_records_skip(self):
        s=self.new_state();data,now=self.good_snapshot()
        amended=recovery.propose_recovery(s,data,now,'Reviewed and accepted missing data')
        self.assertTrue(s['gap_halt'])  # pure, input unchanged
        self.assertFalse(amended['gap_halt'])
        self.assertEqual(amended['status'],'recovery_paused')
        self.assertEqual(amended['balance'],97.5)
        self.assertEqual(amended['trades'],s['trades'])
        self.assertEqual(amended['last_bar'],bars(80)[-1]['time'])
        self.assertEqual(len(amended['candles']),40)
        self.assertIsNone(amended['last_quote'])
        self.assertIn('RECOVERY_GAP_SKIPPED',amended['events'][-1]['message'])
        self.assertEqual(amended['recovery_count'],1)

    def test_open_position_refuses_recovery(self):
        s=self.new_state();s['position']=dict(side='BUY',entry=1.085,units=100)
        data,now=self.good_snapshot()
        with self.assertRaisesRegex(ValueError,'Open paper position'):
            recovery.propose_recovery(s,data,now,'Operator reviewed snapshot')

    def test_unresolved_gap_in_tail_refuses_recovery(self):
        s=self.new_state();data,now=self.good_snapshot()
        del data['values'][2]  # newest OHLC in reverse chronology: gap inside the trailing 40
        with self.assertRaisesRegex(ValueError,'not strictly contiguous'):
            recovery.propose_recovery(s,data,now,'Operator reviewed snapshot')

    def test_old_or_nonadvancing_snapshot_refuses_recovery(self):
        s=self.new_state();data,now=self.good_snapshot()
        with self.assertRaisesRegex(ValueError,'freshness window'):
            recovery.propose_recovery(s,data,now+timedelta(hours=3),'Operator reviewed snapshot')
        s['last_bar']=bars(80)[-1]['time']
        with self.assertRaisesRegex(ValueError,'No newer completed candle'):
            recovery.propose_recovery(s,data,now,'Operator reviewed snapshot')

    def test_recovery_refuses_apply_to_repository_path(self):
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as folder:
            outside=Path(folder)
            data,now=self.good_snapshot()
            reviewed=outside/'reviewed.json'
            reviewed.write_text(json.dumps(data))
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stop:
                    recovery.main(['--state',str(ROOT/'v2'/'state.enc'),
                        '--snapshot',str(reviewed),'--reason','Reviewed exchange holiday',
                        '--apply','--acknowledge-skipped-paper-trades',
                        '--backup',str(outside/'recovery-backup.enc')])
            self.assertEqual(stop.exception.code,2)
            self.assertFalse((outside/'recovery-backup.enc').exists())

    def test_backup_is_exclusive(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'original.enc'
            recovery.backup_bytes_exclusive(target,b'FAKE CIPHERTEXT')
            self.assertEqual(target.read_bytes(),b'FAKE CIPHERTEXT')
            with self.assertRaises(FileExistsError):
                recovery.backup_bytes_exclusive(target,b'OTHER')
            self.assertEqual(target.read_bytes(),b'FAKE CIPHERTEXT')

if __name__=='__main__':unittest.main()
