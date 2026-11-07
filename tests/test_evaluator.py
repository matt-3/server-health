"""The Evaluator: persistence, state changes, reminders and deploys."""

from __future__ import annotations

import unittest

import helpers
from helpers import HOUR, T0
from server_health.config import CORE_THRESHOLDS
from server_health.evaluator import Evaluator
from server_health.metric import Metric


class EvaluatorTest(unittest.TestCase):
    def setUp(self):
        helpers.use_lang("en")

    def run_eval(self, state, metrics, now, deploy=False):
        ev = Evaluator(state, CORE_THRESHOLDS, now, deploy)
        changes = ev.apply(metrics)
        return changes, {"metrics": ev.next_state}

    def m(self, value="80%", level="WARN", **kw):
        return Metric("host.memory", "Server", "Memory", value, level, **kw)

    def test_a_different_value_does_not_repeat_the_alert(self):
        # Regression from the shell monitor: the anti-spam key carried the measured value,
        # and 3067 MB turning into 3071 MB repeated the warning every round.
        changes, state = self.run_eval({}, [self.m("3067 MB")], T0)
        self.assertEqual([c.kind for c in changes], ["new"])
        for i, v in enumerate(("3071 MB", "3069 MB", "3080 MB"), 1):
            changes, state = self.run_eval(state, [self.m(v)], T0 + i * 300)
            self.assertEqual(changes, [])

    def test_persistence_holds_the_warning(self):
        changes, state = self.run_eval({}, [self.m(persist_min=15)], T0)
        self.assertEqual(changes, [])
        changes, state = self.run_eval(state, [self.m(persist_min=15)], T0 + 600)
        self.assertEqual(changes, [])
        changes, state = self.run_eval(state, [self.m(persist_min=15)], T0 + 900)
        self.assertEqual([c.kind for c in changes], ["new"])

    def test_short_peak_does_not_alert(self):
        changes, state = self.run_eval({}, [self.m(persist_min=15)], T0)
        changes, state = self.run_eval(state, [self.m(level="OK", persist_min=15)], T0 + 300)
        changes, state = self.run_eval(state, [self.m(persist_min=15)], T0 + 600)
        self.assertEqual(changes, [])

    def test_reminder_and_back_to_normal(self):
        changes, state = self.run_eval({}, [self.m()], T0)
        changes, state = self.run_eval(state, [self.m()], T0 + 23 * HOUR)
        self.assertEqual(changes, [])
        changes, state = self.run_eval(state, [self.m()], T0 + 24 * HOUR)
        self.assertEqual([c.kind for c in changes], ["reminder"])
        changes, state = self.run_eval(state, [self.m(level="OK")], T0 + 25 * HOUR)
        self.assertEqual([c.kind for c in changes], ["normal"])
        self.assertAlmostEqual(changes[0].duration, 25 * HOUR)

    def test_worse_and_better(self):
        changes, state = self.run_eval({}, [self.m()], T0)
        changes, state = self.run_eval(state, [self.m(level="CRIT")], T0 + 300)
        self.assertEqual([(c.kind, c.after) for c in changes], [("worse", "CRIT")])
        changes, state = self.run_eval(state, [self.m(level="WARN")], T0 + 600)
        self.assertEqual([(c.kind, c.after) for c in changes], [("better", "WARN")])

    def test_event_alerts_every_time_without_back_to_normal(self):
        oom = lambda lv: Metric("host.oom", "Server", "OOM", "x", lv, event=True)  # noqa: E731
        changes, state = self.run_eval({}, [oom("CRIT")], T0)
        self.assertEqual([c.kind for c in changes], ["event"])
        changes, state = self.run_eval(state, [oom("CRIT")], T0 + 300)
        self.assertEqual([c.kind for c in changes], ["event"])
        changes, state = self.run_eval(state, [oom("OK")], T0 + 600)
        self.assertEqual(changes, [])

    def test_deploy_holds_only_the_sensitive_metric(self):
        load = Metric("host.load", "Server", "Load", "3", "CRIT", deploy_sensitive=True)
        app = Metric("app.x.ps", "x", "Processes", "none", "CRIT")
        changes, _ = self.run_eval({}, [load, app], T0, deploy=True)
        self.assertEqual([c.metric.key for c in changes], ["app.x.ps"])
        self.assertIn("deploy", load.note)

    def test_recent_deploy_holds_only_what_looks_back(self):
        # The load has persistence and memory is a snapshot of now: neither alerts for a
        # deploy that already ended. Only the swap-out rate covers the whole round.
        load = Metric("host.load", "Server", "Load", "3", "CRIT", deploy_sensitive=True)
        swap = Metric("host.swap", "Server", "Swap", "1,248 KB/s", "CRIT", deploy_sensitive=True,
                      deploy_lookback=True)
        changes = Evaluator({}, CORE_THRESHOLDS, T0, False, deploy_recent=True).apply([load, swap])
        self.assertEqual([c.metric.key for c in changes], ["host.load"])
        self.assertIn("deploy", swap.note)

    def test_deploy_does_not_close_the_open_alert(self):
        # The critical load needs 10 min in a row: if the deploy reset the timer, the round
        # after it would say a false "back to normal".
        load = lambda: Metric("host.load", "Server", "Load", "3", "CRIT", deploy_sensitive=True,  # noqa: E731
                              crit_persist_min=10)
        changes, state = self.run_eval({}, [load()], T0)
        self.assertEqual([(c.kind, c.after) for c in changes], [("new", "WARN")])
        changes, state = self.run_eval(state, [load()], T0 + 600)
        self.assertEqual([(c.kind, c.after) for c in changes], [("worse", "CRIT")])

        during = load()
        changes, state = self.run_eval(state, [during], T0 + 900, deploy=True)
        self.assertEqual(changes, [])
        self.assertEqual(during.effective, "CRIT")
        self.assertIn("deploy", during.note)

        changes, state = self.run_eval(state, [load()], T0 + 1200)
        self.assertEqual(changes, [])
        self.assertEqual(state["metrics"]["host.load"]["started"], T0)

    def test_alert_held_by_the_deploy_keeps_its_reminder_and_duration(self):
        swap = lambda lv="CRIT": Metric("host.swap", "Server", "Swap", "x", lv, deploy_sensitive=True)  # noqa: E731
        changes, state = self.run_eval({}, [swap()], T0)
        changes, state = self.run_eval(state, [swap()], T0 + HOUR, deploy=True)
        self.assertEqual(changes, [])
        changes, state = self.run_eval(state, [swap()], T0 + 2 * HOUR)
        self.assertEqual([c.kind for c in changes], ["reminder"])
        changes, state = self.run_eval(state, [swap("OK")], T0 + 3 * HOUR)
        self.assertEqual([c.kind for c in changes], ["normal"])
        self.assertAlmostEqual(changes[0].duration, 3 * HOUR)

    def test_really_back_to_normal_during_a_deploy_is_reported(self):
        swap = lambda lv="CRIT": Metric("host.swap", "Server", "Swap", "x", lv, deploy_sensitive=True)  # noqa: E731
        changes, state = self.run_eval({}, [swap()], T0)
        changes, state = self.run_eval(state, [swap("OK")], T0 + 300, deploy=True)
        self.assertEqual([c.kind for c in changes], ["normal"])

    def test_missing_metric_does_not_say_back_to_normal(self):
        changes, state = self.run_eval({}, [self.m()], T0)
        changes, state = self.run_eval(state, [], T0 + 300)
        self.assertEqual(changes, [])
        self.assertIn("host.memory", state["metrics"])

    def test_missing_metric_state_expires_after_a_day(self):
        changes, state = self.run_eval({}, [self.m()], T0)
        changes, state = self.run_eval(state, [], T0 + 25 * HOUR)
        self.assertNotIn("host.memory", state["metrics"])

    def test_summary_only_never_alerts(self):
        m = Metric("ind.x.paid", "x · product", "Paid", "3", "CRIT", summary_only=True)
        changes, state = self.run_eval({}, [m], T0)
        self.assertEqual(changes, [])
        self.assertEqual(m.effective, "OK")
        self.assertNotIn("ind.x.paid", state["metrics"])


if __name__ == "__main__":
    unittest.main()
