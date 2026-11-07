"""The command line and the round itself."""

from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import __version__, http, i18n
from .appconfig import format_value, indicator_level, load_app_config
from .channels import Channels
from .config import CORE_THRESHOLDS, DEFAULT_CONFIG_FILE, is_true, load_config, load_thresholds
from .context import Context
from .evaluator import Evaluator
from .i18n import fdate, fnum, register, t
from .metric import EVENT, LEVELS, NEW, OPENING, REMINDER, Change, Metric, level_name, max_level
from .module import PHASES, builtin_modules, load_plugins, resolve
from .notify import build as build_notifiers
from .parse import in_hours, parse_duration
from .report import (ReportContext, alert_rows, build_alert, build_summary, build_weekly, overall, print_report)
from .source import FixtureSource, SystemSource
from .store import HISTORY_KEEP_DAYS, Store, acquire_lock, make_logger
from .text import plain

SIMULATE_KINDS = ("memory", "disk", "worker", "app", "indicator", "oom")
# State keys a module writes through ctx.updates that are saved only by the daily summary.
DAILY_ONLY = ("tables",)


def setup(args, env: dict):
    """Config, language, modules and thresholds. Returns everything a mode needs."""
    path = args.config or env.get("SERVER_HEALTH_CONFIG", DEFAULT_CONFIG_FILE)
    cfg, problems = load_config(path, env)
    i18n.set_lang(cfg.get("SERVER_HEALTH_LANG"))
    builtins = builtin_modules()
    spec = cfg.get("SERVER_HEALTH_MODULES", "auto")
    wanted = {p.strip() for p in spec.split(",") if p.strip() and not p.strip().startswith("-")} - \
        {m.name for m in builtins} - {"auto"}
    plugins, plugin_problems = load_plugins(cfg["SERVER_HEALTH_MODULES_DIR"], wanted)
    available = builtins + plugins
    thresholds = dict(CORE_THRESHOLDS)
    for m in available:
        i18n.register(m.strings)
        for k, v in m.defaults.items():
            cfg.setdefault(k, v)
        thresholds.update(m.thresholds)
    # Problems were written in English before the language was known: redo them.
    cfg_again, problems = load_config(path, env)
    for k, v in cfg_again.items():
        cfg.setdefault(k, v)
    th = load_thresholds(cfg, thresholds)
    src = FixtureSource(args.fixtures) if args.fixtures else SystemSource()
    enabled, module_problems = resolve(spec, available, src, cfg)
    return cfg, problems + plugin_problems + module_problems, th, src, enabled, available


def collect(ctx: Context, modules: list) -> list[Metric]:
    ctx.modules = modules
    ctx.enabled = [m.name for m in modules]

    def phase(name, *args):
        for m in modules:
            getattr(m, name)(ctx, *args)

    phase("before")
    phase("host")
    phase("discover")
    ctx.load_app_configs(ctx.apps, ctx.dokku_ok)
    phase("services")
    for app in ctx.apps:
        phase("app", app)
    if ctx.daily:
        phase("daily")
    for app in ctx.apps:
        phase("after_app", app)
    phase("finish")
    ctx.name_sections()
    return ctx.metrics


def simulate(kind: str, metrics: list[Metric], server: str) -> Change:
    targets = {
        "memory": ("host.memory", server, t("host.memory"), "CRIT", t("sim.memory")),
        "disk": ("host.disk", server, t("host.disk"), "WARN", t("sim.disk")),
        "worker": ("app.?.sq", "?", "Solid Queue", "CRIT", t("sim.worker")),
        "app": ("app.?.ps", "?", t("dokku.processes"), "CRIT", t("sim.app")),
        "indicator": ("ind.?.example", f"? · {t('section.product')}", t("sim.indicator.label"), "WARN", t("sim.indicator")),
        "oom": ("host.oom", server, "OOM kill", "CRIT", t("sim.oom")),
    }
    key, section, label, level, value = targets[kind]
    ps = next((m for m in metrics if m.key.startswith("app.") and m.key.endswith(".ps")), None)
    app, sec = (ps.key.split(".")[1], ps.section) if ps else ("app", "app")
    key, section = key.replace("?", app), section.replace("?", sec)
    m = Metric(key, section, label, value, level, t("sim.detail"))
    m.effective = level
    metrics[:] = [x for x in metrics if x.key != key] + [m]
    return Change(m, EVENT if kind == "oom" else NEW, "OK", level)


