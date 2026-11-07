"""Each app's health check (`/up` by default), asked by the machine itself, without
the CDN in the middle: it says whether the app answers, not whether the world sees it
(the nginx module's traffic drop covers that side)."""

from __future__ import annotations

from urllib.parse import urlparse

from .. import __version__
from ..i18n import fnum, t
from ..metric import Metric
from ..module import Module
from ..parse import to_float


class Http(Module):
    name = "http"
    order = 55
    defaults = {"SERVER_HEALTH_HEALTH_PATH": "/up"}
    thresholds = {"UP_SLOW_WARN_S": 2.0, "UP_FAIL_PERSIST_MIN": 5}

    # With no app to check (no Dokku, no `health_url`), the module does nothing and
    # costs nothing, so it stays on.

    @staticmethod
    def curl(ctx, key: str, extra: list[str], url: str) -> tuple[str, float]:
        rc, out, _ = ctx.src.run(key, [
            "curl", "-sk", "-o", "/dev/null", "-w", "%{http_code} %{time_total}", "--max-time", "10",
            "-A", f"server-health/{__version__}", *extra, url], timeout=15)
        parts = out.split()
        if len(parts) == 2:
            return parts[0], to_float(parts[1]) or 0.0
        return "000", 0.0

    def app(self, ctx, app):
        conf = ctx.app_config(app)
        if conf and conf.health_url:
            # An app outside Dokku: its URL, as given (it may go through a proxy or a CDN).
            host = urlparse(conf.health_url).hostname or ""
            ctx.app_domains[app] = [host] if conf.health_url.startswith("https://") and host else []
            code, secs = self.curl(ctx, f"curl_{app}_url", [], conf.health_url)
            shown = conf.health_url.split("://", 1)[1]
        elif app in ctx.dokku_apps:
            path = conf.health_path if conf and conf.health_path else ctx.cfg["SERVER_HEALTH_HEALTH_PATH"]
            rc, out, _ = ctx.src.run(f"dokku_domains_{app}", ["dokku", "domains:report", app, "--domains-app-vhosts"])
            domains = out.split() if rc == 0 else []
            ctx.app_domains[app] = domains
            if not domains:
                return
            dom = domains[0]
            code, secs = "000", 0.0
            for scheme, port in (("https", 443), ("http", 80)):
                code, secs = self.curl(ctx, f"curl_{app}_{scheme}", ["--resolve", f"{dom}:{port}:127.0.0.1"],
                                       f"{scheme}://{dom}{path}")
                if code != "000":
                    break
            shown = f"{dom}{path}"
        else:
            return
        value = t("http.value", code=code, ms=fnum(secs * 1000), url=shown)
        ctx.facts["up"][app] = [int(code) if code.isdigit() else 0, round(secs * 1000)]
        label = t("http.label")
        if not code.startswith("2"):
            ctx.add(Metric(f"app.{app}.up", app, label, value, "CRIT", t("http.fail"),
                           crit_persist_min=ctx.th["UP_FAIL_PERSIST_MIN"]))
        elif secs >= ctx.th["UP_SLOW_WARN_S"]:
            ctx.add(Metric(f"app.{app}.up", app, label, value, "WARN",
                           t("http.slow", limit=fnum(ctx.th["UP_SLOW_WARN_S"], 1)), persist_min=15))
        else:
            ctx.add(Metric(f"app.{app}.up", app, label, value))

    def weekly_app_lines(self, r, app):
        ups = [u for u in ((h.get("up") or {}).get(app) for h in r.window(r.now - r.WEEK, r.now)) if u]
        if not ups:
            return []
        ok = sum(1 for code, _ in ups if 200 <= code < 300)
        times = sorted(ms for code, ms in ups if 200 <= code < 300) or [0]
        return [(10, t("http.weekly", pct=fnum(ok * 100 / len(ups), 2), median=fnum(times[len(times) // 2]),
                       worst=fnum(times[-1])))]

    strings = {
        "en": {
            "http.label": "Health",
            "http.value": "{code} in {ms} ms ({url})",
            "http.fail": "the health check did not answer 2xx from the machine itself",
            "http.slow": "slow health check (warning >= {limit} s)",
            "http.weekly": "Available {pct}% of the rounds · median response {median} ms · worst {worst} ms",
        },
        "pt-BR": {
            "http.label": "Saúde",
            "http.value": "{code} em {ms} ms ({url})",
            "http.fail": "o health check não respondeu 2xx pela própria máquina",
            "http.slow": "health check lento (aviso >= {limit} s)",
            "http.weekly": "Disponível {pct}% das rodadas · resposta mediana {median} ms · pior {worst} ms",
        },
    }
