"""Draws the README illustrations: how server-health's messages arrive on Discord, and an
excerpt of the dashboard. Light and dark versions of each, as plain SVG (system fonts,
no script), so GitHub can show the one matching the reader's theme.

    python3 docs/assets/make_images.py

The texts are the monitor's English strings with the fixture's values; when a message
changes, run it again.
"""

from __future__ import annotations

import math
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent
W = 720
FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif'
MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'

THEMES = {
    "light": {"bg": "#ffffff", "embed": "#f2f3f5", "text": "#313338", "strong": "#060607", "muted": "#5c5e66",
              "badge": "#4e5d94", "line": "#e3e5e8", "code": "#e3e5e8", "link": "#006ce7",
              "mention": "#e6e8fd", "mention_text": "#3a46c2"},
    "dark": {"bg": "#313338", "embed": "#2b2d31", "text": "#dbdee1", "strong": "#f2f3f5", "muted": "#949ba4",
             "badge": "#5865f2", "line": "#3f4147", "code": "#1e1f22", "link": "#00a8fc",
             "mention": "#3c4270", "mention_text": "#c9cdfb"},
}
LEVEL = {"crit": "#e74c3c", "warn": "#f3a712", "ok": "#2ecc71"}
AVATAR = "#0f766e"


class Canvas:
    def __init__(self, theme: str):
        self.t = THEMES[theme]
        self.parts: list[str] = []
        self.y = 20

    def text(self, x, y, runs, size=14, color=None, family=FONT):
        """runs: [(text, weight, color or None)]."""
        spans = "".join(
            f'<tspan font-weight="{w}"{f" fill={chr(34)}{c}{chr(34)}" if c else ""}>{escape(s)}</tspan>'
            for s, w, c in runs)
        self.parts.append(f'<text x="{x}" y="{y}" font-family=\'{family}\' font-size="{size}" '
                          f'fill="{color or self.t["text"]}">{spans}</text>')

    def dot(self, x, y, level):
        if level == "normal":  # the ✅ the monitor writes for "back to normal"
            self.parts.append(f'<rect x="{x - 7}" y="{y - 7}" width="14" height="14" rx="3" fill="{LEVEL["ok"]}"/>'
                              f'<path d="M{x - 3.5} {y} l2.5 2.6 l4.6 -5" fill="none" stroke="#fff" '
                              'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')
        else:
            self.parts.append(f'<circle cx="{x}" cy="{y}" r="6" fill="{LEVEL[level]}"/>')

    def svg(self, title: str, desc: str) -> str:
        h = self.y + 14
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" '
                f'role="img" aria-labelledby="t d"><title id="t">{escape(title)}</title><desc id="d">'
                f'{escape(desc)}</desc><rect width="{W}" height="{h}" rx="10" fill="{self.t["bg"]}"/>'
                + "".join(self.parts) + "</svg>\n")


def message(c: Canvas, when: str, embed: dict, content: str | None = None):
    """A bot message: avatar, name, APP badge, time, optional text, then the embed."""
    t = c.t
    top = c.y
    c.parts.append(f'<circle cx="36" cy="{top + 20}" r="20" fill="{AVATAR}"/>'
                   f'<path d="M23 {top + 21} h6 l3 -7 l5 14 l4 -10 l2 3 h7" fill="none" stroke="#fff" '
                   'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>')
    c.text(72, top + 14, [("Server Health", 600, t["strong"])], size=15)
    bx = 72 + 104
    c.parts.append(f'<rect x="{bx}" y="{top + 2}" width="34" height="16" rx="4" fill="{t["badge"]}"/>')
    c.text(bx + 6, top + 14, [("APP", 700, "#ffffff")], size=10)
    c.text(bx + 44, top + 14, [(when, 400, t["muted"])], size=12)
    c.y = top + 24
    if content:  # a mention, highlighted the way Discord shows it
        width = 8 * len(content) + 6
        c.parts.append(f'<rect x="70" y="{c.y + 1}" width="{width}" height="19" rx="3" fill="{t["mention"]}"/>')
        c.text(73, c.y + 15, [(content, 500, t["mention_text"])])
        c.y += 26
    draw_embed(c, embed)
    c.y += 18


