"""Builds the messages: the alert, the daily summary, the weekly report and the
dry-run report. A message is a dict shaped like a Discord embed (title, description,
fields, color) plus `_tag`, `_type` and `_level`, which every notifier understands."""

from __future__ import annotations

from datetime import datetime

from . import __version__
from .i18n import fdate, fdur, fnum, register, t, tn
from .metric import COLORS, EVENT, NEW, NORMAL, OPENING, REMINDER, BETTER, Change, Metric, level_name, lvl, max_level
from .text import MARK_NORMAL, MARKS, bullets, clip, join_blocks, md

WEEK = 7 * 86400


def section_order(metrics: list[Metric], server: str, maintenance: str) -> list[str]:
    order = []
    for m in metrics:
        if m.section not in order:
            order.append(m.section)
    head = [s for s in (server,) if s in order]
    tail = [s for s in (maintenance,) if s in order]
    return head + [s for s in order if s not in head + tail] + tail


def overall(metrics: list[Metric]) -> tuple[str, int, int]:
    crit = sum(1 for m in metrics if m.effective == "CRIT" and not m.event)
    warn = sum(1 for m in metrics if m.effective == "WARN" and not m.event)
    return ("CRIT" if crit else "WARN" if warn else "OK"), crit, warn


def overall_text(crit: int, warn: int) -> str:
    if not crit and not warn:
        return t("report.all_ok")
    parts = []
    if crit:
        parts.append(tn("report.n_crit", crit))
    if warn:
        parts.append(tn("report.n_warn", warn))
    return ", ".join(parts)


def ordered(metrics: list[Metric]) -> list[Metric]:
    """Problems first (critical, then warning), the rest in collection order, and what
    only informs at the end."""
    status = sorted((m for m in metrics if not m.summary_only), key=lambda m: -lvl(m.effective))
    return status + [m for m in metrics if m.summary_only]


def problem_lines(metrics: list[Metric]) -> list[str]:
    return [f"{MARKS[m.effective]} {md(m.section)} · **{md(m.label)}**: {md(m.value)}"
            for m in sorted((m for m in metrics if m.problem), key=lambda m: -lvl(m.effective))]


class ReportContext:
    """What the report hooks of the modules read."""
    WEEK = WEEK

    def __init__(self, *, metrics, history, now, tz, server_section, maintenance_section, channels=None, route="",
                 alerts=None, bots=None, daily_numbers=None, jobs_week=None):
        self.metrics = metrics
        self.history = history
        self.now = now
        self.tz = tz
        self.server_section = server_section
        self.maintenance_section = maintenance_section
        self.channels = channels
        self.route = route
        self.alerts = alerts or []
        self.bots = bots or {}
        self.daily_numbers = daily_numbers or {}
        self.jobs_week = jobs_week or {}

    def window(self, start: float, end: float) -> list[dict]:
        return [h for h in self.history if start <= h.get("ts", 0) < end]

    def alerts_window(self, start: float, end: float) -> list[dict]:
        return [a for a in self.alerts if start <= a.get("ts", 0) < end]

    def bot_days(self, start: float, end: float) -> list[str]:
        first = datetime.fromtimestamp(start, self.tz).strftime("%Y-%m-%d")
        last = datetime.fromtimestamp(end - 1, self.tz).strftime("%Y-%m-%d")
        return [d for d in self.bots.get("days", {}) if first <= d <= last]

    @staticmethod
    def bullets(lines: list[str]) -> str:
        return clip(bullets(lines), 1020)


# -- alert -------------------------------------------------------------------------


