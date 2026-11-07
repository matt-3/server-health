"""TLS certificate expiry of each domain, asked from the machine itself (port 443 on
127.0.0.1 with the domain's name), so it checks the certificate this server serves,
not the CDN's. The domains come from Dokku, from each app's `health_url` (https)
and from SERVER_HEALTH_TLS_DOMAINS.

Certificates change slowly: each domain is checked every TLS_CHECK_H hours and the
result is kept in the state."""

from __future__ import annotations

from ..config import csv
from ..i18n import fdate, fnum, t
from ..metric import Metric
from ..module import Module


class Tls(Module):
    name = "tls"
    order = 95
    defaults = {"SERVER_HEALTH_TLS_DOMAINS": ""}
    thresholds = {"TLS_WARN_DAYS": 21, "TLS_CRIT_DAYS": 7, "TLS_CHECK_H": 6}

    # The domains come from the apps (Dokku or `health_url`) and from the config. With
    # none, the module does nothing and costs nothing, so it stays on.

    def expiry(self, ctx, domain: str) -> float | None:
        if "tls" not in ctx.updates:
            # A domain not checked for 30 days (an app removed) leaves the cache.
            ctx.updates["tls"] = {d: v for d, v in (ctx.state.get("tls") or {}).items() if v[0] >= ctx.now - 30 * 86400}
        cache = ctx.updates["tls"]
        hit = cache.get(domain)
        if hit and ctx.now - hit[0] < ctx.th["TLS_CHECK_H"] * 3600:
            return hit[1]
        value = ctx.src.cert_expiry("127.0.0.1", 443, domain)
        cache[domain] = [int(ctx.now), value]
        return value

    def measure(self, ctx, key: str, section: str, domains: list[str]):
        dates = [(d, self.expiry(ctx, d)) for d in domains]
        dates = [(d, e) for d, e in dates if e is not None]
        if not dates:
            return
        domain, soonest = min(dates, key=lambda x: x[1])
        days = (soonest - ctx.now) / 86400
        th = ctx.th
        level = "CRIT" if days <= th["TLS_CRIT_DAYS"] else "WARN" if days <= th["TLS_WARN_DAYS"] else "OK"
        value = t("tls.value", domain=domain, days=fnum(days), date=fdate(soonest, ctx.tz, "fmt.day"))
        ctx.add(Metric(key, section, t("tls.label"), value, level, t("tls.detail", domain=domain)))

    def after_app(self, ctx, app):
        domains = ctx.app_domains.get(app) or []
        if domains:
            self.measure(ctx, f"app.{app}.tls", app, domains)

    def finish(self, ctx):
        extra = csv(ctx.cfg.get("SERVER_HEALTH_TLS_DOMAINS"))
        if extra:
            self.measure(ctx, "host.tls", ctx.server_section, extra)

    strings = {
        "en": {
            "tls.label": "TLS certificate",
            "tls.value": "{domain} expires in {days} days ({date})",
            "tls.detail": "renew the certificate of {domain} (`dokku letsencrypt:auto-renew` or your provider's); "
                          "an expired one takes the site down for every browser",
        },
        "pt-BR": {
            "tls.label": "Certificado TLS",
            "tls.value": "{domain} vence em {days} dias ({date})",
            "tls.detail": "renove o certificado de {domain} (`dokku letsencrypt:auto-renew` ou o do seu provedor); "
                          "vencido, o site cai para todo navegador",
        },
    }
