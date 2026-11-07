"""Dokku apps: processes, restarts outside a deploy, containers killed for memory, a
suspicious deploy, and (daily) the two upload traps that break nothing on deploy and
only show up days later: a missing storage mount and a body-size limit lower than the
app needs."""

from __future__ import annotations

import re

from ..i18n import fbytes, fdate, fdur, fnum, t
from ..metric import EVENT, Metric
from ..module import Module
from ..parse import dokku_names, parse_body_size, parse_docker_time, to_float


class Dokku(Module):
    name = "dokku"
    order = 50
    defaults = {"SERVER_HEALTH_IGNORE_APPS": ""}
    thresholds = {
        "APP_DOWN_PERSIST_MIN": 5,
        # Suspicious deploy: the 30 min after the deploy against the hour before it.
        "DEPLOY_WINDOW_MIN": 30,
        "DEPLOY_5XX_RATIO": 3.0,
        "DEPLOY_5XX_MIN_PCT": 2.0,
        "DEPLOY_5XX_MIN_COUNT": 10,
        "DEPLOY_JOBS_RATIO": 3.0,
        "DEPLOY_JOBS_MIN": 5,
        "DEPLOY_UP_RATIO": 2.0,
        "DEPLOY_UP_MIN_MS": 300,
    }
    requires = ("docker",)

    def detect(self, src, cfg):
        return src.which("dokku")

    # -- a deploy running right now ------------------------------------------------

    def before(self, ctx):
        rc, out, _ = ctx.src.run("ps_args", ["ps", "-eo", "args"], timeout=10)
        if rc != 0:
            return
        pat = re.compile(r"dokku\S*\s+(git-hook|deploy|ps:rebuild|git:sync|git:from-image|builder)|"
                         r"docker(-buildx)?\s+(build|buildx)|buildkitd", re.IGNORECASE)
        ctx.deploy_running = any(pat.search(line) for line in out.splitlines())

    @staticmethod
    def deploy_since_last_round(ctx) -> bool:
        """`before` only sees the moment of the round: a build that ended between two
        rounds escapes it, but weighs on every metric measured since the previous round.
        The signs are the previous round having seen the build, and an app container
        created in that interval. A build that fails without creating a container and
        fits between two rounds stays invisible."""
        if ctx.state.get("deploy_running"):
            return True
        last = ctx.state.get("last_run")
        if not last:
            return False
        created = (int(to_float(v.partition("@")[2]) or 0) for v in (ctx.facts.get("deploy") or {}).values())
        return any(ts >= last for ts in created)

    # -- apps --------------------------------------------------------------------------

    def discover(self, ctx):
        rc, out, err = ctx.src.run("dokku_apps", ["dokku", "apps:list"], timeout=30)
        if rc != 0:
            ctx.add(Metric("host.dokku", ctx.server_section, "Dokku", t("dokku.no_access"), "WARN",
                           t("dokku.no_access_detail", error=(err or "").strip()[:120])))
            return
        ctx.dokku_ok = True
        ignore = {a.strip() for a in ctx.cfg.get("SERVER_HEALTH_IGNORE_APPS", "").split(",") if a.strip()}
        ctx.apps = [a for a in dokku_names(out) if a not in ignore]
        ctx.dokku_apps = set(ctx.apps)

    def app(self, ctx, app):
        if app not in ctx.dokku_apps:
            return
        rc, out, _ = ctx.src.run(f"dokku_ps_report_{app}", ["dokku", "ps:report", app], timeout=30)
        deployed = bool(re.search(r"Deployed:\s+true", out or ""))
        mine = [c for c in ctx.containers if c.get("Name", "").lstrip("/").split(".")[0] == app
                and not c.get("Name", "").lstrip("/").startswith("dokku.")]
        running = [c for c in mine if c.get("State", {}).get("Running")]
        procs: dict[str, int] = {}
        for c in running:
            parts = c["Name"].lstrip("/").split(".")
            if len(parts) >= 2:
                procs[parts[1]] = procs.get(parts[1], 0) + 1

        label = t("dokku.processes")
        if not deployed and not running:
            ctx.add(Metric(f"app.{app}.ps", app, label, t("dokku.never_deployed")))
            return
        if not running:
            ctx.add(Metric(f"app.{app}.ps", app, label, t("dokku.none_up"), "CRIT", t("dokku.none_up_detail", app=app),
                           crit_persist_min=ctx.th["APP_DOWN_PERSIST_MIN"]))
            return

        started = [ts for ts in (parse_docker_time(c.get("State", {}).get("StartedAt", "")) for c in running) if ts]
        rev = ""
        for c in running:
            for env in c.get("Config", {}).get("Env") or []:
                if env.startswith("GIT_REV="):
                    rev = env.split("=", 1)[1][:7]
                    break
            if rev:
                break
        created = [ts for ts in (parse_docker_time(c.get("Created", "")) for c in running) if ts]
        deploy_txt = ""
        if rev or created:
            ctx.facts.setdefault("deploy", {})[app] = f"{rev or '?'}@{int(max(created)) if created else 0}"
            when = fdate(max(created), ctx.tz) if created else "?"
            deploy_txt = t("dokku.deploy", rev=rev or "?", when=when)
        up_txt = t("dokku.up_for", age=fdur(ctx.now - min(started))) if started else ""
        mem = sum(v for k, v in ctx.mem_by_container.items() if k.split(".")[0] == app)
        mem_txt = f" · {fbytes(mem)}" if mem else ""
        procs_txt = ", ".join(f"{k} {v}" for k, v in sorted(procs.items()))
        ctx.add(Metric(f"app.{app}.ps", app, label, f"{procs_txt}{up_txt}{deploy_txt}{mem_txt}"))

        # A container restart or OOM is an occurrence: alerts once per occurrence.
        prev = ctx.state.get("restarts", {})
        cur = ctx.updates.setdefault("restarts", dict(prev))
        restarted, oomed = [], []
        for c in mine:
            cid = c.get("Id", "")[:12]
            name = c.get("Name", "").lstrip("/")
            count = int(c.get("RestartCount", 0) or 0)
            before = prev.get(cid, {}).get("n")
            if before is not None and count > before:
                restarted.append(f"{name} +{count - before}")
            oom = bool(c.get("State", {}).get("OOMKilled"))
            if oom and not prev.get(cid, {}).get("oom"):
                oomed.append(name)
            cur[cid] = {"n": count, "oom": oom, "seen": int(ctx.now)}
        if oomed:
            ctx.add(Metric(f"app.{app}.oom", app, t("dokku.oom"), ", ".join(oomed), "CRIT", t("dokku.oom_detail"),
                           event=True))
        if restarted:
            ctx.add(Metric(f"app.{app}.restart", app, t("dokku.restart"), ", ".join(restarted), "WARN",
                           t("dokku.restart_detail", app=app), event=True))

    # -- upload traps (daily) ------------------------------------------------------------

    def daily(self, ctx):
        for app in sorted(ctx.dokku_apps):
            conf = ctx.app_config(app)
            if not conf:
                continue
            if conf.storage_mount:
                self.storage(ctx, app)
            if conf.max_body_size:
                self.body_size(ctx, app, conf.max_body_size, conf.max_body_size_text)

    def storage(self, ctx, app):
        rc, out, _ = ctx.src.run(f"dokku_storage_{app}", ["dokku", "storage:list", app], timeout=30)
        if rc != 0:
            return
        mounts = [line.strip() for line in out.splitlines() if ":" in line and not line.startswith("=")]
        if mounts:
            ctx.add(Metric(f"app.{app}.storage", app, t("dokku.storage"), ", ".join(mounts)[:200]))
        else:
            ctx.add(Metric(f"app.{app}.storage", app, t("dokku.storage"), t("dokku.storage.none"), "WARN",
                           t("dokku.storage.detail", app=app)))

    def body_size(self, ctx, app, want: int, want_text: str):
        low = []
        rc, out, _ = ctx.src.run(f"dokku_nginx_body_{app}",
                                 ["dokku", "nginx:report", app, "--nginx-client-max-body-size"], timeout=30)
        if rc == 0:
            raw = out.strip() or "1m"  # empty: nginx's default
            nginx = parse_body_size(raw)
            if nginx is not None and nginx < want:
                low.append(f"nginx {raw}")
        rc, out, _ = ctx.src.run(f"dokku_thruster_body_{app}", ["dokku", "config:get", app, "MAX_REQUEST_BODY"],
                                 timeout=30)
        thruster = parse_body_size(out.strip()) if rc == 0 and out.strip() else None
        if thruster is not None and thruster < want:
            low.append(f"Thruster MAX_REQUEST_BODY {fbytes(thruster)}")
        value = t("dokku.body.low", layers="; ".join(low), want=want_text) if low else t("dokku.body.ok", want=want_text)
        ctx.add(Metric(f"app.{app}.body_size", app, t("dokku.body"), value, "WARN" if low else "OK",
                       t("dokku.body.detail", app=app, want=want_text)))

    # -- after every app ------------------------------------------------------------------

    def after_app(self, ctx, app):
        """Once per deploy, DEPLOY_WINDOW_MIN after it: compares 5xx, job failures and the
        /up time with the hour before. The deploy time is the new container's."""
        cur = (ctx.facts.get("deploy") or {}).get(app)
        if not cur or "@" not in cur:
            return
        rev, _, ts = cur.partition("@")
        deploy_ts = int(to_float(ts) or 0)
        th = ctx.th
        win = th["DEPLOY_WINDOW_MIN"] * 60
        checked = dict(ctx.state.get("deploy_checked", {}))
        if not deploy_ts or checked.get(app) == cur or ctx.now < deploy_ts + win:
            ctx.updates.setdefault("deploy_checked", checked)
            return
        checked[app] = cur
        ctx.updates["deploy_checked"] = checked
        if ctx.now > deploy_ts + 4 * win:
            return  # the monitor was off in the period; comparing now would measure something else
        rows = ctx.history + [ctx.facts]
        before = [h for h in rows if deploy_ts - 2 * win <= h.get("ts", 0) < deploy_ts]
        after = [h for h in rows if deploy_ts < h.get("ts", 0) <= deploy_ts + win + 300]
        if len(before) < 3 or len(after) < 3:
            return

        def req(rows_):
            return (sum((h.get("req") or {}).get(app, [0, 0])[0] for h in rows_),
                    sum((h.get("req") or {}).get(app, [0, 0])[1] for h in rows_))

        def median_up(rows_):
            ms = sorted(u[1] for u in ((h.get("up") or {}).get(app) for h in rows_) if u and 200 <= u[0] < 300)
            return ms[len(ms) // 2] if ms else None

        found = []
        bt, be = req(before)
        at, ae = req(after)
        bpct = be * 100 / bt if bt else 0.0
        apct = ae * 100 / at if at else 0.0
        if ae >= th["DEPLOY_5XX_MIN_COUNT"] and apct >= th["DEPLOY_5XX_MIN_PCT"] and apct >= th["DEPLOY_5XX_RATIO"] * bpct:
            found.append(f"5xx {fnum(bpct, 1)}% → {fnum(apct, 1)}%")
        bjobs = max([(h.get("sqf") or {}).get(app, 0) for h in before] or [0])
        ajobs = max([(h.get("sqf") or {}).get(app, 0) for h in after] or [0])
        if ajobs >= th["DEPLOY_JOBS_MIN"] and ajobs >= th["DEPLOY_JOBS_RATIO"] * bjobs:
            found.append(t("dokku.suspect.jobs", before=bjobs, after=ajobs))
        bup, aup = median_up(before), median_up(after)
        if bup and aup and aup >= th["DEPLOY_UP_MIN_MS"] and aup >= th["DEPLOY_UP_RATIO"] * bup:
            found.append(t("dokku.suspect.up", before=fnum(bup), after=fnum(aup)))
        if found:
            ctx.add(Metric(f"app.{app}.deploy", app, t("dokku.suspect"),
                           f"{rev} ({fdate(deploy_ts, ctx.tz)}): " + "; ".join(found), "WARN",
                           t("dokku.suspect.detail", app=app), event=True))

    def finish(self, ctx):
        ctx.deploy_recent = self.deploy_since_last_round(ctx)

    # -- weekly ----------------------------------------------------------------------------

    def weekly_app_lines(self, r, app):
        cur = r.window(r.now - r.WEEK, r.now)
        prev = r.window(r.now - 2 * r.WEEK, r.now - r.WEEK)
        deploys = {(h.get("deploy") or {}).get(app) for h in cur} - {None}
        before = {(h.get("deploy") or {}).get(app) for h in prev} - {None}
        week_alerts = r.alerts_window(r.now - r.WEEK, r.now)
        restarts = sum(1 for a in week_alerts if a["key"] == f"app.{app}.restart" and a["kind"] == EVENT)
        ooms = sum(1 for a in week_alerts if a["key"] == f"app.{app}.oom" and a["kind"] == EVENT)
        if not (deploys or restarts or ooms):
            return []
        new_deploys = len(deploys - before) if before else max(0, len(deploys) - 1)
        return [(30, t("dokku.weekly", deploys=new_deploys, restarts=restarts, ooms=ooms))]

    strings = {
        "en": {
            "dokku.no_access": "no access",
            "dokku.no_access_detail": "dokku apps:list failed ({error}); apps and databases not measured",
            "dokku.processes": "Processes",
            "dokku.never_deployed": "never deployed",
            "dokku.none_up": "NO process running",
            "dokku.none_up_detail": "app deployed with no running container; `dokku ps:report {app}`",
            "dokku.deploy": " · deploy {rev} ({when})",
            "dokku.up_for": " · up for {age}",
            "dokku.oom": "Container killed for memory",
            "dokku.oom_detail": "the container went over its memory limit and was killed",
            "dokku.restart": "Restart outside a deploy",
            "dokku.restart_detail": "the process died and Docker started it again; `dokku logs {app} -n 200`",
            "dokku.storage": "Storage mount",
            "dokku.storage.none": "NONE",
            "dokku.storage.detail": "the TOML says this app keeps files on disk, and there is no `dokku storage:mount`: "
                                    "every uploaded file is lost on the next deploy. `dokku storage:list {app}`",
            "dokku.body": "Upload size limit",
            "dokku.body.ok": "nginx and Thruster accept {want}",
            "dokku.body.low": "{layers} (the app needs {want})",
            "dokku.body.detail": "a limit below {want} rejects the upload with 413 without a line in the Rails log: "
                                 "`dokku nginx:set {app} client-max-body-size {want}` and `dokku config:set {app} "
                                 "MAX_REQUEST_BODY=<bytes>`",
            "dokku.suspect": "Suspicious deploy",
            "dokku.suspect.jobs": "job failures in 30 min {before} → {after}",
            "dokku.suspect.up": "median /up {before} ms → {after} ms",
            "dokku.suspect.detail": "what got worse in the 30 min after the deploy, against the hour before; see the "
                                    "error tracker and `dokku logs {app} -n 300`. Rolling back is a deploy of the "
                                    "previous commit",
            "dokku.weekly": "Deploys: {deploys} · restarts outside a deploy: {restarts} · killed for memory: {ooms}",
        },
        "pt-BR": {
            "dokku.no_access": "sem acesso",
            "dokku.no_access_detail": "dokku apps:list falhou ({error}); apps e bancos não medidos",
            "dokku.processes": "Processos",
            "dokku.never_deployed": "nunca deployada",
            "dokku.none_up": "NENHUM processo de pé",
            "dokku.none_up_detail": "app deployada sem contêiner rodando; `dokku ps:report {app}`",
            "dokku.deploy": " · deploy {rev} ({when})",
            "dokku.up_for": " · de pé há {age}",
            "dokku.oom": "Contêiner morto por memória",
            "dokku.oom_detail": "o contêiner passou do limite de memória e foi morto",
            "dokku.restart": "Reinício fora de deploy",
            "dokku.restart_detail": "o processo caiu e o Docker o subiu de novo; `dokku logs {app} -n 200`",
            "dokku.storage": "Storage mount",
            "dokku.storage.none": "NENHUM",
            "dokku.storage.detail": "o TOML diz que a app guarda arquivo em disco, e não há `dokku storage:mount`: todo "
                                    "arquivo enviado some no próximo deploy. `dokku storage:list {app}`",
            "dokku.body": "Limite de upload",
            "dokku.body.ok": "nginx e Thruster aceitam {want}",
            "dokku.body.low": "{layers} (a app precisa de {want})",
            "dokku.body.detail": "um limite abaixo de {want} recusa o upload com 413 sem deixar linha no log do Rails: "
                                 "`dokku nginx:set {app} client-max-body-size {want}` e `dokku config:set {app} "
                                 "MAX_REQUEST_BODY=<bytes>`",
            "dokku.suspect": "Deploy suspeito",
            "dokku.suspect.jobs": "falhas de job em 30 min {before} → {after}",
            "dokku.suspect.up": "/up mediano {before} ms → {after} ms",
            "dokku.suspect.detail": "o que piorou nos 30 min depois do deploy, contra a hora antes; veja o rastreador de "
                                    "erros e `dokku logs {app} -n 300`. Voltar é um deploy do commit anterior",
            "dokku.weekly": "Deploys: {deploys} · reinícios fora de deploy: {restarts} · mortos por memória: {ooms}",
        },
    }
