import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"v2"))
from schedule_health import analyze_runs

def run(id, at, conclusion="success", branch="main", event="schedule", created=None):
    return {"id":id,"run_started_at":at,"created_at":created or at,
            "conclusion":conclusion,"head_branch":branch,"event":event}

class ScheduleHealthTests(unittest.TestCase):
    def test_detects_delays_and_failed_runs(self):
        result=analyze_runs([
            run(1,"2026-11-05T08:00:00Z"),
            run(2,"2026-11-05T06:40:00Z","failure"),
            run(3,"2026-11-05T07:50:00Z",branch="feature"),
            run(4,"2026-11-05T07:55:00Z",event="workflow_dispatch")
        ],"2026-11-05T09:20:00Z")
        self.assertEqual(result["intervals_minutes"],[80])
        self.assertEqual(result["failed_or_cancelled_ids"],[2])
        self.assertTrue(result["schedule_over_75"])
    def test_healthy_and_queue_delay(self):
        result=analyze_runs([run(1,"2026-11-05T09:07:30Z",
          created="2026-11-05T09:07:00Z")],"2026-11-05T09:15:00Z")
        self.assertFalse(result["schedule_over_30"])
        self.assertEqual(result["queue_delay_minutes"],[0.5])
    def test_empty_is_unknown_not_healthy(self):
        result=analyze_runs([],"2026-11-05T09:15:00Z")
        self.assertIsNone(result["minutes_since_last_scheduled_start"])
    def test_future_and_naive_timestamps_fail_closed(self):
        with self.assertRaises(ValueError):
            analyze_runs([run(1,"2026-11-05T09:20:00Z")],"2026-11-05T09:15:00Z")
        with self.assertRaises(ValueError):
            analyze_runs([run(1,"2026-11-05T09:00:00")],"2026-11-05T09:15:00Z")

if __name__=="__main__": unittest.main()
