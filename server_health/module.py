"""Modules: what is measured, each in its own file.

A module is a subclass of `Module` with a unique `name`. It declares the modules it
needs (`requires`), its thresholds and settings, its strings in each language, and
how to tell whether this machine has what it measures (`detect`, used by
SERVER_HEALTH_MODULES=auto). Then it implements only the phases it needs.

A round runs the phases in this order, each one across the enabled modules in
registry order:

  before(ctx)            before anything (Dokku: is a deploy running?)
  host(ctx)              the machine (memory, disk, systemd, clock...)
  discover(ctx)          what runs here (Docker containers, Dokku apps)
  services(ctx)          each database service
  app(ctx, app)          each app
  daily(ctx)             only in the daily summary
  after_app(ctx, app)    each app again, after all of them (deploy check, traffic)
  finish(ctx)            last (section names, TLS)

Report hooks add lines to the daily summary and the weekly report; see report.py.

Third-party modules: a .py file in SERVER_HEALTH_MODULES_DIR that defines `MODULE`
(an instance) or `MODULES` (a list). It only loads when its name is listed in
SERVER_HEALTH_MODULES, and only if the file belongs to root (or to the user running
the monitor) and nobody else can write it: the monitor runs as root.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from .i18n import register, t


class Module:
    name = ""
    requires: tuple[str, ...] = ()
    thresholds: dict = {}
    defaults: dict = {}
    strings: dict = {}
    # Sort key of the lines this module adds to the reports; lower comes first.
    order = 100

    def detect(self, src, cfg: dict) -> bool:
        """Whether this machine has what the module measures (for "auto")."""
        return True

    # Phases (all optional) ------------------------------------------------------
    def before(self, ctx): ...
    def host(self, ctx): ...
    def discover(self, ctx): ...
    def services(self, ctx): ...
    def app(self, ctx, app: str): ...
    def daily(self, ctx): ...
    def after_app(self, ctx, app: str): ...
    def finish(self, ctx): ...

    # Database hooks (the postgres module calls them) ------------------------------
    def service_queries(self, ctx, svc: str) -> list[str]:
        """Statements added to the per-service base query."""
        return []

    def app_queries(self, ctx, app: str, svc: str) -> list[str]:
        """Statements added to the per-app query (one read-only session per app)."""
        return []

    def app_results(self, ctx, app: str, res: dict): ...

    def save(self, ctx, store):
        """After a normal round, to keep something of its own (nginx: bots.json)."""

    # Report hooks -------------------------------------------------------------------
    def daily_lines(self, r, section: str, app: str | None) -> list[str]:
        """Lines for the "Last 24h" block of a section (app is None for the server)."""
        return []

    def weekly_app_lines(self, r, app: str) -> list[tuple[int, str]]:
        """(position, line) for an app's block in the weekly report."""
        return []

    def weekly_app_fields(self, r, section: str, app: str) -> list[dict]:
        return []

    def weekly_fields(self, r) -> list[tuple[int, dict]]:
        """(position, field) for the server-wide part of the weekly report."""
        return []


PHASES = ("before", "host", "discover", "services", "app", "daily", "after_app", "finish")


def builtin_modules() -> list[Module]:
    from .modules import BUILTIN
    return [cls() for cls in BUILTIN]


def _safe_file(path: Path) -> str | None:
    """None when the file can be loaded; otherwise the reason."""
    try:
        st = path.stat()
    except OSError as e:
        return str(e)
    if st.st_uid not in (0, os.geteuid()):
        return t("module.err.owner")
    if st.st_mode & 0o022:
        return t("module.err.writable")
    return None


def load_plugins(directory: str, wanted: set[str]) -> tuple[list[Module], list[str]]:
    """Loads the third-party modules named in the config. Returns (modules, problems)."""
    found: list[Module] = []
    problems: list[str] = []
    d = Path(directory)
    if not wanted or not d.is_dir():
        return found, problems
    for path in sorted(d.glob("*.py")):
        if path.stem not in wanted:
            continue
        reason = _safe_file(path)
        if reason:
            problems.append(t("module.err.refused", file=str(path), reason=reason))
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"server_health_plugin_{path.stem}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:  # noqa: BLE001 - a broken plugin must not stop the monitor
            problems.append(t("module.err.load", file=str(path), error=f"{type(e).__name__}: {e}"))
            continue
        items = getattr(mod, "MODULES", None) or ([mod.MODULE] if hasattr(mod, "MODULE") else [])
        if not items:
            problems.append(t("module.err.empty", file=str(path)))
        for item in items:
            if isinstance(item, Module) and item.name:
                found.append(item)
            else:
                problems.append(t("module.err.empty", file=str(path)))
    return found, problems


def resolve(spec: str, available: list[Module], src, cfg: dict) -> tuple[list[Module], list[str]]:
    """Which modules run, in registry order. `spec` is "auto", a list, or "auto,-x"."""
    by_name = {m.name: m for m in available}
    parts = [p.strip() for p in (spec or "auto").split(",") if p.strip()]
    problems: list[str] = []
    auto = "auto" in parts
    include = {p for p in parts if p != "auto" and not p.startswith("-")}
    exclude = {p[1:] for p in parts if p.startswith("-")}
    for name in sorted((include | exclude) - set(by_name)):
        problems.append(t("module.err.unknown", name=name, known=", ".join(by_name)))

    chosen: list[Module] = []
    names: set[str] = set()
    for m in available:
        if m.name in exclude:
            continue
        explicit = m.name in include
        if not explicit and not (auto and m.detect(src, cfg)):
            continue
        missing = [r for r in m.requires if r not in names]
        if missing:
            if explicit:
                problems.append(t("module.err.requires", name=m.name, missing=", ".join(missing)))
            continue
        chosen.append(m)
        names.add(m.name)
    return chosen, problems


register({
    "en": {
        "module.err.unknown": "unknown module `{name}` in SERVER_HEALTH_MODULES (known: {known})",
        "module.err.requires": "module `{name}` needs `{missing}`, which is off: `{name}` does not run",
        "module.err.refused": "{file} not loaded: {reason}",
        "module.err.owner": "the file must belong to root",
        "module.err.writable": "group or others can write the file",
        "module.err.load": "{file} failed to load ({error})",
        "module.err.empty": "{file} defines no MODULE (a Module instance with a name)",
    },
    "pt-BR": {
        "module.err.unknown": "módulo `{name}` desconhecido em SERVER_HEALTH_MODULES (conhecidos: {known})",
        "module.err.requires": "o módulo `{name}` precisa de `{missing}`, que está desligado: `{name}` não roda",
        "module.err.refused": "{file} não carregado: {reason}",
        "module.err.owner": "o arquivo precisa ser do root",
        "module.err.writable": "o grupo ou outros usuários podem escrever no arquivo",
        "module.err.load": "{file} falhou ao carregar ({error})",
        "module.err.empty": "{file} não define MODULE (uma instância de Module com nome)",
    },
})
