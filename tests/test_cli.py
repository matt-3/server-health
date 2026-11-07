"""The command line modes: mute, dashboard, --list-modules and --check-app."""

from __future__ import annotations

import html.parser
import unittest

from helpers import APP_TOML, HOUR, T0, SandboxCase, low_memory


class MuteTest(SandboxCase, unittest.TestCase):
    def test_mute_stores_the_end_and_a_round_inside_sends_nothing(self):
        self.assertEqual(self.sb.run(T0, "--mute", "2h"), 0)
        self.assertEqual(self.sb.state()["mute_until"], T0 + 2 * HOUR)
        self.assertIn("Alerts muted until Oct 31 17:00", self.sb.out)
        self.assertIn("alerts muted until", self.sb.log())

        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.assertEqual(self.sb.run(T0 + 300), 2)
        self.assertEqual(self.sb.discord(), [])
        state = self.sb.state()
        self.assertEqual(state["metrics"]["host.memory"]["alerted"], "CRIT")  # the state still advances
        self.assertEqual(state["mute_until"], T0 + 2 * HOUR)
        self.assertEqual([a["kind"] for a in self.sb.alerts()], ["new"])
        self.assertIn("muted: 1 change(s) not sent", self.sb.log())

        # When the mute ends, the alert already counted as sent: no late "new" (and the
        # reminder keeps its own clock, 2h after the opening).
        self.sb.run(T0 + 2 * HOUR + 60)
        self.assertEqual(self.sb.discord(), [])
        self.assertNotIn("mute_until", self.sb.state())

    def test_summaries_still_go_out_while_muted(self):
        self.sb.run(T0, "--mute", "1d")
        self.sb.run_report(T0 + 300)
        self.assertTrue(self.sb.discord())
        self.assertIn("Summary of", self.sb.titles()[0])

    def test_unmute_clears_it(self):
        self.sb.run(T0, "--mute", "90m")
        self.assertEqual(self.sb.state()["mute_until"], T0 + 90 * 60)
        self.assertEqual(self.sb.run(T0 + 60, "--unmute"), 0)
        self.assertNotIn("mute_until", self.sb.state())
        self.assertIn("Alerts back on.", self.sb.out)
        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.sb.run(T0 + 300)
        self.assertEqual(len(self.sb.discord()), 1)

    def test_dry_run_shows_the_mute(self):
        self.sb.run(T0, "--mute", "2h")
        self.sb.run(T0 + 300, "--dry-run")
        self.assertIn("(alerts muted until Oct 31 17:00)", self.sb.out)

    def test_bad_duration_returns_3(self):
        self.assertEqual(self.sb.run(T0, "--mute", "soon"), 3)
        self.assertIn("Duration like 90m, 2h or 1d.", self.sb.err)
        self.assertFalse((self.sb.state_dir / "state.json").exists())


class TagBalance(html.parser.HTMLParser):
    VOID = {"meta", "link", "br", "img", "input", "hr"}

    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.texts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closes {self.stack[-1] if self.stack else 'nothing'}")
            return
        self.stack.pop()

    def handle_data(self, data):
        self.texts.append(data)


class DashboardTest(SandboxCase, unittest.TestCase):
    def test_dashboard_file_is_a_page_with_the_charts_and_no_script(self):
        for i in range(4):
            self.sb.run(T0 + i * 300)
        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.sb.run(T0 + 1200)
        target = self.sb.tmp / "dashboard.html"
        self.assertEqual(self.sb.run(T0 + 1500, "--dashboard", str(target)), 0)
        self.assertIn(f"Dashboard written to {target}", self.sb.out)
        page = target.read_text()
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertNotIn("<script", page.lower())
        parser = TagBalance()
        parser.feed(page)
        parser.close()
        self.assertEqual(parser.errors, [])
        self.assertEqual(parser.stack, [])
        text = " ".join(parser.texts)
        for title in ("Server health · test", "Open now", "Last days", "Recent alerts", "Memory", "Load", "Disk",
                      "Swap-out", "Databases", "my-app · requests per hour", "my-app · health check"):
            self.assertIn(title, text)
        self.assertIn("host.memory", text)  # open now
        self.assertIn("opened", text)       # recent alerts
        self.assertEqual(len(self.sb.discord()), 1)  # only the alert: the dashboard sends nothing
        self.assertTrue(self.sb.titles()[0].startswith("🔴"))

    def test_empty_state_still_renders(self):
        target = self.sb.tmp / "empty.html"
        self.assertEqual(self.sb.run(T0, "--dashboard", str(target)), 0)
        page = target.read_text()
        self.assertIn("no data yet", page)
        self.assertIn("Nothing open: everything within the thresholds.", page)
        self.assertIn("No alerts in the period.", page)

    def test_dashboard_in_portuguese(self):
        self.sb.env["SERVER_HEALTH_LANG"] = "pt-BR"
        target = self.sb.tmp / "pt.html"
        self.sb.run(T0, "--dashboard", str(target))
        page = target.read_text()
        self.assertIn('<html lang="pt-BR">', page)
        self.assertIn("Saúde do servidor · test", page)


