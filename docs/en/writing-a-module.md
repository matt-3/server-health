# Writing a module

A module is one Python file with a subclass of `server_health.module.Module`. It can be
built in (in `server_health/modules/`, listed in `server_health/modules/__init__.py`) or
a third-party file on the server, without touching the package.

## A complete example

[`examples/modules/url_check.py`](../../examples/modules/url_check.py) checks that some
URLs answer 2xx:

```python
from server_health.i18n import fnum, t
from server_health.metric import Metric
from server_health.module import Module


class UrlCheck(Module):
    name = "url_check"                                  # unique; what SERVER_HEALTH_MODULES lists
    defaults = {"SERVER_HEALTH_URLS": ""}               # settings it reads, with defaults
    thresholds = {"URL_SLOW_WARN_S": 3.0}               # changeable with SERVER_HEALTH_T_URL_SLOW_WARN_S

    def detect(self, src, cfg):                         # for SERVER_HEALTH_MODULES=auto
        return bool(cfg.get("SERVER_HEALTH_URLS"))

    def host(self, ctx):                                # a phase: runs once per round
        ...
        ctx.add(Metric(f"url.{url}", ctx.server_section, url, value, "CRIT", t("url_check.down")))

    strings = {                                         # every text, in each language
        "en": {"url_check.down": "the URL did not answer 2xx"},
        "pt-BR": {"url_check.down": "a URL não respondeu 2xx"},
    }


MODULE = UrlCheck()                                     # what the loader looks for
```

## Installing a third-party module

```bash
sudo install -d -m 755 /etc/server-health/modules.d
sudo install -o root -g root -m 644 url_check.py /etc/server-health/modules.d/url_check.py
```

and add its file name to the config: `SERVER_HEALTH_MODULES=auto,url_check`. Then
`sudo server-health --list-modules` and `--dry-run`.

A file only loads when its name is listed, and only if it belongs to root (or to the
user running the monitor) and nobody else can write it: the monitor runs as root, so a
writable module would be a way into root. A file that fails to load becomes a config
warning in the report; it never stops the monitor.

## The phases

A round runs the phases in this order, each across the enabled modules in registry
order. Implement only the ones you need:

| Phase | Runs | Used by |
|---|---|---|
| `before(ctx)` | first | dokku (is a deploy running?) |
| `host(ctx)` | once | host, backup, ssh, maintenance, systemd, clock, ports |
| `discover(ctx)` | once | docker (containers), dokku (apps into `ctx.apps`) |
| `services(ctx)` | once | postgres (each database service) |
| `app(ctx, app)` | once per app | dokku, http, nginx, postgres |
| `daily(ctx)` | only in the daily summary | maintenance, dokku (upload traps) |
| `after_app(ctx, app)` | once per app, after all of them | dokku (suspicious deploy), nginx (traffic), tls |
| `finish(ctx)` | last | dokku (section names), tls |

After a normal round, `save(ctx, store)` lets a module keep something of its own (nginx
keeps `bots.json`).

## The context

`ctx` is shared by the modules during a round:

| Attribute | What |
|---|---|
| `ctx.src` | where readings come from: `read(path)`, `run(key, argv)`, `stat`, `glob`, `disk`, `now()`... Always use it (never `open` or `subprocess`): the tests replace it with fixtures |
| `ctx.cfg`, `ctx.th` | the config and the thresholds |
| `ctx.state`, `ctx.updates` | the previous round's state, and what to save (`ctx.updates["my_key"] = ...`) |
| `ctx.history` | the samples of the last 35 days |
| `ctx.facts` | this round's sample (`ctx.facts["my_value"] = 1.5` goes to the history) |
| `ctx.add(metric)` | adds a measurement |
| `ctx.server_section`, `ctx.maintenance_section` | the translated section names |
| `ctx.apps`, `ctx.app_config(app)`, `ctx.app_domains`, `ctx.services` | what the other modules found |
| `ctx.daily`, `ctx.weekly`, `ctx.now`, `ctx.tz`, `ctx.log` | the round |

`src.run(key, argv)` takes a key: in the tests, the output comes from
`tests/fixtures/<case>/cmd/<key>.txt` (and `.rc`, `.err`).

## The metric

```python
Metric(key, section, label, value, level="OK", detail="", event=False, persist_min=0,
       crit_persist_min=0, deploy_sensitive=False, summary_only=False, items=[])
```

- `key`: stable identity, never the value. It starts with `host.`, `mnt.`, `app.<app>.`,
  `db.<service>.` or `ind.<app>.`; the `app.` and `ind.` prefixes route the metric to the
  app's own channel.
- `level`: `OK`, `WARN` or `CRIT`, as measured now; the evaluator applies persistence.
- `detail`: why, or what to do. It goes into the alert; `` `code` `` is allowed.
- `event=True`: an occurrence (alerts on each one, no "back to normal").
- `summary_only=True`: information for the daily summary; never alerts.

## Report hooks

| Hook | What |
|---|---|
| `daily_lines(r, section, app)` | lines for the "Last 24h" block of a section |
| `weekly_app_lines(r, app)` | `(position, line)` for an app's block in the weekly report |
| `weekly_app_fields(r, section, app)` | extra fields for an app (nginx: bots) |
| `weekly_fields(r)` | `(position, field)` for the server-wide part (host: server, postgres: databases) |

`r` is a `report.ReportContext`: `r.window(start, end)` returns history rows, plus
`r.alerts_window`, `r.bots`, `r.daily_numbers`, `r.now`, `r.tz`, `r.bullets(lines)`.

## Database hooks

Modules that query an app's Postgres add statements to the postgres module's single
read-only session per app: `service_queries(ctx, svc)` for the per-service query and
`app_queries(ctx, app, svc)` + `app_results(ctx, app, res)` for the per-app one. Each
statement returns rows marked with `server_health.parse.PSQL_MARK`; the helper
`server_health.modules.postgres.q(key_sql, value_sql)` builds one.

## Strings

Every text a person reads goes through `t("key", **values)`, with the key in `strings`
for `en` and `pt-BR` (English is the fallback). Plurals use `tn("key", n)` with
`key.one` and `key.other`. Numbers go through `fnum`, sizes through `fbytes`, durations
through `fdur` and dates through `fdate`: they follow the language.

## A notifier

Channels are pluggable too: a subclass of `server_health.notify.Notifier` with
`configured()` and `send(message, route=..., mention=..., alert=..., attachment=...)`,
added in `server_health/notify/__init__.py`. The message is a dict shaped like a
Discord embed plus `_type`, `_level` and `_tag`; `server_health.text.embed_text` turns
it into plain text. See `notify/webhook.py` for a short one.

## Testing

`python3 -m unittest discover -s tests`. A module test usually copies
`tests/fixtures/basic`, writes the command outputs it needs in `cmd/`, and runs a round
through `tests/helpers.py`.
