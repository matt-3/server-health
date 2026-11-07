"""The TOML of an app: its display name, database, health path and product indicators.

Each app keeps its TOML in its own repository (the SQL changes with its migrations);
on the server it lives in /etc/server-health/apps.d/<app>.toml."""

from __future__ import annotations

import dataclasses
import re
import tomllib
from pathlib import Path

from .i18n import fbytes, fdur, fnum, register, t
from .parse import PSQL_MARK, parse_body_size

APP_KEYS = {"app", "name", "database", "health_path", "jobs_dashboard", "storage_mount", "max_body_size",
            "health_url", "nginx_log", "database_url", "database_container", "indicator"}
INDICATOR_KEYS = {"key", "label", "sql", "warn_at", "critical_at", "direction", "when", "persist_min",
                  "active_hours", "format", "explanation"}
DIRECTIONS = ("above", "below")
WHENS = ("always", "summary")
FORMATS = ("number", "bytes", "duration", "cents")

@dataclasses.dataclass
class Indicator:
    key: str
    label: str
    sql: str
    warn_at: float | None = None
    critical_at: float | None = None
    direction: str = "above"
    when: str = "always"
    persist_min: int = 0
    active_hours: str = ""
    format: str = "number"
    explanation: str = ""


@dataclasses.dataclass
class AppConfig:
    app: str
    path: str
    name: str = ""            # product name in the messages; the Dokku app follows in parentheses
    database: str = ""
    health_path: str = ""
    jobs_dashboard: str = ""  # where the failed jobs are, for the alert text ("/admin/jobs")
    storage_mount: bool = False
    max_body_size: int | None = None
    max_body_size_text: str = ""
    # An app outside Dokku (Docker Compose, Kamal, systemd...) says where things are:
    health_url: str = ""          # the full URL of its health check
    nginx_log: str = ""           # its access log, in nginx's combined format
    database_url: str = ""        # postgres://...; "env:VAR" reads it from the config file
    database_container: str = ""  # or the Postgres container, reached with `docker exec`
    indicators: list[Indicator] = dataclasses.field(default_factory=list)

    @property
    def standalone(self) -> bool:
        """Declares its own sources: it is monitored even without Dokku."""
        return bool(self.health_url or self.nginx_log or self.database_url or self.database_container)


def _unknown(keys, allowed: set, where: str, errors: list[str]):
    for key in sorted(set(keys) - allowed):
        errors.append(t("app.err.unknown", where=where, key=key))


def _choice(raw: dict, key: str, choices: tuple, default: str, where: str, errors: list[str]) -> str | None:
    value = raw.get(key, default)
    if value in choices:
        return value
    errors.append(t("app.err.choice", where=where, key=key, choices=", ".join(f"'{c}'" for c in choices)))
    return None


def load_app_config(path: str) -> tuple[AppConfig | None, list[str]]:
    """Validates an app's TOML. An error in an indicator drops that indicator, not the app."""
    errors: list[str] = []
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        return None, [t("app.err.toml", path=path, error=e)]
    app = data.get("app")
    if not isinstance(app, str) or not app:
        return None, [t("app.err.no_app", path=path)]
    _unknown(data, APP_KEYS, app, errors)
    cfg = AppConfig(app=app, path=path, name=str(data.get("name", "")).strip(),
                    database=str(data.get("database", "")), health_path=str(data.get("health_path", "")),
                    jobs_dashboard=str(data.get("jobs_dashboard", "")).strip())
    if "storage_mount" in data:
        if isinstance(data["storage_mount"], bool):
            cfg.storage_mount = data["storage_mount"]
        else:
            errors.append(t("app.err.bool", where=app, key="storage_mount"))
    if "max_body_size" in data:
        size = parse_body_size(str(data["max_body_size"]))
        if size is None:
            errors.append(t("app.err.body_size", where=app))
        else:
            cfg.max_body_size, cfg.max_body_size_text = size, str(data["max_body_size"])
    health_url = str(data.get("health_url", "")).strip()
    if health_url and not re.match(r"^https?://\S+$", health_url):
        errors.append(t("app.err.url", where=app, key="health_url"))
    else:
        cfg.health_url = health_url
    nginx_log = str(data.get("nginx_log", "")).strip()
    if nginx_log and not nginx_log.startswith("/"):
        errors.append(t("app.err.path", where=app, key="nginx_log"))
    else:
        cfg.nginx_log = nginx_log
    db_url = str(data.get("database_url", "")).strip()
    if db_url and not (db_url.startswith("env:") or re.match(r"^postgres(ql)?://\S+$", db_url)):
        errors.append(t("app.err.db_url", where=app))
    elif re.match(r"^postgres(ql)?://[^/@:]+:[^/@]+@", db_url):
        # The TOML is readable by every user of the machine; the config file is not.
        errors.append(t("app.err.db_password", where=app))
    else:
        cfg.database_url = db_url
    cfg.database_container = str(data.get("database_container", "")).strip()
    seen = set()
    for i, raw in enumerate(data.get("indicator", []), 1):
        where = t("app.where.indicator", app=app, n=i)
        if not isinstance(raw, dict):
            errors.append(t("app.err.not_table", where=where))
            continue
        _unknown(raw, INDICATOR_KEYS, where, errors)
        key = raw.get("key")
        if not isinstance(key, str) or not re.match(r"^[a-z0-9_]+$", key or ""):
            errors.append(t("app.err.key", where=where))
            continue
        where = f"{app}.{key}"
        if key in seen:
            errors.append(t("app.err.duplicate", where=where))
            continue
        sql = str(raw.get("sql", "")).strip().rstrip(";").strip()
        if not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
            errors.append(t("app.err.sql_select", where=where))
            continue
        if ";" in sql:
            errors.append(t("app.err.sql_single", where=where))
            continue
        if PSQL_MARK in sql:
            errors.append(t("app.err.sql_mark", where=where, mark=PSQL_MARK))
            continue
        ind = Indicator(key=key, label=str(raw.get("label") or key), sql=sql)
        ok = True
        for k in ("warn_at", "critical_at"):
            v = raw.get(k)
            if v is not None and not isinstance(v, (int, float)):
                errors.append(t("app.err.number", where=where, key=k))
                ok = False
            setattr(ind, k, v)
        for k, choices, default in (("direction", DIRECTIONS, "above"), ("when", WHENS, "always"),
                                    ("format", FORMATS, "number")):
            value = _choice(raw, k, choices, default, where, errors)
            if value is None:
                ok = False
            else:
                setattr(ind, k, value)
        ind.persist_min = raw.get("persist_min", 0)
        if not isinstance(ind.persist_min, int) or ind.persist_min < 0:
            errors.append(t("app.err.persist", where=where))
            ok = False
        ind.active_hours = str(raw.get("active_hours", ""))
        if ind.active_hours and not re.match(r"^\d{1,2}-\d{1,2}$", ind.active_hours):
            errors.append(t("app.err.hours", where=where))
            ok = False
        ind.explanation = str(raw.get("explanation", ""))
        if ind.when == "summary" and (ind.warn_at is not None or ind.critical_at is not None):
            errors.append(t("app.err.summary_threshold", where=where))
            ok = False
        if ok:
            seen.add(key)
            cfg.indicators.append(ind)
    return cfg, errors


