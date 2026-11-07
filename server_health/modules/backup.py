"""The backup: how long since the last good one, and whether it looks complete.

SERVER_HEALTH_BACKUP_STAMP is a file the backup script touches at the end: its age is
the age of the last good backup. SERVER_HEALTH_BACKUP_GLOB (optional) points at the
backup files: a newest file much smaller than the usual is a broken backup that ran."""

from __future__ import annotations

from ..i18n import fbytes, fdur, fnum, t
from ..metric import Metric
from ..module import Module


class Backup(Module):
    name = "backup"
    order = 15
    defaults = {"SERVER_HEALTH_BACKUP_STAMP": "", "SERVER_HEALTH_BACKUP_GLOB": ""}
    thresholds = {"BACKUP_WARN_H": 26, "BACKUP_CRIT_H": 50, "BACKUP_SHRINK_PCT": 50}

    def detect(self, src, cfg):
        return bool(cfg.get("SERVER_HEALTH_BACKUP_STAMP") or cfg.get("SERVER_HEALTH_BACKUP_GLOB"))

    def host(self, ctx):
        if ctx.cfg.get("SERVER_HEALTH_BACKUP_STAMP"):
            self.age(ctx)
        if ctx.cfg.get("SERVER_HEALTH_BACKUP_GLOB"):
            self.size(ctx)

    def age(self, ctx):
        path = ctx.cfg["SERVER_HEALTH_BACKUP_STAMP"]
        st = ctx.src.stat(path)
        if not st:
            ctx.add(Metric("host.backup", ctx.server_section, "Backup", t("backup.never"), "WARN",
                           t("backup.never_detail", path=path)))
            return
        age_h = (ctx.now - st.st_mtime) / 3600
        th = ctx.th
        level = "CRIT" if age_h >= th["BACKUP_CRIT_H"] else "WARN" if age_h >= th["BACKUP_WARN_H"] else "OK"
        ctx.add(Metric("host.backup", ctx.server_section, "Backup", t("backup.value", age=fdur(age_h * 3600)), level,
                       t("backup.detail")))

    def size(self, ctx):
        """Newest backup file against the median of the ones before it (up to 7)."""
        files = ctx.src.glob(ctx.cfg["SERVER_HEALTH_BACKUP_GLOB"])
        if not files:
            ctx.add(Metric("host.backup_size", ctx.server_section, t("backup.size"), t("backup.size.none"), "WARN",
                           t("backup.size.none_detail", glob=ctx.cfg["SERVER_HEALTH_BACKUP_GLOB"])))
            return
        newest, older = files[0], [f[1] for f in files[1:8]]
        value = t("backup.size.value", size=fbytes(newest[1]))
        if len(older) < 2:
            ctx.add(Metric("host.backup_size", ctx.server_section, t("backup.size"), value))
            return
        older.sort()
        median = older[len(older) // 2]
        pct = newest[1] * 100 / median if median else 100
        value += t("backup.size.usual", usual=fbytes(median), pct=fnum(pct))
        level = "WARN" if pct < ctx.th["BACKUP_SHRINK_PCT"] else "OK"
        ctx.add(Metric("host.backup_size", ctx.server_section, t("backup.size"), value, level,
                       t("backup.size.detail", limit=ctx.th["BACKUP_SHRINK_PCT"])))

    strings = {
        "en": {
            "backup.never": "never recorded",
            "backup.never_detail": "{path} does not exist: the backup script did not run or does not touch it",
            "backup.value": "last one {age} ago",
            "backup.detail": "the daily backup did not finish; check the backup script log",
            "backup.size": "Backup size",
            "backup.size.none": "no file found",
            "backup.size.none_detail": "nothing matches {glob}",
            "backup.size.value": "newest {size}",
            "backup.size.usual": " · usual {usual} ({pct}%)",
            "backup.size.detail": "the newest backup is below {limit}% of the usual size: it probably ran "
                                  "and came out incomplete; open it before trusting it",
        },
        "pt-BR": {
            "backup.never": "nunca registrado",
            "backup.never_detail": "{path} não existe: o script de backup não rodou ou não faz o touch",
            "backup.value": "último há {age}",
            "backup.detail": "o backup diário não terminou; confira o log do script de backup",
            "backup.size": "Tamanho do backup",
            "backup.size.none": "nenhum arquivo",
            "backup.size.none_detail": "nada casa com {glob}",
            "backup.size.value": "mais novo {size}",
            "backup.size.usual": " · habitual {usual} ({pct}%)",
            "backup.size.detail": "o backup mais novo tem menos de {limit}% do tamanho habitual: provavelmente "
                                  "rodou e saiu incompleto; abra antes de confiar nele",
        },
    }
