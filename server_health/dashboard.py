"""The dashboard: one self-contained HTML page with the charts of the last days, built
from what the monitor already keeps (history.jsonl, alerts.jsonl, state.json).

No daemon, no open port and no login to maintain: the page is a file. It goes with the
weekly report on Discord, can be written with `--dashboard FILE`, or served on
127.0.0.1 with `--serve` and opened through an SSH tunnel."""

from __future__ import annotations

import html
import http.server
import socketserver
from datetime import datetime

from .i18n import fbytes, fdate, fdur, fnum, register, t
from .metric import LEVELS
from .store import Store

DAYS = 7
PALETTE = ["#4f7cff", "#e5484d", "#30a46c", "#f5a524", "#8e4ec6", "#12a594"]


def hourly(rows: list[dict], start: float, value) -> list[tuple[float, float]]:
    """Averages a value per hour. `value(row)` returns a number or None."""
    buckets: dict[int, list[float]] = {}
    for r in rows:
        ts = r.get("ts", 0)
        if ts < start:
            continue
        v = value(r)
        if v is None:
            continue
        buckets.setdefault(int(ts // 3600), []).append(v)
    return [(h * 3600, sum(vs) / len(vs)) for h, vs in sorted(buckets.items())]


def hourly_sum(rows: list[dict], start: float, value) -> list[tuple[float, float]]:
    buckets: dict[int, float] = {}
    for r in rows:
        ts = r.get("ts", 0)
        if ts < start:
            continue
        v = value(r)
        if v is None:
            continue
        buckets[int(ts // 3600)] = buckets.get(int(ts // 3600), 0) + v
    return [(h * 3600, v) for h, v in sorted(buckets.items())]


def chart(title: str, series: list[tuple[str, list[tuple[float, float]]]], start: float, end: float,
          fmt=lambda v: fnum(v), tz=None) -> str:
    """A line chart in inline SVG. Each series is (name, [(ts, value)])."""
    w, h, pad_l, pad_b, pad_t = 640, 180, 56, 22, 10
    values = [v for _, pts in series for _, v in pts]
    if not values:
        return f'<section class="card"><h3>{html.escape(title)}</h3><p class="empty">{t("dash.no_data")}</p></section>'
    top = max(values) or 1
    top *= 1.1

    def x(ts):
        return pad_l + (ts - start) / max(1, end - start) * (w - pad_l - 8)

    def y(v):
        return pad_t + (1 - v / top) * (h - pad_t - pad_b)

    grid = []
    for i in range(5):
        v = top * i / 4
        grid.append(f'<line x1="{pad_l}" x2="{w - 8}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                    f'<text x="{pad_l - 6}" y="{y(v) + 4:.1f}" class="axis" text-anchor="end">{html.escape(fmt(v))}</text>')
    days = []
    day = start - start % 86400 + 86400
    while day < end:
        label = fdate(day, tz, "fmt.day") if tz else ""
        days.append(f'<line x1="{x(day):.1f}" x2="{x(day):.1f}" y1="{pad_t}" y2="{h - pad_b}" class="grid"/>'
                    f'<text x="{x(day) + 3:.1f}" y="{h - 6}" class="axis">{html.escape(label)}</text>')
        day += 86400
    lines, legend = [], []
    for i, (name, pts) in enumerate(series):
        color = PALETTE[i % len(PALETTE)]
        if pts:
            d = " ".join(f"{'M' if j == 0 else 'L'}{x(ts):.1f},{y(v):.1f}" for j, (ts, v) in enumerate(pts))
            lines.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6"/>')
            last = pts[-1][1]
            legend.append(f'<span><i style="background:{color}"></i>{html.escape(name)} '
                          f'<b>{html.escape(fmt(last))}</b></span>')
    return (f'<section class="card"><h3>{html.escape(title)}</h3><div class="legend">{"".join(legend)}</div>'
            f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(title)}">{"".join(grid)}{"".join(days)}'
            f'{"".join(lines)}</svg></section>')


def render(store: Store, cfg: dict, label: str, tz, now: float) -> str:
    history = store.load_history()
    state = store.load_state()
    alerts = store.load_alerts()
    start = now - DAYS * 86400
    rows = [r for r in history if r.get("ts", 0) >= start]
    apps = sorted({a for r in rows for a in (r.get("req") or {})} | {a for r in rows for a in (r.get("up") or {})})
    dbs = sorted({d for r in rows for d in (r.get("dbs") or {})})

    cards = [
        chart(t("dash.memory"), [(t("dash.ram_used"), hourly(rows, start, lambda r: r.get("mem_pct"))),
                                 (t("dash.waiting"), hourly(rows, start, lambda r: r.get("psi_some300")))],
              start, now, lambda v: f"{fnum(v)}%", tz),
        chart(t("dash.load"), [("load", hourly(rows, start, lambda r: r.get("load1")))], start, now,
              lambda v: fnum(v, 2), tz),
        chart(t("dash.disk"), [(t("dash.disk_used"), hourly(rows, start, lambda r: r.get("disk_used")))], start, now,
              fbytes, tz),
        chart("Swap-out", [("KB/s", hourly(rows, start, lambda r: r.get("swapout_kbs")))], start, now,
              lambda v: f"{fnum(v)} KB/s", tz),
    ]
    if dbs:
        cards.append(chart(t("dash.databases"), [(d, hourly(rows, start, lambda r, d=d: (r.get("dbs") or {}).get(d)))
                                                  for d in dbs], start, now, fbytes, tz))
    for app in apps:
        cards.append(chart(t("dash.requests", app=app), [
            (t("dash.people"), hourly_sum(rows, start, lambda r, a=app: ((r.get("ppl") or {}).get(a) or [None])[0])),
            (t("dash.bots"), hourly_sum(rows, start, lambda r, a=app: sum(((r.get("bots") or {}).get(a) or {}).values())
                                        if a in (r.get("bots") or {}) else None)),
            ("5xx", hourly_sum(rows, start, lambda r, a=app: ((r.get("req") or {}).get(a) or [None, None])[1])),
        ], start, now, fnum, tz))
        cards.append(chart(t("dash.health", app=app), [
            (t("dash.response"), hourly(rows, start, lambda r, a=app: ((r.get("up") or {}).get(a) or [0, None])[1]
                                        if 200 <= ((r.get("up") or {}).get(a) or [0])[0] < 300 else None)),
        ], start, now, lambda v: f"{fnum(v)} ms", tz))

    open_rows = []
    for key, st in sorted((state.get("metrics") or {}).items(), key=lambda kv: -LEVELS.get(kv[1].get("alerted", "OK"), 0)):
        if st.get("alerted", "OK") == "OK":
            continue
        since = st.get("started")
        open_rows.append(f"<tr><td class='lv {st['alerted']}'>{html.escape(t('level.' + st['alerted']))}</td>"
                         f"<td>{html.escape(key)}</td><td>{html.escape(fdate(since, tz)) if since else ''}</td></tr>")
    recent = [a for a in alerts if a.get("ts", 0) >= start][-40:]
    alert_rows = []
    for a in reversed(recent):
        alert_rows.append(
            f"<tr><td>{html.escape(fdate(a['ts'], tz))}</td><td class='lv {a.get('after', 'OK')}'>"
            f"{html.escape(t('dash.kind.' + a.get('kind', 'new')))}</td><td>{html.escape(a.get('section', ''))} · "
            f"{html.escape(a.get('label', ''))}</td><td>{html.escape(fdur(a['duration'])) if a.get('duration') else ''}"
            "</td></tr>")

    generated = datetime.fromtimestamp(now, tz).strftime(t("fmt.full"))
    title = t("dash.title", label=label)
    return f"""<!doctype html>
<html lang="{'pt-BR' if t('dash.lang') == 'pt-BR' else 'en'}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --fg:#1c2024; --muted:#60646c; --line:#e0e1e6; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111113; --card:#18191b; --fg:#edeef0; --muted:#b0b4ba; --line:#2e3135; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif; }}
main {{ max-width:1360px; margin:0 auto; padding:20px 16px 40px; }}
h1 {{ font-size:20px; margin:0 0 2px; }} h2 {{ font-size:15px; margin:28px 0 10px; }}
h3 {{ font-size:13px; margin:0 0 6px; color:var(--muted); font-weight:600; }}
.sub {{ color:var(--muted); margin:0 0 16px; }}
.grid2 {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(420px,1fr)); gap:14px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
svg {{ width:100%; height:auto; display:block; }}
.grid {{ stroke:var(--line); stroke-width:1; }} .axis {{ fill:var(--muted); font-size:10px; }}
.legend {{ display:flex; flex-wrap:wrap; gap:12px; font-size:12px; color:var(--muted); margin-bottom:4px; }}
.legend i {{ display:inline-block; width:10px; height:3px; margin-right:5px; vertical-align:middle; }}
.legend b {{ color:var(--fg); font-weight:600; }}
.empty {{ color:var(--muted); }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
td {{ padding:6px 8px; border-top:1px solid var(--line); vertical-align:top; }}
.lv {{ white-space:nowrap; font-weight:600; }} .lv.CRIT {{ color:#e5484d; }} .lv.WARN {{ color:#d8a106; }}
.lv.OK {{ color:#30a46c; }}
@media (max-width:480px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
</style></head>
<body><main>
<h1>{html.escape(title)}</h1>
<p class="sub">{html.escape(t('dash.generated', when=generated, days=DAYS))}</p>
<h2>{html.escape(t('dash.open'))}</h2>
<section class="card">{'<table>' + ''.join(open_rows) + '</table>' if open_rows else f'<p class="empty">{html.escape(t("dash.open_none"))}</p>'}</section>
<h2>{html.escape(t('dash.charts'))}</h2>
<div class="grid2">{''.join(cards)}</div>
<h2>{html.escape(t('dash.alerts'))}</h2>
<section class="card">{'<table>' + ''.join(alert_rows) + '</table>' if alert_rows else f'<p class="empty">{html.escape(t("dash.alerts_none"))}</p>'}</section>
</main></body></html>
"""


def serve(store: Store, cfg: dict, label: str, tz, port: int) -> int:
    import time

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the stdlib's name
            body = render(store, cfg, label, tz, time.time()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with socketserver.TCPServer(("127.0.0.1", port), Handler) as server:
        print(t("dash.serving", port=port))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


register({
    "en": {
        "dash.lang": "en",
        "dash.title": "Server health · {label}",
        "dash.generated": "Generated {when} · last {days} days, hourly averages",
        "dash.no_data": "no data yet",
        "dash.memory": "Memory",
        "dash.ram_used": "RAM used %",
        "dash.waiting": "waiting for memory %",
        "dash.load": "Load",
        "dash.disk": "Disk",
        "dash.disk_used": "used",
        "dash.databases": "Databases",
        "dash.requests": "{app} · requests per hour",
        "dash.people": "people",
        "dash.bots": "bots",
        "dash.health": "{app} · health check",
        "dash.response": "response time",
        "dash.open": "Open now",
        "dash.open_none": "Nothing open: everything within the thresholds.",
        "dash.charts": "Last days",
        "dash.alerts": "Recent alerts",
        "dash.alerts_none": "No alerts in the period.",
        "dash.kind.new": "opened",
        "dash.kind.worse": "got worse",
        "dash.kind.better": "improved",
        "dash.kind.normal": "back to normal",
        "dash.kind.event": "event",
        "dash.kind.reminder": "reminder",
        "dash.serving": "Serving on http://127.0.0.1:{port} (Ctrl+C stops). From your computer: "
                        "ssh -L {port}:127.0.0.1:{port} <user>@<server>, then open http://localhost:{port}",
    },
    "pt-BR": {
        "dash.lang": "pt-BR",
        "dash.title": "Saúde do servidor · {label}",
        "dash.generated": "Gerado em {when} · últimos {days} dias, médias por hora",
        "dash.no_data": "sem dados ainda",
        "dash.memory": "Memória",
        "dash.ram_used": "RAM usada %",
        "dash.waiting": "espera por memória %",
        "dash.load": "Load",
        "dash.disk": "Disco",
        "dash.disk_used": "usado",
        "dash.databases": "Bancos",
        "dash.requests": "{app} · requisições por hora",
        "dash.people": "pessoas",
        "dash.bots": "robôs",
        "dash.health": "{app} · health check",
        "dash.response": "tempo de resposta",
        "dash.open": "Abertos agora",
        "dash.open_none": "Nada aberto: tudo dentro dos limiares.",
        "dash.charts": "Últimos dias",
        "dash.alerts": "Alertas recentes",
        "dash.alerts_none": "Nenhum alerta no período.",
        "dash.kind.new": "abriu",
        "dash.kind.worse": "piorou",
        "dash.kind.better": "melhorou",
        "dash.kind.normal": "normalizou",
        "dash.kind.event": "evento",
        "dash.kind.reminder": "lembrete",
        "dash.serving": "Servindo em http://127.0.0.1:{port} (Ctrl+C encerra). Do seu computador: "
                        "ssh -L {port}:127.0.0.1:{port} <usuario>@<servidor> e abra http://localhost:{port}",
    },
})