def indicator_level(ind: Indicator, value: float) -> str:
    def hit(limit):
        if limit is None:
            return False
        return value >= limit if ind.direction == "above" else value <= limit
    if hit(ind.critical_at):
        return "CRIT"
    if hit(ind.warn_at):
        return "WARN"
    return "OK"


def format_value(fmt: str, value: float) -> str:
    if fmt == "bytes":
        return fbytes(value)
    if fmt == "duration":
        return fdur(value)
    if fmt == "cents":
        return fnum(value / 100, 2)
    return fnum(value, 0 if float(value).is_integer() else 2)


register({
    "en": {
        "app.where.indicator": "{app}, indicator {n}",
        "app.err.toml": "{path}: invalid TOML ({error})",
        "app.err.no_app": "{path}: missing `app` (the app name in Dokku)",
        "app.err.unknown": "{where}: unknown key `{key}`",
        "app.err.choice": "{where}: `{key}` is one of {choices}",
        "app.err.bool": "{where}: `{key}` must be true or false",
        "app.err.body_size": "{where}: `max_body_size` like '50m', '512k' or a number of bytes",
        "app.err.url": "{where}: `{key}` must be a full http(s):// URL",
        "app.err.path": "{where}: `{key}` must be an absolute path",
        "app.err.db_url": "{where}: `database_url` is postgres://... or env:VARIABLE",
        "app.err.db_password": "{where}: `database_url` has a password, and this file is readable by every user: "
                               "put the URL in the config file and use `database_url = \"env:VARIABLE\"`",
        "app.err.not_table": "{where}: must be an [[indicator]] table",
        "app.err.key": "{where}: `key` is required, lowercase letters, digits and _ only",
        "app.err.duplicate": "{where}: repeated key",
        "app.err.sql_select": "{where}: `sql` must start with SELECT or WITH",
        "app.err.sql_single": "{where}: `sql` must be a single query (no ';' in the middle)",
        "app.err.sql_mark": "{where}: `sql` cannot contain {mark}",
        "app.err.number": "{where}: `{key}` must be a number",
        "app.err.persist": "{where}: `persist_min` must be an integer >= 0",
        "app.err.hours": "{where}: `active_hours` like '09-23'",
        "app.err.summary_threshold": "{where}: a summary indicator never alerts; remove warn_at/critical_at",
    },
    "pt-BR": {
        "app.where.indicator": "{app}, indicador {n}",
        "app.err.toml": "{path}: TOML inválido ({error})",
        "app.err.no_app": "{path}: falta `app` (o nome da app no Dokku)",
        "app.err.unknown": "{where}: chave desconhecida `{key}`",
        "app.err.choice": "{where}: `{key}` é um de {choices}",
        "app.err.bool": "{where}: `{key}` precisa ser true ou false",
        "app.err.body_size": "{where}: `max_body_size` como '50m', '512k' ou um número de bytes",
        "app.err.url": "{where}: `{key}` precisa ser uma URL completa http(s)://",
        "app.err.path": "{where}: `{key}` precisa ser um caminho absoluto",
        "app.err.db_url": "{where}: `database_url` é postgres://... ou env:VARIAVEL",
        "app.err.db_password": "{where}: `database_url` tem senha, e este arquivo é legível por qualquer usuário: "
                               "ponha a URL no arquivo de config e use `database_url = \"env:VARIAVEL\"`",
        "app.err.not_table": "{where}: precisa ser uma tabela [[indicator]]",
        "app.err.key": "{where}: `key` obrigatória, só minúsculas, números e _",
        "app.err.duplicate": "{where}: chave repetida",
        "app.err.sql_select": "{where}: `sql` precisa começar com SELECT ou WITH",
        "app.err.sql_single": "{where}: `sql` precisa ser uma consulta só (sem ';' no meio)",
        "app.err.sql_mark": "{where}: `sql` não pode conter {mark}",
        "app.err.number": "{where}: `{key}` precisa ser número",
        "app.err.persist": "{where}: `persist_min` precisa ser inteiro >= 0",
        "app.err.hours": "{where}: `active_hours` no formato '09-23'",
        "app.err.summary_threshold": "{where}: indicador de resumo não alerta; tire warn_at/critical_at",
    },
})
