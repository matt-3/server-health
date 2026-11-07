"""A newer version of server-health itself.

Once a day it reads versions.json from the project (one small GET, nothing about the
server is sent). A newer version with a security or critical fix is a warning, repeated
every 24h until the upgrade; a newer version with features or ordinary fixes only shows
in the daily summary. SERVER_HEALTH_UPDATE_CHECK=false turns it off."""

from __future__ import annotations

import json
import re

from .. import __version__
from ..config import is_true
from ..i18n import lang, t
from ..metric import Metric
from ..module import Module

LEVELS = ("security", "critical", "feature", "fix")
URGENT = ("security", "critical")


def vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


class Updates(Module):
    name = "updates"
    order = 35
    defaults = {
        "SERVER_HEALTH_UPDATE_CHECK": "true",
        "SERVER_HEALTH_UPDATE_URL": "https://raw.githubusercontent.com/matt-3/server-health/main/versions.json",
    }
    thresholds = {"UPDATE_CHECK_H": 24}

    def detect(self, src, cfg):
        return is_true(cfg.get("SERVER_HEALTH_UPDATE_CHECK"))

    def host(self, ctx):
        cache = dict(ctx.state.get("updates") or {})
        if ctx.now - cache.get("checked", 0) >= ctx.th["UPDATE_CHECK_H"] * 3600:
            text = ctx.src.fetch(ctx.cfg["SERVER_HEALTH_UPDATE_URL"])
            cache["checked"] = int(ctx.now)
            try:
                listed = json.loads(text)["versions"] if text else None
            except (ValueError, KeyError, TypeError):
                listed = None
            if listed is None:
                ctx.log("update check failed: could not read versions.json")
            else:
                # Only what is newer than this version is kept.
                cache["versions"] = [v for v in listed if isinstance(v, dict) and v.get("level") in LEVELS
                                     and vtuple(str(v.get("version", ""))) > vtuple(__version__)]
            ctx.updates["updates"] = cache
        newer = [v for v in cache.get("versions", []) if vtuple(v["version"]) > vtuple(__version__)]
        if not newer:
            return
        newest = max(newer, key=lambda v: vtuple(v["version"]))
        urgent = [v for v in newer if v["level"] in URGENT]
        shown = max(urgent, key=lambda v: vtuple(v["version"])) if urgent else newest
        summary = shown.get("summary") or ""
        if isinstance(summary, dict):
            text = summary.get(lang()) or summary.get("en") or ""
        else:
            text = str(summary)
        value = t("updates.value", version=newest["version"], level=t(f"updates.level.{shown['level']}"),
                  current=__version__) + (f": {text}" if text else "")
        detail = t("updates.detail", version=newest["version"])
        if urgent:
            ctx.add(Metric("mnt.update", ctx.maintenance_section, t("updates.label"), value, "WARN", detail))
        else:
            ctx.add(Metric("mnt.update", ctx.maintenance_section, t("updates.label"), value, summary_only=True))

    strings = {
        "en": {
            "updates.label": "server-health update",
            "updates.value": "{version} available ({level}; installed {current})",
            "updates.level.security": "security fix",
            "updates.level.critical": "critical fix",
            "updates.level.feature": "new features",
            "updates.level.fix": "fixes",
            "updates.detail": "see CHANGELOG.md and upgrade with `curl -fsSL https://raw.githubusercontent.com/"
                              "matt-3/server-health/v{version}/install.sh | sudo bash -s -- v{version}`",
        },
        "pt-BR": {
            "updates.label": "Atualização do server-health",
            "updates.value": "{version} disponível ({level}; instalada {current})",
            "updates.level.security": "correção de segurança",
            "updates.level.critical": "correção crítica",
            "updates.level.feature": "novidades",
            "updates.level.fix": "correções",
            "updates.detail": "veja o CHANGELOG.md e atualize com `curl -fsSL https://raw.githubusercontent.com/"
                              "matt-3/server-health/v{version}/install.sh | sudo bash -s -- v{version}`",
        },
    }
