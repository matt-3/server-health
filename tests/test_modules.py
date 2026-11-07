"""Which modules run (SERVER_HEALTH_MODULES) and third-party modules."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import helpers
from helpers import T0, SandboxCase
from server_health.module import Module, builtin_modules, load_plugins, resolve


class Fake(Module):
    def __init__(self, name: str, present: bool = True, requires: tuple = ()):
        self.name = name
        self.present = present
        self.requires = requires

    def detect(self, src, cfg):
        return self.present


def names(modules) -> list[str]:
    return [m.name for m in modules]


class ResolveTest(unittest.TestCase):
    def setUp(self):
        helpers.use_lang("en")
        self.available = [Fake("host"), Fake("docker", present=False), Fake("dokku", requires=("docker",)),
                          Fake("ports"), Fake("nginx", requires=("dokku",))]

    def test_auto_follows_detect(self):
        # dokku is present but needs docker, which is not: it stays off silently, and so
        # does nginx after it.
        chosen, problems = resolve("auto", self.available, None, {})
        self.assertEqual(names(chosen), ["host", "ports"])
        self.assertEqual(problems, [])

    def test_explicit_list_runs_only_those(self):
        chosen, problems = resolve("ports,docker,dokku", self.available, None, {})
        self.assertEqual(names(chosen), ["docker", "dokku", "ports"])  # registry order, not the list's
        self.assertEqual(problems, [])

    def test_auto_minus_one_excludes_it(self):
        self.available[1].present = True
        chosen, problems = resolve("auto,-ports", self.available, None, {})
        self.assertEqual(names(chosen), ["host", "docker", "dokku", "nginx"])
        self.assertEqual(problems, [])

    def test_unknown_name_is_a_problem(self):
        chosen, problems = resolve("auto,nope,-gone", self.available, None, {})
        self.assertEqual(names(chosen), ["host", "ports"])
        self.assertEqual(len(problems), 2)
        self.assertIn("unknown module `gone`", problems[0])
        self.assertIn("unknown module `nope`", problems[1])
        self.assertIn("known: host, docker, dokku, ports, nginx", problems[1])

    def test_listed_module_without_its_requirement_is_off_with_a_problem(self):
        chosen, problems = resolve("host,nginx", self.available, None, {})
        self.assertEqual(names(chosen), ["host"])
        self.assertEqual(problems, ["module `nginx` needs `dokku`, which is off: `nginx` does not run"])

    def test_empty_spec_means_auto(self):
        self.assertEqual(names(resolve("", self.available, None, {})[0]), ["host", "ports"])

    def test_builtin_names_are_unique_and_requirements_come_first(self):
        builtins = builtin_modules()
        seen = []
        for m in builtins:
            self.assertNotIn(m.name, seen)
            for r in m.requires:
                self.assertIn(r, seen, f"{m.name} needs {r}, which runs after it")
            seen.append(m.name)


class ModulesInARoundTest(SandboxCase, unittest.TestCase):
    def test_auto_on_the_fixture(self):
        self.sb.run(T0, "--dry-run")
        self.assertIn("Modules: host, ssh, maintenance, updates, systemd, clock, ports, docker, dokku, http, nginx, "
                      "postgres, rails, indicators, tls", self.sb.out)

    def test_host_alone_runs_only_the_host_metrics(self):
        self.sb.env["SERVER_HEALTH_MODULES"] = "host"
        self.assertEqual(self.sb.run(T0), 0)
        self.sb.run(T0 + 300, "--dry-run")
        self.assertIn("Modules: host\n", self.sb.out)
        self.assertIn("[Server]", self.sb.out)
        self.assertNotIn("my-app", self.sb.out)
        self.assertNotIn("[Maintenance]", self.sb.out)
        self.assertNotIn("Who uses the memory", self.sb.out)  # that comes from docker
        state = self.sb.state()
        self.assertEqual(sorted(k.split(".")[0] for k in state["metrics"]), ["host"] * len(state["metrics"]))
        self.assertNotIn("ports", state)
        row = self.sb.history()[0]
        self.assertIn("mem_pct", row)
        self.assertEqual(row["req"], {})

    def test_excluding_a_module(self):
        self.sb.env["SERVER_HEALTH_MODULES"] = "auto,-ports,-tls"
        self.sb.run(T0)
        state = self.sb.state()
        self.assertNotIn("ports", state)
        self.assertNotIn("tls", state)
        self.assertIn("app.my-app.ps", state["metrics"])

    def test_unknown_module_shows_in_the_dry_run(self):
        self.sb.env["SERVER_HEALTH_MODULES"] = "auto,redis"
        self.sb.run(T0, "--dry-run")
        self.assertIn("CONFIG WARNING: unknown module `redis` in SERVER_HEALTH_MODULES", self.sb.out)

    def test_module_listed_without_its_requirement(self):
        self.sb.env["SERVER_HEALTH_MODULES"] = "host,rails"
        self.sb.run(T0, "--dry-run")
        self.assertIn("Modules: host\n", self.sb.out)
        self.assertIn("module `rails` needs `postgres`, which is off", self.sb.out)

    def test_backup_turns_on_when_configured(self):
        self.sb.run(T0, "--dry-run")
        self.assertNotIn("backup", self.sb.out.splitlines()[1])
        self.sb.env["SERVER_HEALTH_BACKUP_STAMP"] = "/var/backups/last-ok"
        self.sb.run(T0, "--dry-run")
        self.assertIn("host, backup, ssh", self.sb.out.splitlines()[1])


PLUGIN = '''
from server_health.i18n import t
from server_health.metric import Metric
from server_health.module import Module


class Probe(Module):
    name = "probe"
    defaults = {"SERVER_HEALTH_PROBE_TARGET": "the moon"}
    thresholds = {"PROBE_LIMIT": 3}
    strings = {
        "en": {"probe.label": "Probe check", "probe.value": "{target} answered ({limit})"},
        "pt-BR": {"probe.label": "Sonda", "probe.value": "{target} respondeu ({limit})"},
    }

    def host(self, ctx):
        ctx.add(Metric("host.probe", ctx.server_section, t("probe.label"),
                       t("probe.value", target=ctx.cfg["SERVER_HEALTH_PROBE_TARGET"], limit=ctx.th["PROBE_LIMIT"])))


MODULE = Probe()
'''


class PluginTest(SandboxCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.dir = Path(self.sb.env["SERVER_HEALTH_MODULES_DIR"])
        self.dir.mkdir()

    def plugin(self, name: str, text: str, mode: int = 0o644) -> Path:
        path = self.dir / f"{name}.py"
        path.write_text(text)
        os.chmod(path, mode)
        return path

    def test_loads_only_when_named(self):
        self.plugin("probe", PLUGIN)
        self.sb.run(T0, "--dry-run")
        self.assertNotIn("Probe check", self.sb.out)
        self.sb.env["SERVER_HEALTH_MODULES"] = "auto,probe"
        self.sb.run(T0, "--dry-run")
        self.assertIn("Probe check: the moon answered (3)", self.sb.out)
        self.assertIn(", tls, probe\n", self.sb.out)
        self.assertNotIn("not loaded", self.sb.out)
        self.assertNotIn("unknown module", self.sb.out)

    def test_plugin_settings_thresholds_and_language(self):
        self.plugin("probe", PLUGIN)
        self.sb.env.update({"SERVER_HEALTH_MODULES": "host,probe", "SERVER_HEALTH_PROBE_TARGET": "the sun",
                            "SERVER_HEALTH_T_PROBE_LIMIT": "7", "SERVER_HEALTH_LANG": "pt-BR"})
        self.sb.run(T0, "--dry-run")
        self.assertIn("Sonda: the sun respondeu (7)", self.sb.out)

    def test_plugin_metric_goes_through_a_normal_round(self):
        self.plugin("probe", PLUGIN)
        self.sb.env["SERVER_HEALTH_MODULES"] = "host,probe"
        self.sb.run(T0)
        self.assertIn("host.probe", self.sb.state()["metrics"])

    def test_writable_file_is_refused(self):
        self.plugin("probe", PLUGIN, mode=0o666)
        self.sb.env["SERVER_HEALTH_MODULES"] = "auto,probe"
        self.assertEqual(self.sb.run(T0, "--dry-run"), 0)
        self.assertNotIn("Probe check", self.sb.out)
        self.assertIn("probe.py not loaded: group or others can write the file", self.sb.out)

    def test_group_writable_file_is_refused(self):
        path = self.plugin("probe", PLUGIN, mode=0o664)
        found, problems = load_plugins(str(self.dir), {"probe"})
        self.assertEqual(found, [])
        self.assertEqual(problems, [f"{path} not loaded: group or others can write the file"])

    def test_file_that_raises_is_a_problem_not_a_crash(self):
        self.plugin("broken", "raise RuntimeError('boom')\n")
        self.sb.env["SERVER_HEALTH_MODULES"] = "auto,broken"
        self.assertEqual(self.sb.run(T0), 0)
        self.assertIn("broken.py failed to load (RuntimeError: boom)", self.sb.log())
        self.assertIn("app.my-app.ps", self.sb.state()["metrics"])  # the rest of the round ran

    def test_file_without_module_is_a_problem(self):
        self.plugin("empty", "X = 1\n")
        found, problems = load_plugins(str(self.dir), {"empty"})
        self.assertEqual(found, [])
        self.assertIn("empty.py defines no MODULE", problems[0])

    def test_modules_list_loads_several(self):
        self.plugin("pair", "from server_health.module import Module\n"
                            "class A(Module):\n    name = 'a'\n"
                            "class B(Module):\n    name = 'b'\n"
                            "MODULES = [A(), B()]\n")
        found, problems = load_plugins(str(self.dir), {"pair"})
        self.assertEqual(names(found), ["a", "b"])
        self.assertEqual(problems, [])

    def test_missing_directory_loads_nothing(self):
        self.assertEqual(load_plugins(str(self.dir / "nope"), {"probe"}), ([], []))


if __name__ == "__main__":
    unittest.main()
