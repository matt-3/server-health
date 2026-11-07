# Installation

Once per server, not per app: the monitor finds Dokku's apps on every round, and other
apps from their TOML. The
commands below run on the server as a user with `sudo`. Long commands are split with
`\`: a very long single line pasted inside `ssh '...'` arrives broken, and each piece
becomes a separate command.

## 1. Requirements

```bash
python3 --version                # 3.11 or newer (Ubuntu 24.04 ships 3.12)
timedatectl | grep "Time zone"   # the cron below counts in UTC
cat /proc/pressure/memory        # PSI must exist (any kernel from 4.20; Ubuntu 24.04: yes)
```

The monitor runs as root, because `docker stats`, `dokku` and the nginx logs need it.
Running it as a normal user would need passwordless `sudo` for `docker`, which is the
same as giving that user root without a password. It only reads: no command changes
the server, the apps or the databases, and the SQL runs in read-only sessions.

## 2. Discord and the heartbeat

- **Webhook:** in the server's channel, Edit channel → Integrations → Webhooks → Copy URL.
  It is a credential: it goes only into the server's config, never into git.
- **A channel per app (optional):** without anything else, every message goes to that
  webhook. To split, add `SERVER_HEALTH_DISCORD_WEBHOOK_<APP>` with the Dokku app name
  uppercased and `-` turned into `_` (`my-app` → `SERVER_HEALTH_DISCORD_WEBHOOK_MY_APP`).
  The app's processes, `/up`, requests, Solid Queue, deploy, bots, indicators and
  database then go to its channel, each channel gets its own daily and weekly report,
  and `@here` only rings in the channel of the critical alert. Server and maintenance
  stay on the server webhook, which remains required. If an app webhook fails, the
  message goes through the server's, and the log says so.
- **Heartbeat (recommended):** at [healthchecks.io](https://healthchecks.io) (the free
  plan is enough) create a check with a 5 min period and 25 min grace, turn on its
  Discord integration, and copy the ping URL. It covers what the monitor cannot see:
  the whole server down. Each server needs its own check.
- **Other channels:** `SERVER_HEALTH_WEBHOOK_URL` sends every message as JSON to any URL
  (see [configuration](configuration.md#generic-webhook)); `SERVER_HEALTH_SENTRY_DSN`
  records new alerts in Sentry.

## 3. Install

If some app has indicators, copy its TOML to the server first (from your computer):

```bash
scp path/to/my-app/scripts/monitor/my-app.toml deploy@<server>:/tmp/
```

On the server (replace `vX.Y.Z` with the version from the [changelog](../../CHANGELOG.md)):

```bash
curl -fsSL https://raw.githubusercontent.com/matt-3/server-health/vX.Y.Z/install.sh \
  | sudo bash -s -- vX.Y.Z /tmp/my-app.toml \
  && rm /tmp/my-app.toml
```

`install.sh`:

1. downloads the tag's `.tar.gz` from GitHub and checks it reports the version you asked;
2. runs the new version's `--check-app` on every TOML (the installed ones and the new
   ones); if one fails, **nothing changes**;
3. installs the package in `/usr/local/lib/server-health/<version>`, points
   `/usr/local/lib/server-health/current` to it and writes the `server-health` command
   in `/usr/local/bin`;
4. installs the TOMLs you passed in `/etc/server-health/apps.d/`;
5. keeps the previous version, for a rollback.

It is safe to run through `curl | bash`: the script runs only after it was read whole,
and no command it runs reads stdin.

## 4. Config

```bash
sudo install -o root -g root -m 600 /dev/null /etc/default/server-health
sudo nano /etc/default/server-health
```

```bash
SERVER_HEALTH_DISCORD_WEBHOOK=https://discord.com/api/webhooks/<id>/<token>
SERVER_HEALTH_LABEL=my-server-prod
SERVER_HEALTH_TZ=America/Sao_Paulo
SERVER_HEALTH_LANG=en
SERVER_HEALTH_PING_URL=https://hc-ping.com/<uuid>
# Optional:
# SERVER_HEALTH_DISCORD_WEBHOOK_MY_APP=https://discord.com/api/webhooks/<id>/<token>
# SERVER_HEALTH_BACKUP_STAMP=/var/backups/my-app/.last-ok
# SERVER_HEALTH_MODULES=auto
```

Every setting is in [configuration.md](configuration.md). Then test before scheduling:

```bash
sudo server-health --list-modules      # which modules are on
sudo server-health --dry-run           # the whole report; check "Channels: Discord=ok"
sudo server-health --simulate memory   # a critical test alert on the server channel
sudo server-health --simulate app      # an app alert, on the app's channel if it has one
sudo server-health --force             # the daily summary, marked as a test
sudo server-health --force-weekly      # the weekly report, marked as a test
```

Tests never change the state and arrive marked `TEST`. The first round shows `1st
sample` for swap and requests: they measure the difference between two rounds.

## 5. Schedule

```bash
sudo tee /etc/cron.d/server-health >/dev/null <<'EOF'
# server-health: one round every 5 minutes. The daily summary and the weekly report go
# out on the first round after SERVER_HEALTH_DAILY_AT / SERVER_HEALTH_WEEKLY_AT.
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * root /usr/local/bin/server-health >/dev/null 2>&1
EOF
sudo chmod 644 /etc/cron.d/server-health

