import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experimentos"))
from forex_v211_contingency_advisory import assess_contingency

NOW = dt.datetime(2026, 11, 4, 22, 15, tzinfo=dt.timezone.utc)

def fixture():
    return {
        "checked_at_utc": NOW.isoformat(),
        "required_evidence_complete": True,
        "operational_health": "degraded",
        "last_run_age_minutes": 48.0,
        "cron_slot_evidence": {
            "tolerance_minutes": 3,
            "unmatched_slots": ["2026-11-04T21:52:00+00:00"],
        },
    }

class ContingencyAdvisoryTests(unittest.TestCase):
    def test_advisory_only_never_dispatch(self):
        result = assess_contingency(fixture(), NOW)
        self.assertEqual(result["advisory"], "REVIEW_ONLY")
        self.assertNotIn("dispatch", result)

    def test_no_unmatched_slots(self):
        x = fixture()
        x["cron_slot_evidence"]["unmatched_slots"] = []
        self.assertEqual(assess_contingency(x, NOW)["advisory"], "NO_ACTION")

    def test_no_recent_report(self):
        x = fixture()
        x["checked_at_utc"] = (NOW - dt.timedelta(minutes=8)).isoformat()
        self.assertEqual(assess_contingency(x, NOW)["advisory"], "NO_ACTION")

    def test_future_report_rejected(self):
        x = fixture()
        x["checked_at_utc"] = (NOW + dt.timedelta(minutes=1)).isoformat()
        self.assertEqual(assess_contingency(x, NOW)["advisory"], "NO_ACTION")

    def test_incomplete_state_rejected(self):
        x = fixture()
        x["required_evidence_complete"] = False
        self.assertEqual(assess_contingency(x, NOW)["advisory"], "NO_ACTION")

    def test_non_degraded_state_rejected(self):
        x = fixture()
        x["operational_health"] = "healthy"
        self.assertEqual(assess_contingency(x, NOW)["advisory"], "NO_ACTION")

    def test_run_not_overdue(self):
        x = fixture()
        x["last_run_age_minutes"] = 10
        self.assertEqual(assess_contingency(x, NOW)["advisory"], "NO_ACTION")

    def test_missing_run_age_rejected(self):
        x = fixture()
        x.pop("last_run_age_minutes")
        self.assertEqual(assess_contingency(x, NOW)["advisory"], "NO_ACTION")

    def test_untrusted_tolerance_rejected(self):
        x = fixture()
        x["cron_slot_evidence"]["tolerance_minutes"] = 30
        self.assertEqual(assess_contingency(x, NOW)["advisory"], "NO_ACTION")

    def test_invalid_slot_rejected(self):
        x = fixture()
        x["cron_slot_evidence"]["unmatched_slots"] = [None]
        self.assertEqual(assess_contingency(x, NOW)["advisory"], "NO_ACTION")

    def test_invalid_cooldown_rejected(self):
        with self.assertRaises(ValueError):
            assess_contingency(fixture(), NOW, cooldown_minutes=1)

    def test_no_io_or_credential_dependencies(self):
        import inspect
        import forex_v211_contingency_advisory as mod
        source = inspect.getsource(mod)
        for forbidden in ("urlopen", "requests.", "subprocess", "git push", "workflow_dispatch", "open(", "GITHUB_TOKEN"):
            self.assertNotIn(forbidden, source)

if __name__ == "__main__":
    unittest.main()