def draw_embed(c: Canvas, e: dict):
    t = c.t
    x, w = 72, W - 72 - 24
    top = c.y + 4
    inner: list[tuple] = []  # (kind, payload)
    y = top + 14
    lines = []  # (y, callable)

    def add(height, fn):
        nonlocal y
        lines.append((y, fn))
        y += height

    if e.get("author"):
        add(22, lambda yy: c.text(x + 16, yy + 12, [(e["author"], 600, t["strong"])], size=13))
    add(24, lambda yy: c.text(x + 16, yy + 14, [(e["title"], 700, t["strong"])], size=16))
    for runs in e.get("description", []):
        add(20, lambda yy, r=runs: line(c, x + 16, yy + 14, r))
    for name, rows in e.get("fields", []):
        y += 8
        add(20, lambda yy, n=name: c.text(x + 16, yy + 14, [(n, 700, t["strong"])], size=14))
        for runs in rows:
            if runs == "gap":  # the blank line the monitor puts around a multi-line block
                y += 10
                continue
            add(20, lambda yy, r=runs: line(c, x + 16, yy + 14, r))
    if e.get("footer"):
        y += 8
        add(18, lambda yy: c.text(x + 16, yy + 12, [(e["footer"], 400, t["muted"])], size=12))
    bottom = y + 10
    c.parts.append(f'<rect x="{x}" y="{top}" width="{w}" height="{bottom - top}" rx="4" fill="{t["embed"]}"/>'
                   f'<path d="M{x + 4} {top} h-0 a4 4 0 0 0 -4 4 v{bottom - top - 8} a4 4 0 0 0 4 4 z" '
                   f'fill="{e["color"]}"/>')
    for yy, fn in lines:
        fn(yy)
    c.y = bottom


def line(c: Canvas, x, y, runs):
    """A line of text. A run ("•crit") draws a status dot; ("-", ...) a bullet."""
    t = c.t
    cx = x
    out = []
    for run in runs:
        s, style = run[0], run[1] if len(run) > 1 else ""
        if s.startswith("•"):
            c.dot(cx + 6, y - 5, s[1:])
            cx += 18
            continue
        out.append((s, style))
    weight = {"b": 700, "": 400, "m": 400}
    color = {"b": t["strong"], "": t["text"], "m": t["muted"]}
    c.text(cx, y, [(s, weight.get(st, 400), color.get(st, t["text"])) for s, st in out])


# -- the messages ------------------------------------------------------------------------

ALERT = {
    "color": LEVEL["crit"], "author": "my-server-prod",
    "title": "Critical · Server · Memory",
    "description": [
        [("3,673/3,819 MB (96%) · 7d average 80% · available 146 MB · waiting 0.2%", "b")],
        [("only 4% available (critical <= 5%)", "")],
    ],
    "fields": [
        ("Server", [
            [("•crit",), ("Memory", "b"), (": 3,673/3,819 MB (96%) · available 146 MB", "")],
            [("•ok",), ("Swap: 290/2,048 MB · swap-out 0 KB/s", "")],
            [("•ok",), ("Disk: 40% (24.00 GB free)", "")],
            [("•ok",), ("Load: 0.35 / 0.28 over 5 min (2 vCPU)", "")],
        ]),
        ("Who uses the memory", [
            [("- shop.web.1 1.20 GB", "")],
            [("- shop.worker.1 900 MB", "")],
            [("- shop-db (pg) 420 MB", "")],
        ]),
        ("Open now", [[("•crit",), ("Server · Memory", "")]]),
    ],
    "footer": "server-health 1.0.0 · vps-7f3a2c",
}
ALERT_TITLE_DOT = "crit"

NORMAL = {
    "color": LEVEL["ok"], "author": "my-server-prod",
    "title": "Back to normal · Server · Memory",
    "description": [[("Back to normal after 35 min.", "")]],
    "fields": [("Open now", [[("nothing: everything within the thresholds", "")]])],
    "footer": "server-health 1.0.0 · vps-7f3a2c",
}

