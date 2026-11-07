"""Languages: a few scenarios in Brazilian Portuguese, number formatting, and every
message existing in both catalogs with the same placeholders."""

from __future__ import annotations

import json
import string
import unittest

import helpers
from helpers import T0, SandboxCase, low_memory, two_weeks_of_state
from server_health import cli, dashboard, i18n  # noqa: F401 - imported for the catalogs they register
from server_health.i18n import fbytes, fdur, fnum
from server_health.module import builtin_modules
from server_health.notify import discord  # noqa: F401 - registers its strings


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class FormattingTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_lang("en")

    def test_brazilian_portuguese_numbers(self):
        i18n.set_lang("pt-BR")
        self.assertEqual(fnum(1234.5, 1), "1.234,5")
        self.assertEqual(fnum(3058), "3.058")
        self.assertEqual(fnum(0.2, 1), "0,2")
        self.assertEqual(fdur(75 * 60), "1h15")
        self.assertEqual(fbytes(1.5 * 1024 ** 3), "1,50 GB")

    def test_language_names(self):
        self.assertEqual(i18n.normalize("pt"), "pt-BR")
        self.assertEqual(i18n.normalize("pt_BR"), "pt-BR")
        self.assertEqual(i18n.normalize("en-US"), "en")
        self.assertEqual(i18n.normalize("fr"), "en")
        self.assertEqual(i18n.normalize(None), "en")

    def test_missing_key_is_shown_as_itself(self):
        self.assertEqual(i18n.t("no.such.key"), "no.such.key")
        i18n.register({"en": {"only.en": "English {x}"}})
        i18n.set_lang("pt-BR")
        self.assertEqual(i18n.t("only.en", x=1), "English 1")  # falls back to English


class CatalogsTest(unittest.TestCase):
    def setUp(self):
        helpers.use_lang("en")

    def tearDown(self):
        i18n.set_lang("en")

    def test_every_module_has_the_same_keys_and_placeholders_in_both_languages(self):
        for m in builtin_modules():
            en, pt = m.strings.get("en", {}), m.strings.get("pt-BR", {})
            self.assertEqual(sorted(set(en) - set(pt)), [], f"{m.name}: missing in pt-BR")
            self.assertEqual(sorted(set(pt) - set(en)), [], f"{m.name}: missing in en")
            for key in en:
                self.assertEqual(placeholders(en[key]), placeholders(pt[key]), f"{m.name}: {key}")

    def test_core_catalogs_have_the_same_keys_and_placeholders(self):
        module_keys = set()
        for m in builtin_modules():
            for items in m.strings.values():
                module_keys |= set(items)
        en = {k: v for k, v in i18n._catalogs["en"].items() if k not in module_keys}
        pt = {k: v for k, v in i18n._catalogs["pt-BR"].items() if k not in module_keys}
        en.pop("only.en", None)  # registered on purpose by the fallback test
        self.assertGreater(len(en), 100)
        self.assertEqual(sorted(set(en) - set(pt)), [], "missing in pt-BR")
        self.assertEqual(sorted(set(pt) - set(en)), [], "missing in en")
        for key in en:
            self.assertEqual(placeholders(en[key]), placeholders(pt[key]), key)

    def test_every_change_kind_has_a_dashboard_name(self):
        for kind in ("new", "worse", "better", "normal", "reminder", "event"):
            self.assertTrue(i18n.has(f"dash.kind.{kind}"), kind)