def check_app_mode(path: str, src, cfg: dict, th: dict, modules: list) -> int:
    conf, errors = load_app_config(path)
    if errors:
        print(t("cli.check.problems"))
        for e in errors:
            print(f"  - {e}")
    if not conf:
        return 3
    print(t("cli.check.app", app=conf.app, n=len(conf.indicators)))
    from .modules.postgres import Postgres, connection, q
    from .parse import dokku_names, parse_psql, to_float
    ctx = Context(src, cfg, th, {}, [], lambda m: None)
    ctx.app_configs = [conf]
    rc, out, _ = src.run("dokku_apps", ["dokku", "apps:list"], timeout=30)
    if rc == 0:
        ctx.dokku_ok, ctx.dokku_apps = True, set(dokku_names(out))
    elif not (conf.database_url or conf.database_container):
        print(t("cli.check.no_dokku"))
        return 1 if errors else 0
    Postgres.connections(ctx)
    svc = Postgres().app_db(ctx, conf.app)
    if not svc:
        print(t("cli.check.no_db", app=conf.app))
        return 3
    print(t("cli.check.db", svc=svc) + "\n")
    failed = 0
    for ind in conf.indicators:
        script = "\n".join(["\\set QUIET on", "\\pset format unaligned", "\\pset tuples_only on",
                            "\\pset fieldsep '|'", "SET default_transaction_read_only = on;",
                            "SET statement_timeout = '5s';", q(f"'ind:{ind.key}'", ind.sql)]) + "\n"
        t0 = time.time()
        argv, env = connection(ctx, svc)
        rc, out, err = src.run(f"psql_{svc}_check_{ind.key}", argv, timeout=60, stdin=script, env=env)
        rows = parse_psql(out)
        ms = (time.time() - t0) * 1000
        value = to_float(rows[0][1] or "0") if rows else None
        if value is not None:
            lvl_txt = level_name(indicator_level(ind, value)).upper() if ind.when == "always" else t("cli.check.summary")
            print(f"  ok    {ind.key}: {format_value(ind.format, value)} [{lvl_txt}] {t('cli.check.in')} {fnum(ms)} ms")
        else:
            failed += 1
            msg = (err or out or t("cli.check.no_output")).strip().splitlines()
            print(f"  {t('cli.check.error')}  {ind.key}: {' / '.join(msg[-3:])[:300]}")
    return 2 if failed else (1 if errors else 0)


def list_modules(available, enabled, cfg, problems) -> int:
    on = {m.name for m in enabled}
    print(t("cli.modules.spec", spec=cfg.get("SERVER_HEALTH_MODULES", "auto")))
    for m in available:
        req = f" ({t('cli.modules.requires')} {', '.join(m.requires)})" if m.requires else ""
        print(f"  [{'x' if m.name in on else ' '}] {m.name}{req}")
    for p in problems:
        print(t("report.dry.config_warning", problem=p))
    return 0


