"""Failed systemd units: what died outside Docker (cron, fail2ban, Docker itself)."""

from __future__ import annotations

from ..config import csv
from ..i18n import t
from ..metric import Metric
from ..module import Module


class Systemd(Module):
    name = "systemd"
    order = 20
    defaults = {"SERVER_HEALTH_SYSTEMD_IGNORE": ""}  # units to ignore, comma separated

    def detect(self, src, cfg):
        return src.which("systemctl")

    def host(self, ctx):
        rc, out, _ = ctx.src.run("systemctl_failed", ["systemctl", "--failed", "--no-legend", "--plain"], timeout=20)
        if rc != 0:
            return
        ignore = set(csv(ctx.cfg.get("SERVER_HEALTH_SYSTEMD_IGNORE")))
        # The bullet ("● unit ...") is a column of its own in some systemd versions,
        # even with --plain: strip it from the line before taking the unit name.
        lines = [line.strip().lstrip("●*").strip() for line in out.splitlines()]
        units = [line.split()[0] for line in lines if line]
        failed = [u for u in units if u not in ignore]
        label = t("systemd.label")
        if not failed:
            ctx.add(Metric("host.systemd", ctx.server_section, label, t("systemd.none")))
            return
        ctx.add(Metric("host.systemd", ctx.server_section, label, ", ".join(failed[:6]), "WARN",
                       t("systemd.detail", unit=failed[0]), persist_min=10))

    strings = {
        "en": {
            "systemd.label": "Failed services",
            "systemd.none": "none",
            "systemd.detail": "a systemd unit failed: `systemctl status {unit}` and `journalctl -u {unit} -n 50`; "
                              "ignore a unit that is expected to fail with SERVER_HEALTH_SYSTEMD_IGNORE",
        },
        "pt-BR": {
            "systemd.label": "Serviços com falha",
            "systemd.none": "nenhum",
            "systemd.detail": "uma unidade do systemd falhou: `systemctl status {unit}` e `journalctl -u {unit} -n 50`; "
                              "a que falha de propósito vai em SERVER_HEALTH_SYSTEMD_IGNORE",
        },
    }
