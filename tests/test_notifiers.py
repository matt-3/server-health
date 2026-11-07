"""The notifiers: Discord (split, mention, fallback), the generic webhook and Sentry."""

from __future__ import annotations

import unittest
from unittest import mock
from zoneinfo import ZoneInfo

import helpers
from helpers import APP_HOOK_KEY, APP_URL, SERVER_URL, T0, SandboxCase, low_memory
from server_health import http
from server_health.metric import Change, Metric
from server_health.notify.discord import Discord, embed_size, split_embed
from server_health.report import build_alert

WEBHOOK_URL = "https://hooks.example/server-health"
SENTRY_DSN = "https://abc123@sentry.example/42"
SENTRY_STORE = "https://sentry.example/api/42/store/"


class DiscordTest(unittest.TestCase):
    def setUp(self):
        helpers.use_lang("en")

    def test_big_message_becomes_several_without_losing_a_section(self):
        metrics = [Metric(f"k{i}", f"sec{i}", "label", "x" * 900, "CRIT", effective="CRIT") for i in range(20)]
        changes = [Change(m, "new", "OK", "CRIT") for m in metrics]
        embed = build_alert("test", changes, [], metrics, [], ZoneInfo("UTC"))
        parts = split_embed(embed)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(embed_size(part), 6000)
            self.assertLessEqual(len(part["fields"]), 25)
        names = [f["name"] for part in parts for f in part["fields"]]
        self.assertEqual(names, [f["name"] for f in embed["fields"]])
        self.assertTrue(parts[1]["title"].endswith("(continued)"), parts[1]["title"])

    def test_big_summary_goes_in_parts_with_the_mention_only_in_the_first(self):
        notifier = Discord({"SERVER_HEALTH_DISCORD_WEBHOOK": SERVER_URL, "SERVER_HEALTH_DISCORD_MENTION": "@here"},
                           "test", lambda m: None)
        sent = []
        fields = [{"name": f"sec{i}", "value": "y" * 1000} for i in range(12)]
        with mock.patch.object(http, "post_json", lambda url, payload, headers=None: sent.append(payload) or 204):
            self.assertTrue(notifier.send({"title": "t", "description": "", "color": 1, "fields": fields},
                                          mention=True))
        self.assertGreater(len(sent), 1)
        self.assertEqual(sent[0].get("content"), "@here")
        self.assertTrue(all("content" not in p for p in sent[1:]))
        self.assertEqual(sent[1]["allowed_mentions"], {"parse": []})

    def test_app_channel_down_sends_through_the_server_channel(self):
        logs = []
        notifier = Discord({"SERVER_HEALTH_DISCORD_WEBHOOK": SERVER_URL, APP_HOOK_KEY: APP_URL}, "test", logs.append)
        sent = []

        def post(url, payload, headers=None):
            sent.append(url)
            return 404 if url == APP_URL else 204

        with mock.patch.object(http, "post_json", post):
            self.assertTrue(notifier.send({"title": "t", "description": "", "color": 1, "fields": []}, route="my-app"))
        self.assertEqual(sent, [APP_URL, SERVER_URL])
        self.assertTrue(any("the channel of my-app failed" in line for line in logs), logs)

    def test_server_channel_down_does_not_try_twice(self):
        notifier = Discord({"SERVER_HEALTH_DISCORD_WEBHOOK": SERVER_URL}, "test", lambda m: None)
        sent = []
        with mock.patch.object(http, "post_json", lambda url, payload, headers=None: sent.append(url) or 500):
            self.assertFalse(notifier.send({"title": "t", "description": "", "color": 1, "fields": []}))
        self.assertEqual(sent, [SERVER_URL])

    def test_attachment_goes_with_the_last_part(self):
        notifier = Discord({"SERVER_HEALTH_DISCORD_WEBHOOK": SERVER_URL}, "test", lambda m: None)
        calls = []
        fields = [{"name": f"sec{i}", "value": "y" * 1000} for i in range(12)]
        with mock.patch.object(http, "post_json", lambda url, payload, headers=None: calls.append("json") or 204), \
                mock.patch.object(http, "post_multipart", lambda *a: calls.append("file") or 200):
            notifier.send({"title": "t", "description": "", "color": 1, "fields": fields},
                          attachment=("x.html", b"<p>", "text/html"))
        self.assertGreater(len(calls), 1)
        self.assertEqual(calls[-1], "file")
        self.assertEqual(calls.count("file"), 1)


