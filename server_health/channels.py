"""Which Discord channel each metric goes to, by its key (stable, and it already says
the owner). Route "" is the server's channel; an app without its own webhook stays
there, so a server with a single webhook gets the same messages as always."""

from __future__ import annotations

import re

from .config import APP_WEBHOOK_PREFIX
from .metric import Metric


def app_webhook_key(app: str) -> str:
    return APP_WEBHOOK_PREFIX + re.sub(r"[^A-Z0-9]", "_", app.upper())


class Channels:
    def __init__(self, cfg: dict, apps, db_app: dict[str, str]):
        apps = list(apps)
        self.own = [a for a in apps if cfg.get(app_webhook_key(a))]
        self.db_app = db_app
        known = {app_webhook_key(a) for a in apps}
        self.unused = sorted(k for k in cfg if k.startswith(APP_WEBHOOK_PREFIX) and k not in known)

    def routes(self) -> list[str]:
        return ["", *self.own]

    def route(self, key: str) -> str:
        kind, _, rest = key.partition(".")
        name = rest.partition(".")[0]
        if kind in ("app", "ind"):
            owner = name
        elif kind == "db":
            owner = self.db_app.get(name, "")
        else:
            owner = ""
        return owner if owner in self.own else ""

    def queued_route(self, route: str) -> str:
        """The webhook may have left the config while the warning waited in the queue."""
        return route if route in self.own else ""

    def metrics(self, route: str, metrics: list[Metric]) -> list[Metric]:
        return [m for m in metrics if self.route(m.key) == route]

    def apps(self, route: str, section_app: dict[str, str]) -> dict[str, str]:
        return {sec: app for sec, app in section_app.items() if self.route(f"app.{app}.") == route}
