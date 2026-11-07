"""Sentry (optional): only NEW alerts, never a reminder, a "back to normal" or a
summary. Tags app:infra, severity and host, so Alert Rules can filter them."""

from __future__ import annotations

import re

from .. import __version__, http
from ..text import embed_text, plain
from . import Notifier


class Sentry(Notifier):
    name = "sentry"

    def configured(self) -> bool:
        return bool(self.cfg.get("SERVER_HEALTH_SENTRY_DSN"))

    def send(self, message, *, route="", mention=False, alert=False, attachment=None) -> bool:
        level = message.get("_alert_level", "OK")
        dsn = self.cfg.get("SERVER_HEALTH_SENTRY_DSN")
        if not alert or not dsn or level == "OK":
            return False
        m = re.match(r"^https://([^@]+)@([^/]+)/(\d+)$", dsn)
        if not m:
            return False
        key, host, project = m.groups()
        title = plain(message["title"])
        payload = {
            "message": title, "level": "fatal" if level == "CRIT" else "warning", "platform": "other",
            "server_name": self.label, "logger": "server-health",
            "tags": {"app": "infra", "source": "server-health-cron", "severity": level, "host": self.label},
            "extra": {"report": embed_text(message)[:8000]},
        }
        auth = f"Sentry sentry_version=7, sentry_client=server-health/{__version__}, sentry_key={key}"
        code = http.post_json(f"https://{host}/api/{project}/store/", payload, {"X-Sentry-Auth": auth})
        self.log(f"Sentry: {'sent' if code == 200 else f'failed (HTTP {code})'}: {title}")
        return code == 200
