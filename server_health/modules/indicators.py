"""Product indicators: the SQL each app declares in its TOML (what only the app knows
matters). `when = "always"` alerts; `when = "summary"` only shows in the daily summary
and adds up in the weekly report."""

from __future__ import annotations

import re
from datetime import datetime

from ..appconfig import format_value, indicator_level
from ..i18n import t
from ..metric import Metric
from ..module import Module
from ..parse import in_hours, to_float
from .postgres import q


def week_label(label: str) -> str:
    """A summary indicator's label talks about 24h; added up over the week, that goes."""
    label = re.sub(r"\(24h,\s*", "(", label)
    return re.sub(r"\s*\(24h\)", "", label)


class Indicators(Module):
    name = "indicators"
    order = 90
    requires = ("postgres",)

    def app_queries(self, ctx, app, svc):
        conf = ctx.app_config(app)
        if not conf:
            return []
        stmts = []
        for ind in self._active(ctx, conf):
            stmts.append(q(f"'ind:{ind.key}'", ind.sql))
        return stmts

    @staticmethod
    def _active(ctx, conf):
        return [ind for ind in conf.indicators if not (ind.when == "summary" and not ctx.daily)]

    def app_results(self, ctx, app, res):
        conf = ctx.app_config(app)
        for ind in self._active(ctx, conf):
            self.metric(ctx, app, ind, res.get(f"ind:{ind.key}"))

    def metric(self, ctx, app, ind, raw: list[str] | None):
        key = f"ind.{app}.{ind.key}"
        section = ctx.product_section(app)
        summary = ind.when == "summary"
        if raw is None:
            ctx.add(Metric(key, section, ind.label, t("ind.failed"), "OK" if summary else "WARN",
                           t("ind.failed_detail", app=app), persist_min=10, summary_only=summary))
            return
        value = to_float(raw[0]) if raw[0] != "" else 0.0
        if value is None:
            ctx.add(Metric(key, section, ind.label, t("ind.not_number", value=raw[0][:40]), "WARN",
                           t("ind.not_number_detail"), summary_only=summary))
            return
        shown = format_value(ind.format, value)
        if summary:
            ctx.numbers[f"{app}|{ind.key}"] = {"label": ind.label, "value": value, "format": ind.format}
            ctx.add(Metric(key, section, ind.label, shown, summary_only=True))
            return
        hour = datetime.fromtimestamp(ctx.now, ctx.tz).hour
        if ind.active_hours and not in_hours(ind.active_hours, hour):
            ctx.add(Metric(key, section, ind.label, t("ind.off_hours", value=shown, hours=ind.active_hours)))
            return
        ctx.add(Metric(key, section, ind.label, shown, indicator_level(ind, value), ind.explanation,
                       persist_min=ind.persist_min, crit_persist_min=ind.persist_min))

    def weekly_app_fields(self, r, section, app):
        today = datetime.fromtimestamp(r.now, r.tz).strftime("%Y-%m-%d")
        start = datetime.fromtimestamp(r.now - r.WEEK, r.tz).strftime("%Y-%m-%d")
        days = [d for d in sorted(r.daily_numbers) if start < d <= today]
        sums: dict[str, dict] = {}
        for d in days:
            for k, v in r.daily_numbers[d].items():
                a, _, key = k.partition("|")
                if a != app:
                    continue
                g = sums.setdefault(key, {"label": v["label"], "format": v["format"], "sum": 0.0, "last": 0.0, "n": 0})
                g["sum"] += v["value"]
                g["last"] = v["value"]
                g["n"] += 1
        out = []
        for g in sums.values():
            if g["format"] in ("number", "cents"):
                out.append(f"{week_label(g['label'])}: {format_value(g['format'], g['sum'])}")
            else:
                out.append(t("ind.weekly.last", label=g["label"], value=format_value(g["format"], g["last"])))
        if not out:
            return []
        n = max(g["n"] for g in sums.values())
        if n < 7:
            out.append(t("ind.weekly.partial.one" if n == 1 else "ind.weekly.partial.other", n=n))
        return [{"name": t("ind.weekly_field", section=section), "value": r.bullets(out)}]

    strings = {
        "en": {
            "ind.failed": "the query failed",
            "ind.failed_detail": "run `server-health --check-app` with the TOML of {app} to see the error",
            "ind.not_number": "non-numeric value: {value}",
            "ind.not_number_detail": "the query must return a number",
            "ind.off_hours": "{value} (outside {hours})",
            "ind.weekly.last": "{label}: {value} (last summary)",
            "ind.weekly.partial.one": "(sum of {n} daily summary)",
            "ind.weekly.partial.other": "(sum of {n} daily summaries)",
            "ind.weekly_field": "{section} · numbers of the week",
        },
        "pt-BR": {
            "ind.failed": "a consulta falhou",
            "ind.failed_detail": "rode `server-health --check-app` com o TOML de {app} para ver o erro",
            "ind.not_number": "valor não numérico: {value}",
            "ind.not_number_detail": "a consulta precisa devolver um número",
            "ind.off_hours": "{value} (fora do horário {hours})",
            "ind.weekly.last": "{label}: {value} (último resumo)",
            "ind.weekly.partial.one": "(soma de {n} resumo diário)",
            "ind.weekly.partial.other": "(soma de {n} resumos diários)",
            "ind.weekly_field": "{section} · números da semana",
        },
    }
