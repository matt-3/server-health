"""Pending security updates and reboot (every round), and in the daily summary the
Fail2Ban count, SSH logins and the space Docker could give back."""

from __future__ import annotations

import re

from ..i18n import fdur, t
from ..metric import Metric
from ..module import Module


def apt_security(src) -> int | None:
    """Pending security updates according to apt-check (which writes to stderr)."""
    rc, out, err = src.run("apt_check", ["/usr/lib/update-notifier/apt-check"], timeout=60)
    m = re.match(r"^\s*(\d+);(\d+)", (err or out or ""))
    return int(m.group(2)) if m else None


class Maintenance(Module):
    name = "maintenance"
    order = 30
    thresholds = {
        "MAINT_STALE_DAYS": 7,  # security update or reboot pending for this many days
        "MAINT_CHECK_H": 6,     # apt-check is slow: it runs in normal rounds every this many hours
    }

    def detect(self, src, cfg):
        return src.read("/etc/os-release") is not None or src.which("apt-get")

    def host(self, ctx):
        sec = ctx.maintenance_section
        mnt = dict(ctx.state.get("maintenance") or {})
        if ctx.now - mnt.get("checked", 0) >= ctx.th["MAINT_CHECK_H"] * 3600:
            pending = apt_security(ctx.src)
            if pending is not None:
                mnt["checked"] = int(ctx.now)
                if pending > 0:
                    mnt["security"] = pending
                    mnt["security_since"] = mnt.get("security_since") or int(ctx.now)
                else:
                    mnt.pop("security", None)
                    mnt.pop("security_since", None)
        ctx.updates["maintenance"] = mnt
        days = ctx.th["MAINT_STALE_DAYS"]
        stale = days * 86400
        if "checked" in mnt:
            since = mnt.get("security_since")
            if since:
                age = ctx.now - since
                ctx.add(Metric("mnt.security_age", sec, t("mnt.security"),
                               t("mnt.security.value", n=mnt.get("security"), age=fdur(age)),
                               "WARN" if age > stale else "OK", t("mnt.security.detail", days=days)))
            else:
                ctx.add(Metric("mnt.security_age", sec, t("mnt.security"), t("mnt.security.none")))
        st = ctx.src.stat("/var/run/reboot-required")
        if st:
            age = ctx.now - st.st_mtime
            pkgs = sorted(set((ctx.src.read("/var/run/reboot-required.pkgs") or "").split()))[:4]
            ctx.add(Metric("mnt.reboot_age", sec, t("mnt.reboot"),
                           t("mnt.reboot.value", age=fdur(age)) + (f" ({', '.join(pkgs)})" if pkgs else ""),
                           "WARN" if age > stale else "OK", t("mnt.reboot.detail", days=days)))
        else:
            ctx.add(Metric("mnt.reboot_age", sec, t("mnt.reboot"), t("mnt.reboot.none")))

    def daily(self, ctx):
        sec = ctx.maintenance_section
        rc, out, err = ctx.src.run("apt_check", ["/usr/lib/update-notifier/apt-check"], timeout=60)
        m = re.match(r"^\s*(\d+);(\d+)", (err or out or ""))
        if m:
            ctx.add(Metric("mnt.updates", sec, t("mnt.updates"), t("mnt.updates.value", n=m.group(1), sec=m.group(2)),
                           summary_only=True))
        rc, out, _ = ctx.src.run("fail2ban", ["fail2ban-client", "status", "sshd"], timeout=20)
        if rc == 0:
            cur = re.search(r"Currently banned:\s*(\d+)", out)
            tot = re.search(r"Total banned:\s*(\d+)", out)
            if cur:
                ctx.add(Metric("mnt.fail2ban", sec, "Fail2Ban (ssh)",
                               t("mnt.fail2ban.value", now=cur.group(1), total=tot.group(1) if tot else "?"),
                               summary_only=True))
        rc, out, _ = ctx.src.run("journal_ssh", ["journalctl", "-u", "ssh", "-u", "sshd", "--since", "24 hours ago",
                                                 "--no-pager", "-q"], timeout=30)
        if rc == 0:
            logins = len(re.findall(r"Accepted (publickey|password)", out))
            ctx.add(Metric("mnt.ssh", sec, t("mnt.ssh"), str(logins), summary_only=True))
        rc, out, _ = ctx.src.run("docker_df", ["docker", "system", "df", "--format", "{{.Type}}|{{.Size}}|{{.Reclaimable}}"],
                                 timeout=60)
        if rc == 0:
            parts = []
            for line in out.splitlines():
                p = line.split("|")
                if len(p) == 3:
                    kind = t(f"mnt.docker.{p[0]}") if p[0] in ("Images", "Containers", "Local Volumes", "Build Cache") \
                        else p[0]
                    parts.append(t("mnt.docker.item", kind=kind, size=p[1], free=p[2]))
            if parts:
                ctx.add(Metric("mnt.docker", sec, "Docker", " · ".join(parts), summary_only=True, items=parts))

    def weekly_fields(self, r):
        from ..text import clip, join_blocks
        mnt = [m for m in r.metrics if m.section == r.maintenance_section]
        if not mnt:
            return []
        return [(30, {"name": r.maintenance_section, "value": clip(join_blocks([m.discord_line() for m in mnt]), 1020)})]

    strings = {
        "en": {
            "mnt.security": "Security updates",
            "mnt.security.value": "{n} pending for {age}",
            "mnt.security.none": "none pending",
            "mnt.security.detail": "pending for more than {days} days: `sudo apt update && sudo apt upgrade` at a "
                                   "quiet hour",
            "mnt.reboot": "Pending reboot",
            "mnt.reboot.value": "for {age}",
            "mnt.reboot.none": "none",
            "mnt.reboot.detail": "the kernel or an updated library has waited for the reboot for more than {days} "
                                 "days: `sudo reboot` at a quiet hour, with the maintenance announced",
            "mnt.updates": "Updates",
            "mnt.updates.value": "{n} pending, {sec} security",
            "mnt.fail2ban.value": "{now} IP(s) banned now, {total} since start",
            "mnt.ssh": "SSH logins (24h)",
            "mnt.docker.Images": "Images",
            "mnt.docker.Containers": "Containers",
            "mnt.docker.Local Volumes": "Volumes",
            "mnt.docker.Build Cache": "Build cache",
            "mnt.docker.item": "{kind}: {size} (reclaimable {free})",
        },
        "pt-BR": {
            "mnt.security": "Atualização de segurança",
            "mnt.security.value": "{n} pendente(s) há {age}",
            "mnt.security.none": "nenhuma pendente",
            "mnt.security.detail": "pendente há mais de {days} dias: `sudo apt update && sudo apt upgrade` num "
                                   "horário calmo",
            "mnt.reboot": "Reinício pendente",
            "mnt.reboot.value": "há {age}",
            "mnt.reboot.none": "nenhum",
            "mnt.reboot.detail": "o kernel ou uma biblioteca atualizada espera o reinício há mais de {days} dias: "
                                 "`sudo reboot` num horário calmo, com o site em manutenção avisada",
            "mnt.updates": "Atualizações",
            "mnt.updates.value": "{n} pendente(s), {sec} de segurança",
            "mnt.fail2ban.value": "{now} IP(s) banido(s) agora, {total} desde o início",
            "mnt.ssh": "Entradas por SSH (24h)",
            "mnt.docker.Images": "Imagens",
            "mnt.docker.Containers": "Contêineres",
            "mnt.docker.Local Volumes": "Volumes",
            "mnt.docker.Build Cache": "Cache de build",
            "mnt.docker.item": "{kind}: {size} (recuperável {free})",
        },
    }
