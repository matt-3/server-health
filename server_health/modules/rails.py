"""Rails apps seen from outside, through Solid Queue's tables: worker, dispatcher and
scheduler alive, failed jobs, a NEW error in a job, queues waiting, scheduled jobs
late, and recurring tasks out of their rhythm.

Only runs where the tables exist; an app without Solid Queue is simply skipped."""

from __future__ import annotations

import re

from ..i18n import fdur, fnum, t
from ..metric import Metric, max_level
from ..module import Module
from ..parse import PSQL_MARK, to_float
from .postgres import q

WEEKDAY_WORDS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "week")


def schedule_interval(schedule: str) -> int | None:
    """Interval in seconds of a recurring.yml schedule (Fugit text or cron, with or
    without a time zone at the end). None when it cannot be told."""
    text = (schedule or "").strip().lower()
    fields = text.split()
    if len(fields) >= 5 and all(re.fullmatch(r"[\d*/,\-]+", f) for f in fields[:5]):
        minute, hour, dom, month, dow = fields[:5]
        if month != "*" or (dom != "*" and dow == "*"):
            return 31 * 86400
        if dow != "*":
            return 7 * 86400
        if hour == "*" or hour.startswith("*/"):
            step = int(hour[2:]) if hour.startswith("*/") and hour[2:].isdigit() else 1
            if minute.startswith("*/") or minute == "*":
                m = int(minute[2:]) if minute.startswith("*/") and minute[2:].isdigit() else 1
                return m * 60
            return step * 3600
        return 86400
    if "every month" in text or "monthly" in text:
        return 31 * 86400
    if any(w in text for w in WEEKDAY_WORDS) or "weekly" in text:
        return 7 * 86400
    m = re.search(r"every (\d+) (minute|hour|day)s?", text)
    if m:
        return int(m.group(1)) * {"minute": 60, "hour": 3600, "day": 86400}[m.group(2)]
    if "every minute" in text:
        return 60
    if "every hour" in text or "hourly" in text:
        return 3600
    if "every day" in text or "daily" in text or re.search(r"\bat \d", text):
        return 86400
    return None


