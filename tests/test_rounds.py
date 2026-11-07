"""Whole rounds against the fixture: what is alerted, when, and what is kept on disk."""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone

from helpers import APP_LOG, APP_SQL, APP_TOML, HOUR, T0, SandboxCase, low_memory, nginx_lines


class RoundTest(SandboxCase, unittest.TestCase):
    def test_healthy_server_sends_nothing(self):
        self.assertEqual(self.sb.run(T0), 0)
        self.assertEqual(self.sb.run(T0 + 300), 0)
        self.assertEqual(self.sb.discord(), [])
        self.assertIn("swap", self.sb.state()["counters"])
        self.assertEqual(len(self.sb.history()), 2)

    def test_critical_memory_alerts_with_mention_and_who_uses_it(self):
        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.assertEqual(self.sb.run(T0), 2)
        payload = self.sb.discord()[0]
        embed = payload["embeds"][0]
        self.assertTrue(embed["title"].startswith("🔴 Critical · Server · Memory"), embed["title"])
        self.assertEqual(embed["author"]["name"], "test")
        self.assertEqual(payload.get("content"), "@here")
        # The log keeps the kind apart: counting the night's warnings filters by it.
        self.assertIn("Discord: sent (CRITICAL): Critical · Server · Memory", self.sb.log())
        names = [f["name"] for f in embed["fields"]]
        self.assertIn("Who uses the memory", names)
        self.assertNotIn("must-not-leak", json.dumps(payload))  # the container env never leaves
        # Same situation 5 min later: nothing new.
        self.sb.run(T0 + 300)
        self.assertEqual(len(self.sb.discord()), 1)
        # Back to normal: a message without a mention.
        self.sb.write("fs/proc/meminfo", low_memory(780000))
        self.sb.run(T0 + 600)
        self.assertTrue(self.sb.titles()[-1].startswith("✅ Back to normal"), self.sb.titles()[-1])
        self.assertNotIn("content", self.sb.discord()[-1])

    def test_high_ram_with_headroom_does_not_alert(self):
        # 80% in use, 20% available, nobody waiting for memory: normal with two apps.
        self.sb.run(T0)
        self.sb.run(T0 + 20 * 60)
        self.assertEqual(self.sb.discord(), [])

    def test_stopped_worker_waits_for_confirmation(self):
        self.sb.replace(APP_SQL, "@@SH@@|sq_hb:Worker|20\n", "")
        self.sb.run(T0)
        self.assertEqual(self.sb.discord(), [])
        self.sb.run(T0 + 300)
        self.assertEqual(len(self.sb.discord()), 1)
        self.assertIn("Solid Queue", self.sb.titles()[0])
        self.assertIn("NO worker", json.dumps(self.sb.discord()[0], ensure_ascii=False))

    def test_app_indicator_alerts(self):
        self.sb.replace(APP_SQL, "ind:orders_stuck|0", "ind:orders_stuck|2")
        self.assertEqual(self.sb.run(T0), 2)
        self.assertIn("my-app · product · Orders stuck in processing", self.sb.titles()[0])

    def test_failing_indicator_becomes_a_warning_after_10_min(self):
        self.sb.replace(APP_SQL, "@@SH@@|ind:orders_stuck|0\n", "")
        self.sb.run(T0)
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.discord(), [])
        self.sb.run(T0 + 600)
        self.assertIn("the query failed", json.dumps(self.sb.discord()[0], ensure_ascii=False))

    def test_toml_name_becomes_the_section_and_the_key_keeps_the_app(self):
        self.sb.replace(APP_TOML, 'app = "my-app"', 'app = "my-app"\nname = "My App"')
        self.sb.replace(APP_SQL, "ind:orders_stuck|0", "ind:orders_stuck|2")
        self.sb.run(T0)
        self.assertIn("My App (my-app) · product · Orders stuck in processing", self.sb.titles()[0])
        self.assertIn("ind.my-app.orders_stuck", self.sb.state()["metrics"])

    def test_opening_and_closing_go_to_the_alert_log(self):
        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.sb.run(T0)
        self.sb.write("fs/proc/meminfo", low_memory(780000))
        self.sb.run(T0 + 600)
        rows = self.sb.alerts()
        self.assertEqual([(r["key"], r["kind"], r["after"]) for r in rows],
                         [("host.memory", "new", "CRIT"), ("host.memory", "normal", "OK")])
        self.assertEqual(rows[1]["duration"], 600)

    def test_quiet_hours_hold_the_warning(self):
        self.sb.env["SERVER_HEALTH_QUIET_HOURS"] = "14-16"  # T0 is 15:00 in São Paulo
        self.sb.write("fs/proc/loadavg", "2.5 2.5 2.5 1/1 1\n")
        for i in range(0, 4):
            self.sb.run(T0 + i * 300)
        self.assertEqual(self.sb.discord(), [])
        self.assertEqual(len(self.sb.state()["quiet_queue"]), 1)
        self.sb.run(T0 + HOUR + 300)  # 16:05: end of the quiet hours
        self.assertEqual(len(self.sb.discord()), 1)
        self.assertIn("During the quiet hours", self.sb.description())

    def test_critical_goes_through_the_quiet_hours(self):
        self.sb.env["SERVER_HEALTH_QUIET_HOURS"] = "14-16"
        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.sb.run(T0)
        self.assertEqual(len(self.sb.discord()), 1)

    def test_simulation_sends_a_test_without_touching_the_state(self):
        self.assertEqual(self.sb.run(T0, "--simulate", "worker"), 0)
        self.assertIn("TEST", self.sb.titles()[0])
        self.assertIn("Sent.", self.sb.out)
        self.assertFalse((self.sb.state_dir / "state.json").exists())

    def test_dry_run_neither_sends_nor_writes(self):
        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.assertEqual(self.sb.run(T0, "--dry-run"), 2)
        self.assertEqual(self.sb.discord(), [])
        self.assertFalse((self.sb.state_dir / "state.json").exists())
        self.assertIn("Would be alerted now:", self.sb.out)
        self.assertIn("CRITICAL · Server · Memory", self.sb.out)

    def test_who_uses_the_memory_one_per_line_in_the_dry_run(self):
        self.sb.run(T0, "--dry-run")
        lines = self.sb.out.split("Who uses the memory:\n", 1)[1].splitlines()
        self.assertTrue(lines[0].startswith("  "), lines[0])
        self.assertNotIn(" · ", lines[0])

    def test_ping_url_is_called_every_round(self):
        self.sb.env["SERVER_HEALTH_PING_URL"] = "https://ping.example/abc"
        self.sb.run(T0)
        self.assertEqual([s["url"] for s in self.sb.sent if s["kind"] == "get"], ["https://ping.example/abc"])