def build_alert(label: str, changes: list[Change], queued: list[str], metrics: list[Metric], top: list[str],
                tz, test: bool = False) -> dict:
    opening = [c for c in changes if c.kind in OPENING]
    if any(c.after == "CRIT" for c in opening):
        tag, mark = t("report.tag.crit"), MARKS["CRIT"]
    elif opening:
        tag, mark = t("report.tag.warn"), MARKS["WARN"]
    elif changes and all(c.kind in (NORMAL, BETTER) for c in changes):
        tag, mark = t("report.tag.normal"), MARK_NORMAL if all(c.kind == NORMAL for c in changes) else MARKS["WARN"]
    elif changes and all(c.kind == REMINDER for c in changes):
        tag, mark = t("report.tag.still"), MARKS[max_level(c.after for c in changes)]
    else:
        tag = t("report.tag.quiet") if queued and not changes else t("report.tag.changes")
        mark = MARKS[max_level([c.after for c in changes] + ["WARN"])]
    prefix = t("report.test_prefix") if test else ""
    if len(changes) == 1 and not queued:
        title = f"{changes[0].mark} {prefix}{changes[0].title()}"
    else:
        n = len(changes) + len(queued)
        title = f"{mark} {prefix}{tag.capitalize()} · {tn('report.n_changes', n)}"

    single = len(changes) == 1 and not queued
    lines = [c.discord_text(single) for c in changes]
    if queued:
        lines.append(t("report.quiet_header"))
        lines += [f"- {md(q)}" for q in queued]
    fields = []
    touched = []
    for c in changes:
        if c.metric.section not in touched:
            touched.append(c.metric.section)
    for sec in touched:
        sec_metrics = ordered([m for m in metrics if m.section == sec and not m.summary_only])
        body = "\n".join(m.discord_line() for m in sec_metrics) or "-"
        fields.append({"name": sec, "value": clip(body, 1020)})
    if top and any(c.metric.key in ("host.memory", "host.swap", "host.oom") or ".oom" in c.metric.key for c in changes):
        fields.append({"name": t("report.who_memory"), "value": clip(bullets(top), 1020)})
    level, _, _ = overall(metrics)
    open_now = [f"{MARKS[m.effective]} {md(m.section)} · {md(m.label)}"
                for m in sorted((m for m in metrics if m.problem), key=lambda m: -lvl(m.effective))]
    fields.append({"name": t("report.open_now"), "value": clip("\n".join(open_now) or t("report.open_none"), 1020)})
    alert_level = max_level(c.after for c in opening) if opening else "OK"
    kind = "test" if test else ("reminder" if changes and all(c.kind == REMINDER for c in changes) else "alert")
    return {
        "_tag": prefix + tag, "_type": kind, "_level": level, "_alert_level": alert_level,
        "author": {"name": clip(label, 250)},
        "title": clip(title, 250),
        "description": clip("\n".join(lines), 3800),
        "color": COLORS[level],
        "fields": fields[:24],
    }


def alert_rows(changes: list[Change], now: float) -> list[dict]:
    """One row per opening, level change or closing: the weekly report takes from it
    how many alerts there were and how long they stayed open."""
    return [{"ts": int(now), "key": c.metric.key, "section": c.metric.section, "label": c.metric.label,
             "kind": c.kind, "before": c.before, "after": c.after, "duration": int(c.duration)}
            for c in changes if c.kind != REMINDER]


# -- daily summary -------------------------------------------------------------------


def build_summary(label: str, r: ReportContext, apps: dict[str, str], top: list[str], modules: list,
                  test: bool = False) -> dict:
    metrics = r.metrics
    level, crit, warn = overall(metrics)
    prefix = t("report.test_prefix") if test else ""
    title = f"{MARKS[level]} {prefix}{t('report.summary_title', date=fdate(r.now, r.tz, 'fmt.day'), state=overall_text(crit, warn))}"
    problems = problem_lines(metrics)
    description = (t("report.attention") + "\n" + "\n".join(problems)) if problems else t("report.all_ok_sentence")
    fields = []
    for sec in section_order(metrics, r.server_section, r.maintenance_section):
        sec_metrics = [m for m in metrics if m.section == sec]
        numbers = [m for m in sec_metrics if m.summary_only and m.key.startswith("ind.")]
        lines = [m.discord_line() for m in ordered([m for m in sec_metrics if m not in numbers])]
        app = apps.get(sec)
        extra = []
        for mod in modules:
            extra += mod.daily_lines(r, sec, app)
        if extra:
            lines.append(t("report.last_24h") + "\n" + bullets(extra))
        if sec == r.server_section and top:
            lines.append(t("report.who_memory") + ":\n" + bullets(top))
        if lines:
            fields.append({"name": sec, "value": clip(join_blocks(lines), 1020)})
        if numbers:
            fields.append({"name": t("report.numbers_of_day", section=sec),
                           "value": clip("\n".join(m.discord_line() for m in numbers), 1020)})
    return {"_tag": prefix + t("report.tag.summary"), "_type": "test" if test else "summary", "_level": level,
            "author": {"name": clip(label, 250)}, "title": clip(title, 250),
            "description": clip(description, 3800), "color": COLORS[level], "fields": fields[:24]}


