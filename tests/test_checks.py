"""The checks of the smaller modules: inodes, systemd, clock, ports, TLS, backup,
Postgres maintenance and the Dokku upload traps."""

from __future__ import annotations

import json
import os
import unittest

from helpers import APP_TOML, DAY, HOUR, T0, SandboxCase

BASE_SQL = "cmd/psql_my-app-db_base.txt"


class HostChecksTest(SandboxCase, unittest.TestCase):
    def test_inodes_warning_and_critical(self):
        self.sb.host(inodes=[1_000_000, 150_000])  # 85% used
        self.assertEqual(self.sb.run(T0), 1)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · Server · Inodes"])
        self.assertIn("85% used (150,000 free)", self.sb.description())
        self.sb.host(inodes=[1_000_000, 50_000])  # 95% used
        self.assertEqual(self.sb.run(T0 + 300), 2)
        self.assertEqual(self.sb.titles()[-1], "🔴 Critical · Server · Inodes")

    def test_filesystem_without_inode_limit_has_no_metric(self):
        self.sb.host(inodes=[0, 0])
        self.sb.run(T0, "--dry-run")
        self.assertNotIn("Inodes", self.sb.out)

    def test_failed_systemd_units_warn_after_10_min_except_the_ignored(self):
        self.sb.env["SERVER_HEALTH_SYSTEMD_IGNORE"] = "certbot.service"
        self.sb.write("cmd/systemctl_failed.txt",
                      "certbot.service        loaded failed failed Certbot\n"
                      "nightly-backup.service loaded failed failed Nightly backup\n")
        self.sb.run(T0)
        self.assertEqual(self.sb.discord(), [])
        self.sb.run(T0 + 600)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · Server · Failed services"])
        text = self.sb.description()
        self.assertIn("nightly-backup.service", text)
        self.assertNotIn("certbot", text)
        self.assertIn("systemctl status nightly-backup.service", text)

    def test_failed_unit_with_a_bullet(self):
        # Some systemd versions print the bullet even with --plain.
        self.sb.write("cmd/systemctl_failed.txt", "● nightly-backup.service loaded failed failed Nightly backup\n")
        self.sb.run(T0, "--dry-run")
        self.assertIn("Failed services: nightly-backup.service", self.sb.out)

    def test_only_ignored_units_failing_is_fine(self):
        self.sb.env["SERVER_HEALTH_SYSTEMD_IGNORE"] = "certbot.service, other.service"
        self.sb.write("cmd/systemctl_failed.txt", "certbot.service loaded failed failed Certbot\n")
        self.sb.run(T0, "--dry-run")
        self.assertIn("Failed services: none", self.sb.out)

    def test_clock_not_synchronized_warns_after_30_min(self):
        self.sb.write("cmd/timedatectl_sync.txt", "no\n")
        self.sb.run(T0)
        self.sb.run(T0 + 900)
        self.assertEqual(self.sb.discord(), [])
        self.sb.run(T0 + 1800)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · Server · Clock"])
        self.assertIn("NOT synchronized", self.sb.description())

    def test_synchronized_clock(self):
        self.sb.run(T0, "--dry-run")
        self.assertIn("Clock: synchronized", self.sb.out)

    def test_first_round_learns_the_ports_and_a_new_one_is_an_event(self):
        self.sb.run(T0)
        self.assertEqual(self.sb.discord(), [])
        self.assertEqual(sorted(self.sb.state()["ports"]), ["*:22", "*:443", "*:80"])
        self.sb.append("cmd/ss_listen.txt", "LISTEN 0 244 0.0.0.0:5432 0.0.0.0:*\n"
                                            "LISTEN 0 244 127.0.0.1:6379 0.0.0.0:*\n")
        self.assertEqual(self.sb.run(T0 + 300), 1)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · Server · New port open"])
        self.assertIn("*:5432", self.sb.description())
        self.assertNotIn("6379", self.sb.description())  # loopback is not exposed
        self.sb.run(T0 + 600)
        self.assertEqual(len(self.sb.discord()), 1)

    def test_backup_age(self):
        stamp = self.sb.fx / "fs/var/backups/last-ok"
        stamp.parent.mkdir(parents=True)
        stamp.write_text("")
        os.utime(stamp, (T0 - 30 * HOUR, T0 - 30 * HOUR))
        self.sb.env["SERVER_HEALTH_BACKUP_STAMP"] = "/var/backups/last-ok"
        self.assertEqual(self.sb.run(T0), 1)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · Server · Backup"])
        self.assertIn("last one 30h ago", self.sb.description())

    def test_backup_never_recorded(self):
        self.sb.env["SERVER_HEALTH_BACKUP_STAMP"] = "/var/backups/last-ok"
        self.sb.run(T0, "--dry-run")
        self.assertIn("Backup: never recorded", self.sb.out)

    def backups(self, sizes: list[int]):
        """One file per day, the last of the list being the newest."""
        folder = self.sb.fx / "fs/var/backups/db"
        folder.mkdir(parents=True, exist_ok=True)
        for i, size in enumerate(sizes):
            path = folder / f"my-app-db-{i:02d}.dump"
            path.write_bytes(b"x" * size)
            ts = T0 - (len(sizes) - i) * DAY
            os.utime(path, (ts, ts))
        self.sb.env["SERVER_HEALTH_BACKUP_GLOB"] = "/var/backups/db/*.dump"

    def test_backup_much_smaller_than_usual_warns(self):
        self.backups([10_000, 10_200, 9_900, 10_100, 2_000])
        self.assertEqual(self.sb.run(T0), 1)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · Server · Backup size"])
        self.assertIn("newest 2 KB · usual 10 KB (20%)", self.sb.description())

    def test_backup_of_the_usual_size_is_fine(self):
        self.backups([10_000, 10_200, 9_900, 10_100, 9_800])
        self.sb.run(T0, "--dry-run")
        self.assertIn("Backup size: newest 10 KB · usual 10 KB (97%)", self.sb.out)
        self.assertIn("Nothing would be alerted now.", self.sb.out)

    def test_backup_glob_without_files_warns(self):
        self.sb.env["SERVER_HEALTH_BACKUP_GLOB"] = "/var/backups/db/*.dump"
        self.sb.run(T0, "--dry-run")
        self.assertIn("Backup size: no file found", self.sb.out)


