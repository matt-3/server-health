"""Whether the clock is synchronized. A clock that drifts breaks TLS, tokens with an
expiry (JWT), cron and every "how long ago" the monitor itself measures."""

from __future__ import annotations

from ..i18n import t
from ..metric import Metric
from ..module import Module


class Clock(Module):
    name = "clock"
    order = 21

    def detect(self, src, cfg):
        return src.which("timedatectl")

    def host(self, ctx):
        rc, out, _ = ctx.src.run("timedatectl_sync", ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                                 timeout=10)
        if rc != 0 or not out.strip():
            return
        synced = out.strip().lower() == "yes"
        label = t("clock.label")
        if synced:
            ctx.add(Metric("host.clock", ctx.server_section, label, t("clock.ok")))
        else:
            ctx.add(Metric("host.clock", ctx.server_section, label, t("clock.not_synced"), "WARN", t("clock.detail"),
                           persist_min=30))

    strings = {
        "en": {
            "clock.label": "Clock",
            "clock.ok": "synchronized",
            "clock.not_synced": "NOT synchronized",
            "clock.detail": "a drifting clock breaks TLS, tokens with an expiry and scheduling: "
                            "`timedatectl timesync-status` and `sudo systemctl restart systemd-timesyncd`",
        },
        "pt-BR": {
            "clock.label": "Relógio",
            "clock.ok": "sincronizado",
            "clock.not_synced": "NÃO sincronizado",
            "clock.detail": "relógio fora de hora quebra TLS, tokens com validade e agendamento: "
                            "`timedatectl timesync-status` e `sudo systemctl restart systemd-timesyncd`",
        },
    }
