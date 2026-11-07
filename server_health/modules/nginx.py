"""Requests per app from Dokku's nginx log: people and bots apart, 5xx, a bot in
volume, a traffic drop, and the bot detail of the weekly report.

The User-Agent is what the visitor declares. Behind a CDN the IP in the log is the
CDN's, and nothing proves a "Googlebot" is Google: the numbers are of declared bots.
Rule order matters: the first rule that matches wins."""

from __future__ import annotations

import re
from datetime import datetime

from ..i18n import fdur, fnum, t, weekday
from ..metric import Metric
from ..module import Module
from ..text import clip

CATEGORIES = ("search", "ai_training", "ai_search", "preview", "seo", "uptime", "scan", "script", "other_bot")
PERSON, MONITOR = "person", "monitor"
HISTORY_KEEP_DAYS = 35
BOT_PAGES_PER_DAY = 200  # pages kept per app, category and day (the most requested)

BOT_RULES = [
    ("ai_search", "OAI-SearchBot", r"OAI-SearchBot"),
    ("ai_search", "ChatGPT-User", r"ChatGPT-User"),
    ("ai_search", "Claude-User", r"Claude-User"),
    ("ai_search", "Claude-SearchBot", r"Claude-SearchBot"),
    ("ai_search", "Perplexity-User", r"Perplexity-User"),
    ("ai_search", "PerplexityBot", r"PerplexityBot"),
    ("ai_search", "MistralAI-User", r"MistralAI-User"),
    ("ai_search", "DuckAssistBot", r"DuckAssistBot"),
    ("ai_training", "GPTBot", r"GPTBot"),
    ("ai_training", "ClaudeBot", r"ClaudeBot|anthropic-ai"),
    ("ai_training", "CCBot", r"CCBot"),
    ("ai_training", "Bytespider", r"Bytespider"),
    ("ai_training", "Amazonbot", r"Amazonbot"),
    ("ai_training", "meta-externalagent", r"meta-external"),
    ("ai_training", "Google-CloudVertexBot", r"Google-CloudVertexBot"),
    ("ai_training", "cohere-ai", r"cohere-(ai|training)"),
    ("ai_training", "Diffbot", r"Diffbot"),
    ("ai_training", "AI2Bot", r"AI2Bot"),
    ("ai_training", "Timpibot", r"Timpibot"),
    ("ai_training", "ImagesiftBot", r"ImagesiftBot"),
    ("ai_training", "omgili", r"omgili"),
    ("preview", "WhatsApp", r"WhatsApp"),
    ("preview", "Facebook", r"facebookexternalhit|facebookcatalog"),
    ("preview", "Twitter", r"Twitterbot"),
    ("preview", "Discord", r"Discordbot"),
    ("preview", "Slack", r"Slackbot|Slack-ImgProxy"),
    ("preview", "Telegram", r"TelegramBot"),
    ("preview", "LinkedIn", r"LinkedInBot"),
    ("preview", "Pinterest", r"Pinterest"),
    ("preview", "Skype", r"SkypeUriPreview"),
    ("search", "Googlebot", r"Googlebot|Google-InspectionTool|GoogleOther|Storebot-Google|AdsBot-Google"),
    ("search", "bingbot", r"bingbot|BingPreview"),
    ("search", "DuckDuckBot", r"DuckDuckBot"),
    ("search", "YandexBot", r"YandexBot|YandexImages"),
    ("search", "Applebot", r"Applebot"),
    ("search", "Baiduspider", r"Baiduspider"),
    ("search", "PetalBot", r"PetalBot"),
    ("search", "SeznamBot", r"SeznamBot"),
    ("search", "Qwant", r"Qwantify|QwantBot"),
    ("seo", "AhrefsBot", r"AhrefsBot"),
    ("seo", "SemrushBot", r"SemrushBot"),
    ("seo", "MJ12bot", r"MJ12bot"),
    ("seo", "DotBot", r"DotBot"),
    ("seo", "DataForSeoBot", r"DataForSeoBot"),
    ("seo", "BLEXBot", r"BLEXBot"),
    ("seo", "serpstatbot", r"serpstatbot"),
    ("seo", "Screaming Frog", r"Screaming Frog"),
    ("uptime", "UptimeRobot", r"UptimeRobot"),
    ("uptime", "Pingdom", r"Pingdom"),
    ("uptime", "Better Stack", r"Better ?Stack|Better Uptime"),
    ("uptime", "StatusCake", r"StatusCake"),
    ("scan", "zgrab", r"zgrab"),
    ("scan", "Nuclei", r"Nuclei"),
    ("scan", "sqlmap", r"sqlmap"),
    ("scan", "Nikto", r"Nikto"),
    ("scan", "masscan", r"masscan"),
    ("scan", "CensysInspect", r"CensysInspect"),
    ("scan", "Expanse", r"Expanse"),
    ("scan", "LeakIX", r"l9explore|l9tcpid|LeakIX"),
    ("scan", "InternetMeasurement", r"InternetMeasurement"),
    ("script", "curl", r"^curl/"),
    ("script", "Wget", r"Wget"),
    ("script", "Python", r"python-requests|python-urllib|aiohttp|python-httpx|Python/"),
    ("script", "Go", r"Go-http-client"),
    ("script", "Node", r"axios|node-fetch|undici"),
    ("script", "Java", r"^Java/|okhttp|Apache-HttpClient"),
    ("script", "headless browser", r"HeadlessChrome|PhantomJS|Puppeteer|Playwright"),
    ("script", "Scrapy", r"Scrapy"),
    ("script", "libwww-perl", r"libwww-perl"),
]
BOT_RULES_RE = [(cat, name, re.compile(rx, re.I)) for cat, name, rx in BOT_RULES]
GENERIC_BOT_RE = re.compile(r"([A-Za-z0-9_.-]*(?:bot|crawl|spider|scrap|slurp)[A-Za-z0-9_.-]*)", re.I)
# Paths only an attack bot asks a Rails app for (nothing here is PHP or WordPress).
ATTACK_PATH_RE = re.compile(
    r"\.(?:php\d?|asp|aspx|jsp|cgi)(?:$|/)|/\.env|/\.git|/\.aws|/\.ssh|/\.DS_Store|/wp-|/wordpress|"
    r"/phpmyadmin|/xmlrpc|/cgi-bin|/vendor/phpunit|/actuator|/boaform|/server-status|/HNAP1|/owa/|"
    r"/autodiscover|/config\.json|/\.vscode|/telescope|/solr/|/console/", re.I)
