"""SSH logins from an IP range that has not logged in for SSH_KNOWN_DAYS days.

The range (/24 on IPv4, /48 on IPv6) counts as known, so a home IP that changes within
the same range does not alert. The first round learns the last 30 days silently."""

from __future__ import annotations

import re

from ..i18n import t
from ..metric import Metric
from ..module import Module

SSH_LOGIN_RE = re.compile(r"Accepted (publickey|password|keyboard-interactive\S*) for (\S+) from (\S+) port")


def parse_ssh_logins(text: str) -> list[tuple[str, str, str]]:
    """(user, IP, method) of each accepted login in the ssh journal."""
    names = {"publickey": t("ssh.method.key"), "password": t("ssh.method.password")}
    return [(m.group(2), m.group(3), names.get(m.group(1), m.group(1))) for m in SSH_LOGIN_RE.finditer(text or "")]


def ssh_prefix(ip: str) -> str:
    """The IP's range: /24 on IPv4, /48 on IPv6."""
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + "::/48"
    parts = ip.split(".")
    return ".".join(parts[:3]) + ".0/24" if len(parts) == 4 else ip


class Ssh(Module):
    name = "ssh"
    order = 16
    thresholds = {"SSH_KNOWN_DAYS": 30}

    def detect(self, src, cfg):
        return src.which("journalctl")

    def host(self, ctx):
        known = ctx.state.get("ssh_ips")
        if known is None:
            rc, out, _ = ctx.src.run("journal_ssh_seed", ["journalctl", "-u", "ssh", "-u", "sshd", "--since",
                                                          "30 days ago", "--no-pager", "-q"], timeout=60)
            if rc != 0:
                return
            ctx.updates["ssh_ips"] = {ssh_prefix(ip): int(ctx.now) for _, ip, _ in parse_ssh_logins(out)}
            return
        since = int((ctx.state.get("last_run") or ctx.now - 600) - 60)
        rc, out, _ = ctx.src.run("journal_ssh_round", ["journalctl", "-u", "ssh", "-u", "sshd", "--since",
                                                       f"@{since}", "--no-pager", "-q"], timeout=30)
        if rc != 0:
            return
        known = dict(known)
        new = []
        for user, ip, method in parse_ssh_logins(out):
            pref = ssh_prefix(ip)
            if pref not in known and not any(ip in x for x in new):
                new.append(t("ssh.login", user=user, ip=ip, method=method))
            known[pref] = int(ctx.now)
        days = ctx.th["SSH_KNOWN_DAYS"]
        cutoff = ctx.now - days * 86400
        ctx.updates["ssh_ips"] = {k: v for k, v in known.items() if v >= cutoff}
        if new:
            ctx.add(Metric("host.ssh_new", ctx.server_section, t("ssh.label"), "; ".join(new[:5]), "WARN",
                           t("ssh.detail", days=days), event=True))

    strings = {
        "en": {
            "ssh.method.key": "key",
            "ssh.method.password": "password",
            "ssh.login": "{user} from {ip} ({method})",
            "ssh.label": "SSH login from a new IP",
            "ssh.detail": "an IP range that has not logged in for {days} days. If nobody on the team did it: "
                          "`sudo last -i`, rotate the key and check `~/.ssh/authorized_keys`",
        },
        "pt-BR": {
            "ssh.method.key": "chave",
            "ssh.method.password": "senha",
            "ssh.login": "{user} de {ip} ({method})",
            "ssh.label": "Entrada por SSH de IP novo",
            "ssh.detail": "faixa de IP que não entrou nos últimos {days} dias. Se não foi ninguém da equipe: "
                          "`sudo last -i`, troque a chave e confira `~/.ssh/authorized_keys`",
        },
    }
