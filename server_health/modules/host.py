"""The machine: memory, swap, disk, load and OOM kills. Linux only, no dependencies.

Memory alerts on LACK OF HEADROOM and on WAITING, not on occupancy: 80% of RAM in use
with two apps is normal for a server; what matters is whether there is room for a
peak and whether processes are waiting for memory."""

from __future__ import annotations

import re

from ..i18n import fbytes, fdur, fnum, t
from ..metric import Metric
from ..module import Module
from ..parse import parse_meminfo, parse_psi, parse_vmstat


class Host(Module):
    name = "host"
    order = 10
    thresholds = {
        "MEM_AVAILABLE_WARN_PCT": 10,
        "MEM_AVAILABLE_CRIT_PCT": 5,
        "PSI_MEM_SOME_WARN": 10.0,  # % of time with some process waiting for memory (5 min average)
        "PSI_MEM_FULL_CRIT": 5.0,   # % of time with ALL waiting for memory (1 min average)
        "MEM_WARN_PERSIST_MIN": 15,
        "SWAP_OUT_WARN_KBS": 50,
        "SWAP_OUT_CRIT_KBS": 200,
        "DISK_WARN_PCT": 75,
        "DISK_CRIT_PCT": 85,
        "DISK_FULL_WARN_DAYS": 14,
        "DISK_FULL_CRIT_DAYS": 3,
        "INODES_WARN_PCT": 80,
        "INODES_CRIT_PCT": 90,
        "LOAD_WARN_RATIO": 1.0,
        "LOAD_CRIT_RATIO": 2.0,
        "LOAD_WARN_PERSIST_MIN": 15,
        "LOAD_CRIT_PERSIST_MIN": 10,
    }

    def detect(self, src, cfg):
        return src.read("/proc/meminfo") is not None

    def host(self, ctx):
        self.memory(ctx)
        self.swap(ctx)
        self.disk(ctx)
        self.inodes(ctx)
        self.load(ctx)
        self.oom(ctx)

    def memory(self, ctx):
        sec, th = ctx.server_section, ctx.th
        mi = parse_meminfo(ctx.src.read("/proc/meminfo") or "")
        total = mi.get("MemTotal", 0) / 1024
        avail = mi.get("MemAvailable", 0) / 1024
        if total <= 0:
            ctx.add(Metric("host.memory", sec, t("host.memory"), t("host.memory.none"), "WARN", t("host.memory.no_file")))
            return
        used = total - avail
        pct = used * 100 / total
        avail_pct = avail * 100 / total
        ctx.facts.update(mem_pct=round(pct, 1), mem_avail_mb=round(avail))

        level, reasons = "OK", []
        if avail_pct <= th["MEM_AVAILABLE_CRIT_PCT"]:
            level = "CRIT"
            reasons.append(t("host.memory.crit", pct=fnum(avail_pct), limit=th["MEM_AVAILABLE_CRIT_PCT"]))
        elif avail_pct <= th["MEM_AVAILABLE_WARN_PCT"]:
            level = "WARN"
            reasons.append(t("host.memory.warn", pct=fnum(avail_pct), limit=th["MEM_AVAILABLE_WARN_PCT"]))

        psi = parse_psi(ctx.src.read("/proc/pressure/memory"))
        psi_txt = ""
        if psi:
            some300 = psi.get("some", {}).get("avg300", 0.0)
            full60 = psi.get("full", {}).get("avg60", 0.0)
            ctx.facts.update(psi_some300=some300, psi_full60=full60)
            psi_txt = t("host.memory.psi", pct=fnum(some300, 1))
            if full60 >= th["PSI_MEM_FULL_CRIT"]:
                level = "CRIT"
                reasons.append(t("host.memory.psi_full", pct=fnum(full60, 1)))
            elif some300 >= th["PSI_MEM_SOME_WARN"] and level == "OK":
                level = "WARN"
                reasons.append(t("host.memory.psi_some", pct=fnum(some300, 1)))

        avg7 = ctx.history_avg("mem_pct", 7 * 86400)
        avg_txt = t("host.memory.avg7", pct=fnum(avg7)) if avg7 is not None else ""
        value = t("host.memory.value", used=fnum(used), total=fnum(total), pct=fnum(pct), avg=avg_txt,
                  avail=fnum(avail), psi=psi_txt)
        ctx.add(Metric("host.memory", sec, t("host.memory"), value, level, "; ".join(reasons),
                       persist_min=th["MEM_WARN_PERSIST_MIN"], deploy_sensitive=True))

    def swap(self, ctx):
        sec, th = ctx.server_section, ctx.th
        mi = parse_meminfo(ctx.src.read("/proc/meminfo") or "")
        total = mi.get("SwapTotal", 0) / 1024
        used = total - mi.get("SwapFree", 0) / 1024
        if total <= 0:
            ctx.add(Metric("host.swap", sec, "Swap", t("host.swap.none"), "WARN", t("host.swap.none_detail")))
            return
        # Swap-OUT only: the kernel pushing pages to disk, pressure right now. Swap-in is
        # the opposite (recovery), and swap in use is the leftover of a past peak:
        # neither says the machine is suffering.
        pages = parse_vmstat(ctx.src.read("/proc/vmstat"), "pswpout")
        if pages is None:
            ctx.add(Metric("host.swap", sec, "Swap", t("host.swap.no_rate", used=fnum(used), total=fnum(total))))
            return
        prev = ctx.state.get("counters", {}).get("swap")
        ctx.updates.setdefault("counters", {})["swap"] = [ctx.now, pages]
        rate = None
        if prev and len(prev) == 2:
            elapsed, delta = ctx.now - prev[0], pages - prev[1]
            if elapsed > 0 and delta >= 0:  # delta < 0: a reboot reset the counter
                rate = delta * 4 / elapsed   # 4 KB pages
        if rate is None:
            ctx.add(Metric("host.swap", sec, "Swap", t("host.swap.first", used=fnum(used), total=fnum(total))))
            return
        ctx.facts["swapout_kbs"] = round(rate, 1)
        level = "CRIT" if rate >= th["SWAP_OUT_CRIT_KBS"] else "WARN" if rate >= th["SWAP_OUT_WARN_KBS"] else "OK"
        ctx.add(Metric("host.swap", sec, "Swap", t("host.swap.value", used=fnum(used), total=fnum(total), rate=fnum(rate)),
                       level, t("host.swap.detail", warn=th["SWAP_OUT_WARN_KBS"], crit=th["SWAP_OUT_CRIT_KBS"]),
                       deploy_sensitive=True, deploy_lookback=True))

    def disk(self, ctx):
        sec, th = ctx.server_section, ctx.th
        total, used, avail = ctx.src.disk("/")
        pct = used * 100 / total if total else 0
        ctx.facts.update(disk_used=used, disk_total=total)
        level, detail = "OK", ""
        if pct >= th["DISK_CRIT_PCT"]:
            level, detail = "CRIT", t("host.disk.full")
        elif pct >= th["DISK_WARN_PCT"]:
            level, detail = "WARN", t("host.disk.full")
        growth = ctx.history_slope("disk_used", 7 * 86400, min_span=2 * 86400)
        trend = ""
        if growth is not None:
            trend = t("host.disk.growth", sign="+" if growth >= 0 else "", growth=fbytes(growth))
            if growth > 0:
                days = avail / growth
                trend += t("host.disk.full_in", days=fnum(days))
                if days <= th["DISK_FULL_CRIT_DAYS"]:
                    level, detail = "CRIT", t("host.disk.pace", days=fnum(days, 1))
                elif days <= th["DISK_FULL_WARN_DAYS"] and level == "OK":
                    level, detail = "WARN", t("host.disk.pace", days=fnum(days))
        ctx.add(Metric("host.disk", sec, t("host.disk"), t("host.disk.value", pct=fnum(pct), free=fbytes(avail), trend=trend),
                       level, detail))

    def inodes(self, ctx):
        """A disk with free space and no free inodes stops just the same: no file can be
        created. Usually a folder with millions of tiny files (sessions, cache)."""
        total, free = ctx.src.inodes("/")
        if not total:
            return  # filesystem without an inode limit (btrfs, some xfs)
        pct = (total - free) * 100 / total
        th = ctx.th
        level = "CRIT" if pct >= th["INODES_CRIT_PCT"] else "WARN" if pct >= th["INODES_WARN_PCT"] else "OK"
        ctx.add(Metric("host.inodes", ctx.server_section, t("host.inodes"), t("host.inodes.value", pct=fnum(pct),
                       free=fnum(free)), level, t("host.inodes.detail")))

    def load(self, ctx):
        sec, th = ctx.server_section, ctx.th
        text = ctx.src.read("/proc/loadavg") or ""
        try:
            load1, load5 = float(text.split()[0]), float(text.split()[1])
        except (IndexError, ValueError):
            return
        cores = ctx.src.cpu_count()
        ctx.facts["load1"] = load1
        level = "CRIT" if load5 >= cores * th["LOAD_CRIT_RATIO"] else "WARN" if load5 >= cores * th["LOAD_WARN_RATIO"] else "OK"
        ctx.add(Metric("host.load", sec, "Load", t("host.load.value", load1=fnum(load1, 2), load5=fnum(load5, 2), cores=cores),
                       level, t("host.load.detail", limit=fnum(cores * th["LOAD_WARN_RATIO"], 1)),
                       persist_min=th["LOAD_WARN_PERSIST_MIN"], crit_persist_min=th["LOAD_CRIT_PERSIST_MIN"],
                       deploy_sensitive=True))

    def oom(self, ctx):
        last = ctx.state.get("last_run") or (ctx.now - 3600)
        minutes = int(min(70, max(6, (ctx.now - last) / 60 + 1)))
        rc, out, _ = ctx.src.run("journal_oom", ["journalctl", "-k", "--since", f"{minutes} min ago",
                                                 "--no-pager", "-q"], timeout=20)
        if rc != 0:
            return
        killed = re.findall(r"Killed process \d+ \(([^)]+)\)", out)
        if not killed:
            killed = ["?"] * len(re.findall(r"out of memory|oom-kill", out, re.IGNORECASE))
        ctx.facts["oom"] = len(killed)
        if killed:
            names = ", ".join(sorted(set(killed)))
            ctx.add(Metric("host.oom", ctx.server_section, "OOM kill", t("host.oom.value", n=len(killed), names=names), "CRIT",
                           t("host.oom.detail"), event=True))
        else:
            ctx.add(Metric("host.oom", ctx.server_section, "OOM kill", t("host.oom.none"), event=True))

    # -- reports -----------------------------------------------------------------

    def daily_lines(self, r, section, app):
        if app is not None or section != r.server_section:
            return []
        day = r.window(r.now - 86400, r.now + 1)
        if not day:
            return [t("report.no_history")]
        out = []

        def stats(field):
            vals = [h[field] for h in day if h.get(field) is not None]
            return (min(vals), sum(vals) / len(vals), max(vals)) if vals else None

        mem = stats("mem_pct")
        if mem:
            out.append(t("host.daily.ram", min=fnum(mem[0]), avg=fnum(mem[1]), max=fnum(mem[2])))
        psi = stats("psi_some300")
        if psi:
            out.append(t("host.daily.psi", max=fnum(psi[2], 1)))
        swp = stats("swapout_kbs")
        if swp:
            out.append(t("host.daily.swap", max=fnum(swp[2])))
        load = stats("load1")
        if load:
            out.append(t("host.daily.load", avg=fnum(load[1], 2), max=fnum(load[2], 2)))
        out.append(t("host.daily.oom", n=sum(h.get("oom", 0) for h in day)))
        out.append(t("host.daily.samples", n=len(day)))
        return out

    def weekly_fields(self, r):
        if r.route:
            return []
        cur = r.window(r.now - r.WEEK, r.now)
        prev = r.window(r.now - 2 * r.WEEK, r.now - r.WEEK)
        if not cur:
            lines = [t("report.no_history")]
        else:
            lines = self._weekly_lines(cur, prev)
        return [(10, {"name": r.server_section, "value": r.bullets(lines)})]

    @staticmethod
    def _weekly_lines(cur, prev):
        def vals(rows, field):
            return [h[field] for h in rows if h.get(field) is not None]

        out = []
        mem, mem_prev = vals(cur, "mem_pct"), vals(prev, "mem_pct")
        if mem:
            cmp_txt = t("host.weekly.ram_prev", pct=fnum(sum(mem_prev) / len(mem_prev))) if mem_prev else ""
            out.append(t("host.weekly.ram", avg=fnum(sum(mem) / len(mem)), max=fnum(max(mem)), prev=cmp_txt))
        psi = vals(cur, "psi_some300")
        if psi:
            out.append(t("host.daily.psi", max=fnum(max(psi), 1)))
        swp = vals(cur, "swapout_kbs")
        if swp:
            out.append(t("host.daily.swap", max=fnum(max(swp))))
        load = vals(cur, "load1")
        if load:
            out.append(t("host.daily.load", avg=fnum(sum(load) / len(load), 2), max=fnum(max(load), 2)))
        out.append(t("host.daily.oom", n=sum(h.get("oom", 0) for h in cur)))
        disk = [(h["ts"], h["disk_used"], h.get("disk_total")) for h in cur if h.get("disk_used") is not None]
        if len(disk) >= 2:
            (t0, u0, _), (t1, u1, tot) = disk[0], disk[-1]
            grow = u1 - u0
            txt = t("host.weekly.disk", pct=fnum(u1 * 100 / tot), sign="+" if grow >= 0 else "",
                    growth=fbytes(grow)) if tot else ""
            per_day = grow / max(1.0, (t1 - t0) / 86400)
            if tot and per_day > 0:
                days = (tot - u1) / per_day
                txt += t("host.disk.full_in", days=fnum(days)) if days < 365 else t("host.weekly.full_year")
            if txt:
                out.append(txt)
        return out

    strings = {
        "en": {
            "host.memory": "Memory",
            "host.memory.none": "not measured",
            "host.memory.no_file": "no /proc/meminfo",
            "host.memory.crit": "only {pct}% available (critical <= {limit}%)",
            "host.memory.warn": "only {pct}% available (warning <= {limit}%)",
            "host.memory.psi": " · waiting {pct}%",
            "host.memory.psi_full": "every process stopped waiting for memory {pct}% of the last minute",
            "host.memory.psi_some": "processes waiting for memory {pct}% of the last 5 min",
            "host.memory.avg7": " · 7d average {pct}%",
            "host.memory.value": "{used}/{total} MB ({pct}%){avg} · available {avail} MB{psi}",
            "host.swap.none": "not configured",
            "host.swap.none_detail": "without swap, a RAM peak goes straight to OOM",
            "host.swap.no_rate": "{used}/{total} MB (rate n/a)",
            "host.swap.first": "{used}/{total} MB (1st sample)",
            "host.swap.value": "{used}/{total} MB · swap-out {rate} KB/s",
            "host.swap.detail": "paging to disk (warning {warn}, critical {crit} KB/s)",
            "host.disk": "Disk",
            "host.disk.full": "a full disk stops deploys, logs and Postgres",
            "host.disk.growth": " · {sign}{growth}/day",
            "host.disk.full_in": " · full in ~{days} days",
            "host.disk.pace": "at last week's pace, the disk fills up in {days} days",
            "host.disk.value": "{pct}% ({free} free){trend}",
            "host.inodes": "Inodes",
            "host.inodes.value": "{pct}% used ({free} free)",
            "host.inodes.detail": "no free inodes means no new file, even with free space: look for a folder "
                                  "with millions of small files (`sudo du --inodes -x / | sort -n | tail`)",
            "host.load.value": "{load1} / {load5} over 5 min ({cores} vCPU)",
            "host.load.detail": "CPU queue above {limit}",
            "host.oom.value": "{n} process(es) killed: {names}",
            "host.oom.detail": "the kernel killed a process for lack of RAM; see who uses the memory",
            "host.oom.none": "none",
            "host.daily.ram": "RAM: min {min}% · avg {avg}% · max {max}%",
            "host.daily.psi": "Waiting for memory: max {max}%",
            "host.daily.swap": "Swap-out: max {max} KB/s",
            "host.daily.load": "Load: avg {avg} · max {max}",
            "host.daily.oom": "OOM kill: {n}",
            "host.daily.samples": "Samples: {n}",
            "host.weekly.ram": "RAM: avg {avg}% · max {max}%{prev}",
            "host.weekly.ram_prev": " · previous week {pct}%",
            "host.weekly.disk": "Disk: {pct}% · {sign}{growth} this week",
            "host.weekly.full_year": " · full in more than a year",
        },
        "pt-BR": {
            "host.memory": "Memória",
            "host.memory.none": "não medida",
            "host.memory.no_file": "sem /proc/meminfo",
            "host.memory.crit": "só {pct}% disponível (crítico <= {limit}%)",
            "host.memory.warn": "só {pct}% disponível (aviso <= {limit}%)",
            "host.memory.psi": " · espera {pct}%",
            "host.memory.psi_full": "todos os processos parados esperando memória {pct}% do último minuto",
            "host.memory.psi_some": "processos esperando memória {pct}% dos últimos 5 min",
            "host.memory.avg7": " · média 7d {pct}%",
            "host.memory.value": "{used}/{total} MB ({pct}%){avg} · disponível {avail} MB{psi}",
            "host.swap.none": "não configurado",
            "host.swap.none_detail": "sem swap, um pico de RAM vira OOM direto",
            "host.swap.no_rate": "{used}/{total} MB (taxa n/d)",
            "host.swap.first": "{used}/{total} MB (1a amostra)",
            "host.swap.value": "{used}/{total} MB · swap-out {rate} KB/s",
            "host.swap.detail": "paginando para o disco (aviso {warn}, crítico {crit} KB/s)",
            "host.disk": "Disco",
            "host.disk.full": "disco cheio trava deploy, log e Postgres",
            "host.disk.growth": " · {sign}{growth}/dia",
            "host.disk.full_in": " · cheio em ~{days} dias",
            "host.disk.pace": "no ritmo da última semana, o disco enche em {days} dias",
            "host.disk.value": "{pct}% ({free} livre){trend}",
            "host.inodes": "Inodes",
            "host.inodes.value": "{pct}% usados ({free} livres)",
            "host.inodes.detail": "sem inode livre nenhum arquivo novo é criado, mesmo com espaço: procure uma "
                                  "pasta com milhões de arquivos pequenos (`sudo du --inodes -x / | sort -n | tail`)",
            "host.load.value": "{load1} / {load5} em 5 min ({cores} vCPU)",
            "host.load.detail": "fila de CPU acima de {limit}",
            "host.oom.value": "{n} processo(s) morto(s): {names}",
            "host.oom.detail": "o kernel matou processo por falta de RAM; veja quem usa a memória",
            "host.oom.none": "nenhum",
            "host.daily.ram": "RAM: mín {min}% · média {avg}% · máx {max}%",
            "host.daily.psi": "Espera por memória: máx {max}%",
            "host.daily.swap": "Swap-out: máx {max} KB/s",
            "host.daily.load": "Load: média {avg} · máx {max}",
            "host.daily.oom": "OOM kill: {n}",
            "host.daily.samples": "Amostras: {n}",
            "host.weekly.ram": "RAM: média {avg}% · máx {max}%{prev}",
            "host.weekly.ram_prev": " · semana anterior {pct}%",
            "host.weekly.disk": "Disco: {pct}% · {sign}{growth} na semana",
            "host.weekly.full_year": " · cheio em mais de um ano",
        },
    }
