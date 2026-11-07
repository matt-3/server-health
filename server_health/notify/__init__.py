"""Where the messages go. A notifier receives the same message (a dict shaped like a
Discord embed, with `_tag` and `_level`) and delivers it its own way.

To add a channel: a subclass of `Notifier` with `configured()` and `send()`, listed
in NOTIFIERS below (or in a third-party module file as `NOTIFIER`)."""

from __future__ import annotations


class Notifier:
    name = ""

    def __init__(self, cfg: dict, label: str, log):
        self.cfg = cfg
        self.label = label
        self.log = log

    def configured(self) -> bool:
        return False

    def send(self, message: dict, *, route: str = "", mention: bool = False, alert: bool = False,
             attachment: tuple[str, bytes, str] | None = None) -> bool:
        """`alert` is True for a new or worse alert (not a reminder or a summary)."""
        raise NotImplementedError


def build(cfg: dict, label: str, log) -> list[Notifier]:
    from .discord import Discord
    from .sentry import Sentry
    from .webhook import Webhook
    return [cls(cfg, label, log) for cls in (Discord, Webhook, Sentry)]
