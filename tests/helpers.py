"""Shared helpers for the tests: the Sandbox runs the whole monitor against a copy of
tests/fixtures/basic, with its own state and no network."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server_health import cli, http, i18n  # noqa: E402
from server_health.module import builtin_modules  # noqa: E402

FIXTURE = HERE / "fixtures" / "basic"
T0 = json.loads((FIXTURE / "host.json").read_text())["now"]  # 2026-10-31 15:00 in America/Sao_Paulo
HOUR = 3600
DAY = 86400

SERVER_URL = "https://discord.test/api/webhooks/1/token"
APP_URL = "https://discord.test/api/webhooks/2/token"
APP_HOOK_KEY = "SERVER_HEALTH_DISCORD_WEBHOOK_MY_APP"
APP_SQL = "cmd/psql_my-app-db_app.txt"
APP_LOG = "fs/var/log/nginx/my-app-access.log"
APP_TOML = "apps.d/my-app.toml"


def use_lang(code: str = "en") -> None:
    """For tests that call the builders directly, without `cli.main`: registers the
    strings of the built-in modules and picks the language."""
    for m in builtin_modules():
        i18n.register(m.strings)
    i18n.set_lang(code)


def low_memory(available_kb: int) -> str:
    return (f"MemTotal:        3911000 kB\nMemFree:          50000 kB\nMemAvailable:     {available_kb} kB\n"
            "SwapTotal:       2097148 kB\nSwapFree:        1800000 kB\n")


def nginx_lines(ua: str, n: int, path: str = "/", ip: str = "1.1.1.1", status: int = 200) -> str:
    return f'{ip} - - [31/Oct/2026:18:01:00 +0000] "GET {path} HTTP/1.1" {status} 0 "-" "{ua}"\n' * n


class Sandbox:
    """A copy of the fixture each test can change, with its own state directory. Every
    outgoing HTTP call is captured in `sent` instead of leaving the machine."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="server-health-"))
        self.fx = self.tmp / "fx"
        shutil.copytree(FIXTURE, self.fx)
        self.state_dir = self.tmp / "state"
        self.sent: list[dict] = []
        self.out = ""
        self.err = ""
        self.env = {
            "SERVER_HEALTH_CONFIG": str(self.tmp / "no-config"),
            "SERVER_HEALTH_DISCORD_WEBHOOK": SERVER_URL,
            "SERVER_HEALTH_STATE_DIR": str(self.state_dir),
            "SERVER_HEALTH_LOG": str(self.tmp / "server-health.log"),
            "SERVER_HEALTH_APPS_DIR": str(self.fx / "apps.d"),
            "SERVER_HEALTH_MODULES_DIR": str(self.tmp / "modules.d"),
            "SERVER_HEALTH_LABEL": "test",
            "SERVER_HEALTH_QUIET_HOURS": "",
            "SERVER_HEALTH_LANG": "en",
            "SERVER_HEALTH_TZ": "America/Sao_Paulo",
            "SERVER_HEALTH_WEEKLY_DASHBOARD": "false",
            # Off by default: the fixture's clock is a Saturday afternoon, so the first
            # round of every test would send both. `run_report` turns one on.
            "SERVER_HEALTH_DAILY_AT": "off",
            "SERVER_HEALTH_WEEKLY_AT": "off",
        }

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- the fixture -------------------------------------------------------------

    def read(self, rel: str) -> str:
        return (self.fx / rel).read_text()

    def write(self, rel: str, text: str):
        path = self.fx / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def append(self, rel: str, text: str):
        with open(self.fx / rel, "a") as fh:
            fh.write(text)

    def replace(self, rel: str, old: str, new: str):
        text = self.read(rel)
        assert old in text, f"{old!r} not in {rel}"
        self.write(rel, text.replace(old, new, 1))

    def host(self, **values):
        host = json.loads(self.read("host.json"))
        host.update(values)
        self.write("host.json", json.dumps(host))

    def set_now(self, ts: float):
        self.host(now=ts)

    # -- running -------------------------------------------------------------------

    def run(self, ts: float, *args: str) -> int:
        self.set_now(ts)
        sent = self.sent

        def post_json(url, payload, headers=None):
            sent.append({"kind": "json", "url": url, "payload": payload, "headers": headers or {}})
            return 204 if "discord" in url else 200

        def post_multipart(url, payload, filename, content, content_type):
            sent.append({"kind": "multipart", "url": url, "payload": payload, "filename": filename,
                         "content": content, "content_type": content_type})
            return 200

        def get(url):
            sent.append({"kind": "get", "url": url})
            return 200

        originals = (http.post_json, http.post_multipart, http.get, http.LOG)
        http.post_json, http.post_multipart, http.get = post_json, post_multipart, get
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                return cli.main(["--fixtures", str(self.fx), *args], env=self.env)
        finally:
            http.post_json, http.post_multipart, http.get, http.LOG = originals
            self.out, self.err = out.getvalue(), err.getvalue()

    def run_report(self, ts: float, kind: str = "daily") -> int:
        """A normal round whose time has the daily summary (or the weekly report) due,
        the way cron triggers it. Returns the round's exit code."""
        key = "SERVER_HEALTH_DAILY_AT" if kind == "daily" else "SERVER_HEALTH_WEEKLY_AT"
        local = datetime.fromtimestamp(ts, ZoneInfo(self.env["SERVER_HEALTH_TZ"]))
        self.env[key] = "00:00" if kind == "daily" else f"{local.strftime('%a').lower()} 00:00"
        try:
            return self.run(ts)
        finally:
            self.env[key] = "off"

    def reports(self) -> list[dict]:
        """The summary and weekly messages sent (the alerts of the round are left out)."""
        return [p for p in self.discord()
                if any(w in p["embeds"][0]["title"] for w in ("Summary of", "Week from", "Resumo de", "Semana de"))]

    # -- what went out ---------------------------------------------------------------

    def discord(self) -> list[dict]:
        return [s["payload"] for s in self.sent if "discord" in s["url"]]

    def titles(self) -> list[str]:
        return [p["embeds"][0]["title"] for p in self.discord()]

    def description(self, i: int = 0) -> str:
        return self.discord()[i]["embeds"][0]["description"]

    def by_url(self, url: str) -> list[dict]:
        return [s["payload"] for s in self.sent if s["url"] == url]

    def log(self) -> str:
        path = Path(self.env["SERVER_HEALTH_LOG"])
        return path.read_text() if path.exists() else ""

    # -- the state on disk -------------------------------------------------------------

    def state(self) -> dict:
        return json.loads((self.state_dir / "state.json").read_text())

    def write_state(self, state: dict):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "state.json").write_text(json.dumps(state))

    def history(self) -> list[dict]:
        return [json.loads(x) for x in (self.state_dir / "history.jsonl").read_text().splitlines()]

    def write_history(self, rows: list[dict]):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def write_jsonl(self, name: str, rows: list[dict]):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / name).write_text("".join(json.dumps(r) + "\n" for r in rows))

    def alerts(self) -> list[dict]:
        return [json.loads(x) for x in (self.state_dir / "alerts.jsonl").read_text().splitlines()]