NGINX_LINE_RE = re.compile(r'^(\S+) \S+ \S+ \[[^\]]*\] "([^"]*)" (\d{3}) \S+ "[^"]*" "([^"]*)"')
SEGMENT_ID_RE = re.compile(r"^(?:\d+|[0-9a-f-]{32,36}|[A-Za-z0-9_-]{16,}|(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9_-]{6,})$")


def classify_request(remote: str, path: str, ua: str) -> tuple[str, str]:
    """(category, name). PERSON for the rest, MONITOR for the monitor's own /up (the
    only local origin: everything else comes through the CDN)."""
    if remote in ("127.0.0.1", "::1") or ua.startswith("server-health/"):
        return MONITOR, "server-health"
    if ATTACK_PATH_RE.search(path):
        return "scan", attack_name(path)
    if not ua or ua == "-":
        return "script", "no User-Agent"
    for cat, name, rx in BOT_RULES_RE:
        if rx.search(ua):
            return cat, name
    m = GENERIC_BOT_RE.search(ua)
    if m:
        return "other_bot", m.group(1)[:40] or "unnamed"
    return PERSON, ""


def attack_name(path: str) -> str:
    """The path tried, without the query: in a scan it is generic (/wp-login.php, /.env)."""
    return clip(normalize_path(path), 60)


def normalize_path(path: str) -> str:
    """Path without the query and with identifiers turned into :id. The raw path may
    carry a record id or a code; the normalized one only says which page it was."""
    path = path.split("?", 1)[0].split("#", 1)[0] or "/"
    parts = [(":id" if SEGMENT_ID_RE.match(seg) else seg) for seg in path.split("/")]
    return clip("/".join(parts), 80)


