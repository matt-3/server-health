"""The TOML of an app: validation, the refused version-1 keys, and indicator levels."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import helpers
from server_health.appconfig import Indicator, format_value, indicator_level, load_app_config


class AppConfigTest(unittest.TestCase):
    def setUp(self):
        helpers.use_lang("en")

    def write(self, text: str) -> str:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
        tmp.write(text)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_example_and_fixture_tomls_are_valid(self):
        files = sorted((helpers.ROOT / "examples").glob("*.toml")) + sorted((helpers.FIXTURE / "apps.d").glob("*.toml"))
        self.assertTrue(files, "no TOML in examples/ or in the fixtures")
        for f in files:
            conf, errors = load_app_config(str(f))
            self.assertEqual(errors, [], f"{f.name}: {errors}")
            self.assertTrue(conf.indicators, f.name)

    def test_errors_drop_only_the_indicator(self):
        conf, errors = load_app_config(self.write('''
app = "x"
[[indicator]]
key = "good"
sql = "SELECT 1"
warn_at = 1
[[indicator]]
key = "two"
sql = "SELECT 1; DELETE FROM users"
[[indicator]]
key = "write"
sql = "UPDATE users SET name = 'x'"
[[indicator]]
key = "summary_with_threshold"
sql = "SELECT 1"
when = "summary"
warn_at = 1
[[indicator]]
key = "good"
sql = "SELECT 2"
[[indicator]]
key = "odd_key"
sql = "SELECT 1"
limit = 3
'''))
        self.assertEqual([i.key for i in conf.indicators], ["good", "odd_key"])
        joined = " ".join(errors)
        for frag in ("a single query", "SELECT or WITH", "summary indicator never alerts", "repeated key",
                     "unknown key `limit`"):
            self.assertIn(frag, joined)

    def test_toml_without_app(self):
        conf, errors = load_app_config(self.write('database = "x"'))
        self.assertIsNone(conf)
        self.assertIn("missing `app`", errors[0])

    def test_invalid_toml(self):
        conf, errors = load_app_config(self.write('app = "x"\n[[indicator]\n'))
        self.assertIsNone(conf)
        self.assertIn("invalid TOML", errors[0])

    def test_indicator_level(self):
        ind = Indicator("k", "k", "SELECT 1", warn_at=1, critical_at=3)
        self.assertEqual([indicator_level(ind, v) for v in (0, 1, 3)], ["OK", "WARN", "CRIT"])
        low = Indicator("k", "k", "SELECT 1", warn_at=0, direction="below")
        self.assertEqual([indicator_level(low, v) for v in (0, 5)], ["WARN", "OK"])

    def test_all_fields_are_read(self):
        conf, errors = load_app_config(self.write('''
app = "my-app"
name = "My App"
database = "my-app-db"
health_path = "/healthz"
jobs_dashboard = "/admin/jobs"
[[indicator]]
key = "revenue_24h"
label = "Revenue (24h)"
sql = "WITH t AS (SELECT 1 AS v) SELECT sum(v) FROM t"
when = "summary"
format = "cents"
[[indicator]]
key = "signups"
label = "Signups in the last hour"
sql = "SELECT count(*) FROM users"
warn_at = 0
direction = "below"
persist_min = 30
active_hours = "09-23"
explanation = "nobody signed up: check the `signup` form"
'''))
        self.assertEqual(errors, [])
        self.assertEqual((conf.app, conf.name, conf.database, conf.health_path, conf.jobs_dashboard),
                         ("my-app", "My App", "my-app-db", "/healthz", "/admin/jobs"))
        revenue, signups = conf.indicators
        self.assertEqual((revenue.when, revenue.format), ("summary", "cents"))
        self.assertEqual((signups.direction, signups.persist_min, signups.active_hours),
                         ("below", 30, "09-23"))
        self.assertIn("`signup`", signups.explanation)

    def test_bad_values_are_named(self):
        conf, errors = load_app_config(self.write('''
app = "x"
[[indicator]]
key = "a"
sql = "SELECT 1"
warn_at = "three"
[[indicator]]
key = "b"
sql = "SELECT 1"
direction = "up"
[[indicator]]
key = "c"
sql = "SELECT 1"
persist_min = -1
[[indicator]]
key = "d"
sql = "SELECT 1"
active_hours = "morning"
[[indicator]]
key = "Bad-Key"
sql = "SELECT 1"
'''))
        self.assertEqual(conf.indicators, [])
        joined = " ".join(errors)
        for frag in ("x.a: `warn_at` must be a number", "x.b: `direction` is one of 'above', 'below'",
                     "x.c: `persist_min` must be an integer >= 0", "x.d: `active_hours` like '09-23'",
                     "x, indicator 5: `key` is required"):
            self.assertIn(frag, joined)

    def test_format_value(self):
        self.assertEqual(format_value("number", 3), "3")
        self.assertEqual(format_value("number", 2.5), "2.50")
        self.assertEqual(format_value("cents", 123456), "1,234.56")
        self.assertEqual(format_value("bytes", 5 * 1024 ** 2), "5 MB")
        self.assertEqual(format_value("duration", 3600), "1h")


class UnknownKeysTest(unittest.TestCase):
    """An unknown key or value is named, and only its indicator is dropped."""

    def setUp(self):
        helpers.use_lang("en")

    def write(self, text: str) -> str:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
        tmp.write(text)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_unknown_keys_are_named(self):
        conf, errors = load_app_config(self.write('''
app = "x"
nome = "X"
[[indicador]]
key = "a"
sql = "SELECT 1"
[[indicator]]
key = "b"
rotulo = "B"
sql = "SELECT 1"
'''))
        self.assertIn("x: unknown key `nome`", errors)
        self.assertIn("x: unknown key `indicador`", errors)
        self.assertIn("x, indicator 1: unknown key `rotulo`", errors)
        self.assertEqual([i.key for i in conf.indicators], ["b"])

    def test_values_out_of_the_list_drop_the_indicator(self):
        conf, errors = load_app_config(self.write('''
app = "x"
[[indicator]]
key = "a"
sql = "SELECT 1"
direction = "abaixo"
[[indicator]]
key = "b"
sql = "SELECT 1"
when = "resumo"
[[indicator]]
key = "c"
sql = "SELECT 1"
format = "duracao"
'''))
        self.assertEqual(conf.indicators, [])
        self.assertIn("x.a: `direction` is one of 'above', 'below'", errors)
        self.assertIn("x.b: `when` is one of 'always', 'summary'", errors)
        self.assertIn("x.c: `format` is one of 'number', 'bytes', 'duration', 'cents'", errors)


class UploadSettingsTest(unittest.TestCase):
    def setUp(self):
        helpers.use_lang("en")

    def write(self, text: str) -> str:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
        tmp.write(text)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_storage_mount_must_be_a_bool(self):
        conf, errors = load_app_config(self.write('app = "x"\nstorage_mount = true\n'))
        self.assertEqual(errors, [])
        self.assertTrue(conf.storage_mount)
        conf, errors = load_app_config(self.write('app = "x"\nstorage_mount = "yes"\n'))
        self.assertFalse(conf.storage_mount)
        self.assertEqual(errors, ["x: `storage_mount` must be true or false"])

    def test_max_body_size(self):
        for raw, expected in (('"50m"', 50 * 1024 ** 2), ('"512k"', 512 * 1024), ("1048576", 1048576)):
            conf, errors = load_app_config(self.write(f'app = "x"\nmax_body_size = {raw}\n'))
            self.assertEqual(errors, [], raw)
            self.assertEqual(conf.max_body_size, expected, raw)
        self.assertEqual(conf.max_body_size_text, "1048576")
        conf, errors = load_app_config(self.write('app = "x"\nmax_body_size = "huge"\n'))
        self.assertIsNone(conf.max_body_size)
        self.assertIn("x: `max_body_size` like '50m'", errors[0])


if __name__ == "__main__":
    unittest.main()