class TlsTest(SandboxCase, unittest.TestCase):
    def test_far_expiry_is_fine(self):
        self.sb.run(T0, "--dry-run")
        self.assertIn("TLS certificate: my-app.example expires in 60 days (Dec 30)", self.sb.out)

    def test_expiry_warning_and_critical(self):
        self.sb.host(certs={"my-app.example": T0 + 10 * DAY})
        self.assertEqual(self.sb.run(T0), 1)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · my-app · TLS certificate"])
        self.assertIn("my-app.example expires in 10 days", self.sb.description())
        self.sb.host(certs={"my-app.example": T0 + 5 * DAY})
        self.sb.run(T0 + 7 * HOUR)
        self.assertEqual(self.sb.titles()[-1], "🔴 Critical · my-app · TLS certificate")

    def test_result_is_cached_for_tls_check_h(self):
        self.sb.run(T0)
        self.assertEqual(self.sb.state()["tls"]["my-app.example"], [T0, T0 + 60 * DAY])
        self.sb.host(certs={"my-app.example": T0 + 2 * DAY})
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.discord(), [])  # still the cached date
        self.sb.run(T0 + 6 * HOUR)
        self.assertEqual(self.sb.titles(), ["🔴 Critical · my-app · TLS certificate"])

    def test_extra_domains_go_to_the_server_section(self):
        self.sb.env["SERVER_HEALTH_TLS_DOMAINS"] = "status.my-app.example"
        self.sb.host(certs={"my-app.example": T0 + 60 * DAY, "status.my-app.example": T0 + 3 * DAY})
        self.assertEqual(self.sb.run(T0), 2)
        self.assertEqual(self.sb.titles(), ["🔴 Critical · Server · TLS certificate"])

    def test_domain_without_certificate_has_no_metric(self):
        self.sb.host(certs={})
        self.sb.run(T0, "--dry-run")
        self.assertNotIn("TLS certificate", self.sb.out)