class ListModulesTest(SandboxCase, unittest.TestCase):
    def test_list_modules(self):
        self.assertEqual(self.sb.run(T0, "--list-modules"), 0)
        lines = self.sb.out.splitlines()
        self.assertEqual(lines[0], "SERVER_HEALTH_MODULES=auto")
        self.assertIn("  [x] host", lines)
        self.assertIn("  [ ] backup", lines)
        self.assertIn("  [x] http", lines)
        self.assertIn("  [x] dokku (needs docker)", lines)
        self.assertIn("  [x] rails (needs postgres)", lines)
        self.assertIn("  [x] updates", lines)
        self.assertEqual(len([x for x in lines if x.startswith("  [")]), 16)

    def test_list_modules_with_a_list(self):
        self.sb.env["SERVER_HEALTH_MODULES"] = "host,docker,-ssh,typo"
        self.sb.run(T0, "--list-modules")
        lines = self.sb.out.splitlines()
        self.assertEqual(lines[0], "SERVER_HEALTH_MODULES=host,docker,-ssh,typo")
        self.assertEqual([x[6:] for x in lines if x.startswith("  [x]")], ["host", "docker"])
        self.assertTrue(any("unknown module `typo`" in x for x in lines), lines)


CHECK_TOML = '''
app = "my-app"
database = "my-app-db"

[[indicator]]
key = "orders_stuck"
label = "Orders stuck in processing"
sql = "SELECT count(*) FROM orders WHERE status = 'processing'"
warn_at = 1
critical_at = 5

[[indicator]]
key = "paid_orders_24h"
label = "Paid orders (24h)"
sql = "SELECT count(*) FROM orders"
when = "summary"
'''


class CheckAppTest(SandboxCase, unittest.TestCase):
    def toml(self, text: str) -> str:
        path = self.sb.tmp / "check.toml"
        path.write_text(text)
        return str(path)

    def answers(self, stuck: str = "2", paid: str = "3"):
        self.sb.write("cmd/psql_my-app-db_check_orders_stuck.txt", f"SET\n@@SH@@|ind:orders_stuck|{stuck}\n")
        self.sb.write("cmd/psql_my-app-db_check_paid_orders_24h.txt", f"@@SH@@|ind:paid_orders_24h|{paid}\n")

    def test_english_toml_runs_each_query(self):
        self.answers()
        self.assertEqual(self.sb.run(T0, "--check-app", self.toml(CHECK_TOML)), 0)
        out = self.sb.out
        self.assertIn("App `my-app`: 2 valid indicator(s)", out)
        self.assertIn("Database: my-app-db", out)
        self.assertRegex(out, r"  ok    orders_stuck: 2 \[WARNING\] in [\d,.]+ ms")
        self.assertRegex(out, r"  ok    paid_orders_24h: 3 \[summary\] in [\d,.]+ ms")
        self.assertNotIn("Problems", out)

    def test_database_found_by_the_dokku_link(self):
        self.answers()
        self.assertEqual(self.sb.run(T0, "--check-app", self.toml(CHECK_TOML.replace('database = "my-app-db"\n', ""))), 0)
        self.assertIn("Database: my-app-db", self.sb.out)

    def test_failing_query_returns_2(self):
        self.answers()
        self.sb.write("cmd/psql_my-app-db_check_orders_stuck.txt", "")
        self.sb.write("cmd/psql_my-app-db_check_orders_stuck.err",
                      'ERROR:  relation "orders" does not exist\nLINE 1: ...\n')
        self.assertEqual(self.sb.run(T0, "--check-app", self.toml(CHECK_TOML)), 2)
        self.assertIn('ERROR  orders_stuck: ERROR:  relation "orders" does not exist / LINE 1: ...', self.sb.out)
        self.assertIn("  ok    paid_orders_24h", self.sb.out)

    def test_portuguese_toml_fails_with_the_hint(self):
        old = '''
app = "my-app"
banco = "my-app-db"

[[indicador]]
chave = "orders_stuck"
rotulo = "Orders stuck in processing"
sql = "SELECT 1"
aviso_em = 1
'''
        rc = self.sb.run(T0, "--check-app", self.toml(old))
        self.assertNotEqual(rc, 0)
        out = self.sb.out
        self.assertIn("Problems in the TOML:", out)
        self.assertIn("  - my-app: unknown key `banco`", out)
        self.assertIn("  - my-app: unknown key `indicador`", out)
        self.assertIn("App `my-app`: 0 valid indicator(s)", out)

    def test_toml_without_app_returns_3(self):
        self.assertEqual(self.sb.run(T0, "--check-app", self.toml('database = "x"\n')), 3)
        self.assertIn("missing `app`", self.sb.out)

    def test_fixture_toml_is_valid(self):
        self.answers()
        self.assertEqual(self.sb.run(T0, "--check-app", str(self.sb.fx / APP_TOML)), 0)
        self.assertIn("orders_stuck: 2 [CRITICAL]", self.sb.out)


if __name__ == "__main__":
    unittest.main()