SUMMARY = {
    "color": LEVEL["warn"], "author": "my-server-prod",
    "title": "Summary of Oct 31 · 1 warning",
    "description": [
        [("Needs attention", "b")],
        [("•warn",), ("shop · ", ""), ("Failed jobs", "b"), (": 2 in 30 min · 7 in 24h (Orders::SyncJob 2)", "")],
    ],
    "fields": [
        ("Server", [
            [("•ok",), ("Memory: 3,058/3,819 MB (80%) · 7d average 78% · available 762 MB", "")],
            [("•ok",), ("Disk: 40% (24.00 GB free) · +120 MB/day · full in ~200 days", "")],
            "gap",
            [("Last 24h", "b")],
            [("- RAM: min 71% · avg 79% · max 88%", "")],
            [("- Load: avg 0.35 · max 1.12", "")],
        ]),
        ("Shop (shop)", [
            [("•warn",), ("Failed jobs", "b"), (": 2 in 30 min · 7 in 24h", "")],
            [("•ok",), ("Health: 200 in 85 ms (shop.example.com/up)", "")],
            [("•ok",), ("Solid Queue: heartbeat worker 20 s · dispatcher 15 s · scheduler 30 s", "")],
            "gap",
            [("Last 24h", "b")],
            [("- Requests: 48,210 · people 31,540 · 5xx 12 (0.02%)", "")],
            [("- Bots: 16,670 (35% of the requests) · search engines 9,120, AI training 4,880", "")],
        ]),
        ("Shop (shop) · numbers of the day", [
            [("Paid orders (24h): 318", "")],
            [("Revenue (24h): 27,415.90", "")],
        ]),
    ],
    "footer": "server-health 1.0.0 · vps-7f3a2c",
}


def title_with_dot(embed, level):
    """The monitor writes the level as an emoji at the start of the title: drawn as a dot."""
    return embed, level


def build_messages(theme: str, which: str) -> str:
    c = Canvas(theme)
    if which == "alert":
        message_with_dot(c, "Today at 15:05", ALERT, "crit", content="@here")
        message_with_dot(c, "Today at 15:40", NORMAL, "normal")
        return c.svg("A server-health alert on Discord",
                     "A critical memory alert with the measured value, the reason, the server's other metrics, "
                     "the containers that use the most memory and the open alerts; below it, the "
                     "'Back to normal' message 35 minutes later.")
    message_with_dot(c, "Today at 09:00", SUMMARY, "warn")
    return c.svg("A server-health daily summary on Discord",
                 "The daily summary: what needs attention, each section with its metrics, the last 24 hours "
                 "of the server, the app's requests and bots, and the product numbers of the day.")


def message_with_dot(c: Canvas, when, embed, level, content=None):
    """Same as message(), with the title's level drawn as a dot before it."""
    e = dict(embed)
    e["title"] = "  " + embed["title"]  # room for the dot
    start = len(c.parts)
    message(c, when, e, content)
    # the title is the second text inside the embed (after the author): find its y
    for part in c.parts[start:]:
        if escape(e["title"]) in part:
            y = float(part.split(' y="', 1)[1].split('"', 1)[0])
            c.dot(72 + 16 + 7, y - 5, level)
            break


# -- the dashboard excerpt -----------------------------------------------------------------

DASH = {
    "light": {"bg": "#f6f7f9", "card": "#ffffff", "fg": "#1c2024", "muted": "#60646c", "line": "#e0e1e6"},
    "dark": {"bg": "#111113", "card": "#18191b", "fg": "#edeef0", "muted": "#b0b4ba", "line": "#2e3135"},
}
PALETTE = ["#4f7cff", "#e5484d", "#30a46c"]


def series(seed: float, base: float, amp: float, n=7 * 24, spike_at=None) -> list[float]:
    out = []
    for i in range(n):
        day = math.sin((i % 24) / 24 * 2 * math.pi - 1.6)
        v = base + amp * day + 2.2 * math.sin(i * 0.37 + seed) + 1.4 * math.sin(i * 1.7 + seed * 2)
        if spike_at and spike_at[0] <= i < spike_at[1]:
            v += 14 * math.sin((i - spike_at[0]) / (spike_at[1] - spike_at[0]) * math.pi)
        out.append(v)
    return out


