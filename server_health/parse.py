"""Parsers for what the host prints. Pure functions: no I/O, easy to test."""

from __future__ import annotations

import re
from datetime import datetime

# Marks the rows we asked psql for: it also prints headers, "SET" and notices.
PSQL_MARK = "@@SH@@"


def parse_meminfo(text: str) -> dict[str, int]:
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^(\w+):\s+(\d+)", line)
        if m:
            out[m.group(1)] = int(m.group(2))  # kB
    return out


def parse_psi(text: str | None) -> dict[str, dict[str, float]] | None:
    if not text:
        return None
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        values = {}
        for p in parts[1:]:
            k, _, v = p.partition("=")
            try:
                values[k] = float(v)
            except ValueError:
                pass
        out[parts[0]] = values
    return out or None


def parse_vmstat(text: str | None, field: str) -> int | None:
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == field and parts[1].isdigit():
            return int(parts[1])
    return None


def parse_size(s: str) -> float:
    """'1.234GiB', '512MiB', '20kB' -> bytes."""
    m = re.match(r"^\s*([\d.]+)\s*([KMGT]?i?B)\s*$", s.strip(), re.IGNORECASE)
    if not m:
        return 0.0
    num = float(m.group(1))
    unit = m.group(2).upper()
    mult = {"B": 1, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
            "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4}
    return num * mult.get(unit, 1)


def parse_body_size(s: str) -> int | None:
    """nginx and Thruster sizes ('1m', '50M', '512k', '10485760') -> bytes. '0' in
    nginx means no limit, returned as a very large number."""
    m = re.match(r"^\s*(\d+)\s*([kmg]?)b?\s*$", (s or "").strip(), re.IGNORECASE)
    if not m:
        return None
    n = int(m.group(1))
    if n == 0:
        return 2 ** 62
    return n * {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[m.group(2).lower()]


def dokku_names(text: str) -> list[str]:
    """Names from a Dokku listing, without headers ('=====> ...', 'NAME ...')."""
    names = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("=") or line.startswith("!") or line.upper().startswith("NAME"):
            continue
        names.append(line.split()[0])
    return names


def parse_psql(text: str) -> list[tuple[str, str]]:
    """Only the marked rows."""
    rows = []
    for line in (text or "").splitlines():
        if not line.startswith(PSQL_MARK + "|"):
            continue
        parts = line.split("|", 2)
        if len(parts) == 3:
            rows.append((parts[1], parts[2]))
    return rows


def to_float(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s.strip())
    except ValueError:
        return None


def parse_docker_time(s: str) -> float | None:
    if not s or s.startswith("0001-"):
        return None
    try:
        return datetime.fromisoformat(s[:19] + "+00:00").timestamp()
    except ValueError:
        return None


def in_hours(spec: str, hour: int) -> bool:
    """'09-23' -> from 9:00 to 22:59; '23-7' crosses midnight."""
    m = re.match(r"^\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$", spec or "")
    if not m:
        return False
    start, end = int(m.group(1)) % 24, int(m.group(2)) % 24
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def parse_duration(spec: str) -> int | None:
    """'90m', '2h', '1d', '45' (minutes) -> seconds."""
    m = re.match(r"^\s*(\d+)\s*([mhd]?)\s*$", spec or "", re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1)) * {"": 60, "m": 60, "h": 3600, "d": 86400}[m.group(2).lower()]
