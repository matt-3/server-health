"""A new port listening to the outside. Same idea as the SSH login from a new IP: a
service exposed that nobody on the team opened (a database published by mistake, a
debug server left running). The first round learns what is open, silently."""

from __future__ import annotations

import re

from ..i18n import t
from ..metric import Metric
from ..module import Module

LOCAL = re.compile(r"^(127\.|\[?::1\]?|\[?::ffff:127\.)")


def listening(text: str) -> set[str]:
    """'addr:port' of each TCP socket listening outside loopback, from `ss -H -ltn`."""
    out = set()
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        addr = parts[3]
        if LOCAL.match(addr) or "%lo" in addr:
            continue
        out.add(addr.replace("0.0.0.0:", "*:").replace("[::]:", "*:"))
    return out


class Ports(Module):
    name = "ports"
    order = 22

    def detect(self, src, cfg):
        return src.which("ss")

    def host(self, ctx):
        rc, out, _ = ctx.src.run("ss_listen", ["ss", "-H", "-ltn"], timeout=10)
        if rc != 0:
            return
        now = listening(out)
        known = ctx.state.get("ports")
        # Each port with the last time it was seen; one closed for 30 days is forgotten
        # (and would alert again if it came back).
        seen = {**(known or {}), **{addr: int(ctx.now) for addr in now}}
        ctx.updates["ports"] = {a: ts for a, ts in seen.items() if ts >= ctx.now - 30 * 86400}
        if known is None:
            return
        new = sorted(now - set(known))
        if new:
            ctx.add(Metric("host.ports_new", ctx.server_section, t("ports.label"), ", ".join(new[:8]), "WARN",
                           t("ports.detail"), event=True))

    strings = {
        "en": {
            "ports.label": "New port open",
            "ports.detail": "a port started listening outside the machine: `sudo ss -ltnp` shows the process. If "
                            "nobody opened it on purpose, close it or put it behind the firewall",
        },
        "pt-BR": {
            "ports.label": "Porta nova aberta",
            "ports.detail": "uma porta passou a escutar para fora da máquina: `sudo ss -ltnp` mostra o processo. Se "
                            "ninguém abriu de propósito, feche ou ponha atrás do firewall",
        },
    }
