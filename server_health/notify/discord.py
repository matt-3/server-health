"""Discord, by webhook: one channel for the server and, optionally, one per app."""

from __future__ import annotations

import socket
from datetime import datetime, timezone

from .. import __version__, http
from ..channels import app_webhook_key
from ..i18n import register, t
from ..text import clip, plain
from . import Notifier

EMBED_BUDGET = 5600  # Discord refuses the WHOLE message above 6000 characters in total


def embed_size(e: dict) -> int:
    return (len(e["title"]) + len(e.get("description", "")) + len((e.get("author") or {}).get("name", ""))
            + sum(len(f["name"]) + len(f["value"]) for f in e["fields"]))


def split_embed(embed: dict) -> list[dict]:
    """Splits into several messages instead of cutting sections: in the summary of a
    server with two apps, no part can disappear to fit."""
    first = dict(embed, fields=[])
    parts = [first]
    for field in embed.get("fields", [])[:25 * 10]:
        cur = parts[-1]
        if embed_size(cur) + len(field["name"]) + len(field["value"]) > EMBED_BUDGET or len(cur["fields"]) >= 25:
            cur = {"title": clip(t("discord.continued", title=embed["title"]), 250), "description": "",
                   "color": embed.get("color"), "fields": []}
            if embed.get("author"):
                cur["author"] = embed["author"]
            parts.append(cur)
        cur["fields"].append(field)
    return parts


class Discord(Notifier):
    name = "discord"

    def configured(self) -> bool:
        return bool(self.cfg.get("SERVER_HEALTH_DISCORD_WEBHOOK"))

    def send(self, message, *, route="", mention=False, alert=False, attachment=None) -> bool:
        server_url = self.cfg.get("SERVER_HEALTH_DISCORD_WEBHOOK")
        url = (self.cfg.get(app_webhook_key(route)) if route else None) or server_url
        if not url:
            return False
        ok = True
        who = self.cfg.get("SERVER_HEALTH_DISCORD_MENTION", "")
        embed = {k: v for k, v in message.items() if not k.startswith("_")}
        # The kind goes apart in the log (the title has an emoji and changes shape):
        # counting the night's warnings filters by it.
        tag = message.get("_tag", "")
        for i, part in enumerate(split_embed(embed)):
            part["footer"] = {"text": f"server-health {__version__} · {socket.gethostname()}"}
            part["timestamp"] = datetime.now(timezone.utc).isoformat()
            payload = {"username": "Server Health", "embeds": [part]}
            if mention and who and i == 0:
                payload["content"] = who
                payload["allowed_mentions"] = {"parse": ["everyone", "roles", "users"]}
            else:
                payload["allowed_mentions"] = {"parse": []}
            last = i == len(split_embed(embed)) - 1

            def post(target):
                if attachment and last:
                    return http.post_multipart(target, payload, *attachment)
                return http.post_json(target, payload)

            code = post(url)
            if code not in (200, 204) and server_url and url != server_url:
                # A deleted webhook or a removed channel: the alert cannot vanish silently.
                self.log(f"Discord: the channel of {route} failed (HTTP {code}); sending to the server channel")
                code = post(server_url)
            if code in (200, 204):
                self.log(f"Discord: sent ({tag}): {plain(part['title'])}")
            else:
                self.log(f"Discord: failed (HTTP {code}) ({tag}): {plain(part['title'])}")
                ok = False
        return ok


register({
    "en": {"discord.continued": "{title} (continued)"},
    "pt-BR": {"discord.continued": "{title} (continua)"},
})