class PortugueseRoundsTest(SandboxCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.sb.env["SERVER_HEALTH_LANG"] = "pt-BR"

    def tearDown(self):
        i18n.set_lang("en")

    def test_critical_memory_alert(self):
        self.sb.write("fs/proc/meminfo", low_memory(150000))
        self.assertEqual(self.sb.run(T0), 2)
        embed = self.sb.discord()[0]["embeds"][0]
        self.assertTrue(embed["title"].startswith("🔴 Crítico · Servidor · Memória"), embed["title"])
        self.assertIn("Quem usa a memória", [f["name"] for f in embed["fields"]])
        self.assertIn("Abertos agora", [f["name"] for f in embed["fields"]])
        self.assertIn("só 4% disponível (crítico <= 5%)", embed["description"])
        self.assertIn("Discord: sent (CRÍTICO): Crítico · Servidor · Memória", self.sb.log())
        self.sb.write("fs/proc/meminfo", low_memory(780000))
        self.sb.run(T0 + 600)
        self.assertTrue(self.sb.titles()[-1].startswith("✅ Normalizou"), self.sb.titles()[-1])
        self.assertIn("Voltou ao normal depois de 10 min.", self.sb.description(-1))

    def test_dry_run_uses_brazilian_numbers(self):
        self.sb.run(T0, "--dry-run")
        out = self.sb.out
        self.assertIn("=== test · 31/10/2026 15:00", out)
        self.assertIn("Módulos: host, ssh", out)
        self.assertIn("[Servidor]", out)
        self.assertIn("Memória: 3.058/3.819 MB (80%) · disponível 762 MB · espera 0,2%", out)
        self.assertIn("Disco: 40% (24,00 GB livre)", out)
        self.assertIn("Load: 0,35 / 0,28 em 5 min (2 vCPU)", out)
        self.assertIn("[my-app · produto]", out)
        self.assertIn("[Manutenção]", out)
        self.assertIn("Nada seria avisado agora.", out)
        self.assertIn("Canais: Discord=ok Sentry=ausente Ping=ausente", out)

    def test_daily_summary(self):
        self.sb.run(T0)
        self.sb.run_report(T0 + 300)
        embed = self.sb.discord()[0]["embeds"][0]
        self.assertIn("Resumo de 31/10 · tudo dentro dos limiares", embed["title"])
        text = json.dumps(embed, ensure_ascii=False)
        for frag in ("**Últimas 24h**", "RAM: mín", "3 de segurança", "Maiores tabelas", "events 100 MB",
                     "Acerto do cache: 99,9%", "Imagens: 4.2GB (recuperável 2.1GB (50%))",
                     "my-app · produto · números do dia", "Paid orders (24h): 3"):
            self.assertIn(frag, text)

    def test_weekly_report(self):
        two_weeks_of_state(self.sb)
        self.sb.run(T0, "--force-weekly")
        embed = self.sb.discord()[0]["embeds"][0]
        self.assertTrue(embed["title"].startswith("🟢 TESTE · Semana de 24/10 a 31/10 · 1 alerta · 100,00% no ar"),
                        embed["title"])
        fields = {f["name"]: f["value"] for p in self.sb.discord() for f in p["embeds"][0]["fields"]}
        self.assertIn("1 alerta aberto na semana · semana anterior: 0", fields["Alertas da semana"])
        self.assertIn("**Load**: 1 vez, 20 min no total", fields["Alertas da semana"])
        self.assertIn("Disponível 100,00% das rodadas", fields["my-app"])
        self.assertIn("Novos na semana: ChatGPT-User", fields["my-app · robôs"])
        self.assertIn("IA de treino", fields["my-app · robôs"])
        self.assertIn("Paid orders: 21", fields["my-app · números da semana"])
        self.assertIn("my-app-db", fields["Bancos"])

    def test_ssh_login_from_a_new_range(self):
        self.sb.write("cmd/journal_ssh_seed.txt",
                      "Oct 20 sshd[1]: Accepted publickey for deploy from 45.224.27.130 port 5 ssh2\n")
        self.sb.run(T0)
        self.sb.write("cmd/journal_ssh_round.txt",
                      "Oct 31 sshd[2]: Accepted password for root from 203.0.113.9 port 7 ssh2\n")
        self.sb.run(T0 + 300)
        self.assertEqual(self.sb.titles(), ["🟡 Aviso · Servidor · Entrada por SSH de IP novo"])
        self.assertIn("root de 203.0.113.9 (senha)", self.sb.description())

    def test_check_app_hint_in_portuguese(self):
        path = self.sb.tmp / "old.toml"
        path.write_text('app = "my-app"\n[[indicador]]\nchave = "x"\nsql = "SELECT 1"\n')
        self.assertNotEqual(self.sb.run(T0, "--check-app", str(path)), 0)
        self.assertIn("Problemas no TOML:", self.sb.out)
        self.assertIn("my-app: chave desconhecida `indicador`", self.sb.out)


if __name__ == "__main__":
    unittest.main()
