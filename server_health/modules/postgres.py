"""Postgres databases: size and growth, connections, the longest transaction, vacuum
falling behind, cache hit ratio and (daily) the largest tables.

The databases come from Dokku (`dokku postgres:connect`) and from the apps' TOMLs:
`database_url` (psql on the machine; the password travels in PGPASSWORD, never in the
command line, which every user can see in `ps`) or `database_container` (psql inside
that container, through `docker exec`).

Every query runs in a read-only session with a statement timeout. Other modules (rails,
indicators) add their statements to the same session through `service_queries` and
`app_queries`, so each app costs one connection per round."""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

from ..i18n import fbytes, fdur, fnum, t
from ..metric import Metric
from ..module import Module
from ..parse import PSQL_MARK, dokku_names, parse_psql, to_float


def q(key_sql: str, value_sql: str, tail: str = "") -> str:
    return f"SELECT '{PSQL_MARK}', {key_sql}, ({value_sql})::text {tail};"


def connection(ctx, svc: str) -> tuple[list[str], dict | None]:
    """The command (and environment) that opens psql for a service."""
    kind, *rest = ctx.db_conn.get(svc, ("dokku",))
    if kind == "dokku":
        return ["dokku", "postgres:connect", svc], None
    url = rest[1] if kind == "container" else rest[0]
    u = urlparse(url) if url else None
    if kind == "container":
        argv = ["docker", "exec", "-i", rest[0], "psql", "-X", "-q"]
        if u and u.username:
            argv += ["-U", unquote(u.username)]
        if u and u.path.strip("/"):
            argv += ["-d", unquote(u.path.strip("/"))]
        if not (u and u.username):
            argv += ["-U", "postgres"]
        return argv, None
    env = {"PGHOST": u.hostname or "localhost", "PGPORT": str(u.port or 5432),
           "PGDATABASE": unquote(u.path.strip("/")) or "postgres", "PGUSER": unquote(u.username or "postgres"),
           "PGAPPNAME": "server-health", "PGCONNECT_TIMEOUT": "10"}
    if u.password:
        env["PGPASSWORD"] = unquote(u.password)
    ssl = parse_qs(u.query).get("sslmode")
    if ssl:
        env["PGSSLMODE"] = ssl[0]
    return ["psql", "-X", "-q"], env


def psql(ctx, svc: str, phase: str, statements: list[str]) -> dict[str, list[str]] | None:
    """One read-only session. Returns {key: [values]} or None if the database did not
    answer. A query with an error only goes missing from the result."""
    script = "\n".join([
        "\\set QUIET on",
        "\\set ON_ERROR_STOP off",
        "\\pset format unaligned",
        "\\pset tuples_only on",
        "\\pset fieldsep '|'",
        "\\pset pager off",
        "SET default_transaction_read_only = on;",
        "SET statement_timeout = '5s';",
        "SET lock_timeout = '1s';",
        "SET application_name = 'server-health';",
        *statements,
    ]) + "\n"
    argv, env = connection(ctx, svc)
    rc, out, err = ctx.src.run(f"psql_{svc}_{phase}", argv, timeout=90, stdin=script, env=env)
    rows = parse_psql(out)
    if not rows and rc != 0:
        ctx.log(f"psql {svc}/{phase} failed (rc {rc}): {(err or '').strip()[:200]}")
        return None
    result: dict[str, list[str]] = {}
    for k, v in rows:
        result.setdefault(k, []).append(v)
    return result


