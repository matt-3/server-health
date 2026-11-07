"""The messages: alert, daily summary and weekly report."""

from __future__ import annotations

import json
import types
import unittest
from zoneinfo import ZoneInfo

import helpers
from helpers import T0, SandboxCase, two_weeks_of_state
from server_health.context import Context
from server_health.metric import Change, Metric
from server_health.modules.host import Host
from server_health.report import ReportContext, build_alert, build_summary
from server_health.text import join_blocks, md, plain

UTC = ZoneInfo("UTC")


class WeeklyTest(SandboxCase, unittest.TestCase):
    def test_weekly_report(self):
        two_weeks_of_state(self.sb)
        before = (self.sb.state_dir / "state.json").read_text()
        self.assertEqual(self.sb.run(T0, "--force-weekly"), 0)
        embed = self.sb.discord()[0]["embeds"][0]
        self.assertTrue(embed["title"].startswith("🟢 TEST · Week from Oct 24 to Oct 31 · 1 alert · 100.00% up"),
                        embed["title"])
        fields = {f["name"]: f["value"] for p in self.sb.discord() for f in p["embeds"][0]["fields"]}
        self.assertIn("1 alert opened this week · previous week: 0", fields["Alerts of the week"])
        self.assertIn("**Load**: 1 time, 20 min in total", fields["Alerts of the week"])
        app = fields["my-app"]
        self.assertIn("Available 100.00% of the rounds", app)
        self.assertIn("(+100% vs previous week)", app)
        self.assertIn("Deploys: 1", app)
        self.assertIn("Failed jobs: 2 (Orders::SyncJob 2)", app)
        bots = fields["my-app · bots"]
        self.assertIn("New this week: ChatGPT-User", bots)
        self.assertIn("Pages most read by AI: /en/how-it-works", bots)
        self.assertIn("Paid orders: 21", fields["my-app · numbers of the week"])
        self.assertIn("my-app-db", fields["Databases"])
        self.assertIn("RAM: avg 50%", fields["Server"])
        self.assertEqual((self.sb.state_dir / "state.json").read_text(), before)  # the test writes nothing

    def test_weekly_report_without_dashboard_sends_no_file(self):
        two_weeks_of_state(self.sb)
        self.sb.run(T0, "--force-weekly")
        self.assertEqual([s for s in self.sb.sent if s["kind"] == "multipart"], [])

    def test_weekly_report_carries_the_dashboard(self):
        two_weeks_of_state(self.sb)
        self.sb.env["SERVER_HEALTH_WEEKLY_DASHBOARD"] = "true"
        self.assertEqual(self.sb.run(T0, "--force-weekly"), 0)
        files = [s for s in self.sb.sent if s["kind"] == "multipart"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["url"], helpers.SERVER_URL)
        self.assertEqual(files[0]["filename"], "server-health-2026-10-31.html")
        self.assertEqual(files[0]["content_type"], "text/html")
        html = files[0]["content"].decode("utf-8")
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("Server health · test", html)
        self.assertIn("Week from Oct 24", files[0]["payload"]["embeds"][0]["title"])

    def test_weekly_cron_round_keeps_the_state(self):
        two_weeks_of_state(self.sb)
        self.assertEqual(self.sb.run_report(T0, "weekly"), 0)
        self.assertIn("Week from Oct 24", self.sb.titles()[0])
        self.assertNotIn("TEST", self.sb.titles()[0])
        self.assertIn("weekly report sent", self.sb.log())


class DailySummaryTest(SandboxCase, unittest.TestCase):
    def test_daily_summary(self):
        self.sb.run(T0)
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.run_report(T0 + 600), 0)
        embed = self.sb.discord()[0]["embeds"][0]
        self.assertIn("Summary of Oct 31", embed["title"])
        text = json.dumps(embed, ensure_ascii=False)
        for frag in ("Paid orders (24h): 3", "Largest tables", "events 100 MB", "Updates", "3 security",
                     "Last 24h", "RAM: min", "Docker", "- Images", "Cache hit ratio: 99.9%"):
            self.assertIn(frag, text)
        server = next(f["value"] for f in embed["fields"] if f["name"] == "Server")
        self.assertIn("\n\n**Last 24h**\n- RAM:", server)
        self.assertTrue(all(not f["value"].startswith("\n") for f in embed["fields"]))
        self.assertIn("my-app-db", self.sb.state()["tables"])

    def test_daily_summary_keeps_the_numbers_and_the_round_keeps_them_too(self):
        self.sb.run(T0)
        self.sb.run_report(T0 + 60)
        self.sb.run(T0 + 300)
        state = self.sb.state()
        self.assertEqual(state["daily_numbers"]["2026-10-31"]["my-app|paid_orders_24h"],
                         {"label": "Paid orders (24h)", "value": 3.0, "format": "number"})