class WebhookAndSentryTest(SandboxCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.sb.env["SERVER_HEALTH_WEBHOOK_URL"] = WEBHOOK_URL
        self.sb.env["SERVER_HEALTH_SENTRY_DSN"] = SENTRY_DSN

    def to(self, url: str) -> list[dict]:
        return [s for s in self.sb.sent if s["url"] == url]

    def test_alert_goes_to_the_webhook_as_json(self):
        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.sb.run(T0)
        [hook] = self.to(WEBHOOK_URL)
        body = hook["payload"]
        self.assertEqual(set(body), {"server", "type", "level", "route", "title", "text"})
        self.assertEqual((body["server"], body["type"], body["level"], body["route"]), ("test", "alert", "CRIT", None))
        self.assertEqual(body["title"], "Critical · Server · Memory")
        self.assertIn("== Open now", body["text"])
        self.assertNotIn("**", body["text"])
        self.assertIn("Webhook: sent: Critical · Server · Memory", self.sb.log())

    def test_webhook_route_names_the_app_with_its_own_channel(self):
        self.sb.env[APP_HOOK_KEY] = APP_URL
        self.sb.replace(helpers.APP_SQL, "ind:orders_stuck|0", "ind:orders_stuck|2")
        self.sb.run(T0)
        self.assertEqual([h["payload"]["route"] for h in self.to(WEBHOOK_URL)], ["my-app"])

    def test_sentry_gets_only_new_alerts(self):
        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.sb.run(T0)
        [event] = self.to(SENTRY_STORE)
        self.assertEqual(event["payload"]["level"], "fatal")
        self.assertEqual(event["payload"]["tags"]["severity"], "CRIT")
        self.assertEqual(event["payload"]["message"], "Critical · Server · Memory")
        self.assertIn("sentry_key=abc123", event["headers"]["X-Sentry-Auth"])

        self.sb.write("fs/proc/meminfo", low_memory(780000))
        self.sb.run(T0 + 600)  # back to normal: Discord and the webhook, not Sentry
        self.assertEqual(len(self.to(SENTRY_STORE)), 1)
        self.assertEqual([h["payload"]["type"] for h in self.to(WEBHOOK_URL)], ["alert", "alert"])
        self.assertEqual(len(self.sb.discord()), 2)

    def test_daily_summary_goes_to_the_webhook_and_not_to_sentry(self):
        self.sb.run(T0)
        self.sb.run_report(T0 + 300)
        self.assertEqual([h["payload"]["type"] for h in self.to(WEBHOOK_URL)], ["summary"])
        self.assertIn("Paid orders (24h): 3", self.to(WEBHOOK_URL)[0]["payload"]["text"])
        self.assertEqual(self.to(SENTRY_STORE), [])

    def test_reminder_goes_to_the_webhook_and_not_to_sentry(self):
        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.sb.run(T0)
        self.sb.run(T0 + 2 * helpers.HOUR)
        self.assertEqual([h["payload"]["type"] for h in self.to(WEBHOOK_URL)], ["alert", "reminder"])
        self.assertEqual(len(self.to(SENTRY_STORE)), 1)

    def test_webhook_alone_counts_as_a_channel(self):
        del self.sb.env["SERVER_HEALTH_DISCORD_WEBHOOK"]
        self.assertEqual(self.sb.run(T0, "--force"), 0)
        self.assertEqual([h["payload"]["type"] for h in self.to(WEBHOOK_URL)], ["test"])
        self.assertEqual(self.sb.discord(), [])
        self.assertIn("Sent.", self.sb.out)

    def test_sentry_alone_is_not_a_channel(self):
        del self.sb.env["SERVER_HEALTH_DISCORD_WEBHOOK"]
        del self.sb.env["SERVER_HEALTH_WEBHOOK_URL"]
        self.assertEqual(self.sb.run(T0, "--force"), 3)
        self.assertIn("Not sent", self.sb.out)

    def test_invalid_webhook_url_is_a_problem(self):
        self.sb.env["SERVER_HEALTH_WEBHOOK_URL"] = "paste-here"
        self.sb.run(T0, "--dry-run")
        self.assertIn("SERVER_HEALTH_WEBHOOK_URL does not look like a URL", self.sb.out)
        self.assertNotIn("Webhook=ok", self.sb.out)

    def test_dry_run_lists_the_webhook(self):
        self.sb.run(T0, "--dry-run")
        self.assertIn("Channels: Discord=ok Sentry=ok Ping=absent Webhook=ok", self.sb.out)
        self.assertEqual(self.sb.sent, [])


if __name__ == "__main__":
    unittest.main()