class Rails(Module):
    name = "rails"
    order = 80
    requires = ("postgres",)
    thresholds = {
        "SQ_HEARTBEAT_WARN_S": 180,
        "SQ_HEARTBEAT_CRIT_S": 300,
        "SQ_HEARTBEAT_PERSIST_MIN": 5,
        "SQ_FAILED_WARN": 1,
        "SQ_FAILED_CRIT": 20,
        "SQ_QUEUE_AGE_WARN_S": 600,
        "SQ_QUEUE_AGE_CRIT_S": 1800,
        # A job error is "new" when that job class fails with an exception class that
        # has not shown up for this many days (or ever).
        "JOB_ERROR_NEW_DAYS": 7,
        # A daily, weekly or monthly task without running: its interval plus this grace.
        "TASK_GRACE_H": 2,
    }

    def service_queries(self, ctx, svc):
        return [q("'has_sq'", "SELECT to_regclass('public.solid_queue_processes') IS NOT NULL"),
                q("'has_rt'", "SELECT to_regclass('public.solid_queue_recurring_tasks') IS NOT NULL")]

    def app_queries(self, ctx, app, svc):
        flags = ctx.db_flags.get(svc) or {}
        if not flags.get("has_sq"):
            return []
        stmts = [
            f"SELECT '{PSQL_MARK}', 'sq_hb:' || kind, extract(epoch FROM now() - max(last_heartbeat_at))::bigint::text "
            "FROM solid_queue_processes GROUP BY kind;",
            q("'sq_failed_30m'", "SELECT count(*) FROM solid_queue_failed_executions "
                                 "WHERE created_at > now() - interval '30 minutes'"),
            q("'sq_failed_24h'", "SELECT count(*) FROM solid_queue_failed_executions "
                                 "WHERE created_at > now() - interval '24 hours'"),
            f"SELECT '{PSQL_MARK}', 'sq_failed_class:' || j.class_name, count(*)::text "
            "FROM solid_queue_failed_executions f JOIN solid_queue_jobs j ON j.id = f.job_id "
            "WHERE f.created_at > now() - interval '30 minutes' GROUP BY j.class_name ORDER BY count(*) DESC LIMIT 3;",
            f"SELECT '{PSQL_MARK}', 'sq_ready:' || queue_name, count(*)::text || ' ' || "
            "extract(epoch FROM now() - min(created_at))::bigint::text FROM solid_queue_ready_executions GROUP BY queue_name;",
            q("'sq_sched_overdue'", "SELECT count(*) FROM solid_queue_scheduled_executions "
                                    "WHERE scheduled_at < now() - interval '5 minutes'"),
            # The exception class comes out of the `error` JSON by regular expression: a
            # `::json` on a malformed row would break the whole query.
            f"SELECT '{PSQL_MARK}', 'sq_fail_kind:' || j.class_name || '#' || "
            "coalesce(substring(f.error from '\"exception_class\":\\s*\"([^\"]+)\"'), '?'), "
            "count(*)::text || ' ' || extract(epoch FROM max(f.created_at))::bigint::text "
            "FROM solid_queue_failed_executions f JOIN solid_queue_jobs j ON j.id = f.job_id "
            "WHERE f.created_at > now() - interval '1 day' GROUP BY 2;",
        ]
        if ctx.weekly:
            stmts.append(
                f"SELECT '{PSQL_MARK}', 'sq_failed_week:' || j.class_name, count(*)::text "
                "FROM solid_queue_failed_executions f JOIN solid_queue_jobs j ON j.id = f.job_id "
                "WHERE f.created_at > now() - interval '7 days' GROUP BY j.class_name ORDER BY count(*) DESC LIMIT 10;")
        if flags.get("has_rt"):
            stmts += [
                q("'sq_rt_count'", "SELECT count(*) FROM solid_queue_recurring_tasks"),
                f"SELECT '{PSQL_MARK}', 'sq_task:' || key, schedule FROM solid_queue_recurring_tasks;",
                # Lateness by each task's usual interval (median of the gaps), without
                # reading the schedule: works for any recurring.yml.
                "WITH r AS (SELECT e.task_key, e.run_at, e.run_at - lag(e.run_at) OVER "
                "(PARTITION BY e.task_key ORDER BY e.run_at) AS gap FROM solid_queue_recurring_executions e "
                "JOIN solid_queue_recurring_tasks t ON t.key = e.task_key WHERE e.run_at > now() - interval '8 days') "
                f"SELECT '{PSQL_MARK}', 'sq_rec:' || task_key, extract(epoch FROM now() - max(run_at))::bigint::text "
                "|| ' ' || coalesce(extract(epoch FROM percentile_cont(0.5) WITHIN GROUP (ORDER BY gap))::bigint, -1)::text "
                "|| ' ' || count(*)::text FROM r GROUP BY task_key;",
            ]
        return stmts

    def _where(self, ctx, app) -> str:
        conf = ctx.app_config(app)
        return t("rails.see", path=conf.jobs_dashboard) if conf and conf.jobs_dashboard else t("rails.see_generic")

    def app_results(self, ctx, app, res):
        th = ctx.th
        flags = ctx.db_flags.get(ctx.app_service.get(app, ""), {}) or {}
        hb = {k.split(":", 1)[1]: int(to_float(v[0]) or 0) for k, v in res.items() if k.startswith("sq_hb:")}
        need = ["Worker", "Dispatcher"]
        if flags.get("has_rt") and int(to_float(res.get("sq_rt_count", ["0"])[0]) or 0) > 0:
            need.append("Scheduler")
        parts, level, missing = [], "OK", []
        for kind in need:
            ages = [age for k, age in hb.items() if k.startswith(kind)]
            if not ages:
                missing.append(kind.lower())
                level = "CRIT"
                continue
            age = min(ages)
            parts.append(f"{kind.lower()} {fdur(age)}")
            if age >= th["SQ_HEARTBEAT_CRIT_S"]:
                level = "CRIT"
            elif age >= th["SQ_HEARTBEAT_WARN_S"]:
                level = max_level([level, "WARN"])
        value = t("rails.hb", parts=" · ".join(parts)) if parts else ""
        if missing:
            value = (value + " · " if value else "") + t("rails.hb_missing", kinds=", ".join(missing))
        ctx.add(Metric(f"app.{app}.sq", app, "Solid Queue", value, level, t("rails.hb_detail"),
                       persist_min=th["SQ_HEARTBEAT_PERSIST_MIN"], crit_persist_min=th["SQ_HEARTBEAT_PERSIST_MIN"]))

        if ctx.weekly:
            ctx.jobs_week[app] = sorted(((k.split(":", 1)[1], int(to_float(v[0]) or 0)) for k, v in res.items()
                                         if k.startswith("sq_failed_week:")), key=lambda kv: -kv[1])
        failed = int(to_float(res.get("sq_failed_30m", ["0"])[0]) or 0)
        f24 = int(to_float(res.get("sq_failed_24h", ["0"])[0]) or 0)
        ctx.facts.setdefault("sqf", {})[app] = failed
        self.new_job_errors(ctx, app, res)
        classes = [f"{k.split(':', 1)[1]} {v[0]}" for k, v in res.items() if k.startswith("sq_failed_class:")]
        flevel = "CRIT" if failed >= th["SQ_FAILED_CRIT"] else "WARN" if failed >= th["SQ_FAILED_WARN"] else "OK"
        ftxt = t("rails.failed.value", n30=failed, n24=f24) + (f" ({', '.join(classes)})" if classes else "")
        ctx.add(Metric(f"app.{app}.sq_failed", app, t("rails.failed"), ftxt, flevel,
                       t("rails.failed.detail", where=self._where(ctx, app))))

        queues, qlevel = [], "OK"
        for k, v in sorted(res.items()):
            if not k.startswith("sq_ready:"):
                continue
            count, age = (v[0].split() + ["0", "0"])[:2]
            age_s = int(to_float(age) or 0)
            queues.append(t("rails.queue", name=k.split(":", 1)[1], n=count, age=fdur(age_s)))
            if age_s >= th["SQ_QUEUE_AGE_CRIT_S"]:
                qlevel = "CRIT"
            elif age_s >= th["SQ_QUEUE_AGE_WARN_S"]:
                qlevel = max_level([qlevel, "WARN"])
        overdue = int(to_float(res.get("sq_sched_overdue", ["0"])[0]) or 0)
        if overdue:
            queues.append(t("rails.overdue", n=overdue))
            qlevel = max_level([qlevel, "WARN"])
        ctx.add(Metric(f"app.{app}.sq_queues", app, t("rails.queues"), " · ".join(queues) or t("rails.queues.empty"),
                       qlevel, t("rails.queues.detail")))

        late, ontime = [], 0
        for k, v in res.items():
            if not k.startswith("sq_rec:"):
                continue
            try:
                age, median, count = (int(x) for x in v[0].split())
            except ValueError:
                continue
            if count < 3 or median <= 0:
                continue  # a daily or new task: not enough samples to know its rhythm
            if age > 2 * median + 120:
                late.append(t("rails.recurring.late_item", task=k.split(":", 1)[1], age=fdur(age), usual=fdur(median)))
            else:
                ontime += 1
        if late or ontime:
            ctx.add(Metric(f"app.{app}.sq_recurring", app, t("rails.recurring"),
                           t("rails.recurring.late", items="; ".join(late)) if late else t("rails.on_time", n=ontime),
                           "WARN" if late else "OK", t("rails.recurring.detail")))
        self.long_tasks(ctx, app, res)

    def new_job_errors(self, ctx, app, res):
        """An event when a job class fails with an exception that has not shown up for
        JOB_ERROR_NEW_DAYS days. The first time the monitor sees the app, it only learns
        what is already in the failed list, without alerting."""
        # The "<app>|" mark says the app was already seen; without it, the round only learns.
        known = dict(ctx.state.get("job_errors") or {})
        learning = f"{app}|" not in known
        upd = ctx.updates.setdefault("job_errors", dict(known))
        new = []
        days = ctx.th["JOB_ERROR_NEW_DAYS"]
        for k, v in res.items():
            if not k.startswith("sq_fail_kind:"):
                continue
            kind = k.split(":", 1)[1]
            count, last = (v[0].split() + ["0", "0"])[:2]
            last_ts = int(to_float(last) or 0)
            prev = known.get(f"{app}|{kind}")
            if not learning and (prev is None or last_ts - prev > days * 86400):
                job, _, exc = kind.partition("#")
                new.append(f"{job}: {exc} ({count}x)")
            upd[f"{app}|{kind}"] = max(last_ts, prev or 0)
        upd[f"{app}|"] = int(ctx.now)
        cutoff = ctx.now - 30 * 86400
        ctx.updates["job_errors"] = {k: v for k, v in upd.items() if v >= cutoff}
        if new:
            ctx.add(Metric(f"app.{app}.job_error", app, t("rails.new_error"), "; ".join(new[:5]), "WARN",
                           t("rails.new_error.detail", days=days, where=self._where(ctx, app)), event=True))

    def long_tasks(self, ctx, app, res):
        """Daily, weekly and monthly tasks: Solid Queue deletes the execution together with
        the finished job, so the monitor keeps the last one it saw. A task it never saw
        run counts from the first time it saw it scheduled."""
        runs = dict(ctx.state.get("task_runs") or {})
        upd: dict[str, int] = {}
        late, ontime = [], 0
        names = {86400: t("rails.daily"), 604800: t("rails.weekly"), 2678400: t("rails.monthly")}
        for k, v in res.items():
            if not k.startswith("sq_task:"):
                continue
            task = k.split(":", 1)[1]
            interval = schedule_interval(v[0])
            if not interval or interval < 6 * 3600:
                continue  # the ones of minutes and hours have the rhythm measured above
            key = f"{app}|{task}"
            rec = res.get(f"sq_rec:{task}")
            last = runs.get(key)
            if rec:
                age = int(to_float(rec[0].split()[0]) or 0)
                last = max(last or 0, int(ctx.now - age))
            last = last or int(ctx.now)
            upd[key] = last
            if ctx.now - last > interval + ctx.th["TASK_GRACE_H"] * 3600:
                kind = names.get(interval, t("rails.every", interval=fdur(interval)))
                late.append(t("rails.long.late_item", task=task, kind=kind, age=fdur(ctx.now - last)))
            else:
                ontime += 1
        # Other apps' tasks stay, unless not seen for 62 days (twice a monthly task):
        # an app or a task that was removed.
        other = {k: v for k, v in runs.items() if not k.startswith(f"{app}|") and v >= ctx.now - 62 * 86400}
        ctx.updates["task_runs"] = {**ctx.updates.get("task_runs", other), **upd}
        if late or ontime:
            ctx.add(Metric(f"app.{app}.sq_long_tasks", app, t("rails.long"),
                           t("rails.long.late", items="; ".join(late)) if late else t("rails.on_time", n=ontime),
                           "WARN" if late else "OK", t("rails.long.detail", where=self._where(ctx, app))))

    def weekly_app_lines(self, r, app):
        jobs = r.jobs_week.get(app)
        if jobs:
            top = ", ".join(f"{c} {n}" for c, n in jobs[:3])
            return [(40, t("rails.weekly", n=fnum(sum(n for _, n in jobs)), top=top))]
        if jobs == []:
            return [(40, t("rails.weekly.none"))]
        return []

    strings = {
        "en": {
            "rails.see": "see {path}",
            "rails.see_generic": "see the app's jobs dashboard",
            "rails.hb": "heartbeat {parts}",
            "rails.hb_missing": "NO {kinds}",
            "rails.hb_detail": "a Solid Queue process with no heartbeat: jobs and scheduled tasks do not run",
            "rails.failed": "Failed jobs",
            "rails.failed.value": "{n30} in 30 min · {n24} in 24h",
            "rails.failed.detail": "{where} for each one's error",
            "rails.queue": "{name} {n} (oldest {age})",
            "rails.overdue": "{n} scheduled job(s) late",
            "rails.queues": "Queues",
            "rails.queues.empty": "empty",
            "rails.queues.detail": "a job waiting for long: worker stopped, slow or without a free thread",
            "rails.recurring": "Recurring tasks",
            "rails.recurring.late": "late: {items}",
            "rails.recurring.late_item": "{task} (last {age} ago, usually every {usual})",
            "rails.recurring.detail": "the task did not run at its usual rhythm: check the scheduler",
            "rails.on_time": "{n} on time",
            "rails.new_error": "New error in a job",
            "rails.new_error.detail": "a job class failing with an error that has not shown up for {days} days; the "
                                      "message and the backtrace are in the failed jobs ({where})",
            "rails.daily": "daily",
            "rails.weekly": "Failed jobs: {n} ({top})",
            "rails.weekly.none": "Failed jobs: 0",
            "rails.monthly": "monthly",
            "rails.every": "every {interval}",
            "rails.long": "Daily and weekly tasks",
            "rails.long.late": "stopped: {items}",
            "rails.long.late_item": "{task} ({kind}, not run for {age})",
            "rails.long.detail": "the scheduler is up, but the task did not run on time: it failed, hung or left "
                                 "`recurring.yml`; {where}",
        },
        "pt-BR": {
            "rails.see": "veja em {path}",
            "rails.see_generic": "veja no painel de jobs da app",
            "rails.hb": "sinal há {parts}",
            "rails.hb_missing": "SEM {kinds}",
            "rails.hb_detail": "processo do Solid Queue sem sinal de vida: jobs e tarefas agendadas não rodam",
            "rails.failed": "Jobs com falha",
            "rails.failed.value": "{n30} em 30 min · {n24} em 24h",
            "rails.failed.detail": "{where} o erro de cada um",
            "rails.queue": "{name} {n} (mais antigo {age})",
            "rails.overdue": "{n} agendado(s) atrasado(s)",
            "rails.queues": "Filas",
            "rails.queues.empty": "vazias",
            "rails.queues.detail": "job esperando há muito tempo: worker parado, lento ou sem thread livre",
            "rails.recurring": "Tarefas recorrentes",
            "rails.recurring.late": "atrasadas: {items}",
            "rails.recurring.late_item": "{task} (última há {age}, costuma {usual})",
            "rails.recurring.detail": "a tarefa não rodou no ritmo de sempre: confira o scheduler",
            "rails.on_time": "{n} em dia",
            "rails.new_error": "Erro novo em job",
            "rails.new_error.detail": "classe de job falhando com um erro que não aparecia há {days} dias; a mensagem e "
                                      "o backtrace estão nos jobs com falha ({where})",
            "rails.daily": "diária",
            "rails.weekly": "Jobs com falha: {n} ({top})",
            "rails.weekly.none": "Jobs com falha: 0",
            "rails.monthly": "mensal",
            "rails.every": "a cada {interval}",
            "rails.long": "Tarefas diárias e semanais",
            "rails.long.late": "paradas: {items}",
            "rails.long.late_item": "{task} ({kind}, sem rodar há {age})",
            "rails.long.detail": "o scheduler está de pé, mas a tarefa não rodou no prazo: falhou, travou ou saiu do "
                                 "`recurring.yml`; {where}",
        },
    }