class Postgres(Module):
    name = "postgres"
    order = 70
    thresholds = {
        "DB_WARN_GB": 2.0,
        "DB_CRIT_GB": 3.0,
        "DB_CONN_WARN_PCT": 80,
        "DB_CONN_CRIT_PCT": 95,
        "DB_LONG_TX_WARN_S": 300,
        "DB_LONG_TX_CRIT_S": 1800,
        # A table with this many dead rows and no vacuum for DB_VACUUM_STALE_DAYS days.
        "DB_DEAD_ROWS_MIN": 10000,
        "DB_VACUUM_STALE_DAYS": 7,
    }

    # Without Dokku and without a database in any TOML it does nothing, so it stays on.

    @staticmethod
    def connections(ctx):
        """Every database to watch: Dokku's services, and the ones the TOMLs declare."""
        if ctx.dokku_ok:
            rc, out, _ = ctx.src.run("dokku_pg_list", ["dokku", "postgres:list"], timeout=30)
            for svc in (dokku_names(out) if rc == 0 else []):
                ctx.services.append(svc)
                ctx.db_conn[svc] = ("dokku",)
        for conf in ctx.app_configs:
            if not (conf.database_url or conf.database_container):
                continue
            svc = conf.database or f"{conf.app}-db"
            url = ctx.setting(conf.database_url) if conf.database_url else ""
            if conf.database_container:
                ctx.db_conn[svc] = ("container", conf.database_container, url)
            elif url:
                ctx.db_conn[svc] = ("url", url)
            else:
                continue  # env:VARIABLE not set in the config
            if svc not in ctx.services:
                ctx.services.append(svc)

    def services(self, ctx):
        self.connections(ctx)
        for svc in ctx.services:
            ctx.db_flags[svc] = self.check_service(ctx, svc)
            if ctx.daily:
                self.tables(ctx, svc)

    def check_service(self, ctx, svc: str) -> dict | None:
        th = ctx.th
        stmts = [
            q("'size'", "SELECT sum(pg_database_size(datname)) FROM pg_database "
                        "WHERE datname NOT IN ('template0','template1','postgres')"),
            q("'conn'", "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'"),
            q("'maxconn'", "SELECT current_setting('max_connections')"),
            q("'long_tx'", "SELECT coalesce(max(extract(epoch FROM now() - xact_start))::bigint, 0) "
                           "FROM pg_stat_activity WHERE backend_type = 'client backend' "
                           "AND state IN ('active', 'idle in transaction') AND pid <> pg_backend_pid()"),
            q("'vacuum_stale'", f"SELECT count(*) FROM pg_stat_user_tables WHERE n_dead_tup >= {int(th['DB_DEAD_ROWS_MIN'])} "
                                "AND coalesce(greatest(last_autovacuum, last_vacuum), '-infinity') < "
                                f"now() - interval '{int(th['DB_VACUUM_STALE_DAYS'])} days'"),
            q("'cache_hit'", "SELECT round(100.0 * sum(blks_hit) / nullif(sum(blks_hit) + sum(blks_read), 0), 2) "
                             "FROM pg_stat_database WHERE datname = current_database()"),
        ]
        for m in ctx.modules:
            stmts += m.service_queries(ctx, svc)
        res = psql(ctx, svc, "base", stmts)
        if res is None or "size" not in res:
            ctx.add(Metric(f"db.{svc}", svc, t("pg.db"), t("pg.no_answer"), "CRIT", t("pg.no_answer_detail", svc=svc),
                           crit_persist_min=5))
            return None
        size = to_float(res["size"][0]) or 0
        ctx.facts["dbs"][svc] = size
        gb = size / 1024 ** 3
        level = "CRIT" if gb >= th["DB_CRIT_GB"] else "WARN" if gb >= th["DB_WARN_GB"] else "OK"
        growth = None
        pts = [(h["ts"], h["dbs"][svc]) for h in ctx.history
               if h.get("ts", 0) >= ctx.now - 7 * 86400 and svc in (h.get("dbs") or {})]
        if len(pts) >= 2 and pts[-1][0] - pts[0][0] >= 86400:
            growth = (size - pts[0][1]) / ((ctx.now - pts[0][0]) / 86400)
        g_txt = t("pg.growth", sign="+" if growth >= 0 else "", growth=fbytes(growth)) if growth is not None else ""
        conn = int(to_float(res.get("conn", ["0"])[0]) or 0)
        maxc = int(to_float(res.get("maxconn", ["100"])[0]) or 100)
        long_tx = int(to_float(res.get("long_tx", ["0"])[0]) or 0)
        ctx.add(Metric(f"db.{svc}.size", svc, t("pg.db"), f"{fbytes(size)}{g_txt}", level, t("pg.size_detail")))
        cpct = conn * 100 / maxc
        clevel = "CRIT" if cpct >= th["DB_CONN_CRIT_PCT"] else "WARN" if cpct >= th["DB_CONN_WARN_PCT"] else "OK"
        ctx.add(Metric(f"db.{svc}.conn", svc, t("pg.conn"), f"{conn}/{maxc}", clevel, t("pg.conn_detail")))
        tlevel = "CRIT" if long_tx >= th["DB_LONG_TX_CRIT_S"] else "WARN" if long_tx >= th["DB_LONG_TX_WARN_S"] else "OK"
        if tlevel != "OK" or ctx.daily:
            ctx.add(Metric(f"db.{svc}.long_tx", svc, t("pg.long_tx"), fdur(long_tx), tlevel, t("pg.long_tx_detail")))
        stale = int(to_float(res.get("vacuum_stale", ["0"])[0]) or 0)
        if stale:
            ctx.add(Metric(f"db.{svc}.vacuum", svc, t("pg.vacuum"), t("pg.vacuum.value", n=stale), "WARN",
                           t("pg.vacuum.detail", rows=fnum(th["DB_DEAD_ROWS_MIN"]), days=th["DB_VACUUM_STALE_DAYS"])))
        hit = to_float((res.get("cache_hit") or [""])[0])
        if ctx.daily and hit is not None:
            ctx.add(Metric(f"db.{svc}.cache_hit", svc, t("pg.cache_hit"), f"{fnum(hit, 1)}%", summary_only=True))

        def flag(key):  # boolean::text is 'true'/'false'
            return res.get(key, ["false"])[0] in ("t", "true")
        return {k: flag(k) for k in res if k.startswith("has_")}

    def tables(self, ctx, svc: str):
        res = psql(ctx, svc, "tables", [
            f"SELECT '{PSQL_MARK}', c.relname, pg_total_relation_size(c.oid)::text FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p') AND n.nspname = 'public' "
            "ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 6;"])
        if not res:
            return
        prev = ctx.state.get("tables", {}).get(svc, {})
        prev_ts, prev_sizes = prev.get("ts"), prev.get("sizes", {})
        sizes = {k: to_float(v[0]) or 0 for k, v in res.items()}
        parts = []
        for name, size in sorted(sizes.items(), key=lambda kv: kv[1], reverse=True):
            delta = ""
            if prev_ts and name in prev_sizes and ctx.now - prev_ts > 3600:
                per_day = (size - prev_sizes[name]) / ((ctx.now - prev_ts) / 86400)
                if abs(per_day) >= 1024 ** 2:
                    delta = t("pg.growth", sign="+" if per_day >= 0 else "", growth=fbytes(per_day))
            parts.append(f"{name} {fbytes(size)}{delta}")
        ctx.updates.setdefault("tables", dict(ctx.state.get("tables", {})))[svc] = {"ts": int(ctx.now), "sizes": sizes}
        ctx.add(Metric(f"db.{svc}.tables", svc, t("pg.tables"), " · ".join(parts), summary_only=True, items=parts))

    def app_db(self, ctx, app: str) -> str:
        conf = ctx.app_config(app)
        if conf and (conf.database_url or conf.database_container):
            svc = conf.database or f"{conf.app}-db"
            return svc if svc in ctx.db_conn else ""
        if conf and conf.database:
            return conf.database
        if app not in ctx.dokku_apps:
            return ""
        rc, out, _ = ctx.src.run(f"dokku_pg_links_{app}", ["dokku", "postgres:app-links", app], timeout=30)
        linked = [s for s in dokku_names(out) if s in ctx.services] if rc == 0 else []
        if linked:
            return linked[0]
        guess = f"{app}-db"
        return guess if guess in ctx.services else ""

    def app(self, ctx, app):
        svc = self.app_db(ctx, app)
        if not svc:
            return
        ctx.db_app[svc] = app
        ctx.app_service[app] = svc
        if ctx.db_flags.get(svc) is None:
            return
        stmts, users = [], []
        for m in ctx.modules:
            got = m.app_queries(ctx, app, svc)
            if got:
                stmts += got
                users.append(m)
        if not stmts:
            return
        res = psql(ctx, svc, "app", stmts)
        if res is None:
            ctx.add(Metric(f"app.{app}.dbq", app, t("pg.app_queries"), t("pg.app_queries.none"), "WARN",
                           t("pg.app_queries.detail"), persist_min=10))
            return
        for m in users:
            m.app_results(ctx, app, res)

    def weekly_fields(self, r):
        cur = r.window(r.now - r.WEEK, r.now)
        first: dict[str, float] = {}
        last: dict[str, float] = {}
        for h in cur:
            for svc, size in (h.get("dbs") or {}).items():
                if r.channels.route(f"db.{svc}") != r.route:
                    continue
                first.setdefault(svc, size)
                last[svc] = size
        lines = [f"{svc}: {fbytes(first[svc])} → {fbytes(last[svc])} ({'+' if last[svc] >= first[svc] else ''}"
                 f"{fbytes(last[svc] - first[svc])})" for svc in sorted(last)]
        if not lines:
            return []
        return [(20, {"name": t("pg.weekly_field"), "value": r.bullets(lines)})]

    strings = {
        "en": {
            "pg.db": "Database",
            "pg.no_answer": "no answer",
            "pg.no_answer_detail": "Postgres did not answer; `dokku postgres:info {svc}`",
            "pg.growth": " ({sign}{growth}/day)",
            "pg.size_detail": "a large database eats the page cache; look at the largest tables in the daily summary",
            "pg.conn": "Connections",
            "pg.conn_detail": "near the limit, the app starts refusing requests",
            "pg.long_tx": "Longest transaction",
            "pg.long_tx_detail": "a query or transaction open for long holds locks and the vacuum",
            "pg.vacuum": "Vacuum falling behind",
            "pg.vacuum.value": "{n} table(s)",
            "pg.vacuum.detail": "tables with {rows} or more dead rows and no vacuum for {days} days: queries get slower "
                                "and the table grows. `SELECT relname, n_dead_tup, last_autovacuum FROM "
                                "pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 5;`",
            "pg.cache_hit": "Cache hit ratio",
            "pg.tables": "Largest tables",
            "pg.app_queries": "App queries",
            "pg.app_queries.none": "the database did not answer",
            "pg.app_queries.detail": "Solid Queue and indicators not measured this round",
            "pg.weekly_field": "Databases",
        },
        "pt-BR": {
            "pg.db": "Banco",
            "pg.no_answer": "sem resposta",
            "pg.no_answer_detail": "o Postgres não respondeu; `dokku postgres:info {svc}`",
            "pg.growth": " ({sign}{growth}/dia)",
            "pg.size_detail": "banco grande come page cache; olhe as maiores tabelas no resumo diário",
            "pg.conn": "Conexões",
            "pg.conn_detail": "perto do limite, a app passa a recusar requisições",
            "pg.long_tx": "Transação mais longa",
            "pg.long_tx_detail": "consulta ou transação aberta há muito tempo segura locks e o vacuum",
            "pg.vacuum": "Vacuum atrasado",
            "pg.vacuum.value": "{n} tabela(s)",
            "pg.vacuum.detail": "tabelas com {rows} ou mais linhas mortas e sem vacuum há {days} dias: as consultas "
                                "ficam lentas e a tabela incha. `SELECT relname, n_dead_tup, last_autovacuum FROM "
                                "pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 5;`",
            "pg.cache_hit": "Acerto do cache",
            "pg.tables": "Maiores tabelas",
            "pg.app_queries": "Consultas da app",
            "pg.app_queries.none": "o banco não respondeu",
            "pg.app_queries.detail": "Solid Queue e indicadores não medidos nesta rodada",
            "pg.weekly_field": "Bancos",
        },
    }