def main(argv: list[str] | None = None, env: dict | None = None) -> int:
    ap = argparse.ArgumentParser(prog="server-health", description="Watches a server and its Dokku apps, and alerts on "
                                 "Discord when something changes state.")
    ap.add_argument("--dry-run", action="store_true", help="print the report and what would be alerted; send nothing")
    ap.add_argument("--force", action="store_true", help="send the current summary now, marked as a test")
    ap.add_argument("--force-weekly", action="store_true", help="send the weekly report now, marked as a test")
    ap.add_argument("--simulate", choices=SIMULATE_KINDS, help="send a test alert")
    ap.add_argument("--check-app", metavar="TOML", help="validate an app's TOML and run each query once")
    ap.add_argument("--list-modules", action="store_true", help="show the modules and which ones are on")
    ap.add_argument("--mute", metavar="DURATION", help="mute alerts for a while (90m, 2h, 1d); summaries still go out")
    ap.add_argument("--unmute", action="store_true", help="end the mute now")
    ap.add_argument("--dashboard", metavar="FILE", help="write the dashboard (HTML with the charts) to FILE")
    ap.add_argument("--serve", metavar="PORT", nargs="?", const="8730",
                    help="serve the dashboard on 127.0.0.1:PORT (default 8730); open it through an SSH tunnel")
    ap.add_argument("--fixtures", metavar="DIR", help=argparse.SUPPRESS)
    ap.add_argument("--config", default=None, help=f"config file (default {DEFAULT_CONFIG_FILE})")
    ap.add_argument("--version", action="version", version=f"server-health {__version__}")
    args = ap.parse_args(argv)

    env = dict(os.environ if env is None else env)
    cfg, problems, th, src, enabled, available = setup(args, env)
    tz = ZoneInfo(cfg["SERVER_HEALTH_TZ"])
    label = cfg["SERVER_HEALTH_LABEL"] or socket.gethostname()

    if args.list_modules:
        return list_modules(available, enabled, cfg, problems)
    if args.check_app:
        return check_app_mode(args.check_app, src, cfg, th, enabled)

    store = Store(cfg["SERVER_HEALTH_STATE_DIR"])
    read_only = args.dry_run or args.force or args.force_weekly or bool(args.simulate) or bool(args.dashboard) \
        or args.serve is not None
    try:
        store.ensure()
    except OSError as e:
        if not read_only:
            print(t("cli.state_dir", dir=store.dir, error=e), file=sys.stderr)
            return 3
    log = make_logger(cfg["SERVER_HEALTH_LOG"], str(store.dir), echo=False)
    http.LOG = log
    for p in problems:
        log(f"config: {p}")

    if args.dashboard or args.serve is not None:
        from .dashboard import render, serve
        if args.serve is not None:
            return serve(store, cfg, label, tz, int(args.serve))
        with open(args.dashboard, "w", encoding="utf-8") as fh:
            fh.write(render(store, cfg, label, tz, src.now()))
        print(t("cli.dashboard.written", path=args.dashboard))
        return 0

    if args.mute or args.unmute:
        lock = acquire_lock(str(store.dir), wait=30)
        state = store.load_state()
        if args.unmute:
            state.pop("mute_until", None)
            print(t("cli.mute.off"))
            log("alerts unmuted")
        else:
            secs = parse_duration(args.mute)
            if not secs:
                print(t("cli.mute.bad"), file=sys.stderr)
                return 3
            state["mute_until"] = src.now() + secs
            print(t("cli.mute.on", until=fdate(state["mute_until"], tz)))
            log(f"alerts muted until {datetime.fromtimestamp(state['mute_until'], tz).isoformat()}")
        store.save_state(state)
        if lock:
            lock.close()
        return 0

    daily_at, weekly_at, schedule_problems = schedule(cfg)
    problems = problems + schedule_problems

    def execute(mode: str, test: bool = False) -> int:
        """One collection and what follows it. mode: normal, dry, simulate, daily, weekly."""
        state = store.load_state()
        history = store.load_history()
        ctx = Context(src, cfg, th, state, history, log, daily=mode in ("daily", "weekly"), weekly=mode == "weekly")
        try:
            metrics = collect(ctx, enabled)
        except Exception as e:  # noqa: BLE001 - cron needs a line in the log, not a silent traceback
            log(f"ERROR while collecting: {type(e).__name__}: {e}")
            raise
        now = ctx.now
        top = ctx.top_memory()
        notifiers = build_notifiers(cfg, label, log)
        apps = dict(sorted(ctx.section_app.items()))
        channels = Channels(cfg, apps.values(), ctx.db_app)
        for key in channels.unused:
            log(f"config: {key} matches no app; its alerts go to the server channel")

        ev = Evaluator(state, th, now, ctx.deploy_running, ctx.deploy_recent)
        changes = ev.apply(metrics)
        level, crit, warn = overall(metrics)
        # Occurrences (OOM, restart) do not count as state in "Open now", but they count in
        # the exit code: there was a warning or a critical in this round.
        exit_code = max([LEVELS[level]] + [LEVELS[m.effective] for m in metrics if m.event])
        mute_until = state.get("mute_until") if (state.get("mute_until") or 0) > now else None

        def send(message, route="", mention=False, alert=False, attachment=None) -> bool:
            """Every configured notifier gets every message; Sentry keeps only new alerts.
            Returns whether some channel took it (Sentry does not count: it is a record)."""
            sent = False
            for n in notifiers:
                if not n.configured():
                    continue
                ok = n.send(message, route=route, mention=mention, alert=alert,
                            attachment=attachment if n.name == "discord" else None)
                sent = sent or (ok and n.name != "sentry")
            return sent

        def done(sent: bool) -> int:
            print(t("cli.sent") if sent else t("cli.not_sent"))
            return 0 if sent else 3

        if mode == "dry":
            print_report(label, metrics, changes, cfg, problems, top, tz, ctx.deploy_running, now, ctx.server_section,
                         ctx.maintenance_section, channels, modules=ctx.enabled, muted_until=mute_until,
                         next_reports=next_reports(now, tz, daily_at, weekly_at, state))
            return exit_code

        if mode == "simulate":
            change = simulate(args.simulate, metrics, ctx.server_section)
            route = channels.route(change.metric.key)
            message = build_alert(label, [change], [], channels.metrics(route, metrics), top, tz, test=True)
            return done(send(message, route=route))

        def report_ctx(route: str, alerts=None, bots=None):
            return ReportContext(metrics=channels.metrics(route, metrics), history=history, now=now, tz=tz,
                                 server_section=ctx.server_section, maintenance_section=ctx.maintenance_section,
                                 channels=channels, route=route, alerts=alerts, bots=bots,
                                 daily_numbers=state.get("daily_numbers", {}), jobs_week=ctx.jobs_week)

        today = datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d")
        if mode == "weekly":
            alerts, bots = store.load_alerts(), store.load_bots()
            attachment = None
            if is_true(cfg.get("SERVER_HEALTH_WEEKLY_DASHBOARD")):
                from .dashboard import render
                html = render(store, cfg, label, tz, now).encode("utf-8")
                attachment = (f"server-health-{today}.html", html, "text/html")
            sent = True
            for route in channels.routes():
                r = report_ctx(route, [a for a in alerts if channels.route(a.get("key", "")) == route], bots)
                message = build_weekly(label, r, channels.apps(route, apps), enabled, test=test)
                sent = send(message, route=route, attachment=attachment if not route else None) and sent
            if test:
                return done(sent)
            state["weekly_sent"] = today
            store.save_state(state)
            log(f"weekly report sent ({level})")
            return exit_code

        if mode == "daily":
            sent = True
            for route in channels.routes():
                message = build_summary(label, report_ctx(route), channels.apps(route, apps), top, enabled, test=test)
                sent = send(message, route=route) and sent
            if test:
                return done(sent)
            # The summary only saves the snapshot of the tables (for the next day's growth).
            for key in DAILY_ONLY:
                if key in ctx.updates:
                    state[key] = ctx.updates[key]
            # Each day's summary numbers, for the weekly sum.
            cutoff = datetime.fromtimestamp(now - HISTORY_KEEP_DAYS * 86400, tz).strftime("%Y-%m-%d")
            numbers = {d: v for d, v in state.get("daily_numbers", {}).items() if d >= cutoff}
            if ctx.numbers:
                numbers[today] = ctx.numbers
            state["daily_numbers"] = numbers
            state["daily_sent"] = today
            state["pruned_at"] = now
            store.save_state(state)
            store.prune_history(now)
            store.prune_alerts(now)
            log(f"daily summary sent ({level})")
            return exit_code

        # Normal round ---------------------------------------------------------------
        hour = datetime.fromtimestamp(now, tz).hour
        quiet = bool(cfg["SERVER_HEALTH_QUIET_HOURS"]) and in_hours(cfg["SERVER_HEALTH_QUIET_HOURS"], hour)
        queue = list(state.get("quiet_queue", []))
        send_now = []
        for c in changes:
            if quiet and not c.urgent:
                if c.kind == REMINDER:
                    # A warning reminder wakes nobody: it goes back to its clock and goes out later.
                    ev.next_state[c.metric.key]["alerted_at"] = state.get("metrics", {}).get(c.metric.key, {}).get(
                        "alerted_at", now)
                    continue
                stamp = datetime.fromtimestamp(now, tz).strftime("%H:%M")
                queue.append({"route": channels.route(c.metric.key), "text": f"{stamp} {c.text(tz).splitlines()[0]}"})
                continue
            send_now.append(c)
        if mute_until:
            if send_now:
                log(f"muted: {len(send_now)} change(s) not sent")
            send_now = []
        flush = [] if (quiet or mute_until) else queue
        if send_now or flush:
            for route in channels.routes():
                route_changes = [c for c in send_now if channels.route(c.metric.key) == route]
                route_queue = [q["text"] for q in flush if channels.queued_route(q["route"]) == route]
                if not route_changes and not route_queue:
                    continue
                message = build_alert(label, route_changes, route_queue, channels.metrics(route, metrics), top, tz)
                mention = any(c.after == "CRIT" and c.kind in OPENING for c in route_changes)
                alerting = any(c.kind in OPENING for c in route_changes)
                send(message, route=route, mention=mention, alert=alerting)
                if not any(n.configured() for n in notifiers):
                    log("NO channel configured: " + plain(message["title"]))
            queue = queue if quiet else []

        new_state = {k: v for k, v in state.items() if k not in ("metrics", "quiet_queue")}
        new_state.update({k: v for k, v in ctx.updates.items() if k not in DAILY_ONLY and k != "bots_round"})
        new_state.update({
            "version": __version__,
            "last_run": now,
            "deploy_running": ctx.deploy_running,
            "metrics": ev.next_state,
            "quiet_queue": queue[-50:],
        })
        if "restarts" in new_state:
            new_state["restarts"] = {k: v for k, v in new_state["restarts"].items()
                                     if now - v.get("seen", now) < 7 * 86400}
        if not mute_until:
            new_state.pop("mute_until", None)
        # The history and the alerts are trimmed once a day here too, so the files stay
        # bounded even with the daily summary turned off.
        prune = now - (state.get("pruned_at") or 0) >= 86400
        if prune:
            new_state["pruned_at"] = now
        store.save_state(new_state)
        store.append_history(ctx.facts)
        store.append_alerts(alert_rows(changes, now))
        if prune:
            store.prune_history(now)
            store.prune_alerts(now)
        for m in enabled:
            m.save(ctx, store)
        if cfg.get("SERVER_HEALTH_PING_URL"):
            code = http.get(cfg["SERVER_HEALTH_PING_URL"])
            if not 200 <= code < 300:
                log(f"ping failed (HTTP {code})")
        log(f"round: {crit} critical, {warn} warning(s), {len(send_now)} change(s) sent")
        return exit_code

    if args.dry_run:
        return execute("dry")
    if args.simulate:
        return execute("simulate")
    if args.force:
        return execute("daily", test=True)
    if args.force_weekly:
        return execute("weekly", test=True)

    lock = acquire_lock(str(store.dir), wait=0)
    if lock is None:
        log("another round is running; this one was skipped")
        return 0
    exit_code = execute("normal")
    # The daily summary and the weekly report go out on the first round after their
    # time (SERVER_HEALTH_DAILY_AT / SERVER_HEALTH_WEEKLY_AT, in SERVER_HEALTH_TZ).
    for mode in due_reports(src.now(), tz, daily_at, weekly_at, store.load_state()):
        execute(mode)
    return exit_code


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def schedule(cfg: dict) -> tuple[tuple | None, tuple | None, list[str]]:
    """(daily (h, m) or None, weekly (weekday, h, m) or None, problems). A value that
    cannot be read falls back to the default, with a config warning."""
    problems = []

    def hhmm(text: str):
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", text.strip())
        if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
            return int(m.group(1)), int(m.group(2))
        return None

    raw = (cfg.get("SERVER_HEALTH_DAILY_AT") or "").strip().lower()
    if raw == "off":
        daily = None
    else:
        daily = hhmm(raw)
        if daily is None:
            problems.append(t("cli.schedule.bad", key="SERVER_HEALTH_DAILY_AT", value=raw, example="09:00"))
            daily = (9, 0)
    raw = (cfg.get("SERVER_HEALTH_WEEKLY_AT") or "").strip().lower()
    if raw == "off":
        weekly = None
    else:
        day, _, time_part = raw.partition(" ")
        at = hhmm(time_part)
        if day[:3] in WEEKDAYS and at:
            weekly = (WEEKDAYS.index(day[:3]), *at)
        else:
            problems.append(t("cli.schedule.bad", key="SERVER_HEALTH_WEEKLY_AT", value=raw, example="sat 07:00"))
            weekly = (5, 7, 0)
    return daily, weekly, problems