def two_weeks_of_state(sb: Sandbox):
    """Two weeks of history, one alert, bots per day and the numbers of 7 summaries."""
    tz = ZoneInfo("America/Sao_Paulo")

    def day(days_ago: int) -> str:
        return datetime.fromtimestamp(T0 - days_ago * DAY, tz).strftime("%Y-%m-%d")

    rows = []
    for i in range(14 * 288, 0, -1):
        ts = T0 - i * 300
        this_week = ts >= T0 - 7 * DAY
        rows.append({"ts": ts, "mem_pct": 50, "load1": 0.2, "oom": 0, "dbs": {"my-app-db": 33e6 + (14 * 288 - i) * 1000},
                     "req": {"my-app": [3, 0]}, "ppl": {"my-app": [2 if this_week else 1, 0]},
                     "bots": {"my-app": {"ai_training": 1}}, "up": {"my-app": [200, 20]},
                     "deploy": {"my-app": "aaa@1" if i > 1000 else "bbb@2"}})
    sb.write_history(rows)
    sb.write_jsonl("alerts.jsonl", [
        {"ts": T0 - DAY, "key": "host.load", "section": "Server", "label": "Load", "kind": "new",
         "before": "OK", "after": "WARN", "duration": 0},
        {"ts": T0 - DAY + 1200, "key": "host.load", "section": "Server", "label": "Load",
         "kind": "normal", "before": "WARN", "after": "OK", "duration": 1200}])
    bots = {day(d): {"my-app": {"names": {"ai_training|GPTBot": 10, **({"ai_search|ChatGPT-User": 2} if d < 7 else {})},
                                "pages": {"ai_training|/en/how-it-works": 10}}} for d in range(14)}
    (sb.state_dir / "bots.json").write_text(json.dumps({"days": bots}))
    numbers = {day(d): {"my-app|paid_orders_24h": {"label": "Paid orders (24h)", "value": 3, "format": "number"}}
               for d in range(7)}
    sb.write_state({"daily_numbers": numbers, "last_run": T0 - 300})
    sb.append(APP_SQL, "@@SH@@|sq_failed_week:Orders::SyncJob|2\n")


class SandboxCase:
    """Mixin: a fresh Sandbox in self.sb for each test."""

    def setUp(self):
        super().setUp()
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
