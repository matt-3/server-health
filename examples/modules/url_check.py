"""Example of a third-party module: checks that some URLs answer 2xx.

To use it on a server:

  sudo install -o root -g root -m 644 url_check.py /etc/server-health/modules.d/url_check.py

and in /etc/default/server-health:

  SERVER_HEALTH_MODULES=auto,url_check
  SERVER_HEALTH_URLS=https://example.com/,https://status.example.com/health

The file must belong to root and nobody else may write it: the monitor runs as root
and refuses it otherwise. A module only loads when its file name is listed in
SERVER_HEALTH_MODULES.

What a module can do is in server_health/module.py (the phases and the hooks) and in
docs/en/writing-a-module.md.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

from server_health.i18n import fnum, t
from server_health.metric import Metric
from server_health.module import Module


class UrlCheck(Module):
    name = "url_check"
    defaults = {"SERVER_HEALTH_URLS": ""}
    thresholds = {"URL_SLOW_WARN_S": 3.0, "URL_FAIL_PERSIST_MIN": 5}

    def detect(self, src, cfg):
        return bool(cfg.get("SERVER_HEALTH_URLS"))

    def host(self, ctx):
        for url in [u.strip() for u in ctx.cfg.get("SERVER_HEALTH_URLS", "").split(",") if u.strip()]:
            started = time.time()
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    code = resp.status
            except urllib.error.HTTPError as e:
                code = e.code
            except (urllib.error.URLError, OSError):
                code = 0
            secs = time.time() - started
            value = t("url_check.value", code=code or "-", ms=fnum(secs * 1000))
            key = f"url.{url}"
            if not 200 <= code < 300:
                ctx.add(Metric(key, ctx.server_section, url, value, "CRIT", t("url_check.down"),
                               crit_persist_min=ctx.th["URL_FAIL_PERSIST_MIN"]))
            elif secs >= ctx.th["URL_SLOW_WARN_S"]:
                ctx.add(Metric(key, ctx.server_section, url, value, "WARN", t("url_check.slow"), persist_min=15))
            else:
                ctx.add(Metric(key, ctx.server_section, url, value))

    strings = {
        "en": {
            "url_check.value": "{code} in {ms} ms",
            "url_check.down": "the URL did not answer 2xx",
            "url_check.slow": "the URL is slow",
        },
        "pt-BR": {
            "url_check.value": "{code} em {ms} ms",
            "url_check.down": "a URL não respondeu 2xx",
            "url_check.slow": "a URL está lenta",
        },
    }


MODULE = UrlCheck()
