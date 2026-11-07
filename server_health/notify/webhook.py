"""A generic webhook: every message as JSON to SERVER_HEALTH_WEBHOOK_URL.

It is the bridge to anything that is not Discord (ntfy, Slack through a workflow, n8n,
a script of yours). The body:

  {"server": "my-server-prod", "type": "alert", "level": "CRIT", "route": "my-app",
   "title": "Critical · Server · Memory", "text": "<the whole message as plain text>"}

`type` is alert, reminder, summary, weekly or test; `level` is OK, WARN or CRIT;
`route` is the app with its own channel, or null for the server."""

from __future__ import annotations

from .. import http
from ..text import embed_text, plain
from . import Notifier


class Webhook(Notifier):
    name = "webhook"

    def configured(self) -> bool:
        return bool(self.cfg.get("SERVER_HEALTH_WEBHOOK_URL"))

    def send(self, message, *, route="", mention=False, alert=False, attachment=None) -> bool:
        url = self.cfg.get("SERVER_HEALTH_WEBHOOK_URL")
        if not url:
            return False
        body = {"server": self.label, "type": message.get("_type", "alert"), "level": message.get("_level", "OK"),
                "route": route or None, "title": plain(message["title"]), "text": embed_text(message)}
        code = http.post_json(url, body)
        ok = 200 <= code < 300
        self.log(f"Webhook: {'sent' if ok else f'failed (HTTP {code})'}: {body['title']}")
        return ok
