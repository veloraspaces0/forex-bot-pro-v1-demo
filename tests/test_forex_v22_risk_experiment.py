import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"experimentos"))
from forex_v22_risk_experiment import adaptive_risk_fraction, trailing_stop, exposure_allowed

class RiskExperiments(unittest.TestCase):
    def test_baseline_risk_and_loss_streak(self):
        self.assertEqual(adaptive_risk_fraction([]),0.005)
        self.assertEqual(adaptive_risk_fraction([{"pnl":-1}]),0.0025)
        self.assertEqual(adaptive_risk_fraction([{"pnl":-1},{"pnl":-1}]),0.00125)
        self.assertEqual(adaptive_risk_fraction([{"pnl":-1}]*10),0.00125)
        self.assertEqual(adaptive_risk_fraction([{"pnl":-1},{"pnl":1}]),0.005)
    def test_buy_trailing_only_tightens(self):
        p={"side":"BUY","entry":1.1,"stop":1.09}
        self.assertEqual(trailing_stop(p,{"high":1.12,"low":1.09,"close":1.115},0.005),1.1075)
        self.assertEqual(p["stop"],1.09)
        self.assertEqual(trailing_stop(p,{"high":1.2,"low":1.05,"close":1.08},0.005),1.09)
    def test_sell_trailing_only_tightens(self):
        p={"side":"SELL","entry":1.1,"stop":1.11}
        self.assertAlmostEqual(trailing_stop(p,{"high":1.11,"low":1.08,"close":1.09},0.005),1.0975)
        self.assertEqual(trailing_stop(p,{"high":1.12,"low":1.08,"close":1.12},0.005),1.11)
    def test_single_position(self):
        self.assertTrue(exposure_allowed(None))
        self.assertFalse(exposure_allowed({"side":"BUY"}))
        with self.assertRaises(ValueError): exposure_allowed(None,2)
    def test_fail_closed_invalid_parameters(self):
        with self.assertRaises(ValueError): adaptive_risk_fraction([{"pnl":float("nan")}])
        with self.assertRaises(ValueError): adaptive_risk_fraction([],base=0.01)
        with self.assertRaises(ValueError):
            trailing_stop({"side":"BUY","entry":1.1,"stop":1.09},{"high":1.1,"low":1.09,"close":1.1},0)
if __name__=="__main__": unittest.main()
