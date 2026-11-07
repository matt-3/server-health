"""Message catalogs and number/date formatting.

Every text a person reads (Discord, the dry-run report, logs meant for people) goes
through `t()`. Each module registers its own strings, so a plugin is a single file
that carries its translations. A key missing in the chosen language falls back to
English, and a key missing everywhere is shown as the key itself, never an error:
a monitor that crashes over a missing translation is worse than an ugly line.
"""

from __future__ import annotations

SUPPORTED = ("en", "pt-BR")
_ALIASES = {"pt": "pt-BR", "pt_br": "pt-BR", "pt-br": "pt-BR", "en_us": "en", "en-us": "en"}

_lang = "en"
_catalogs: dict[str, dict[str, str]] = {lang: {} for lang in SUPPORTED}


def normalize(lang: str | None) -> str:
    raw = (lang or "").strip()
    if raw in SUPPORTED:
        return raw
    return _ALIASES.get(raw.lower(), "en")


def set_lang(lang: str | None) -> str:
    global _lang
    _lang = normalize(lang)
    return _lang


def lang() -> str:
    return _lang


def register(strings: dict[str, dict[str, str]]):
    """Adds `{lang: {key: text}}` to the catalogs. Later registrations win."""
    for code, items in strings.items():
        _catalogs.setdefault(normalize(code) if code not in _catalogs else code, {}).update(items)


def has(key: str) -> bool:
    return key in _catalogs.get(_lang, {}) or key in _catalogs["en"]


def t(key: str, /, **kw) -> str:
    # `key` is positional-only: several messages have a {key} placeholder of their own.
    text = _catalogs.get(_lang, {}).get(key)
    if text is None:
        text = _catalogs["en"].get(key, key)
    return text.format(**kw) if kw else text


def tn(key: str, n: float, /, **kw) -> str:
    """Plural: `key.one` when n == 1, `key.other` otherwise. `n` is passed to the text."""
    return t(f"{key}.one" if n == 1 else f"{key}.other", n=n, **kw)


# -- formatting ---------------------------------------------------------------

_SEPARATORS = {"en": (",", "."), "pt-BR": (".", ",")}


def fnum(x: float, dec: int = 0) -> str:
    thousands, decimal = _SEPARATORS.get(_lang, _SEPARATORS["en"])
    s = f"{x:,.{dec}f}"
    return s.replace(",", "\0").replace(".", decimal).replace("\0", thousands)


def fbytes(n: float) -> str:
    n = float(n)
    if abs(n) >= 1024 ** 3:
        return f"{fnum(n / 1024 ** 3, 2)} GB"
    if abs(n) >= 1024 ** 2:
        return f"{fnum(n / 1024 ** 2)} MB"
    if abs(n) >= 1024:
        return f"{fnum(n / 1024)} KB"
    return f"{fnum(n)} B"


def fdur(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 90:
        return f"{s} s"
    m = s // 60
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}h{m:02d}" if m else f"{h}h"
    return f"{h // 24}d"


def fdate(ts: float, tz, fmt_key: str = "fmt.day_time") -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts, tz).strftime(t(fmt_key))


def weekday(n: int) -> str:
    return t("fmt.weekdays").split(",")[n]


register({
    "en": {
        "fmt.day_time": "%b %d %H:%M",
        "fmt.day": "%b %d",
        "fmt.full": "%Y-%m-%d %H:%M",
        "fmt.utc": "%b %d %H:%M UTC",
        "fmt.weekdays": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
    },
    "pt-BR": {
        "fmt.day_time": "%d/%m %H:%M",
        "fmt.day": "%d/%m",
        "fmt.full": "%d/%m/%Y %H:%M",
        "fmt.utc": "%d/%m %H:%M UTC",
        "fmt.weekdays": "seg,ter,qua,qui,sex,sáb,dom",
    },
})
