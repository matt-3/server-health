"""A measurement, and a change in its alert state."""

from __future__ import annotations

import dataclasses

from .i18n import fdate, fdur, register, t
from .text import MARK_NORMAL, MARKS, md

LEVELS = {"OK": 0, "WARN": 1, "CRIT": 2}
COLORS = {"OK": 3066993, "WARN": 15965202, "CRIT": 15158332}

# Change kinds. They are stored in alerts.jsonl, so they never change spelling.
NEW, WORSE, BETTER, NORMAL, REMINDER, EVENT = "new", "worse", "better", "normal", "reminder", "event"
OPENING = (NEW, WORSE, EVENT)


def lvl(name: str) -> int:
    return LEVELS.get(name, 0)


def max_level(names) -> str:
    best = "OK"
    for n in names:
        if lvl(n) > lvl(best):
            best = n
    return best


def level_name(level: str) -> str:
    return t(f"level.{level}")


@dataclasses.dataclass
class Metric:
    key: str             # stable identity: the anti-spam compares it, never the value
    section: str         # display name of the group: the server, an app, a database
    label: str
    value: str
    level: str = "OK"    # level measured in this round
    detail: str = ""     # why the level, or what to do
    event: bool = False  # occurrence (OOM, restart): alerts on each one, no "back to normal"
    persist_min: int = 0       # the warning only counts after this many minutes in a row
    crit_persist_min: int = 0  # same for critical
    deploy_sensitive: bool = False  # during a deploy the level is ignored (the build weighs)
    deploy_lookback: bool = False   # measured since the previous round: a finished deploy still weighs
    summary_only: bool = False      # informs only, never alerts
    effective: str = "OK"           # level after persistence (set by the Evaluator)
    note: str = ""                  # Evaluator note ("waiting for confirmation")
    items: list = dataclasses.field(default_factory=list)  # long value: one line per item on Discord

    @property
    def problem(self) -> bool:
        return self.effective != "OK" and not self.summary_only and not self.event

    def discord_line(self) -> str:
        """The metric's line on Discord: state as an emoji, the label in bold when there
        is a problem. Informative metrics have no emoji."""
        note = f" *({md(self.note)})*" if self.note else ""
        if self.items:
            return f"{md(self.label)}:\n" + "\n".join(f"- {md(i)}" for i in self.items)
        if self.summary_only:
            return f"{md(self.label)}: {md(self.value)}{note}"
        if self.effective == "OK":
            return f"{MARKS['OK']} {md(self.label)}: {md(self.value)}{note}"
        return f"{MARKS[self.effective]} **{md(self.label)}**: {md(self.value)}{note}"


@dataclasses.dataclass
class Change:
    metric: Metric
    kind: str        # NEW, WORSE, BETTER, NORMAL, REMINDER, EVENT
    before: str
    after: str
    duration: float = 0.0
    since: float = 0.0

    @property
    def urgent(self) -> bool:
        """Critical, or leaving critical: does not wait for the end of quiet hours."""
        return "CRIT" in (self.before, self.after)

    def text(self, tz) -> str:
        """One plain line (two with the detail), for the report and the quiet queue."""
        m = self.metric
        if self.kind == NORMAL:
            return t("change.text.normal", section=m.section, label=m.label, duration=fdur(self.duration))
        if self.kind == BETTER:
            return t("change.text.better", section=m.section, label=m.label, value=m.value)
        tag = level_name(self.after).upper()
        if self.kind == REMINDER:
            return t("change.text.reminder", tag=tag, since=fdate(self.since, tz), section=m.section,
                     label=m.label, value=m.value)
        out = f"{tag} · {m.section} · {m.label}: {m.value}"
        if m.detail:
            out += f"\n   {m.detail}"
        return out

    @property
    def mark(self) -> str:
        return MARK_NORMAL if self.kind == NORMAL else MARKS[self.after]

    def title(self) -> str:
        m = self.metric
        if self.kind == NORMAL:
            what = t("change.title.normal")
        elif self.kind == BETTER:
            what = t("change.title.better")
        elif self.kind == REMINDER:
            what = t("change.title.reminder", level=level_name(self.after).lower())
        else:
            what = level_name(self.after).capitalize()
        return f"{what} · {m.section} · {m.label}"

    def discord_text(self, single: bool) -> str:
        """Alone, the change already has its name in the title: the text starts with the
        value. Together with others, each one carries its own name."""
        m = self.metric
        since = int(self.since)
        if single:
            if self.kind == NORMAL:
                return t("change.single.normal", duration=fdur(self.duration))
            out = f"**{md(m.value)}**"
            if self.kind == BETTER:
                return out + "\n" + t("change.single.better")
            if self.kind == REMINDER:
                return out + "\n" + t("change.single.reminder", level=level_name(self.after).lower(), since=since)
            if m.detail:
                out += f"\n{m.detail}"  # written by us or by the TOML `explanation`: `code` is allowed
            if self.kind == WORSE:
                out += "\n" + t("change.single.worse")
            return out
        name = f"{self.mark} **{md(m.section)} · {md(m.label)}**"
        if self.kind == NORMAL:
            return t("change.many.normal", name=name, duration=fdur(self.duration))
        if self.kind == BETTER:
            return t("change.many.better", name=name, value=md(m.value))
        if self.kind == REMINDER:
            return t("change.many.reminder", name=name, since=since, value=md(m.value))
        out = f"{name}: {md(m.value)}"
        if m.detail:
            out += f"\n*{m.detail}*"
        return out


register({
    "en": {
        "level.OK": "OK",
        "level.WARN": "warning",
        "level.CRIT": "critical",
        "change.text.normal": "BACK TO NORMAL · {section} · {label} (lasted {duration})",
        "change.text.better": "DOWN TO WARNING · {section} · {label}: {value}",
        "change.text.reminder": "STILL {tag} since {since} · {section} · {label}: {value}",
        "change.title.normal": "Back to normal",
        "change.title.better": "Down to warning",
        "change.title.reminder": "Still {level}",
        "change.single.normal": "Back to normal after {duration}.",
        "change.single.better": "Went down from critical to warning.",
        "change.single.reminder": "*{level} since <t:{since}:f> (<t:{since}:R>)*",
        "change.single.worse": "*was a warning, became critical*",
        "change.many.normal": "{name} back to normal (lasted {duration})",
        "change.many.better": "{name} down to warning: {value}",
        "change.many.reminder": "{name} still open since <t:{since}:R>: {value}",
    },
    "pt-BR": {
        "level.OK": "OK",
        "level.WARN": "aviso",
        "level.CRIT": "crítico",
        "change.text.normal": "NORMALIZOU · {section} · {label} (durou {duration})",
        "change.text.better": "BAIXOU PARA AVISO · {section} · {label}: {value}",
        "change.text.reminder": "SEGUE {tag} desde {since} · {section} · {label}: {value}",
        "change.title.normal": "Normalizou",
        "change.title.better": "Baixou para aviso",
        "change.title.reminder": "Segue {level}",
        "change.single.normal": "Voltou ao normal depois de {duration}.",
        "change.single.better": "Baixou de crítico para aviso.",
        "change.single.reminder": "*{level} desde <t:{since}:f> (<t:{since}:R>)*",
        "change.single.worse": "*era aviso, virou crítico*",
        "change.many.normal": "{name} normalizou (durou {duration})",
        "change.many.better": "{name} baixou para aviso: {value}",
        "change.many.reminder": "{name} segue desde <t:{since}:R>: {value}",
    },
})
