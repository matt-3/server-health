"""Decides what to alert: persistence, state changes and reminders.

The identity of an alert is the metric KEY with its level, never the measured value:
3067 MB and 3071 MB are the same warning."""

from __future__ import annotations

from .i18n import register, t
from .metric import BETTER, EVENT, NEW, NORMAL, REMINDER, WORSE, Change, Metric, lvl


class Evaluator:
    def __init__(self, state: dict, th: dict, now: float, deploy_running: bool, deploy_recent: bool = False):
        self.prev = state.get("metrics", {})
        self.th = th
        self.now = now
        self.deploy_running = deploy_running
        self.deploy_recent = deploy_recent
        self.next_state: dict = {}

    def apply(self, metrics: list[Metric]) -> list[Change]:
        changes = []
        seen = set()
        for m in metrics:
            seen.add(m.key)
            st = dict(self.prev.get(m.key, {}))
            raw = m.level
            if m.deploy_sensitive and self.deploy_running and raw != "OK":
                raw = "OK"
                m.note = t("eval.deploy_running")
            elif m.deploy_lookback and self.deploy_recent and raw != "OK":
                raw = "OK"
                m.note = t("eval.deploy_recent")
            if m.summary_only:
                m.effective = "OK"
                continue
            st["seen"] = self.now
            # A deploy hides the measure, it does not solve the problem: the open alert
            # stays open with its timers intact, or the next round would say "back to
            # normal" and then open a new alert. When the measure is normal again,
            # raw == m.level and it closes.
            if raw != m.level and st.get("alerted", "OK") != "OK":
                m.effective = st["alerted"]
                self.next_state[m.key] = st
                continue
            st["warn_since"] = (st.get("warn_since") or self.now) if lvl(raw) >= 1 else None
            st["crit_since"] = (st.get("crit_since") or self.now) if raw == "CRIT" else None
            eff = "OK"
            if raw == "CRIT" and self.now - st["crit_since"] >= m.crit_persist_min * 60 - 30:
                eff = "CRIT"
            elif lvl(raw) >= 1 and self.now - st["warn_since"] >= m.persist_min * 60 - 30:
                eff = "WARN"
            if lvl(eff) < lvl(raw):
                m.note = m.note or t("eval.waiting")
            m.effective = eff

            if m.event:
                if eff != "OK":
                    changes.append(Change(m, EVENT, "OK", eff))
                self.next_state[m.key] = {"seen": self.now}
                continue

            before = st.get("alerted", "OK")
            if lvl(eff) > lvl(before):
                changes.append(Change(m, NEW if before == "OK" else WORSE, before, eff))
                st["alerted"], st["alerted_at"] = eff, self.now
                if before == "OK":
                    st["started"] = self.now
            elif lvl(eff) < lvl(before):
                kind = NORMAL if eff == "OK" else BETTER
                changes.append(Change(m, kind, before, eff, duration=self.now - st.get("started", self.now)))
                st["alerted"], st["alerted_at"] = eff, self.now
                if eff == "OK":
                    st.pop("started", None)
            elif eff != "OK":
                remind = self.th["REMIND_CRIT_MIN"] if eff == "CRIT" else self.th["REMIND_WARN_MIN"]
                if self.now - st.get("alerted_at", self.now) >= remind * 60 - 30:
                    changes.append(Change(m, REMINDER, eff, eff, since=st.get("started", self.now)))
                    st["alerted_at"] = self.now
            self.next_state[m.key] = st

        # A metric that went missing this round (app removed, collection failed): its
        # state stays for 24h, silently, so there is no false "back to normal".
        for key, st in self.prev.items():
            if key not in seen and self.now - st.get("seen", 0) < 86400:
                self.next_state[key] = st
        return changes


register({
    "en": {
        "eval.deploy_running": "deploy running, ignored",
        "eval.deploy_recent": "deploy since the previous round, ignored",
        "eval.waiting": "waiting for confirmation",
    },
    "pt-BR": {
        "eval.deploy_running": "deploy em andamento, ignorado",
        "eval.deploy_recent": "deploy desde a rodada anterior, ignorado",
        "eval.waiting": "aguardando confirmação",
    },
})