def due_reports(now: float, tz, daily, weekly, state: dict) -> list[str]:
    """What is due now. Each one goes out once, on the day it is scheduled: a server off
    for the rest of that day skips it."""
    local = datetime.fromtimestamp(now, tz)
    today = local.strftime("%Y-%m-%d")
    due = []
    if daily and (local.hour, local.minute) >= daily and state.get("daily_sent") != today:
        due.append("daily")
    if weekly and local.weekday() == weekly[0] and (local.hour, local.minute) >= weekly[1:] \
            and state.get("weekly_sent") != today:
        due.append("weekly")
    return due


def next_reports(now: float, tz, daily, weekly, state: dict) -> tuple[float | None, float | None]:
    """When the next daily summary and weekly report go out (for the dry run)."""
    from datetime import timedelta
    local = datetime.fromtimestamp(now, tz)
    today = local.strftime("%Y-%m-%d")

    def first(days: range, ok) -> float | None:
        for d in days:
            day = (local + timedelta(days=d)).date()
            if ok(day):
                return day
        return None

    nd = nw = None
    if daily:
        sent_today = state.get("daily_sent") == today
        day = local.date() if not sent_today else (local + timedelta(days=1)).date()
        nd = datetime(day.year, day.month, day.day, *daily, tzinfo=tz).timestamp()
        if not sent_today and (local.hour, local.minute) >= daily:
            nd = now  # due on the next round
    if weekly:
        day = first(range(0, 8), lambda d: d.weekday() == weekly[0]
                    and not (d == local.date() and state.get("weekly_sent") == today))
        nw = datetime(day.year, day.month, day.day, *weekly[1:], tzinfo=tz).timestamp()
        if day == local.date() and (local.hour, local.minute) >= weekly[1:]:
            nw = now
    return nd, nw


