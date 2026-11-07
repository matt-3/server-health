"""One Discord channel for the server and, optionally, one per app."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import helpers
from helpers import APP_HOOK_KEY, APP_SQL, APP_TOML, APP_URL, HOUR, SERVER_URL, T0, SandboxCase, low_memory
from server_health.channels import Channels, app_webhook_key
from server_health.config import load_config


class RoutesTest(unittest.TestCase):
    def setUp(self):
        helpers.use_lang("en")

    def test_route_comes_from_the_metric_key(self):
        channels = Channels({"SERVER_HEALTH_DISCORD_WEBHOOK_SHOP": APP_URL}, ["shop", "other"], {"shop-db": "shop"})
        self.assertEqual(channels.routes(), ["", "shop"])
        self.assertEqual(channels.route("app.shop.ps"), "shop")
        self.assertEqual(channels.route("ind.shop.invoices"), "shop")
        self.assertEqual(channels.route("db.shop-db.size"), "shop")
        self.assertEqual(channels.route("db.shop-db"), "shop")
        for key in ("host.memory", "mnt.reboot_age", "cfg.shop", "app.other.ps", "db.loose-db.size"):
            self.assertEqual(channels.route(key), "", key)

    def test_app_name_with_a_hyphen_becomes_an_underscore_in_the_config(self):
        self.assertEqual(app_webhook_key("my-app"), "SERVER_HEALTH_DISCORD_WEBHOOK_MY_APP")

    def test_invalid_app_webhook_becomes_a_problem_and_leaves_the_config(self):
        cfg, problems = load_config("/nonexistent/server-health", {
            "SERVER_HEALTH_DISCORD_WEBHOOK": SERVER_URL,
            "SERVER_HEALTH_DISCORD_WEBHOOK_SHOP": "paste-here",
            APP_HOOK_KEY: "",
        })
        self.assertNotIn("SERVER_HEALTH_DISCORD_WEBHOOK_SHOP", cfg)
        self.assertNotIn(APP_HOOK_KEY, cfg)
        joined = " ".join(problems)
        self.assertIn("SERVER_HEALTH_DISCORD_WEBHOOK_SHOP", joined)
        self.assertNotIn(APP_HOOK_KEY, joined)

    def test_app_webhook_without_the_server_one_is_a_problem(self):
        _, problems = load_config("/nonexistent/server-health", {"SERVER_HEALTH_DISCORD_WEBHOOK_SHOP": APP_URL})
        self.assertTrue(any("server alerts have no channel" in p for p in problems), problems)

    def test_config_file_is_read(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("# comment\nexport SERVER_HEALTH_LABEL='prod-1'\nSERVER_HEALTH_TZ=\"Europe/Lisbon\"\n"
                     "SERVER_HEALTH_DISCORD_WEBHOOK=paste-here\nSERVER_HEALTH_SENTRY_DSN=nope\n")
        self.addCleanup(Path(fh.name).unlink)
        cfg, problems = load_config(fh.name, {"SERVER_HEALTH_TZ": "UTC"})
        self.assertEqual(cfg["SERVER_HEALTH_LABEL"], "prod-1")
        self.assertEqual(cfg["SERVER_HEALTH_TZ"], "UTC")  # the environment wins over the file
        self.assertEqual(cfg["SERVER_HEALTH_DISCORD_WEBHOOK"], "")
        self.assertEqual(cfg["SERVER_HEALTH_SENTRY_DSN"], "")
        joined = " ".join(problems)
        self.assertIn("SERVER_HEALTH_DISCORD_WEBHOOK does not look like a URL", joined)
        self.assertIn("SERVER_HEALTH_SENTRY_DSN does not look like a DSN", joined)


class ChannelsTest(SandboxCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.sb.env[APP_HOOK_KEY] = APP_URL

    def fields(self, url: str) -> list[str]:
        return [f["name"] for p in self.sb.by_url(url) for e in p["embeds"] for f in e["fields"]]

    def description(self, url: str) -> str:
        return "\n".join(e.get("description", "") for p in self.sb.by_url(url) for e in p["embeds"])

    def write_queue(self, items: list):
        self.sb.write_state({"quiet_queue": items})

    def activate_indicator(self, level: str = "critical_at"):
        self.sb.replace(APP_TOML, "critical_at = 1", f"{level} = 1")
        self.sb.replace(APP_SQL, "ind:orders_stuck|0", "ind:orders_stuck|2")

    def test_without_app_webhook_everything_goes_to_the_server_channel(self):
        del self.sb.env[APP_HOOK_KEY]
        self.sb.run(T0, "--force")
        self.assertEqual({s["url"] for s in self.sb.sent}, {SERVER_URL})
        names = self.fields(SERVER_URL)
        self.assertIn("Server", names)
        self.assertTrue(any("my-app" in n for n in names), names)

    def test_summary_goes_to_each_channel_with_only_its_own(self):
        self.assertEqual(self.sb.run(T0, "--force"), 0)
        server, app = self.fields(SERVER_URL), self.fields(APP_URL)
        self.assertIn("Server", server)
        self.assertNotIn("Server", app)
        self.assertTrue(any("my-app-db" in n for n in app), app)
        self.assertFalse(any("my-app" in n for n in server), server)
        self.assertTrue(any("product" in n for n in app), app)

    def test_toml_error_does_not_take_the_app_numbers_to_the_server_channel(self):
        # "Monitor config" stays in the server channel and uses the section named after the app.
        self.sb.append(APP_TOML, '\n[[indicator]]\nkey = "INVALID"\n')
        self.sb.write_history([{"ts": T0 - 300, "req": {"my-app": [100, 1]}}])
        self.sb.run(T0, "--force")

        def section(url: str) -> str:
            return next(f["value"] for p in self.sb.by_url(url) for e in p["embeds"] for f in e["fields"]
                        if f["name"] == "my-app")

        self.assertIn("Monitor config", section(SERVER_URL))
        self.assertNotIn("Last 24h", section(SERVER_URL))
        self.assertIn("Last 24h", section(APP_URL))

    def test_weekly_report_goes_to_each_channel_with_only_its_own(self):
        self.sb.run(T0)
        self.assertEqual(self.sb.run(T0 + 300, "--force-weekly"), 0)
        server, app = self.fields(SERVER_URL), self.fields(APP_URL)
        self.assertIn("Server", server)
        self.assertNotIn("Server", app)
        self.assertNotIn("Databases", server)
        self.assertIn("Databases", app)

    def test_server_alert_does_not_touch_the_app_channel(self):
        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.sb.run(T0)
        self.assertEqual(self.sb.by_url(APP_URL), [])
        self.assertEqual(self.sb.by_url(SERVER_URL)[0].get("content"), "@here")

    def test_round_with_alerts_on_both_sides_sends_one_message_per_channel(self):
        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.sb.replace(APP_SQL, "ind:orders_stuck|0", "ind:orders_stuck|2")
        self.sb.run(T0)

        server, app = self.sb.by_url(SERVER_URL), self.sb.by_url(APP_URL)
        self.assertEqual((len(server), len(app)), (1, 1))
        self.assertEqual(server[0].get("content"), "@here")
        self.assertEqual(app[0].get("content"), "@here")
        self.assertIn("Memory", server[0]["embeds"][0]["title"])
        self.assertIn("Orders stuck", app[0]["embeds"][0]["title"])

        def open_now(payload: dict) -> str:
            return next(f["value"] for f in payload["embeds"][0]["fields"] if f["name"] == "Open now")

        self.assertNotIn("Orders", open_now(server[0]))
        self.assertNotIn("Memory", open_now(app[0]))
        self.assertFalse(any(f["name"] == "Who uses the memory" for f in app[0]["embeds"][0]["fields"]))

    def test_mention_only_rings_in_the_critical_channel(self):
        self.activate_indicator()
        self.sb.host(disk=[100 * 1024 ** 3, 80 * 1024 ** 3, 20 * 1024 ** 3])  # 80%: warning, not critical
        self.sb.run(T0)
        self.assertEqual(self.sb.by_url(APP_URL)[0].get("content"), "@here")
        server = self.sb.by_url(SERVER_URL)
        self.assertIn("Disk", server[0]["embeds"][0]["title"])
        self.assertNotIn("content", server[0])

    def test_weekly_report_counts_only_the_channel_alerts(self):
        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.sb.run(T0)
        self.sb.sent.clear()
        self.sb.run(T0 + 300, "--force-weekly")

        def week_alerts(url: str) -> str:
            return next(f["value"] for p in self.sb.by_url(url) for f in p["embeds"][0]["fields"]
                        if f["name"] == "Alerts of the week")

        self.assertIn("Memory", week_alerts(SERVER_URL))
        self.assertNotIn("Memory", week_alerts(APP_URL))
        self.assertTrue(week_alerts(APP_URL).startswith("0 alerts"), week_alerts(APP_URL))

    def test_app_warning_held_in_quiet_hours_goes_out_in_its_channel(self):
        self.sb.env["SERVER_HEALTH_QUIET_HOURS"] = "14-16"  # T0 is 15:00 in São Paulo
        self.activate_indicator("warn_at")
        self.sb.run(T0)
        self.assertEqual(self.sb.discord(), [])
        self.assertEqual(self.sb.state()["quiet_queue"][0]["route"], "my-app")
        self.sb.run(T0 + HOUR + 300)  # 16:05: end of the quiet hours
        self.assertIn("Orders stuck in processing", self.description(APP_URL))
        self.assertEqual(self.sb.by_url(SERVER_URL), [])

    def test_simulation_goes_to_the_metric_channel(self):
        self.sb.run(T0, "--simulate", "indicator")
        self.assertEqual(len(self.sb.by_url(APP_URL)), 1)
        self.assertEqual(self.sb.by_url(SERVER_URL), [])
        self.sb.run(T0, "--simulate", "memory")
        self.assertEqual(len(self.sb.by_url(SERVER_URL)), 1)

    def test_quiet_queue_keeps_the_channel(self):
        self.sb.env["SERVER_HEALTH_QUIET_HOURS"] = "14-16"  # T0 is 15:00 in São Paulo
        self.sb.write("fs/proc/loadavg", "2.5 2.5 2.5 1/1 1\n")
        for i in range(0, 4):
            self.sb.run(T0 + i * 300)
        queue = self.sb.state()["quiet_queue"]
        self.assertEqual(queue[0]["route"], "")
        self.assertIn("Load", queue[0]["text"])

    def test_queue_releases_each_warning_in_its_channel(self):
        self.write_queue([{"route": "my-app", "text": "02:00 WARNING from the app"},
                          {"route": "", "text": "02:05 WARNING from the server"}])
        self.sb.run(T0)
        self.assertIn("WARNING from the app", self.description(APP_URL))
        self.assertNotIn("WARNING from the server", self.description(APP_URL))
        self.assertIn("WARNING from the server", self.description(SERVER_URL))
        self.assertEqual(self.sb.state()["quiet_queue"], [])

    def test_kept_warning_of_an_app_without_webhook_goes_to_the_server_channel(self):
        del self.sb.env[APP_HOOK_KEY]
        self.write_queue([{"route": "my-app", "text": "02:00 WARNING of a removed webhook"}])
        self.sb.run(T0)
        self.assertIn("removed webhook", self.description(SERVER_URL))

    def test_wrong_app_name_in_the_config_shows_in_the_dry_run(self):
        self.sb.env["SERVER_HEALTH_DISCORD_WEBHOOK_MY_AP"] = APP_URL
        self.sb.run(T0, "--dry-run")
        self.assertIn("Discord channels: server, my-app", self.sb.out)
        self.assertIn("SERVER_HEALTH_DISCORD_WEBHOOK_MY_AP matches no app", self.sb.out)
        self.assertNotIn(APP_URL, self.sb.out)  # the webhook is a secret: never printed


if __name__ == "__main__":
    unittest.main()
