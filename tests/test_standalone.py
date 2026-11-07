"""Apps outside Dokku: declared only in their TOML, with their own health URL, access
log and database. The server modules work the same without Dokku or Docker."""

from __future__ import annotations

import types
import unittest

from helpers import T0, SandboxCase, nginx_lines

from server_health.appconfig import load_app_config
from server_health.modules.postgres import connection

SHOP_TOML = '''
app = "shop"
name = "Shop"
health_url = "https://shop.example/up"
nginx_log = "/var/log/shop/access.log"
database_url = "env:SERVER_HEALTH_DB_SHOP"

[[indicator]]
key = "orders_stuck"
label = "Orders stuck"
sql = "SELECT 1"
critical_at = 1
'''

BASE_SQL = ("@@SH@@|size|52428800\n@@SH@@|conn|3\n@@SH@@|maxconn|100\n@@SH@@|long_tx|0\n"
            "@@SH@@|vacuum_stale|0\n@@SH@@|cache_hit|99.5\n@@SH@@|has_sq|false\n@@SH@@|has_rt|false\n")


class StandaloneAppTest(SandboxCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        sb = self.sb
        # A plain VPS: no docker, no dokku.
        sb.host(commands=["journalctl", "systemctl", "ss", "timedatectl", "apt-get"],
                certs={"shop.example": T0 + 90 * 86400})
        (sb.fx / "apps.d" / "my-app.toml").unlink()
        sb.write("apps.d/shop.toml", SHOP_TOML)
        sb.env["SERVER_HEALTH_DB_SHOP"] = "postgres://shop:s3cret@db.internal:6432/shop_prod?sslmode=require"
        sb.write("cmd/curl_shop_url.txt", "200 0.120")
        sb.write("cmd/psql_shop-db_base.txt", BASE_SQL)
        sb.write("cmd/psql_shop-db_app.txt", "@@SH@@|ind:orders_stuck|0\n")
        sb.write("fs/var/log/shop/access.log", nginx_lines("Mozilla/5.0", 3))

    def test_the_app_is_monitored_without_dokku(self):
        sb = self.sb
        sb.run(T0)
        sb.append("fs/var/log/shop/access.log", nginx_lines("Mozilla/5.0", 4) + nginx_lines("GPTBot/1.2", 2))
        self.assertEqual(sb.run(T0 + 300, "--dry-run"), 0)
        out = sb.out
        modules = out.splitlines()[1]
        self.assertNotIn("dokku", modules)
        self.assertNotIn("docker", modules)
        self.assertIn("[Shop (shop)]", out)
        self.assertIn("Health: 200 in 120 ms (shop.example/up)", out)
        self.assertIn("Requests: people 4 in 5 min · bots 2 (AI training 2)", out)
        self.assertIn("[shop-db]", out)
        self.assertIn("Database: 50 MB", out)
        self.assertIn("[Shop (shop) · product]", out)
        self.assertIn("Orders stuck: 0", out)
        self.assertIn("TLS certificate: shop.example expires in 90 days", out)
        self.assertNotIn("Monitor config", out)

    def test_the_password_goes_in_the_environment_never_in_the_command(self):
        sb = self.sb
        sb.run(T0, "--dry-run")
        # The FixtureSource keeps the environment of each command.
        from server_health import source
        captured = {}
        original = source.FixtureSource.run

        def spy(self, key, argv, timeout=30, stdin=None, env=None):
            captured[key] = (argv, env)
            return original(self, key, argv, timeout, stdin, env)

        source.FixtureSource.run = spy
        try:
            sb.run(T0 + 300, "--dry-run")
        finally:
            source.FixtureSource.run = original
        argv, env = captured["psql_shop-db_base"]
        self.assertEqual(argv, ["psql", "-X", "-q"])
        self.assertEqual(env["PGPASSWORD"], "s3cret")
        self.assertEqual((env["PGHOST"], env["PGPORT"], env["PGDATABASE"], env["PGUSER"], env["PGSSLMODE"]),
                         ("db.internal", "6432", "shop_prod", "shop", "require"))
        self.assertNotIn("s3cret", " ".join(argv))
        self.assertNotIn("s3cret", sb.log())

    def test_env_variable_missing_skips_the_database(self):
        del self.sb.env["SERVER_HEALTH_DB_SHOP"]
        self.sb.run(T0, "--dry-run")
        self.assertNotIn("[shop-db]", self.sb.out)
        self.assertIn("Health: 200", self.sb.out)

    def test_check_app_runs_the_queries_without_dokku(self):
        self.sb.write("cmd/psql_shop-db_check_orders_stuck.txt", "@@SH@@|ind:orders_stuck|2\n")
        rc = self.sb.run(T0, "--check-app", str(self.sb.fx / "apps.d" / "shop.toml"))
        self.assertEqual(rc, 0)
        self.assertIn("Database: shop-db", self.sb.out)
        self.assertIn("orders_stuck: 2 [CRITICAL]", self.sb.out)


class StandaloneConfigTest(unittest.TestCase):
    def write(self, text: str) -> str:
        import tempfile
        from pathlib import Path
        tmp = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
        tmp.write(text)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_password_in_the_toml_is_refused(self):
        conf, errors = load_app_config(self.write('app = "x"\ndatabase_url = "postgres://u:pw@h/db"\n'))
        self.assertEqual(conf.database_url, "")
        self.assertTrue(any("readable by every user" in e for e in errors), errors)

    def test_url_without_password_is_accepted(self):
        conf, errors = load_app_config(self.write('app = "x"\ndatabase_url = "postgres://u@h/db"\n'))
        self.assertEqual(errors, [])
        self.assertTrue(conf.standalone)

    def test_bad_values(self):
        _, errors = load_app_config(self.write('app = "x"\nhealth_url = "shop.example/up"\nnginx_log = "logs/a.log"\n'
                                               'database_url = "mysql://h/db"\n'))
        self.assertEqual(len(errors), 3, errors)

    def test_container_connection(self):
        ctx = types.SimpleNamespace(db_conn={"a": ("container", "pg", ""), "b": ("container", "pg", "postgres://app@x/appdb")})
        self.assertEqual(connection(ctx, "a"), (["docker", "exec", "-i", "pg", "psql", "-X", "-q", "-U", "postgres"], None))
        self.assertEqual(connection(ctx, "b"), (["docker", "exec", "-i", "pg", "psql", "-X", "-q", "-U", "app", "-d", "appdb"],
                                                None))

    def test_dokku_connection_is_the_default(self):
        ctx = types.SimpleNamespace(db_conn={})
        self.assertEqual(connection(ctx, "svc"), (["dokku", "postgres:connect", "svc"], None))


if __name__ == "__main__":
    unittest.main()