# -- weekly report -------------------------------------------------------------------


def weekly_alert_lines(r: ReportContext) -> tuple[list[str], int, int]:
    cur = r.alerts_window(r.now - WEEK, r.now)
    prev = r.alerts_window(r.now - 2 * WEEK, r.now - WEEK)
    opened = [a for a in cur if a["kind"] in (NEW, EVENT)]
    n_prev = sum(1 for a in prev if a["kind"] in (NEW, EVENT))
    groups: dict[str, dict] = {}
    for a in cur:
        g = groups.setdefault(a["key"], {"section": a["section"], "label": a["label"], "n": 0, "worst": "OK", "dur": 0})
        if a["kind"] in (NEW, EVENT):
            g["n"] += 1
        g["worst"] = max_level([g["worst"], a["after"], a["before"]])
        if a["kind"] == NORMAL:
            g["dur"] += a.get("duration", 0)
    open_now = {m.key for m in r.metrics if m.problem}
    lines = []
    for key, g in sorted(groups.items(), key=lambda kv: (-lvl(kv[1]["worst"]), -kv[1]["n"])):
        if not g["n"] and key not in open_now:
            continue
        parts = [tn("report.times", g["n"])] if g["n"] else []
        if g["dur"]:
            parts.append(t("report.total_time", dur=fdur(g["dur"])))
        if key in open_now:
            parts.append(t("report.still_open"))
        lines.append(f"{MARKS[g['worst'] if g['worst'] != 'OK' else 'WARN']} {md(g['section'])} · "
                     f"**{md(g['label'])}**: {', '.join(parts)}")
    return lines, len(opened), n_prev


def build_weekly(label: str, r: ReportContext, apps: dict[str, str], modules: list, test: bool = False) -> dict:
    start = fdate(r.now - WEEK, r.tz, "fmt.day")
    end = fdate(r.now, r.tz, "fmt.day")
    alert_lines, n_alerts, n_prev = weekly_alert_lines(r)
    level, _, _ = overall(r.metrics)
    ups = [u for h in r.window(r.now - WEEK, r.now) for app, u in (h.get("up") or {}).items()
           if r.channels.route(f"app.{app}.up") == r.route]
    avail = t("report.weekly_up", pct=fnum(sum(1 for c, _ in ups if 200 <= c < 300) * 100 / len(ups), 2)) if ups else ""
    prefix = t("report.test_prefix") if test else ""
    title = f"{MARKS[level]} {prefix}{t('report.weekly_title', start=start, end=end, alerts=tn('report.n_alerts', n_alerts), up=avail)}"
    problems = problem_lines(r.metrics)
    description = (t("report.attention") + "\n" + "\n".join(problems)) if problems else t("report.all_ok_now")
    fields = [{"name": t("report.weekly_alerts"),
               "value": clip(join_blocks([tn("report.weekly_alerts_head", n_alerts, prev=n_prev)] + alert_lines), 1020)}]
    for sec, app in apps.items():
        lines = []
        for mod in modules:
            lines += mod.weekly_app_lines(r, app)
        lines = [line for _, line in sorted(lines, key=lambda x: x[0])]
        if lines:
            fields.append({"name": sec, "value": r.bullets(lines)})
        for mod in modules:
            fields += mod.weekly_app_fields(r, sec, app)
    extra = []
    for mod in modules:
        extra += mod.weekly_fields(r)
    fields += [f for _, f in sorted(extra, key=lambda x: x[0])]
    return {"_tag": prefix + t("report.tag.weekly"), "_type": "test" if test else "weekly", "_level": level,
            "author": {"name": clip(label, 250)}, "title": clip(title, 250),
            "description": clip(description, 3800), "color": COLORS[level], "fields": fields[:24]}


# -- dry run -------------------------------------------------------------------------


