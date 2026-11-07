"""Small text helpers shared by the report builders and the notifiers."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .i18n import t

# Discord does not color text (the `ansi` block only works on desktop): each line's
# state goes in an emoji, which reads the same on a phone.
MARKS = {"OK": "🟢", "WARN": "🟡", "CRIT": "🔴"}
MARK_NORMAL = "✅"


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def md(s: str) -> str:
    """Escapes what Discord would read as formatting. An underscore inside a word
    (solid_queue_jobs) does not become italics, so it stays as is."""
    return re.sub(r"([\\*`~|])", r"\\\1", str(s))


def plain(s: str) -> str:
    """Discord text without formatting, for Sentry, webhooks and the log."""
    s = re.sub(r"<t:(\d+):[a-zA-Z]>",
               lambda m: datetime.fromtimestamp(int(m.group(1)), timezone.utc).strftime(t("fmt.utc")), s)
    s = s.replace("**", "")
    s = re.sub(r"(?<!\\)\*", "", s)
    s = re.sub(r"\\(.)", r"\1", s)
    for mark in (*MARKS.values(), MARK_NORMAL):
        s = s.replace(mark + " ", "")
    return s


def join_blocks(lines: list[str]) -> str:
    """Joins the lines of a field, with a blank line around each multi-line block (a
    list, "Last 24h"): glued to the status lines, the block looks like a continuation
    of the line above. The field never starts with a blank line."""
    out: list[str] = []
    for i, line in enumerate(lines):
        if i and ("\n" in line or "\n" in lines[i - 1]):
            out.append("")
        out.append(line)
    return "\n".join(out)


def bullets(lines: list[str]) -> str:
    return "\n".join(f"- {md(x)}" for x in lines)


def embed_text(embed: dict) -> str:
    """The message as plain text, for Sentry, the generic webhook and the log."""
    parts = [plain(embed["title"])]
    if embed.get("description"):
        parts.append(plain(embed["description"]))
    for f in embed.get("fields", []):
        parts.append(f"\n== {f['name']}\n{plain(f['value'])}")
    return "\n".join(parts)
