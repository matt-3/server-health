"""The update check, and the data that is trimmed so nothing grows forever."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import DAY, T0, SandboxCase

from server_health import __version__

URL = "https://raw.githubusercontent.com/matt-3/server-health/main/versions.json"


def versions(*items) -> str:
    return json.dumps({"versions": [{"version": v, "level": lvl, "summary": {"en": f"{lvl} notes", "pt-BR": "notas"}}
                                    for v, lvl in items]})


class UpdatesTest(SandboxCase, unittest.TestCase):
    def test_security_fix_is_a_warning(self):
        self.sb.host(urls={URL: versions(("2.0.1", "security"), ("2.0.2", "fix"))})
        self.sb.run(T0)
        state = self.sb.state()
        self.assertEqual(state["metrics"]["mnt.update"]["alerted"], "WARN")
        desc = self.sb.description()
        self.assertIn("2.0.2 available (security fix; installed 1.0.0): security notes", desc)
        self.assertIn("install.sh | sudo bash -s -- v2.0.2", desc)

    def test_features_only_show_in_the_summary(self):
        self.sb.host(urls={URL: versions(("2.1.0", "feature"))})
        self.sb.run(T0)
        self.assertEqual(self.sb.discord(), [])
        self.sb.run_report(T0 + 300)
        maintenance = [f["value"] for f in self.sb.discord()[0]["embeds"][0]["fields"] if f["name"] == "Maintenance"][0]
        self.assertIn("server-health update: 2.1.0 available (new features", maintenance)

    def test_checks_at_most_once_a_day(self):
        self.sb.host(urls={URL: versions(("2.1.0", "feature"))})
        self.sb.run(T0)
        checked = self.sb.state()["updates"]["checked"]
        self.sb.host(urls={URL: versions(("2.0.1", "critical"))})
        self.sb.run(T0 + 3600)
        self.assertEqual(self.sb.state()["updates"]["checked"], checked)
        self.assertNotIn("mnt.update", self.sb.state()["metrics"])  # still the cached feature release
        self.sb.run(T0 + DAY + 60)
        self.assertEqual(self.sb.state()["metrics"]["mnt.update"]["alerted"], "WARN")

    def test_older_or_same_version_says_nothing(self):
        self.sb.host(urls={URL: versions((__version__, "security"), ("0.9.0", "critical"))})
        self.sb.run(T0, "--dry-run")
        self.assertNotIn("server-health update", self.sb.out)

    def test_unreadable_list_is_only_logged(self):
        self.sb.host(urls={URL: "<html>not json</html>"})
        self.sb.run(T0)
        self.assertIn("update check failed", self.sb.log())
        self.assertEqual(self.sb.discord(), [])

    def test_can_be_turned_off(self):
        self.sb.env["SERVER_HEALTH_UPDATE_CHECK"] = "false"
        self.sb.run(T0, "--dry-run")
        self.assertNotIn("updates", self.sb.out.splitlines()[1])


class VersionsFileTest(unittest.TestCase):
    def test_the_repository_list_has_this_version(self):
        from helpers import ROOT
        from server_health.modules.updates import LEVELS
        listed = json.loads((ROOT / "versions.json").read_text())["versions"]
        this = [v for v in listed if v["version"] == __version__]
        self.assertEqual(len(this), 1, "versions.json needs an entry for the version in __init__.py")
        for v in listed:
            self.assertIn(v["level"], LEVELS)
            self.assertTrue(v["summary"]["en"] and v["summary"]["pt-BR"])


class RetentionTest(SandboxCase, unittest.TestCase):
    def test_normal_round_trims_history_and_alerts_once_a_day(self):
        old, recent = T0 - 40 * DAY, T0 - DAY
        self.sb.write_history([{"ts": old, "mem_pct": 1}, {"ts": recent, "mem_pct": 2}])
        self.sb.write_jsonl("alerts.jsonl", [
            {"ts": T0 - 100 * DAY, "key": "host.load", "section": "Server", "label": "Load", "kind": "new",
             "before": "OK", "after": "WARN", "duration": 0},
            {"ts": recent, "key": "host.load", "section": "Server", "label": "Load", "kind": "normal",
             "before": "WARN", "after": "OK", "duration": 60}])
        self.sb.run(T0)
        self.assertEqual([r["ts"] for r in self.sb.history()][:1], [recent])
        self.assertEqual([a["ts"] for a in self.sb.alerts()], [recent])
        self.assertEqual(self.sb.state()["pruned_at"], T0)

        # Within the same day it does not rewrite the files again.
        with open(self.sb.state_dir / "history.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": old, "mem_pct": 9}) + "\n")
        self.sb.run(T0 + 300)
        self.assertIn(old, [r["ts"] for r in self.sb.history()])
        self.sb.run(T0 + DAY + 300)
        self.assertNotIn(old, [r["ts"] for r in self.sb.history()])

    def test_ports_closed_for_30_days_are_forgotten(self):
        self.sb.write_state({"ports": {"*:22": T0 - 300, "*:9999": T0 - 300}, "last_run": T0 - 300})
        self.sb.run(T0)
        ports = self.sb.state()["ports"]
        self.assertIsInstance(ports, dict)
        self.assertIn("*:9999", ports)
        self.sb.run(T0 + 31 * DAY)
        self.assertNotIn("*:9999", self.sb.state()["ports"])

    def test_the_log_has_a_size_cap(self):
        log = Path(self.sb.env["SERVER_HEALTH_LOG"])
        log.write_text("x" * (5 * 1024 * 1024 + 10))
        self.sb.run(T0)
        self.assertTrue(Path(str(log) + ".1").exists())
        self.assertLess(log.stat().st_size, 10_000)


if __name__ == "__main__":
    unittest.main()