class TrafficTest(SandboxCase, unittest.TestCase):
    def test_5xx_in_nginx(self):
        self.sb.run(T0)  # the first round only marks the end of the log
        line = '1.1.1.1 - - [31/Oct/2026:18:01:00 +0000] "GET / HTTP/1.1" 502 0 "-" "x"\n'
        self.sb.append(APP_LOG, line * 60)
        self.assertEqual(self.sb.run(T0 + 300), 2)
        self.assertIn("Requests", self.sb.titles()[0])
        self.assertEqual(self.sb.history()[-1]["req"]["my-app"], [60, 60])

    def test_people_and_bots_counted_apart_without_the_monitor(self):
        self.sb.run(T0)
        self.sb.append(APP_LOG,
                       nginx_lines("Mozilla/5.0 Safari", 30, "/en/orders/AbC123xYz")
                       + nginx_lines("Mozilla/5.0 (compatible; GPTBot/1.2)", 20, "/en/orders/AbC123xYz")
                       + nginx_lines("Mozilla/5.0", 5, "/.env", status=404)
                       + nginx_lines("server-health/2.0.0", 3, "/up", ip="127.0.0.1"))
        self.sb.run(T0 + 300)
        row = self.sb.history()[-1]
        self.assertEqual(row["req"]["my-app"], [55, 0])
        self.assertEqual(row["ppl"]["my-app"], [30, 0])
        self.assertEqual(row["bots"]["my-app"], {"ai_training": 20, "scan": 5})
        self.assertEqual(row["up"]["my-app"], [200, 85])
        bots_file = self.sb.state_dir / "bots.json"
        day = json.loads(bots_file.read_text())["days"]["2026-10-31"]["my-app"]
        self.assertEqual(day["names"], {"ai_training|GPTBot": 20, "scan|/.env": 5})
        # Only bot pages, and without the identifier: people's paths are never kept.
        self.assertEqual(day["pages"], {"ai_training|/en/orders/:id": 20, "scan|/.env": 5})
        self.assertNotIn("AbC123xYz", bots_file.read_text())

    def test_bot_in_volume_warns_and_a_search_engine_does_not(self):
        self.sb.run(T0)
        self.sb.append(APP_LOG, nginx_lines("Googlebot/2.1", 500))
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.discord(), [])
        self.sb.append(APP_LOG, nginx_lines("GPTBot/1.2", 400))
        self.assertEqual(self.sb.run(T0 + 600), 1)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · my-app · Bot in volume"])
        self.assertIn("GPTBot", self.sb.description())

    def history_of_people(self, days: int, recent_zero_rounds: int):
        self.sb.write_history([{"ts": T0 - i * 300, "ppl": {"my-app": [0 if i <= recent_zero_rounds else 20, 0]},
                                "req": {"my-app": [20, 0]}} for i in range(days * 288, 0, -1)])
        self.sb.write_state({"nginx": {"my-app": [0, 0]}, "last_run": T0 - 300})

    def test_traffic_drop_warns_after_10_min(self):
        self.history_of_people(15, recent_zero_rounds=6)
        self.sb.run(T0)
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.discord(), [])
        self.sb.run(T0 + 600)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · my-app · People in 30 min"])
        self.assertIn("usual at this hour: 120", self.sb.description())

    def test_traffic_drop_waits_for_14_days_of_history(self):
        self.history_of_people(10, recent_zero_rounds=6)
        for k in range(3):
            self.sb.run(T0 + k * 300)
        self.assertEqual(self.sb.discord(), [])