class PostgresChecksTest(SandboxCase, unittest.TestCase):
    def test_vacuum_falling_behind_warns(self):
        self.sb.replace(BASE_SQL, "vacuum_stale|0", "vacuum_stale|3")
        self.assertEqual(self.sb.run(T0), 1)
        self.assertEqual(self.sb.titles(), ["🟡 Warning · my-app-db · Vacuum falling behind"])
        self.assertIn("3 table(s)", self.sb.description())

    def test_cache_hit_only_in_the_summary(self):
        self.sb.run(T0, "--dry-run")
        self.assertNotIn("Cache hit ratio", self.sb.out)
        self.sb.run(T0, "--force")
        self.assertIn("Cache hit ratio: 99.9%", json.dumps(self.sb.discord(), ensure_ascii=False))

    def test_database_without_answer_is_critical(self):
        self.sb.write(BASE_SQL, "")
        self.sb.write("cmd/psql_my-app-db_base.rc", "2\n")
        self.sb.run(T0)  # a warning at once, critical only after 5 min in a row
        self.assertEqual(self.sb.titles(), ["🟡 Warning · my-app-db · Database"])
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.titles()[-1], "🔴 Critical · my-app-db · Database")
        self.assertIn("Postgres did not answer", self.sb.description(-1))


class UploadTrapsTest(SandboxCase, unittest.TestCase):
    """The two Dokku traps that break nothing on deploy, checked in the daily summary."""

    def settings(self, text: str):
        self.sb.replace(APP_TOML, 'app = "my-app"', f'app = "my-app"\n{text}')

    def summary(self) -> str:
        return json.dumps([p["embeds"] for p in self.sb.discord()], ensure_ascii=False)

    def test_storage_mount_missing(self):
        self.settings("storage_mount = true")
        self.sb.write("cmd/dokku_storage_my-app.txt", "=====> my-app volume bind-mounts\n")
        self.sb.run(T0, "--force")
        text = self.summary()
        self.assertIn("**Storage mount**: NONE", text)
        self.assertIn("Needs attention", text)

    def test_storage_mount_present(self):
        self.settings("storage_mount = true")
        self.sb.write("cmd/dokku_storage_my-app.txt",
                      "=====> my-app volume bind-mounts\n     /var/lib/dokku/data/storage/my-app:/rails/storage\n")
        self.sb.run(T0, "--force")
        self.assertIn("Storage mount: /var/lib/dokku/data/storage/my-app:/rails/storage", self.summary())

    def test_storage_mount_not_checked_in_a_normal_round(self):
        self.settings("storage_mount = true")
        self.sb.run(T0, "--dry-run")
        self.assertNotIn("Storage mount", self.sb.out)

    def test_body_size_below_what_the_app_needs(self):
        self.settings('max_body_size = "50m"')
        self.sb.write("cmd/dokku_nginx_body_my-app.txt", "1m\n")
        self.sb.write("cmd/dokku_thruster_body_my-app.txt", "10485760\n")
        self.sb.run(T0, "--force")
        self.assertIn("**Upload size limit**: nginx 1m; Thruster MAX_REQUEST_BODY 10 MB (the app needs 50m)",
                      self.summary())

    def test_empty_nginx_setting_is_its_1m_default(self):
        self.settings('max_body_size = "50m"')
        self.sb.write("cmd/dokku_nginx_body_my-app.txt", "\n")
        self.sb.run(T0, "--force")
        self.assertIn("nginx 1m (the app needs 50m)", self.summary())

    def test_body_size_enough(self):
        self.settings('max_body_size = "50m"')
        self.sb.write("cmd/dokku_nginx_body_my-app.txt", "100m\n")
        self.sb.write("cmd/dokku_thruster_body_my-app.txt", "104857600\n")
        self.sb.run(T0, "--force")
        self.assertIn("Upload size limit: nginx and Thruster accept 50m", self.summary())


if __name__ == "__main__":
    unittest.main()
