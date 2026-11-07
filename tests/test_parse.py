"""Pure parsers and classifiers: no I/O, no round."""

from __future__ import annotations

import unittest

import helpers
from server_health.i18n import fbytes, fdur, fnum
from server_health.modules.nginx import classify_request, normalize_path
from server_health.modules.ports import listening
from server_health.modules.rails import schedule_interval
from server_health.modules.ssh import parse_ssh_logins, ssh_prefix
from server_health.parse import (dokku_names, in_hours, parse_body_size, parse_duration, parse_psi, parse_psql,
                                 parse_size)


class ParsersTest(unittest.TestCase):
    def setUp(self):
        helpers.use_lang("en")

    def test_psql_ignores_noise(self):
        out = "Output format is unaligned.\nSET\n@@SH@@|size|123\nERROR:  boom\n@@SH@@|has_sq|true\n(1 row)\n"
        self.assertEqual(parse_psql(out), [("size", "123"), ("has_sq", "true")])

    def test_value_with_a_pipe_stays_whole(self):
        self.assertEqual(parse_psql("@@SH@@|k|a|b"), [("k", "a|b")])

    def test_dokku_names_without_headers(self):
        self.assertEqual(dokku_names("=====> My Apps\nmy-app\nshop\n"), ["my-app", "shop"])
        self.assertEqual(dokku_names("NAME   VERSION\nshop-db postgres:18\n"), ["shop-db"])

    def test_psi(self):
        psi = parse_psi("some avg10=1.50 avg60=2.00 avg300=3.25 total=1\nfull avg10=0 avg60=0.5 avg300=0 total=2")
        self.assertEqual(psi["some"]["avg300"], 3.25)
        self.assertEqual(psi["full"]["avg60"], 0.5)

    def test_parse_size(self):
        self.assertAlmostEqual(parse_size("1.5GiB"), 1.5 * 1024 ** 3)
        self.assertAlmostEqual(parse_size("20kB"), 20000)
        self.assertEqual(parse_size("garbage"), 0.0)

    def test_hours_cross_midnight(self):
        self.assertTrue(in_hours("23-7", 2))
        self.assertTrue(in_hours("23-7", 23))
        self.assertFalse(in_hours("23-7", 7))
        self.assertTrue(in_hours("09-23", 9))
        self.assertFalse(in_hours("09-23", 23))
        self.assertTrue(in_hours("12-0", 23))
        self.assertFalse(in_hours("12-0", 0))

    def test_english_number_formats(self):
        self.assertEqual(fnum(1234.5, 1), "1,234.5")
        self.assertEqual(fdur(75 * 60), "1h15")
        self.assertEqual(fbytes(1.5 * 1024 ** 3), "1.50 GB")

    def test_body_size(self):
        self.assertEqual(parse_body_size("1m"), 1024 ** 2)
        self.assertEqual(parse_body_size("50M"), 50 * 1024 ** 2)
        self.assertEqual(parse_body_size("512k"), 512 * 1024)
        self.assertEqual(parse_body_size("10485760"), 10485760)
        self.assertEqual(parse_body_size("2g"), 2 * 1024 ** 3)
        self.assertGreater(parse_body_size("0"), 1024 ** 4)  # nginx: 0 is no limit
        self.assertIsNone(parse_body_size("fifty"))
        self.assertIsNone(parse_body_size(""))

    def test_duration(self):
        self.assertEqual(parse_duration("90m"), 90 * 60)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("1d"), 86400)
        self.assertEqual(parse_duration("45"), 45 * 60)
        self.assertIsNone(parse_duration("soon"))
        self.assertIsNone(parse_duration(""))


class BotsTest(unittest.TestCase):
    def test_classified_by_user_agent_and_path(self):
        cases = {
            ("1.1.1.1", "/", "Mozilla/5.0 (iPhone) Safari"): ("person", ""),
            ("1.1.1.1", "/", "Mozilla/5.0 (compatible; Googlebot/2.1)"): ("search", "Googlebot"),
            ("1.1.1.1", "/", "Mozilla/5.0 (compatible; GPTBot/1.2)"): ("ai_training", "GPTBot"),
            ("1.1.1.1", "/", "Mozilla/5.0 (compatible; ClaudeBot/1.0)"): ("ai_training", "ClaudeBot"),
            ("1.1.1.1", "/", "Mozilla/5.0 (compatible; ChatGPT-User/1.0)"): ("ai_search", "ChatGPT-User"),
            ("1.1.1.1", "/", "WhatsApp/2.23"): ("preview", "WhatsApp"),
            ("1.1.1.1", "/wp-login.php?x=1", "Mozilla/5.0"): ("scan", "/wp-login.php"),
            ("1.1.1.1", "/", "python-requests/2.31"): ("script", "Python"),
            ("1.1.1.1", "/", "-"): ("script", "no User-Agent"),
            ("1.1.1.1", "/", "Mozilla/5.0 (compatible; FooCrawler/1.0)"): ("other_bot", "FooCrawler"),
            ("127.0.0.1", "/up", "curl/8.5"): ("monitor", "server-health"),
        }
        for (remote, path, ua), expected in cases.items():
            self.assertEqual(classify_request(remote, path, ua), expected, ua)

    def test_path_loses_identifiers_and_query(self):
        self.assertEqual(normalize_path("/en/orders/AbC123xYz?token=1"), "/en/orders/:id")
        self.assertEqual(normalize_path("/tv/pair/482913"), "/tv/pair/:id")
        self.assertEqual(normalize_path("/en-US/how-it-works"), "/en-US/how-it-works")


class ScheduleTest(unittest.TestCase):
    def test_interval_of_the_schedule(self):
        cases = {
            "every day at 4am America/Sao_Paulo": 86400,
            "0 4 * * * America/Sao_Paulo": 86400,
            "30 3 * * *": 86400,
            "every monday at 9am": 7 * 86400,
            "0 3 * * 1": 7 * 86400,
            "0 4 1 * *": 31 * 86400,
            "every hour at minute 12": 3600,
            "every 5 minutes": 300,
            "*/10 * * * *": 600,
            "something I cannot read": None,
        }
        for schedule, expected in cases.items():
            self.assertEqual(schedule_interval(schedule), expected, schedule)


class SshAndPortsParsingTest(unittest.TestCase):
    def setUp(self):
        helpers.use_lang("en")

    def test_ssh_logins_and_range(self):
        text = ("Oct 31 sshd[1]: Accepted publickey for deploy from 45.224.27.130 port 5 ssh2\n"
                "Oct 31 sshd[2]: Accepted password for root from 2001:db8:abcd:12::1 port 7 ssh2\n"
                "Oct 31 sshd[3]: Failed password for root from 203.0.113.9 port 7 ssh2\n")
        self.assertEqual(parse_ssh_logins(text), [("deploy", "45.224.27.130", "key"),
                                                  ("root", "2001:db8:abcd:12::1", "password")])
        self.assertEqual(ssh_prefix("45.224.27.130"), "45.224.27.0/24")
        self.assertEqual(ssh_prefix("2001:db8:abcd:12::1"), "2001:db8:abcd::/48")

    def test_listening_ports_skip_loopback(self):
        text = (helpers.FIXTURE / "cmd" / "ss_listen.txt").read_text()
        self.assertEqual(listening(text), {"*:22", "*:80", "*:443"})


if __name__ == "__main__":
    unittest.main()
