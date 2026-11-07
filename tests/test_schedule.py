"""When the daily summary and the weekly report go out: SERVER_HEALTH_DAILY_AT and
SERVER_HEALTH_WEEKLY_AT, in SERVER_HEALTH_TZ, on the first round after their time.

The fixture's clock (T0) is Saturday, 2026-10-31, 15:00 in America/Sao_Paulo."""

from __future__ import annotations

import unittest

from helpers import HOUR, T0, SandboxCase


class ScheduleTest(SandboxCase, unittest.TestCase):
    def defaults(self):
        del self.sb.env["SERVER_HEALTH_DAILY_AT"]
        del self.sb.env["SERVER_HEALTH_WEEKLY_AT"]

    def test_defaults_send_both_once(self):
        self.defaults()
        self.sb.run(T0)
        self.assertEqual(len(self.sb.reports()), 2)
        self.assertTrue(self.sb.reports()[0]["embeds"][0]["title"].endswith("everything within the thresholds"))
        self.assertIn("Week from", self.sb.reports()[1]["embeds"][0]["title"])
        state = self.sb.state()
        self.assertEqual((state["daily_sent"], state["weekly_sent"]), ("2026-10-31", "2026-10-31"))
        self.sb.run(T0 + 300)
        self.sb.run(T0 + 6 * HOUR)
        self.assertEqual(len(self.sb.reports()), 2)

    def test_custom_time_waits_for_it(self):
        self.sb.env["SERVER_HEALTH_DAILY_AT"] = "18:30"
        self.sb.run(T0)                      # 15:00
        self.sb.run(T0 + 3 * HOUR + 1500)    # 18:25
        self.assertEqual(self.sb.reports(), [])
        self.sb.run(T0 + 3 * HOUR + 1860)    # 18:31
        self.assertEqual(len(self.sb.reports()), 1)

    def test_weekly_only_on_its_day(self):
        self.sb.env["SERVER_HEALTH_WEEKLY_AT"] = "mon 09:00"
        self.sb.run(T0)
        self.assertEqual(self.sb.reports(), [])
        self.sb.run(T0 + 42 * HOUR)          # Monday 09:00
        titles = [p["embeds"][0]["title"] for p in self.sb.reports()]
        self.assertEqual(len(titles), 1)
        self.assertTrue(titles[0].startswith("🟢 Week from"), titles)

    def test_server_off_at_the_time_sends_later_the_same_day_but_not_the_next(self):
        self.sb.env["SERVER_HEALTH_DAILY_AT"] = "09:00"
        self.sb.run(T0 + 8 * HOUR)           # 23:00, the first round of the day
        self.assertEqual(len(self.sb.reports()), 1)
        # Off the whole next day until 08:55: Sunday's summary is not sent early or twice.
        self.sb.run(T0 + 17 * HOUR + 3300)   # Sunday 08:55
        self.assertEqual(len(self.sb.reports()), 1)
        self.sb.run(T0 + 18 * HOUR + 120)    # Sunday 09:02
        self.assertEqual(len(self.sb.reports()), 2)

    def test_the_day_is_the_configured_time_zone(self):
        # 23:30 on Saturday in São Paulo is already Sunday in UTC.
        self.sb.env["SERVER_HEALTH_DAILY_AT"] = "23:00"
        self.sb.run(T0 + 8 * HOUR + 1800)
        self.assertEqual(self.sb.state()["daily_sent"], "2026-10-31")
        self.sb.env["SERVER_HEALTH_TZ"] = "UTC"
        self.sb.run(T0 + 8 * HOUR + 2100)    # 02:35 UTC on Sunday: before 23:00 UTC
        self.assertEqual(len(self.sb.reports()), 1)

    def test_off_turns_them_off(self):
        self.sb.run(T0)
        self.sb.run(T0 + 300, "--dry-run")
        self.assertEqual(self.sb.reports(), [])
        self.assertIn("Next daily summary: off · next weekly report: off", self.sb.out)

    def test_dry_run_says_when(self):
        self.defaults()
        self.sb.run(T0, "--dry-run")
        self.assertIn("Next daily summary: on the next round · next weekly report: on the next round", self.sb.out)
        self.sb.run(T0)
        self.sb.run(T0 + 300, "--dry-run")
        self.assertIn("Next daily summary: Nov 01 09:00 · next weekly report: Nov 07 07:00", self.sb.out)

    def test_invalid_value_warns_and_uses_the_default(self):
        self.sb.env["SERVER_HEALTH_DAILY_AT"] = "9am"
        self.sb.env["SERVER_HEALTH_WEEKLY_AT"] = "someday"
        self.sb.run(T0, "--dry-run")
        self.assertIn('CONFIG WARNING: SERVER_HEALTH_DAILY_AT="9am" cannot be read', self.sb.out)
        self.assertIn('CONFIG WARNING: SERVER_HEALTH_WEEKLY_AT="someday" cannot be read', self.sb.out)
        self.assertIn("next weekly report: on the next round", self.sb.out)

    def test_force_sends_now_without_marking(self):
        self.sb.run(T0, "--force")
        self.assertIn("TEST · Summary of", self.sb.discord()[0]["embeds"][0]["title"])
        self.assertNotIn("daily_sent", self.sb.state() if (self.sb.state_dir / "state.json").exists() else {})

    def test_the_old_flags_are_gone(self):
        for flag in ("--daily-summary", "--weekly-summary"):
            with self.assertRaises(SystemExit):
                self.sb.run(T0, flag)


if __name__ == "__main__":
    unittest.main()
