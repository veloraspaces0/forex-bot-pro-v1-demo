import sys
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v2'))
import paper_bot as b

UTC=timezone.utc

def bars(count=60, start=datetime(2026, 11, 2, 9, 0, tzinfo=UTC)):
    out=[]
    for i in range(count):
        o=1.08 + i*.00001
        out.append(dict(time=b.stamp(start+timedelta(minutes=15*i)),
                        open=o, high=o+.00025, low=o-.00025, close=o+.00005))
    return out

class PaperBotTests(unittest.TestCase):
    def test_initial_mode(self):
        s=b.initial()
        self.assertEqual(s['balance'],100)
        self.assertEqual(s['mode'],'PAPER_ONLY')
    def test_indicators(self):
        data=bars()
        self.assertGreater(b.ema([x['close'] for x in data],9),1)
        self.assertGreater(b.atr(data),0)
        self.assertTrue(0<=b.rsi([x['close'] for x in data])<=100)
    def test_bootstrap_no_backfill(self):
        data=bars()
        now=b.date(data[-1]['time'])+timedelta(minutes=10)
        s=b.process(b.initial(),data,now)
        self.assertEqual(len(s['trades']),0)
        self.assertIsNone(s['position'])
        self.assertEqual(s['last_bar'],data[-2]['time'])
    def test_risk_and_leverage(self):
        s=b.initial()
        s['candles']=bars()
        s['last_bar']=s['candles'][-1]['time']
        s['last_quote']=1.08
        b.enter(s,'BUY',1.08,b.date(s['last_bar'])+timedelta(minutes=20))
        p=s['position']
        self.assertIsNotNone(p)
        self.assertLessEqual(p['planned_risk_usd'],.5+0.001)
        self.assertLessEqual(p['units']*p['entry'],2000+0.1)
        self.assertGreater(p['target'],p['entry'])
    def test_stop_first_same_bar(self):
        s=b.initial()
        s['position']=dict(side='BUY',entry=1.1000,stop=1.099, target=1.101,units=100,
                           first_exit_bar='2026-11-02T00:00:00Z')
        b.check_exit(s,dict(time='2026-11-02T12:00:00Z',
                            open=1.1000, high=1.102,low=1.098,close=1.1000))
        self.assertIsNone(s['position'])
        self.assertEqual(s['trades'][0]['reason'],'STOP')
        self.assertLess(s['balance'],100)
    def test_gapped_history_halts(self):
        s=b.initial()
        s['last_bar']='2026-11-01T00:00:00Z'
        data=bars()
        now=b.date(data[-1]['time'])+timedelta(minutes=20)
        b.process(s,data,now)
        self.assertEqual(s['status'],'data_gap')
        self.assertIsNone(s['position'])
if __name__=='__main__':
    unittest.main()