def parse_nginx_line(line: str) -> tuple[str, str, str, str] | None:
    """(origin, path, status, User-Agent) from nginx's combined format."""
    m = NGINX_LINE_RE.match(line)
    if not m:
        return None
    remote, request, status, ua = m.groups()
    parts = request.split(" ")
    return remote, (parts[1] if len(parts) >= 2 else "/"), status, ua


def category(cat: str) -> str:
    return t(f"nginx.cat.{cat}") if cat in CATEGORIES else cat


def merge_bots(data: dict, rounds: dict, now: float, tz) -> dict:
    """Adds the round's detail (per bot and per page) to the day's total, in the
    monitor's time zone, and keeps 35 days."""
    day = datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d")
    days = data.setdefault("days", {})
    bucket = days.setdefault(day, {})
    for app, r in rounds.items():
        a = bucket.setdefault(app, {"names": {}, "pages": {}})
        for k, n in r.get("names", {}).items():
            a["names"][k] = a["names"].get(k, 0) + n
        for k, n in r.get("pages", {}).items():
            a["pages"][k] = a["pages"].get(k, 0) + n
        by_cat: dict[str, list] = {}
        for k, n in a["pages"].items():
            by_cat.setdefault(k.split("|", 1)[0], []).append((k, n))
        a["pages"] = {k: n for items in by_cat.values() for k, n in sorted(items, key=lambda kv: -kv[1])[:BOT_PAGES_PER_DAY]}
    cutoff = datetime.fromtimestamp(now - HISTORY_KEEP_DAYS * 86400, tz).strftime("%Y-%m-%d")
    data["days"] = {d: v for d, v in days.items() if d >= cutoff}
    return data


def sum_bot_categories(rows: list[dict], app: str) -> dict[str, int]:
    cats: dict[str, int] = {}
    for h in rows:
        for c, n in ((h.get("bots") or {}).get(app) or {}).items():
            cats[c] = cats.get(c, 0) + n
    return cats


def change_txt(cur: float, prev: float) -> str:
    """Comparison with the previous week, in %, or the previous number when it was zero."""
    if prev <= 0:
        return t("report.prev_week_number", n=fnum(prev))
    delta = (cur - prev) * 100 / prev
    return t("report.prev_week_pct", pct=f"{'+' if delta >= 0 else ''}{fnum(delta)}")


