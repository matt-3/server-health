"""The config file and the thresholds.

The config is a VAR=value file (/etc/default/server-health), outside git because it
holds webhooks. Each module declares its own settings and thresholds; this file only
knows the core ones and how to merge."""

from __future__ import annotations

import re
from pathlib import Path

from .i18n import register, t

DEFAULT_CONFIG_FILE = "/etc/default/server-health"

# An app's own Discord channel: SERVER_HEALTH_DISCORD_WEBHOOK_<APP>, with the Dokku app
# name uppercased and `-` turned into `_` (SERVER_HEALTH_DISCORD_WEBHOOK_MY_APP). It is
# not in CORE_DEFAULTS because the apps come from Dokku on every round.
APP_WEBHOOK_PREFIX = "SERVER_HEALTH_DISCORD_WEBHOOK_"

CORE_DEFAULTS = {
    # Which modules run: "auto" turns on what this machine has (Docker, Dokku...);
    # a list ("host,docker") runs only those; "auto,-ports" is auto without one.
    "SERVER_HEALTH_MODULES": "auto",
    # Language of the messages: en or pt-BR.
    "SERVER_HEALTH_LANG": "en",
    # Server name in the messages. A VPS hostname is often unreadable (vps-735d5c35).
    "SERVER_HEALTH_LABEL": "",
    "SERVER_HEALTH_TZ": "UTC",
    # Warnings (not critical) in this window wait and go out in one message at its
    # end. Critical always goes out. Empty turns it off. Example: "23-7".
    "SERVER_HEALTH_QUIET_HOURS": "",
    "SERVER_HEALTH_DISCORD_WEBHOOK": "",
    # Mention on a new critical alert, so the phone rings. "@here", "@everyone" or
    # "<@&ROLE_ID>". Empty turns it off.
    "SERVER_HEALTH_DISCORD_MENTION": "@here",
    # Generic webhook: every alert as JSON to this URL (ntfy, Slack bridges, n8n...).
    "SERVER_HEALTH_WEBHOOK_URL": "",
    "SERVER_HEALTH_SENTRY_DSN": "",
    # External heartbeat (healthchecks.io or similar): called on every round; when the
    # calls stop, that service warns. Covers what the monitor cannot see: the whole
    # server down.
    "SERVER_HEALTH_PING_URL": "",
    # When the daily summary and the weekly report go out, in SERVER_HEALTH_TZ ("off"
    # turns one off). The cron only runs the 5-minute round: the monitor sends them on
    # the first round after their time.
    "SERVER_HEALTH_DAILY_AT": "09:00",
    "SERVER_HEALTH_WEEKLY_AT": "sat 07:00",
    # The weekly report carries the dashboard (an HTML file with the charts).
    "SERVER_HEALTH_WEEKLY_DASHBOARD": "true",
    "SERVER_HEALTH_APPS_DIR": "/etc/server-health/apps.d",
    "SERVER_HEALTH_MODULES_DIR": "/etc/server-health/modules.d",
    "SERVER_HEALTH_STATE_DIR": "/var/lib/server-health",
    "SERVER_HEALTH_LOG": "/var/log/server-health.log",
}

# Reminders of an alert that stays open. A NEW alert goes out at once; these repeat
# while the problem is not solved.
CORE_THRESHOLDS = {
    "REMIND_CRIT_MIN": 120,
    "REMIND_WARN_MIN": 1440,
}

URL_RE = re.compile(r"^https?://\S+/\S*$")


def load_config(path: str, env: dict | None = None, defaults: dict | None = None) -> tuple[dict, list[str]]:
    """Reads the VAR=value file. Returns the config and the problems found."""
    cfg = dict(CORE_DEFAULTS)
    cfg.update(defaults or {})
    problems = []
    p = Path(path)
    if p.exists():
        try:
            text = p.read_text(encoding="utf-8")
        except PermissionError:
            problems.append(t("config.unreadable", path=path))
            text = ""
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            cfg[key.strip()] = value
    else:
        problems.append(t("config.missing", path=path))
    for key, value in (env or {}).items():
        if key.startswith("SERVER_HEALTH_"):
            cfg[key] = value

    hook = cfg["SERVER_HEALTH_DISCORD_WEBHOOK"]
    if hook and not re.match(r"^https://\S+/\S+$", hook):
        problems.append(t("config.bad_url", key="SERVER_HEALTH_DISCORD_WEBHOOK"))
        cfg["SERVER_HEALTH_DISCORD_WEBHOOK"] = ""
    app_hooks = [k for k in cfg if k.startswith(APP_WEBHOOK_PREFIX)]
    for key in app_hooks:
        if cfg[key] and not re.match(r"^https://\S+/\S+$", cfg[key]):
            problems.append(t("config.bad_url", key=key))
        if not re.match(r"^https://\S+/\S+$", cfg[key]):
            del cfg[key]
    if not cfg["SERVER_HEALTH_DISCORD_WEBHOOK"] and any(k in cfg for k in app_hooks):
        problems.append(t("config.app_hook_without_server"))
    if cfg["SERVER_HEALTH_WEBHOOK_URL"] and not URL_RE.match(cfg["SERVER_HEALTH_WEBHOOK_URL"]):
        problems.append(t("config.bad_url", key="SERVER_HEALTH_WEBHOOK_URL"))
        cfg["SERVER_HEALTH_WEBHOOK_URL"] = ""
    dsn = cfg["SERVER_HEALTH_SENTRY_DSN"]
    if dsn and not re.match(r"^https://[^@\s]+@[^/\s]+/\d+$", dsn):
        problems.append(t("config.bad_dsn"))
        cfg["SERVER_HEALTH_SENTRY_DSN"] = ""
    return cfg, problems


def load_thresholds(cfg: dict, defaults: dict) -> dict:
    """Thresholds can be changed in the config with the SERVER_HEALTH_T_ prefix
    (SERVER_HEALTH_T_DB_WARN_GB=4)."""
    th = dict(defaults)
    for key, default in defaults.items():
        raw = cfg.get(f"SERVER_HEALTH_T_{key}")
        if raw in (None, ""):
            continue
        try:
            th[key] = type(default)(float(raw)) if isinstance(default, int) else float(raw)
        except ValueError:
            pass
    return th


def is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def csv(value: str | None) -> list[str]:
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


register({
    "en": {
        "config.unreadable": "{path} exists but cannot be read: no alert goes out without it",
        "config.missing": "{path} does not exist: with no channel configured, the report only goes to the log",
        "config.bad_url": "{key} does not look like a URL (placeholder not replaced?)",
        "config.app_hook_without_server": "an app webhook is set without SERVER_HEALTH_DISCORD_WEBHOOK: "
                                          "server alerts have no channel",
        "config.bad_dsn": "SERVER_HEALTH_SENTRY_DSN does not look like a DSN (https://<key>@<host>/<id>)",
    },
    "pt-BR": {
        "config.unreadable": "{path} existe mas não é legível: nenhum alerta sai sem ele",
        "config.missing": "{path} não existe: sem canal configurado, o relatório só vai ao log",
        "config.bad_url": "{key} não parece uma URL (placeholder não trocado?)",
        "config.app_hook_without_server": "há webhook de app sem SERVER_HEALTH_DISCORD_WEBHOOK: "
                                          "os alertas do servidor ficam sem canal",
        "config.bad_dsn": "SERVER_HEALTH_SENTRY_DSN não parece um DSN (https://<key>@<host>/<id>)",
    },
})