class JobsTest(SandboxCase, unittest.TestCase):
    def test_new_job_error_alerts_once_and_learns_what_was_there(self):
        base = self.sb.read(APP_SQL)
        self.sb.write(APP_SQL, base + f"@@SH@@|sq_fail_kind:CleanupJob#Timeout|2 {T0 - 600}\n")
        self.sb.run(T0)
        self.assertEqual(self.sb.discord(), [])  # what was already in the failed list does not alert
        self.sb.write(APP_SQL, base + f"@@SH@@|sq_fail_kind:CleanupJob#Timeout|3 {T0}\n"
                      f"@@SH@@|sq_fail_kind:Orders::SyncJob#Net::ReadTimeout|1 {T0 + 200}\n")
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · my-app · New error in a job"])
        self.assertIn("Orders::SyncJob: Net::ReadTimeout", self.sb.description())
        self.assertNotIn("CleanupJob", self.sb.description())
        self.sb.run(T0 + 600)
        self.assertEqual(len(self.sb.discord()), 1)

    def test_error_comes_back_after_7_clean_days(self):
        base = self.sb.read(APP_SQL)
        self.sb.run(T0)
        state = self.sb.state()
        state["job_errors"]["my-app|CleanupJob#Timeout"] = T0 - 8 * 86400
        self.sb.write_state(state)
        self.sb.write(APP_SQL, base + f"@@SH@@|sq_fail_kind:CleanupJob#Timeout|1 {T0 + 100}\n")
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · my-app · New error in a job"])

    def test_jobs_dashboard_from_the_toml_goes_in_the_alert(self):
        self.sb.replace(APP_TOML, 'app = "my-app"', 'app = "my-app"\njobs_dashboard = "/admin/jobs"')
        base = self.sb.read(APP_SQL)
        self.sb.run(T0)
        self.sb.write(APP_SQL, base + f"@@SH@@|sq_fail_kind:Orders::SyncJob#KeyError|1 {T0 + 200}\n")
        self.sb.run(T0 + 300)
        self.assertIn("see /admin/jobs", self.sb.description())

    def test_daily_task_stopped_alerts_even_without_the_execution_in_the_database(self):
        self.sb.run(T0)  # the execution 50,000 s ago is seen and kept
        # Solid Queue deletes the execution with the finished job: the monitor goes by the one it kept.
        self.sb.replace(APP_SQL, "@@SH@@|sq_rec:expire_trials|50000 -1 1\n", "")
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.discord(), [])
        self.sb.run(T0 + 13 * HOUR)  # 26h53 since the last one: past 24h + 2h
        self.assertEqual(self.sb.titles(), ["🟡 Warning · my-app · Daily and weekly tasks"])
        self.assertIn("expire_trials (daily", self.sb.description())