class Nginx(Module):
    name = "nginx"
    order = 60
    # Dokku writes /var/log/nginx/<app>-access.log; another setup sets its own pattern
    # here, or `nginx_log` in the app's TOML. Without a log, nothing is read.
    defaults = {"SERVER_HEALTH_NGINX_LOG": "/var/log/nginx/{app}-access.log"}
    thresholds = {
        "NGINX_5XX_WARN_COUNT": 10,
        "NGINX_5XX_WARN_PCT": 5.0,
        "NGINX_5XX_CRIT_COUNT": 50,
        "NGINX_5XX_CRIT_PCT": 20.0,
        # One bot (besides search engines, link previews and uptime monitors) with this
        # many requests in 5 min: an aggressive crawler weighing on the CPU.
        "BOT_VOLUME_WARN": 300,
        # Traffic drop: people in the last 30 min against the median of the same half
        # hour in the 7 previous days. Only with 14 days of history and a baseline.
        "TRAFFIC_MIN_BASELINE": 50,
        "TRAFFIC_DROP_PCT": 20,
        "TRAFFIC_HISTORY_DAYS": 14,
    }

    def app(self, ctx, app):
        conf = ctx.app_config(app)
        path = conf.nginx_log if conf and conf.nginx_log else ctx.cfg["SERVER_HEALTH_NGINX_LOG"].replace("{app}", app)
        offsets = ctx.state.get("nginx", {})
        prev = offsets.get(app)
        st = ctx.src.stat(path)
        if not st:
            return
        label = t("nginx.label")
        upd = ctx.updates.setdefault("nginx", dict(offsets))
        if not prev:
            upd[app] = [st.st_ino, st.st_size]
            ctx.add(Metric(f"app.{app}.http", app, label, t("nginx.first")))
            return
        offset = prev[1] if prev[0] == st.st_ino and st.st_size >= prev[1] else 0
        got = ctx.src.read_from(path, offset, 50 * 1024 * 1024)
        if not got:
            return
        data, ino, _ = got
        upd[app] = [ino, offset + len(data)]
        total = err = people = people_err = 0
        cats: dict[str, int] = {}
        names: dict[str, int] = {}      # "category|name" -> requests
        pages: dict[str, int] = {}      # "category|normalized path" -> requests (bots only)
        for line in data.decode("utf-8", errors="replace").splitlines():
            parsed = parse_nginx_line(line)
            if parsed:
                remote, req_path, status, ua = parsed
            else:
                m = re.search(r'"\s(\d{3})\s', line)
                if not m:
                    continue
                remote, req_path, status, ua = "", "/", m.group(1), "?"
            cat, name = classify_request(remote, req_path, ua)
            if cat == MONITOR:
                continue
            is_5xx = status.startswith("5")
            total += 1
            err += is_5xx
            if cat == PERSON:
                people += 1
                people_err += is_5xx
                continue
            cats[cat] = cats.get(cat, 0) + 1
            names[f"{cat}|{name}"] = names.get(f"{cat}|{name}", 0) + 1
            page = name if cat == "scan" else normalize_path(req_path)
            pages[f"{cat}|{page}"] = pages.get(f"{cat}|{page}", 0) + 1
        ctx.facts["req"][app] = [total, err]
        ctx.facts["ppl"][app] = [people, people_err]
        ctx.facts["bots"][app] = cats
        ctx.updates.setdefault("bots_round", {})[app] = {"names": names, "pages": pages}
        pct = err * 100 / total if total else 0.0
        th = ctx.th
        level = "OK"
        if err >= th["NGINX_5XX_CRIT_COUNT"] and pct >= th["NGINX_5XX_CRIT_PCT"]:
            level = "CRIT"
        elif err >= th["NGINX_5XX_WARN_COUNT"] and pct >= th["NGINX_5XX_WARN_PCT"]:
            level = "WARN"
        elapsed = max(1, ctx.now - (ctx.state.get("last_run") or ctx.now - 300))
        bots_total = total - people
        bots_txt = ""
        if bots_total:
            top = sorted(cats.items(), key=lambda kv: -kv[1])[:2]
            bots_txt = t("nginx.bots", n=fnum(bots_total), top=", ".join(f"{category(c)} {fnum(n)}" for c, n in top))
        ctx.add(Metric(f"app.{app}.http", app, label,
                       t("nginx.value", people=fnum(people), elapsed=fdur(elapsed), bots=bots_txt, err=fnum(err),
                         pct=fnum(pct, 1)), level, t("nginx.detail", app=app)))
        self.bot_volume(ctx, app, names, elapsed)

    def bot_volume(self, ctx, app, names: dict[str, int], elapsed: float):
        exempt = ("search", "preview", "uptime")
        per5 = {k: n * 300 / elapsed for k, n in names.items() if k.split("|", 1)[0] not in exempt}
        limit = ctx.th["BOT_VOLUME_WARN"]
        heavy = sorted(((k, n) for k, n in per5.items() if n >= limit), key=lambda kv: -kv[1])
        label = t("nginx.volume")
        if not heavy:
            ctx.add(Metric(f"app.{app}.bots", app, label, t("nginx.volume.none")))
            return
        shown = ", ".join(f"{k.split('|', 1)[1]} ({category(k.split('|', 1)[0])}) {fnum(n)}" for k, n in heavy[:3])
        ctx.add(Metric(f"app.{app}.bots", app, label, t("nginx.volume.value", shown=shown), "WARN",
                       t("nginx.volume.detail", limit=fnum(limit))))

    def after_app(self, ctx, app):
        """People in the last 30 min against the median of the same half hour in the 7
        previous days. The /up is asked from inside the machine and does not see broken
        DNS, the CDN blocking or a wrong redirect; a drop in visitors does."""
        if app not in (ctx.facts.get("ppl") or {}):
            return
        rows = [h for h in ctx.history if app in (h.get("ppl") or {})]
        th = ctx.th
        if not rows or rows[0]["ts"] > ctx.now - th["TRAFFIC_HISTORY_DAYS"] * 86400:
            return
        span = 1800

        def people(start: float, end: float) -> tuple[int, int]:
            sel = [h for h in rows if start < h["ts"] <= end]
            return sum(h["ppl"][app][0] for h in sel), len(sel)

        base = []
        for d in range(1, 8):
            n, samples = people(ctx.now - d * 86400 - span, ctx.now - d * 86400)
            if samples >= 3:
                base.append(n)
        if len(base) < 5:
            return
        base.sort()
        baseline = base[len(base) // 2]
        cur, _ = people(ctx.now - span, ctx.now)
        cur += ctx.facts["ppl"][app][0]
        level = "OK"
        if baseline >= th["TRAFFIC_MIN_BASELINE"] and cur < baseline * th["TRAFFIC_DROP_PCT"] / 100:
            level = "WARN"
        ctx.add(Metric(f"app.{app}.traffic", app, t("nginx.traffic"),
                       t("nginx.traffic.value", cur=fnum(cur), usual=fnum(baseline)), level,
                       t("nginx.traffic.detail"), persist_min=10))

    def save(self, ctx, store):
        if ctx.updates.get("bots_round"):
            store.save_bots(merge_bots(store.load_bots(), ctx.updates["bots_round"], ctx.now, ctx.tz))

    # -- reports -----------------------------------------------------------------

    def daily_lines(self, r, section, app):
        if app is None:
            return []
        day = r.window(r.now - 86400, r.now + 1)
        total = sum((h.get("req") or {}).get(app, [0, 0])[0] for h in day)
        errs = sum((h.get("req") or {}).get(app, [0, 0])[1] for h in day)
        if not total:
            return []
        people = sum((h.get("ppl") or {}).get(app, [0, 0])[0] for h in day)
        out = [t("nginx.daily", total=fnum(total), people=fnum(people), err=fnum(errs), pct=fnum(errs * 100 / total, 2))]
        cats = sum_bot_categories(day, app)
        if cats:
            bots = sum(cats.values())
            top = ", ".join(f"{category(c)} {fnum(n)}" for c, n in sorted(cats.items(), key=lambda kv: -kv[1])[:4])
            out.append(t("nginx.daily.bots", n=fnum(bots), pct=fnum(bots * 100 / total), top=top))
        return out

    def weekly_app_lines(self, r, app):
        cur = r.window(r.now - r.WEEK, r.now)
        prev = r.window(r.now - 2 * r.WEEK, r.now - r.WEEK)
        total = sum((h.get("req") or {}).get(app, [0, 0])[0] for h in cur)
        if not total:
            return []
        errs = sum((h.get("req") or {}).get(app, [0, 0])[1] for h in cur)
        people = sum((h.get("ppl") or {}).get(app, [0, 0])[0] for h in cur)
        people_prev = sum((h.get("ppl") or {}).get(app, [0, 0])[0] for h in prev)
        by_hour: dict[tuple, int] = {}
        for h in cur:
            n = (h.get("ppl") or {}).get(app, [0, 0])[0]
            if n:
                when = datetime.fromtimestamp(h["ts"], r.tz)
                by_hour[(when.weekday(), when.hour)] = by_hour.get((when.weekday(), when.hour), 0) + n
        peak = ""
        if by_hour:
            (wd, hr), _ = max(by_hour.items(), key=lambda kv: kv[1])
            peak = t("nginx.weekly.peak", day=weekday(wd), hour=hr)
        return [(20, t("nginx.weekly.people", n=fnum(people), cmp=change_txt(people, people_prev), peak=peak)),
                (21, t("nginx.weekly.5xx", err=fnum(errs), total=fnum(total), pct=fnum(errs * 100 / total, 2)))]

    def weekly_app_fields(self, r, section, app):
        lines = self._weekly_bots(r, app)
        if not lines:
            return []
        return [{"name": t("nginx.weekly.bots_field", section=section), "value": r.bullets(lines)}]

    def _weekly_bots(self, r, app) -> list[str]:
        bots = r.bots
        cur_days = r.bot_days(r.now - r.WEEK, r.now)
        if not cur_days:
            return []
        names: dict[str, int] = {}
        pages: dict[str, int] = {}
        for d in cur_days:
            a = bots["days"][d].get(app) or {}
            for k, n in a.get("names", {}).items():
                names[k] = names.get(k, 0) + n
            for k, n in a.get("pages", {}).items():
                pages[k] = pages.get(k, 0) + n
        if not names:
            return []
        seen_before = set()
        for d in r.bot_days(r.now - 5 * r.WEEK, r.now - r.WEEK):
            seen_before |= set((bots["days"][d].get(app) or {}).get("names", {}))
        # Total and comparison from the history (both weeks from the same source);
        # bots.json only gives the names and the pages.
        cur_hist = r.window(r.now - r.WEEK, r.now)
        total = sum((h.get("req") or {}).get(app, [0, 0])[0] for h in cur_hist)
        bots_total = sum(sum_bot_categories(cur_hist, app).values()) or sum(names.values())
        prev_total = sum(sum_bot_categories(r.window(r.now - 2 * r.WEEK, r.now - r.WEEK), app).values())
        share = t("nginx.weekly.share", pct=fnum(bots_total * 100 / total)) if total else ""
        out = [t("nginx.weekly.total", n=fnum(bots_total), share=share, cmp=change_txt(bots_total, prev_total))]
        by_cat: dict[str, list] = {}
        for k, n in names.items():
            cat, name = k.split("|", 1)
            by_cat.setdefault(cat, []).append((name, n))
        for cat in sorted(by_cat, key=lambda c: -sum(n for _, n in by_cat[c])):
            items = sorted(by_cat[cat], key=lambda kv: -kv[1])
            top = ", ".join(f"{name} {fnum(n)}" for name, n in items[:3])
            out.append(f"{category(cat)}: {fnum(sum(n for _, n in items))} ({top})")
        new = sorted(k.split("|", 1)[1] for k in names if k not in seen_before and k.split("|", 1)[0] != "scan")
        if seen_before and new:
            out.append(t("nginx.weekly.new", names=", ".join(new[:6])))

        def top_pages(cats: tuple, n: int = 5) -> str:
            agg: dict[str, int] = {}
            for k, c in pages.items():
                cat, page = k.split("|", 1)
                if cat in cats:
                    agg[page] = agg.get(page, 0) + c
            return ", ".join(f"{page} {fnum(c)}" for page, c in sorted(agg.items(), key=lambda kv: -kv[1])[:n])

        for cats, key in ((("ai_training", "ai_search"), "nginx.weekly.pages_ai"),
                          (("search",), "nginx.weekly.pages_search"),
                          (("scan",), "nginx.weekly.pages_scan")):
            txt = top_pages(cats)
            if txt:
                out.append(f"{t(key)}: {txt}")
        return out

    strings = {
        "en": {
            "nginx.cat.search": "search engines",
            "nginx.cat.ai_training": "AI training",
            "nginx.cat.ai_search": "AI search and assistants",
            "nginx.cat.preview": "link previews",
            "nginx.cat.seo": "SEO",
            "nginx.cat.uptime": "uptime monitors",
            "nginx.cat.scan": "scanners",
            "nginx.cat.script": "scripts",
            "nginx.cat.other_bot": "other bots",
            "nginx.label": "Requests",
            "nginx.first": "1st sample",
            "nginx.bots": " · bots {n} ({top})",
            "nginx.value": "people {people} in {elapsed}{bots} · 5xx {err} ({pct}%)",
            "nginx.detail": "5xx responses in nginx; `dokku logs {app} -n 300`",
            "nginx.volume": "Bot in volume",
            "nginx.volume.none": "none",
            "nginx.volume.value": "{shown} in 5 min",
            "nginx.volume.detail": "a single bot with {limit} or more requests in 5 min weighs on the CPU; blocking "
                                   "its User-Agent at the CDN (on Cloudflare: Security, WAF) solves it at the edge",
            "nginx.traffic": "People in 30 min",
            "nginx.traffic.value": "{cur} (usual at this hour: {usual})",
            "nginx.traffic.detail": "visitors well below the usual with `/up` answering: DNS, the CDN (a rule or a "
                                    "block), the certificate or a broken redirect. Open the site from outside",
            "nginx.daily": "Requests: {total} · people {people} · 5xx {err} ({pct}%)",
            "nginx.daily.bots": "Bots: {n} ({pct}% of the requests) · {top}",
            "nginx.weekly.peak": " · peak {day} {hour}h",
            "nginx.weekly.people": "People: {n} requests ({cmp}){peak}",
            "nginx.weekly.5xx": "5xx: {err} of {total} ({pct}%)",
            "nginx.weekly.bots_field": "{section} · bots",
            "nginx.weekly.share": " ({pct}% of the requests)",
            "nginx.weekly.total": "Total: {n}{share} · {cmp}",
            "nginx.weekly.new": "New this week: {names}",
            "nginx.weekly.pages_ai": "Pages most read by AI",
            "nginx.weekly.pages_search": "Pages most read by search engines",
            "nginx.weekly.pages_scan": "Attack paths most tried",
        },
        "pt-BR": {
            "nginx.cat.search": "buscadores",
            "nginx.cat.ai_training": "IA de treino",
            "nginx.cat.ai_search": "IA de busca e de usuário",
            "nginx.cat.preview": "prévias de link",
            "nginx.cat.seo": "SEO",
            "nginx.cat.uptime": "monitores externos",
            "nginx.cat.scan": "varredura",
            "nginx.cat.script": "scripts",
            "nginx.cat.other_bot": "outros robôs",
            "nginx.label": "Requisições",
            "nginx.first": "1a amostra",
            "nginx.bots": " · robôs {n} ({top})",
            "nginx.value": "pessoas {people} em {elapsed}{bots} · 5xx {err} ({pct}%)",
            "nginx.detail": "respostas 5xx no nginx; `dokku logs {app} -n 300`",
            "nginx.volume": "Robô em volume",
            "nginx.volume.none": "nenhum",
            "nginx.volume.value": "{shown} em 5 min",
            "nginx.volume.detail": "um robô só com {limit} ou mais requisições em 5 min pesa na CPU; bloquear pelo "
                                   "User-Agent na borda (na Cloudflare: Security, WAF) resolve antes de chegar aqui",
            "nginx.traffic": "Pessoas em 30 min",
            "nginx.traffic.value": "{cur} (habitual nesta hora: {usual})",
            "nginx.traffic.detail": "visitantes bem abaixo do habitual com o `/up` respondendo: DNS, a CDN (regra ou "
                                    "bloqueio), certificado ou um redirect quebrado. Abra o site de fora",
            "nginx.daily": "Requisições: {total} · pessoas {people} · 5xx {err} ({pct}%)",
            "nginx.daily.bots": "Robôs: {n} ({pct}% das requisições) · {top}",
            "nginx.weekly.peak": " · pico {day} {hour}h",
            "nginx.weekly.people": "Pessoas: {n} requisições ({cmp}){peak}",
            "nginx.weekly.5xx": "5xx: {err} de {total} ({pct}%)",
            "nginx.weekly.bots_field": "{section} · robôs",
            "nginx.weekly.share": " ({pct}% das requisições)",
            "nginx.weekly.total": "Total: {n}{share} · {cmp}",
            "nginx.weekly.new": "Novos na semana: {names}",
            "nginx.weekly.pages_ai": "Páginas mais lidas pela IA",
            "nginx.weekly.pages_search": "Páginas mais lidas pelos buscadores",
            "nginx.weekly.pages_scan": "Caminhos de ataque mais tentados",
        },
    }