def print_report(label: str, metrics: list[Metric], changes: list[Change], cfg: dict, problems: list[str],
                 top: list[str], tz, deploy: bool, now: float, server: str, maintenance: str, channels=None,
                 modules: list[str] | None = None, muted_until: float | None = None,
                 next_reports: tuple | None = None):
    print(f"=== {label} · {datetime.fromtimestamp(now, tz).strftime(t('fmt.full'))} · server-health {__version__} ===")
    if modules is not None:
        print(t("report.dry.modules", names=", ".join(modules) or "-"))
    if muted_until:
        print(t("report.dry.muted", until=fdate(muted_until, tz)))
    if next_reports is not None:
        def when(ts):
            if ts is None:
                return t("report.dry.off")
            return t("report.dry.next_round") if ts <= now else fdate(ts, tz)
        print(t("report.dry.next", daily=when(next_reports[0]), weekly=when(next_reports[1])))
    if deploy:
        print(t("report.dry.deploy"))
    for sec in section_order(metrics, server, maintenance):
        print(f"\n[{sec}]")
        for m in metrics:
            if m.section != sec:
                continue
            lv = level_name(m.effective if not m.summary_only else "OK").upper()
            raw = t("report.dry.measured", level=level_name(m.level).upper()) \
                if m.level != m.effective and not m.summary_only else ""
            print(f"  {lv:8} {m.label}: {m.value}{raw}")
            if m.detail and m.level != "OK":
                print(f"           {m.detail}")
    if top:
        print("\n" + t("report.who_memory") + ":")
        for item in top:
            print(f"  {item}")
    print("\n" + (t("report.dry.would") if changes else t("report.dry.nothing")))
    for c in changes:
        print("  " + c.text(tz).replace("\n", "\n  "))
    ok, absent, absent_main = t("report.dry.ok"), t("report.dry.absent"), t("report.dry.absent").upper()
    line = (t("report.dry.channels") + f": Discord={ok if cfg.get('SERVER_HEALTH_DISCORD_WEBHOOK') else absent_main} "
            f"Sentry={ok if cfg.get('SERVER_HEALTH_SENTRY_DSN') else absent} "
            f"Ping={ok if cfg.get('SERVER_HEALTH_PING_URL') else absent}")
    if cfg.get("SERVER_HEALTH_WEBHOOK_URL"):
        line += f" Webhook={ok}"
    print("\n" + line)
    if channels and channels.own:
        print(t("report.dry.discord_channels", apps=", ".join(channels.own)))
    for key in channels.unused if channels else []:
        print(t("report.dry.config_warning", problem=t("report.dry.unused_hook", key=key)))
    for p in problems:
        print(t("report.dry.config_warning", problem=p))