class BuildersTest(unittest.TestCase):
    def setUp(self):
        helpers.use_lang("en")

    def test_who_uses_the_memory_in_order_with_the_short_database_name(self):
        fake = types.SimpleNamespace(mem_by_container={"app.web.1": 2 * 1024 ** 2, "dokku.postgres.app-db": 5 * 1024 ** 2})
        self.assertEqual(Context.top_memory(fake), ["app-db (pg) 5 MB", "app.web.1 2 MB"])
        self.assertEqual(Context.top_memory(types.SimpleNamespace(mem_by_container={})), [])

    def test_who_uses_the_memory_one_per_line_in_the_alert(self):
        m = Metric("host.memory", "Server", "Memory", "1", "CRIT", effective="CRIT")
        embed = build_alert("t", [Change(m, "new", "OK", "CRIT")], [], [m], ["a 1 MB", "b 2 MB"], UTC)
        field = next(f for f in embed["fields"] if f["name"] == "Who uses the memory")
        self.assertEqual(field["value"], "- a 1 MB\n- b 2 MB")

    def test_who_uses_the_memory_one_per_line_and_outside_the_last_24h_in_the_summary(self):
        m = Metric("host.memory", "Server", "Memory", "1")
        other = Metric("app.x.ps", "x", "Processes", "1")
        r = ReportContext(metrics=[m, other], history=[], now=T0, tz=UTC, server_section="Server",
                          maintenance_section="Maintenance")
        embed = build_summary("t", r, {}, ["a 1 MB", "b 2 MB"], [Host()])
        self.assertNotIn("Who uses", next(f["value"] for f in embed["fields"] if f["name"] == "x"))
        value = next(f["value"] for f in embed["fields"] if f["name"] == "Server")
        self.assertIn("Who uses the memory:\n- a 1 MB\n- b 2 MB", value)
        block = value.split("**Last 24h**")[1].split("Who uses the memory")[0]
        self.assertIn("no history yet", block)
        self.assertNotIn("a 1 MB", block)

    def test_problem_line_comes_first_and_open_now_lists_which(self):
        ok = Metric("a", "Server", "Disk", "40%", effective="OK")
        bad = Metric("b", "Server", "Load", "3.0", "WARN", effective="WARN")
        embed = build_alert("test", [Change(bad, "new", "OK", "WARN")], [], [ok, bad], [], UTC)
        self.assertEqual(embed["title"], "🟡 Warning · Server · Load")
        self.assertEqual(embed["fields"][0]["value"].splitlines(), ["🟡 **Load**: 3.0", "🟢 Disk: 40%"])
        self.assertEqual(embed["fields"][-1], {"name": "Open now", "value": "🟡 Server · Load"})

    def test_several_changes_get_a_count_in_the_title(self):
        a = Metric("a", "Server", "Disk", "90%", "CRIT", effective="CRIT")
        b = Metric("b", "Server", "Load", "3.0", "WARN", effective="WARN")
        embed = build_alert("test", [Change(a, "new", "OK", "CRIT"), Change(b, "new", "OK", "WARN")], [], [a, b], [], UTC)
        self.assertEqual(embed["title"], "🔴 Critical · 2 changes")
        self.assertEqual(embed["_type"], "alert")
        self.assertEqual(embed["_alert_level"], "CRIT")

    def test_multi_line_block_gets_a_blank_line_around(self):
        self.assertEqual(join_blocks(["a", "List:\n- x", "b"]), "a\n\nList:\n- x\n\nb")
        self.assertEqual(join_blocks(["List:\n- x", "b"]), "List:\n- x\n\nb")
        self.assertEqual(join_blocks(["a", "b"]), "a\nb")

    def test_sentry_text_has_no_formatting(self):
        self.assertEqual(md("a*b`c"), "a\\*b\\`c")
        self.assertEqual(plain("🔴 **Memory**: " + md("3*") + " *(note)*"), "Memory: 3* (note)")
        self.assertEqual(plain("since <t:1793469600:R>"), "since Oct 31 18:00 UTC")


if __name__ == "__main__":
    unittest.main()
