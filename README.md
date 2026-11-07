# server-health

English | [Português](README.pt-BR.md)

[![test](https://github.com/matt-3/server-health/actions/workflows/test.yml/badge.svg)](https://github.com/matt-3/server-health/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)

**A light monitor for a Linux VPS that tells you when something changes, and stays quiet
otherwise.** It runs from cron every 5 minutes, measures the machine and the apps on it,
and posts to Discord (or any webhook) when a problem starts, while it lasts, and when it
ends. Every day a summary; every week a report with a dashboard.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/alert-dark.svg">
  <img alt="A critical memory alert on Discord, with the containers that use the most memory, and the 'Back to normal' message 35 minutes later" src="docs/assets/alert-light.svg" width="720">
</picture>

- **No agent, no daemon, no database, no web UI.** One Python package with no dependency
  and a cron line. A round takes about 70 ms and 30 MB, then nothing runs until the next.
- **Alerts on pressure, not on numbers that look high.** RAM 80% in use is a normal day;
  running out of headroom, processes waiting for memory or the kernel paging out is not.
- **Alerts on changes.** One message when a problem starts, a reminder while it lasts,
  "back to normal" with how long it took. The same problem never floods the channel.
- **Grows with the server.** The machine first; then Docker, Dokku, any app's health URL
  and access log, Postgres, Rails' Solid Queue and your own SQL indicators, each as an
  optional module. A module of your own is one Python file.

## Contents

- [Quick start](#quick-start)
- [What it watches](#what-it-watches)
- [Examples](#examples)
- [Daily summary and weekly report](#daily-summary-and-weekly-report)
- [Commands](#commands)
- [FAQ](#faq)
- [Documentation](#documentation)
- [Contributing](#contributing)

## Quick start

On the server, as a user with `sudo` (any Linux with Python 3.11+; Ubuntu 24.04 has it):

```bash
# 1. Install a version (the latest is in CHANGELOG.md)
curl -fsSL https://raw.githubusercontent.com/matt-3/server-health/v1.0.0/install.sh \
  | sudo bash -s -- v1.0.0

# 2. Tell it where to post (Discord: channel settings > Integrations > Webhooks)
sudo install -o root -g root -m 600 /dev/null /etc/default/server-health
sudo tee /etc/default/server-health >/dev/null <<'EOF'
SERVER_HEALTH_DISCORD_WEBHOOK=https://discord.com/api/webhooks/<id>/<token>
SERVER_HEALTH_LABEL=my-server
SERVER_HEALTH_TZ=Europe/Lisbon
EOF

# 3. Look, test, schedule
sudo server-health --dry-run              # what it measures right now
sudo server-health --simulate memory      # a test alert on Discord
echo '*/5 * * * * root /usr/local/bin/server-health >/dev/null 2>&1' \
  | sudo tee /etc/cron.d/server-health >/dev/null
```

> [!WARNING]
> `/etc/default/server-health` holds secrets: the webhooks and, if you add them, database
> URLs. Keep it owned by root with mode 600, as step 2 creates it.

That is the whole setup. The rest of this page is what you can add.

> [!TIP]
> A monitor on the server cannot tell you the server is down. Add a free heartbeat check
> with `SERVER_HEALTH_PING_URL`, and it alerts when the pings stop
> ([how](docs/en/installation.md#2-discord-and-the-heartbeat)).

## What it watches

**The server**, on any Linux:

| Module | Alerts when |
|---|---|
| `host` | available memory runs out or processes wait for memory; the kernel pages out; the disk fills up or will within days; inodes run out; the CPU queue grows; the kernel kills a process (OOM) |
| `systemd` | a systemd unit fails |
| `clock` | the clock stops being synchronized |
| `ports` | a new port starts listening to the outside |
| `ssh` | someone logs in from an IP range not seen in 30 days |
| `maintenance` | security updates or a reboot wait for more than a week (Debian/Ubuntu) |
| `backup` | the last good backup is too old, or the newest one is much smaller than usual |
| `tls` | a certificate expires in 21 days (warning) or 7 (critical) |
| `updates` | a newer server-health fixes a security or critical problem |

**The apps**, when there are any:

| Module | Alerts when | Works with |
|---|---|---|
| `docker` | (feeds "who uses the memory") | Docker |
| `dokku` | an app has no process running; a container restarts or is killed for memory; a deploy makes 5xx, job failures or response time much worse; an upload would be lost or refused | Dokku |
| `http` | the health URL stops answering 2xx, or gets slow | Dokku apps, or `health_url` |
| `nginx` | 5xx in volume; one bot hammers the app; visitors drop far below the usual for the hour | an access log |
| `postgres` | a database grows too big, runs out of connections, holds a long transaction or falls behind on vacuum | Dokku, `database_url` or `database_container` |
| `rails` | Solid Queue's worker, dispatcher or scheduler stops; jobs fail; a job fails with an error never seen before; a queue stops; a recurring task misses its rhythm | Postgres with Solid Queue |
| `indicators` | one of your SQL indicators crosses its limit | Postgres |

With `SERVER_HEALTH_MODULES=auto` (the default) a module with nothing to watch does
nothing and costs nothing. Every module, metric and threshold:
[docs/en/modules.md](docs/en/modules.md).

## Examples

### Only the machine

A VPS with a database or a game server, nothing else: the default already does it. To
keep exactly the basics:

```bash
SERVER_HEALTH_MODULES=host,systemd,ssh
```

### A server with Dokku

Nothing to declare: the apps, their databases and their logs are found on every round.
Give each app its own Discord channel if you like (the server's channel keeps the
machine's alerts):

```bash
SERVER_HEALTH_DISCORD_WEBHOOK_SHOP=https://discord.com/api/webhooks/<id>/<token>
```

### An app outside Dokku (Docker Compose, Kamal, systemd)

Declare where its things are in `/etc/server-health/apps.d/shop.toml`:

```toml
app = "shop"
health_url = "https://shop.example.com/up"
nginx_log = "/var/log/nginx/shop.access.log"
database_url = "env:SHOP_DATABASE_URL"   # the URL itself stays in the root-only config
```

and in `/etc/default/server-health`:

```bash
SHOP_DATABASE_URL=postgres://monitor:secret@127.0.0.1:5432/shop
```

> [!NOTE]
> A `database_url` with a password written in the TOML is refused: every user of the
> machine can read that file, and only root reads the config file.

### Your own indicators

What only the app knows matters, in SQL (read-only, counts and sums):

```toml
[[indicator]]
key = "orders_stuck"
label = "Orders stuck in processing"
sql = "SELECT count(*) FROM orders WHERE status = 'processing' AND updated_at < now() - interval '15 minutes'"
warn_at = 1
critical_at = 5
explanation = "the processing job stopped halfway: check Solid Queue"
```

`sudo server-health --check-app shop.toml` validates the file and runs each query once.
More in [docs/en/app-indicators.md](docs/en/app-indicators.md).

### Not Discord

Every message also goes, as JSON, to any URL: [ntfy](https://ntfy.sh), a Slack workflow,
n8n or a script of yours.

```bash
SERVER_HEALTH_WEBHOOK_URL=https://ntfy.sh/my-server-alerts
```

### Planned maintenance

```bash
sudo server-health --mute 2h   # rounds keep measuring; no alert goes out
sudo server-health --unmute
```

## Daily summary and weekly report

Every day the summary lists what needs attention, each part of the server and of each
app, the last 24 hours and the product numbers of the day:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/summary-dark.svg">
  <img alt="The daily summary on Discord: what needs attention, the server, an app with its requests and bots, and the numbers of the day" src="docs/assets/summary-light.svg" width="720">
</picture>

The weekly report compares the week with the one before (alerts, availability, traffic,
bots, databases) and carries the dashboard, an HTML page with the charts of the last
seven days:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/dashboard-dark.svg">
  <img alt="The dashboard: memory used and waiting for memory over seven days, and an app's requests per hour" src="docs/assets/dashboard-light.svg" width="720">
</picture>

They go out at 09:00 and on Saturday at 07:00, in your time zone. Change or turn them off:

```bash
SERVER_HEALTH_DAILY_AT=18:30
SERVER_HEALTH_WEEKLY_AT=mon 09:00   # or off
```

## Commands

| Command | What it does |
|---|---|
| `server-health` | a round (what cron runs every 5 minutes) |
| `server-health --dry-run` | the whole report, what would be alerted and when the next summary goes out; sends nothing |
| `server-health --simulate memory` | a test alert (`memory`, `disk`, `worker`, `app`, `indicator`, `oom`) |
| `server-health --force` / `--force-weekly` | the daily summary / weekly report now, marked as a test |
| `server-health --mute 2h` / `--unmute` | silence alerts during planned maintenance |
| `server-health --list-modules` | which modules exist and which are on |
| `server-health --check-app app.toml` | validate an app's TOML and run each query once |
| `server-health --dashboard out.html` | write the dashboard |
| `server-health --serve` | serve the dashboard on 127.0.0.1:8730, to open through an SSH tunnel |

## FAQ

### Weight and cost

<details>
<summary><b>How heavy is it?</b></summary>

About 70 ms of CPU and 30 MB of memory per round, and nothing runs in between. Rounds come
every 5 minutes; slow or slow-changing checks (certificates, the update check, `apt`) are
cached for hours, and it opens at most one database session per app.

</details>

<details>
<summary><b>Does it keep growing on disk?</b></summary>

No: history is kept 35 days, alerts 90 days, and the log is capped at 5 MB. Old data is
pruned once a day by the normal rounds.

</details>

### Data and privacy

<details>
<summary><b>Does it change anything on my server?</b></summary>

No. Every command only reads, and SQL runs in read-only sessions with a timeout.

</details>

<details>
<summary><b>What data leaves the server?</b></summary>

Only the messages you see in your channel. Indicators are counts and sums; request paths
are stored with ids replaced by `:id`; no visitor IP is kept. The update check is one GET a
day to this repository, carrying nothing about the server
(`SERVER_HEALTH_UPDATE_CHECK=false` turns it off).

</details>

### Setup and operation

<details>
<summary><b>Does it need Docker or Dokku?</b></summary>

No. They add modules; the machine is watched without them.

</details>

<details>
<summary><b>What if the server is down?</b></summary>

A monitor on the server cannot tell you that. Add a free heartbeat check
(healthchecks.io or similar) with `SERVER_HEALTH_PING_URL`: when the pings stop, it
alerts.

</details>

<details>
<summary><b>How do I turn off a module I do not want?</b></summary>

List it with a minus after `auto`: `SERVER_HEALTH_MODULES=auto,-ports,-maintenance`.
`server-health --list-modules` shows which modules exist and which are on.

</details>

<details>
<summary><b>How do I upgrade?</b></summary>

Run the install command again with the new version. The cron picks it up on its next
round, the state survives, and the previous version is kept for a rollback
([installation](docs/en/installation.md#upgrade)).

</details>

<details>
<summary><b>Can I see the data without Discord?</b></summary>

Yes: `--dry-run` shows the current state, and `--dashboard` and `--serve` show the
charts.

</details>

## Documentation

- [Installation](docs/en/installation.md): requirements, config, cron, data kept, upgrade and rollback
- [Configuration](docs/en/configuration.md): every setting, thresholds, channels, reports, quiet hours, language
- [Modules](docs/en/modules.md): what each module measures and how `auto` decides
- [Apps](docs/en/app-indicators.md): apps outside Dokku and your SQL indicators
- [Alerts](docs/en/alerts.md): when it alerts, thresholds and why, and what to do for each alert
- [Dashboard](docs/en/dashboard.md): the HTML page with the charts
- [Writing a module](docs/en/writing-a-module.md): your own checks or notifiers
- [Releasing](docs/en/releasing.md): tests, versions and tags

Messages come in English or Portuguese (`SERVER_HEALTH_LANG=pt-BR`).

## Contributing

Bug reports, fixes, better messages, new modules (another database, another web server,
another platform), new notifiers and translations are welcome. A module is one file:
see [writing a module](docs/en/writing-a-module.md) and [CONTRIBUTING.md](CONTRIBUTING.md).
For something big, open an issue first.

```bash
python3 -m unittest discover -s tests   # the whole monitor against fixtures, no server needed
```

## License

[MIT](LICENSE).