class DeployTest(SandboxCase, unittest.TestCase):
    def deploy_at(self, ts_iso: str):
        inspect = json.loads(self.sb.read("cmd/docker_inspect.txt"))
        for c in inspect[:2]:
            c["Created"] = ts_iso
            c["Config"]["Env"] = ["GIT_REV=d28260b3aaaa"]
        self.sb.write("cmd/docker_inspect.txt", json.dumps(inspect))

    def history_around_deploy(self, deploy_ts: int, err_after: int, jobs_after: int):
        self.sb.write_history([{"ts": T0 - i * 300,
                                "req": {"my-app": [100, err_after if T0 - i * 300 > deploy_ts else 0]},
                                "sqf": {"my-app": jobs_after if T0 - i * 300 > deploy_ts else 0},
                                "up": {"my-app": [200, 20]}} for i in range(24, 0, -1)])

    def test_suspicious_deploy_warns_once(self):
        self.deploy_at("2026-10-31T17:29:00Z")  # 31 min before T0
        self.history_around_deploy(T0 - 31 * 60, err_after=30, jobs_after=6)
        self.sb.run(T0)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · my-app · Suspicious deploy"])
        text = self.sb.description()
        self.assertIn("d28260b (Oct 31 14:29)", text)
        self.assertIn("5xx 0.0% → 30.0%", text)
        self.assertIn("job failures in 30 min 0 → 6", text)
        self.sb.run(T0 + 300)
        self.assertEqual(len(self.sb.discord()), 1)

    def test_deploy_without_regression_does_not_warn(self):
        self.deploy_at("2026-10-31T17:29:00Z")
        self.history_around_deploy(T0 - 31 * 60, err_after=1, jobs_after=1)
        self.sb.run(T0)
        self.assertEqual(self.sb.discord(), [])
        self.assertEqual(self.sb.state()["deploy_checked"]["my-app"], f"d28260b@{T0 - 31 * 60}")

    def test_running_deploy_holds_the_load(self):
        self.sb.write("fs/proc/loadavg", "5.0 5.0 5.0 1/1 1\n")
        self.sb.write("cmd/ps_args.txt", "/bin/bash /usr/bin/dokku git-hook my-app\n")
        for i in range(0, 5):
            self.sb.run(T0 + i * 300)
        self.assertEqual(self.sb.discord(), [])

    def test_container_restart_is_an_event(self):
        self.sb.run(T0)
        data = json.loads(self.sb.read("cmd/docker_inspect.txt"))
        data[1]["RestartCount"] = 2
        self.sb.write("cmd/docker_inspect.txt", json.dumps(data))
        self.assertEqual(self.sb.run(T0 + 300), 1)
        self.assertIn("Restart", self.sb.titles()[0])
        self.sb.run(T0 + 600)
        self.assertEqual(len(self.sb.discord()), 1)

    def swap_out(self, pages: int):
        self.sb.write("fs/proc/vmstat", f"nr_free_pages 37500\npswpin 120000\npswpout {pages}\n")

    def test_swap_out_outside_a_deploy_alerts(self):
        self.sb.run(T0)
        self.swap_out(250000 + 93750)  # 1,250 KB/s over 5 min
        self.assertEqual(self.sb.run(T0 + 300), 2)
        self.assertEqual(self.sb.titles(), ["🔴 Critical · Server · Swap"])

    def test_deploy_that_ended_between_rounds_does_not_alert_swap(self):
        # Regression: the build pages, ends at 13:12, and the 13:15 round no longer sees a
        # build process, but its rate covers since 13:10.
        self.sb.run(T0)
        created = datetime.fromtimestamp(T0 + 150, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        self.deploy_at(created)
        self.swap_out(250000 + 93750)
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.discord(), [])

    def test_swap_out_after_the_deploy_alerts_again(self):
        # The previous round saw the build: the next one still measures its final part.
        # Only one: paging that goes on is no longer the deploy's.
        self.sb.write("cmd/ps_args.txt", "/bin/bash /usr/bin/dokku git-hook my-app\n")
        self.sb.run(T0)
        self.sb.write("cmd/ps_args.txt", "/sbin/init\n")
        self.swap_out(250000 + 93750)
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.discord(), [])
        self.swap_out(250000 + 2 * 93750)
        self.sb.run(T0 + 600)
        self.assertEqual(self.sb.titles(), ["🔴 Critical · Server · Swap"])

    def test_open_swap_alert_survives_a_deploy_between_rounds(self):
        # Without this, the same problem said "back to normal" and a second @here the round after.
        self.sb.run(T0)
        self.swap_out(250000 + 93750)
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.titles(), ["🔴 Critical · Server · Swap"])
        self.sb.sent.clear()

        created = datetime.fromtimestamp(T0 + 450, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        self.deploy_at(created)
        self.swap_out(250000 + 2 * 93750)
        self.sb.run(T0 + 600)
        self.assertEqual(self.sb.discord(), [])
        self.swap_out(250000 + 3 * 93750)
        self.sb.run(T0 + 900)
        self.assertEqual(self.sb.discord(), [])


class SecurityTest(SandboxCase, unittest.TestCase):
    def test_ssh_from_a_new_range_warns_and_a_known_one_does_not(self):
        self.sb.write("cmd/journal_ssh_seed.txt",
                      "Oct 20 sshd[1]: Accepted publickey for deploy from 45.224.27.130 port 5 ssh2\n")
        self.sb.run(T0)
        self.assertEqual(self.sb.discord(), [])  # the first round only learns
        self.sb.write("cmd/journal_ssh_round.txt",
                      "Oct 31 sshd[1]: Accepted publickey for deploy from 45.224.27.200 port 5 ssh2\n"
                      "Oct 31 sshd[2]: Accepted password for root from 203.0.113.9 port 7 ssh2\n")
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · Server · SSH login from a new IP"])
        text = self.sb.description()
        self.assertIn("root from 203.0.113.9 (password)", text)
        self.assertNotIn("45.224.27.200", text)  # same /24 as the one that already logged in
        self.sb.run(T0 + 600)
        self.assertEqual(len(self.sb.discord()), 1)

    def test_maintenance_pending_for_more_than_7_days(self):
        self.sb.run(T0)
        state = self.sb.state()
        self.assertEqual(state["maintenance"]["security"], 3)
        state["maintenance"]["security_since"] = T0 - 8 * 86400
        self.sb.write_state(state)
        reboot = self.sb.fx / "fs/var/run/reboot-required"
        reboot.parent.mkdir(parents=True)
        reboot.write_text("")
        os.utime(reboot, (T0 - 9 * 86400, T0 - 9 * 86400))
        self.sb.run(T0 + 300)
        text = self.sb.description()
        self.assertIn("Security updates**: 3 pending for 8d", text)
        self.assertIn("Pending reboot**: for 9d", text)


if __name__ == "__main__":
    unittest.main()
