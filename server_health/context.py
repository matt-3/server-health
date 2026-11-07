"""What the modules share during a round."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from .appconfig import AppConfig, load_app_config
from .i18n import register, t
from .metric import Metric


class Context:
    """One round. Modules read the previous state (for rates and deltas) and write
    their state changes to `updates`, which are saved only in a normal round: the
    dry run and the summaries never move the counters."""

    def __init__(self, src, cfg: dict, th: dict, state: dict, history: list[dict], log,
                 daily: bool = False, weekly: bool = False):
        self.src = src
        self.cfg = cfg
        self.th = th
        self.state = state
        self.history = history
        self.log = log
        self.daily = daily or weekly
        self.weekly = weekly
        self.now = src.now()
        self.tz = ZoneInfo(cfg["SERVER_HEALTH_TZ"])
        self.metrics: list[Metric] = []
        # req: every request except the monitor's own; ppl: people only; bots: by
        # category. The per-bot and per-page detail goes to bots.json.
        self.facts: dict = {"ts": int(self.now), "dbs": {}, "req": {}, "ppl": {}, "bots": {}, "up": {}, "oom": 0}
        self.updates: dict = {}
        self.enabled: list[str] = []
        self.modules: list = []                    # enabled Module instances, in order
        self.apps: list[str] = []                  # Dokku apps measured this round
        self.services: list[str] = []              # Postgres services
        self.containers: list[dict] = []
        self.mem_by_container: dict[str, float] = {}
        self.deploy_running = False
        self.deploy_recent = False                 # a deploy ended since the previous round
        self.dokku_ok = False
        self.dokku_apps: set[str] = set()          # the apps that come from Dokku
        self.db_conn: dict[str, tuple] = {}        # service -> how to reach it (dokku, url or container)
        self.app_configs: list[AppConfig] = []
        self.app_domains: dict[str, list[str]] = {}
        self.app_service: dict[str, str] = {}      # app -> its Postgres service
        self.db_flags: dict[str, dict] = {}        # service -> flags from the base query (has_sq...)
        self.section_app: dict[str, str] = {}      # message section -> Dokku app
        self.db_app: dict[str, str] = {}           # database service -> Dokku app
        self.numbers: dict[str, dict] = {}         # summary indicators of this round, raw value
        self.jobs_week: dict[str, list] = {}       # app -> [(class, failures in 7 days)]

    # -- sections --------------------------------------------------------------

    @property
    def server_section(self) -> str:
        return t("section.server")

    @property
    def maintenance_section(self) -> str:
        return t("section.maintenance")

    @staticmethod
    def product_section(app: str) -> str:
        return f"{app} · {t('section.product')}"

    def add(self, m: Metric):
        self.metrics.append(m)

    def on(self, module: str) -> bool:
        return module in self.enabled

    # -- apps ------------------------------------------------------------------

    def load_app_configs(self, apps: list[str], dokku_ok: bool):
        """Reads apps.d. An app declared only in its TOML (with its own health URL, log
        or database) joins `self.apps`: that is how apps outside Dokku are monitored.
        A TOML naming a Dokku app that does not exist, and declaring nothing of its
        own, is most likely a typo: it becomes a config warning."""
        d = Path(self.cfg["SERVER_HEALTH_APPS_DIR"])
        files = sorted(d.glob("*.toml")) if d.is_dir() else []
        for f in files:
            conf, errors = load_app_config(str(f))
            name = conf.app if conf else f.stem
            if errors:
                self.add(Metric(f"cfg.{name}", name, t("appcfg.label"), t("appcfg.problems", n=len(errors), file=f.name),
                                "WARN", "; ".join(errors)[:900]))
            if not conf:
                continue
            if conf.app not in apps:
                if conf.standalone:
                    self.apps.append(conf.app)
                elif dokku_ok:
                    self.add(Metric(f"cfg.{name}.missing", name, t("appcfg.label"),
                                    t("appcfg.no_app", file=f.name, app=conf.app), "WARN"))
                    continue
            self.app_configs.append(conf)

    def name_sections(self):
        """With `name` in the TOML, an app's section becomes "My App (my-app)". Metric
        keys and the history keep the app's name."""
        names = {c.app: f"{c.name} ({c.app})" for c in self.app_configs if c.name}
        for m in self.metrics:
            base, sep, tail = m.section.partition(" · ")
            if base in names and (m.key.startswith(f"app.{base}.") or m.key.startswith(f"ind.{base}.")):
                m.section = names[base] + (sep + tail if tail else "")
        self.section_app = {names.get(a, a): a for a in self.apps}

    def setting(self, value: str) -> str:
        """`env:NAME` reads NAME from the config file (which only root can read)."""
        if value.startswith("env:"):
            return self.cfg.get(value[4:], "")
        return value

    def app_config(self, app: str) -> AppConfig | None:
        return next((c for c in self.app_configs if c.app == app), None)

    # -- history -----------------------------------------------------------------

    def history_avg(self, field: str, window: int) -> float | None:
        vals = [h[field] for h in self.history if h.get("ts", 0) >= self.now - window and h.get(field) is not None]
        return sum(vals) / len(vals) if vals else None

    def history_slope(self, field: str, window: int, min_span: int) -> float | None:
        """Growth per day between the first and the last sample of the window."""
        pts = [(h["ts"], h[field]) for h in self.history if h.get("ts", 0) >= self.now - window and h.get(field) is not None]
        if len(pts) < 2 or pts[-1][0] - pts[0][0] < min_span:
            return None
        return (pts[-1][1] - pts[0][1]) / ((pts[-1][0] - pts[0][0]) / 86400)

    def top_memory(self, n: int = 6) -> list[str]:
        from .i18n import fbytes
        items = sorted(self.mem_by_container.items(), key=lambda kv: kv[1], reverse=True)[:n]

        def short(name):
            if name.startswith("dokku.postgres."):
                return name[len("dokku.postgres."):] + " (pg)"
            return name
        return [f"{short(k)} {fbytes(v)}" for k, v in items]


register({
    "en": {
        "section.server": "Server",
        "section.maintenance": "Maintenance",
        "section.product": "product",
        "appcfg.label": "Monitor config",
        "appcfg.problems": "{n} problem(s) in {file}",
        "appcfg.no_app": "{file} names the app `{app}`, which does not exist in Dokku",
    },
    "pt-BR": {
        "section.server": "Servidor",
        "section.maintenance": "Manutenção",
        "section.product": "produto",
        "appcfg.label": "Config do monitor",
        "appcfg.problems": "{n} problema(s) em {file}",
        "appcfg.no_app": "{file} cita a app `{app}`, que não existe no Dokku",
    },
})