def chart(parts, d, x, y, w, h, title, lines, unit, top):
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{d["card"]}" stroke="{d["line"]}"/>')
    parts.append(f'<text x="{x + 14}" y="{y + 24}" font-family=\'{FONT}\' font-size="13" font-weight="600" '
                 f'fill="{d["muted"]}">{escape(title)}</text>')
    lx = x + 14
    for i, (name, data) in enumerate(lines):
        parts.append(f'<rect x="{lx}" y="{y + 38}" width="10" height="3" fill="{PALETTE[i]}"/>')
        label = f"{name} "
        parts.append(f'<text x="{lx + 15}" y="{y + 43}" font-family=\'{FONT}\' font-size="12" fill="{d["muted"]}">'
                     f'{escape(label)}<tspan font-weight="600" fill="{d["fg"]}">{data[-1]:.0f}{unit}</tspan></text>')
        lx += 15 + 7 * len(label) + 40
    gx, gy, gw, gh = x + 40, y + 56, w - 54, h - 82
    for k in range(5):
        yy = gy + gh * k / 4
        parts.append(f'<line x1="{gx}" x2="{gx + gw}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{d["line"]}"/>')
        parts.append(f'<text x="{gx - 6}" y="{yy + 4:.1f}" text-anchor="end" font-family=\'{FONT}\' font-size="10" '
                     f'fill="{d["muted"]}">{top * (4 - k) / 4:.0f}{unit}</text>')
    for dd, label in enumerate(("Oct 25", "Oct 26", "Oct 27", "Oct 28", "Oct 29", "Oct 30", "Oct 31")):
        xx = gx + gw * (dd * 24) / (7 * 24)
        parts.append(f'<text x="{xx + 3:.1f}" y="{gy + gh + 16}" font-family=\'{FONT}\' font-size="10" '
                     f'fill="{d["muted"]}">{label}</text>')
    for i, (_, data) in enumerate(lines):
        pts = " ".join(f"{gx + gw * j / (len(data) - 1):.1f},{gy + gh * (1 - max(0, min(v, top)) / top):.1f}"
                       for j, v in enumerate(data))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{PALETTE[i]}" stroke-width="1.6" '
                     'stroke-linejoin="round"/>')


def build_dashboard(theme: str) -> str:
    d = DASH[theme]
    parts = []
    h = 452
    parts.append(f'<text x="20" y="34" font-family=\'{FONT}\' font-size="18" font-weight="700" '
                 f'fill="{d["fg"]}">Server health · my-server-prod</text>')
    parts.append(f'<text x="20" y="56" font-family=\'{FONT}\' font-size="13" fill="{d["muted"]}">'
                 'Generated Oct 31 15:00 · last 7 days, hourly averages</text>')
    ram = series(1.0, 74, 6, spike_at=(150, 160))
    psi = [max(0.0, (v - 82) * 0.6) for v in ram]
    chart(parts, d, 20, 74, W - 40, 180, "Memory", [("RAM used %", ram), ("waiting for memory %", psi)], "%", 100)
    req = [max(0.0, 900 + 700 * math.sin((i % 24) / 24 * 2 * math.pi - 1.9) + 60 * math.sin(i)) for i in range(168)]
    bots = [v * 0.5 + 120 for v in req]
    chart(parts, d, 20, 266, W - 40, 170, "shop · requests per hour", [("people", req), ("bots", bots)], "", 1800)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" role="img" '
            'aria-labelledby="t d"><title id="t">The server-health dashboard</title><desc id="d">An excerpt of '
            'the dashboard page: memory used and time waiting for memory over the last 7 days, with a peak on '
            'October 31, and the requests per hour of an app, people and bots.</desc>'
            f'<rect width="{W}" height="{h}" rx="12" fill="{d["bg"]}"/>' + "".join(parts) + "</svg>\n")


def main():
    for theme in ("light", "dark"):
        (OUT / f"alert-{theme}.svg").write_text(build_messages(theme, "alert"))
        (OUT / f"summary-{theme}.svg").write_text(build_messages(theme, "summary"))
        (OUT / f"dashboard-{theme}.svg").write_text(build_dashboard(theme))
    print("written:", ", ".join(sorted(p.name for p in OUT.glob("*.svg"))))


if __name__ == "__main__":
    main()