register({
    "en": {
        "sim.memory": "300/3819 MB available (simulated)",
        "sim.disk": "78% (8 GB free) (simulated)",
        "sim.worker": "NO worker (simulated)",
        "sim.app": "NO process running (simulated)",
        "sim.indicator.label": "Example indicator",
        "sim.indicator": "3 (simulated)",
        "sim.oom": "1 process killed: ruby (simulated)",
        "sim.detail": "test alert: nothing really happened",
        "cli.sent": "Sent.",
        "cli.not_sent": "Not sent: check the webhook and the log.",
        "cli.state_dir": "could not create {dir}: {error}",
        "cli.check.problems": "Problems in the TOML:",
        "cli.check.app": "App `{app}`: {n} valid indicator(s)",
        "cli.check.no_dokku": "dokku is not available here: only the TOML was validated.",
        "cli.check.no_db": "Could not find the database of `{app}`: set `database` in the TOML.",
        "cli.check.db": "Database: {svc}",
        "cli.check.summary": "summary",
        "cli.check.in": "in",
        "cli.check.error": "ERROR",
        "cli.check.no_output": "no output",
        "cli.modules.spec": "SERVER_HEALTH_MODULES={spec}",
        "cli.modules.requires": "needs",
        "cli.mute.on": "Alerts muted until {until}. Summaries still go out; `--unmute` ends it.",
        "cli.mute.off": "Alerts back on.",
        "cli.mute.bad": "Duration like 90m, 2h or 1d.",
        "cli.dashboard.written": "Dashboard written to {path}",
        "cli.schedule.bad": "{key}=\"{value}\" cannot be read (like \"{example}\" or \"off\"): using the default",
    },
    "pt-BR": {
        "sim.memory": "300/3819 MB disponível (simulado)",
        "sim.disk": "78% (8 GB livre) (simulado)",
        "sim.worker": "SEM worker (simulado)",
        "sim.app": "NENHUM processo de pé (simulado)",
        "sim.indicator.label": "Indicador de exemplo",
        "sim.indicator": "3 (simulado)",
        "sim.oom": "1 processo morto: ruby (simulado)",
        "sim.detail": "alerta de teste: nada aconteceu de verdade",
        "cli.sent": "Enviado.",
        "cli.not_sent": "Não enviado: confira o webhook e o log.",
        "cli.state_dir": "não consegui criar {dir}: {error}",
        "cli.check.problems": "Problemas no TOML:",
        "cli.check.app": "App `{app}`: {n} indicador(es) válido(s)",
        "cli.check.no_dokku": "dokku indisponível aqui: só a validação do TOML foi feita.",
        "cli.check.no_db": "Não achei o banco de `{app}`: defina `database` no TOML.",
        "cli.check.db": "Banco: {svc}",
        "cli.check.summary": "resumo",
        "cli.check.in": "em",
        "cli.check.error": "ERRO",
        "cli.check.no_output": "sem saída",
        "cli.modules.spec": "SERVER_HEALTH_MODULES={spec}",
        "cli.modules.requires": "precisa de",
        "cli.mute.on": "Alertas silenciados até {until}. Os resumos continuam; `--unmute` encerra.",
        "cli.mute.off": "Alertas religados.",
        "cli.mute.bad": "Duração como 90m, 2h ou 1d.",
        "cli.dashboard.written": "Painel gravado em {path}",
        "cli.schedule.bad": "{key}=\"{value}\" não pôde ser lido (como \"{example}\" ou \"off\"): usando o padrão",
    },
})