register({
    "en": {
        "report.test_prefix": "TEST · ",
        "report.tag.crit": "CRITICAL",
        "report.tag.warn": "WARNING",
        "report.tag.normal": "BACK TO NORMAL",
        "report.tag.still": "STILL OPEN",
        "report.tag.quiet": "QUIET HOURS WARNINGS",
        "report.tag.changes": "CHANGES",
        "report.tag.summary": "SUMMARY",
        "report.tag.weekly": "WEEK",
        "report.n_changes.one": "{n} change",
        "report.n_changes.other": "{n} changes",
        "report.quiet_header": "**During the quiet hours:**",
        "report.who_memory": "Who uses the memory",
        "report.open_now": "Open now",
        "report.open_none": "nothing: everything within the thresholds",
        "report.all_ok": "everything within the thresholds",
        "report.all_ok_sentence": "Everything within the thresholds.",
        "report.all_ok_now": "Everything within the thresholds now.",
        "report.n_crit.one": "{n} critical",
        "report.n_crit.other": "{n} critical",
        "report.n_warn.one": "{n} warning",
        "report.n_warn.other": "{n} warnings",
        "report.attention": "**Needs attention**",
        "report.summary_title": "Summary of {date} · {state}",
        "report.last_24h": "**Last 24h**",
        "report.numbers_of_day": "{section} · numbers of the day",
        "report.no_history": "no history yet (samples start with the first cron round)",
        "report.prev_week_number": "previous week: {n}",
        "report.prev_week_pct": "{pct}% vs previous week",
        "report.times.one": "{n} time",
        "report.times.other": "{n} times",
        "report.total_time": "{dur} in total",
        "report.still_open": "still open",
        "report.weekly_up": " · {pct}% up",
        "report.n_alerts.one": "{n} alert",
        "report.n_alerts.other": "{n} alerts",
        "report.weekly_title": "Week from {start} to {end} · {alerts}{up}",
        "report.weekly_alerts": "Alerts of the week",
        "report.weekly_alerts_head.one": "{n} alert opened this week · previous week: {prev}",
        "report.weekly_alerts_head.other": "{n} alerts opened this week · previous week: {prev}",
        "report.dry.modules": "Modules: {names}",
        "report.dry.muted": "(alerts muted until {until})",
        "report.dry.next": "Next daily summary: {daily} · next weekly report: {weekly}",
        "report.dry.off": "off",
        "report.dry.next_round": "on the next round",
        "report.dry.deploy": "(deploy running: RAM, swap and load ignored this round)",
        "report.dry.measured": " (measured: {level})",
        "report.dry.would": "Would be alerted now:",
        "report.dry.nothing": "Nothing would be alerted now.",
        "report.dry.ok": "ok",
        "report.dry.absent": "absent",
        "report.dry.channels": "Channels",
        "report.dry.discord_channels": "Discord channels: server, {apps}",
        "report.dry.config_warning": "CONFIG WARNING: {problem}",
        "report.dry.unused_hook": "{key} matches no app; its alerts go to the server channel",
    },
    "pt-BR": {
        "report.test_prefix": "TESTE · ",
        "report.tag.crit": "CRÍTICO",
        "report.tag.warn": "AVISO",
        "report.tag.normal": "NORMALIZOU",
        "report.tag.still": "SEGUE",
        "report.tag.quiet": "AVISOS DA MADRUGADA",
        "report.tag.changes": "MUDANÇAS",
        "report.tag.summary": "RESUMO",
        "report.tag.weekly": "SEMANA",
        "report.n_changes.one": "{n} mudança",
        "report.n_changes.other": "{n} mudanças",
        "report.quiet_header": "**Durante o silêncio da madrugada:**",
        "report.who_memory": "Quem usa a memória",
        "report.open_now": "Abertos agora",
        "report.open_none": "nada: tudo dentro dos limiares",
        "report.all_ok": "tudo dentro dos limiares",
        "report.all_ok_sentence": "Tudo dentro dos limiares.",
        "report.all_ok_now": "Tudo dentro dos limiares agora.",
        "report.n_crit.one": "{n} crítico",
        "report.n_crit.other": "{n} críticos",
        "report.n_warn.one": "{n} aviso",
        "report.n_warn.other": "{n} avisos",
        "report.attention": "**Precisa de atenção**",
        "report.summary_title": "Resumo de {date} · {state}",
        "report.last_24h": "**Últimas 24h**",
        "report.numbers_of_day": "{section} · números do dia",
        "report.no_history": "sem histórico ainda (as amostras começam na primeira rodada do cron)",
        "report.prev_week_number": "semana anterior: {n}",
        "report.prev_week_pct": "{pct}% vs semana anterior",
        "report.times.one": "{n} vez",
        "report.times.other": "{n} vezes",
        "report.total_time": "{dur} no total",
        "report.still_open": "ainda aberto",
        "report.weekly_up": " · {pct}% no ar",
        "report.n_alerts.one": "{n} alerta",
        "report.n_alerts.other": "{n} alertas",
        "report.weekly_title": "Semana de {start} a {end} · {alerts}{up}",
        "report.weekly_alerts": "Alertas da semana",
        "report.weekly_alerts_head.one": "{n} alerta aberto na semana · semana anterior: {prev}",
        "report.weekly_alerts_head.other": "{n} alertas abertos na semana · semana anterior: {prev}",
        "report.dry.modules": "Módulos: {names}",
        "report.dry.muted": "(alertas silenciados até {until})",
        "report.dry.next": "Próximo resumo diário: {daily} · próximo relatório semanal: {weekly}",
        "report.dry.off": "desligado",
        "report.dry.next_round": "na próxima rodada",
        "report.dry.deploy": "(deploy em andamento: RAM, swap e load ignorados nesta rodada)",
        "report.dry.measured": " (medido: {level})",
        "report.dry.would": "Seria avisado agora:",
        "report.dry.nothing": "Nada seria avisado agora.",
        "report.dry.ok": "ok",
        "report.dry.absent": "ausente",
        "report.dry.channels": "Canais",
        "report.dry.discord_channels": "Canais do Discord: servidor, {apps}",
        "report.dry.config_warning": "AVISO da config: {problem}",
        "report.dry.unused_hook": "{key} não corresponde a nenhuma app; os alertas dela vão para o canal do servidor",
    },
})