sudo tee /etc/logrotate.d/server-health >/dev/null <<'EOF'
/var/log/server-health.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
}
EOF
```

The `cron.d` file must end with a newline and have no dot in its name: cron ignores it
silently otherwise.

The daily summary goes out at 09:00 and the weekly report on Saturday at 07:00, in
`SERVER_HEALTH_TZ`; change them in the config ([configuration](configuration.md#reports)).
`sudo server-health --dry-run` says when the next ones go out. Ten minutes later:

```bash
sudo tail -n 5 /var/log/server-health.log   # one "round: ..." line every 5 min
sudo ls -la /var/lib/server-health/         # state.json and history.jsonl
```

The heartbeat ping goes out only in cron rounds, never in `--dry-run`, `--force` or
`--simulate`.

## Files on the server

| Path | What |
|---|---|
| `/usr/local/bin/server-health` | the command |
| `/usr/local/lib/server-health/<version>/` | each installed version (the active one and the previous) |
| `/usr/local/lib/server-health/current` | link to the active version |
| `/etc/default/server-health` | the config, with the webhooks (root, **600**, outside git) |
| `/etc/server-health/apps.d/<app>.toml` | each app's indicators (root, 644) |
| `/etc/server-health/modules.d/<name>.py` | third-party modules (root, 644) |
| `/etc/cron.d/server-health` | the 5-minute round |
| `/var/lib/server-health/state.json` | the state: each metric's level, counters, nginx offsets |
| `/var/lib/server-health/history.jsonl` | one sample per round, 35 days, for averages, trends and the dashboard |
| `/var/lib/server-health/bots.json` | per day, each bot's requests and the pages bots asked for, 35 days |
| `/var/lib/server-health/alerts.jsonl` | one line per alert opened, changed or closed, 90 days |
| `/var/log/server-health.log` | one line per round and per message sent |

## Data kept

Everything the monitor writes has a limit, so nothing piles up:

| What | Kept |
|---|---|
| history (`history.jsonl`) and bot detail (`bots.json`) | 35 days |
| alerts (`alerts.jsonl`) | 90 days |
| state: metrics that disappeared, container restarts | 24 hours / 7 days |
| state: job errors seen, SSH ranges, open ports, certificate cache | 30 days |
| state: last run of daily, weekly and monthly tasks | 62 days |
| the log | 5 MB, then it becomes `server-health.log.1` (plus logrotate, if you set it up) |
| installed versions | the active one and the previous |

The history and the alerts are trimmed once a day. On a server with two apps, the whole state directory stays around a
few megabytes.

## Upgrade

The same command as the install, with the new version. Changed only an app's TOML? Run
it with the version already installed (`server-health --version`) and the new TOML.
The cron picks up the new version on its next round; the state survives the upgrade.

## Rollback

```bash
ls /usr/local/lib/server-health/                       # the versions kept
sudo ln -sfn 1.0.0 /usr/local/lib/server-health/current
server-health --version
```

## Uninstall

```bash
sudo rm -f /etc/cron.d/server-health /etc/logrotate.d/server-health \
  /usr/local/bin/server-health
sudo rm -rf /usr/local/lib/server-health /var/lib/server-health /etc/server-health
sudo rm -f /etc/default/server-health /var/log/server-health.log*
```
