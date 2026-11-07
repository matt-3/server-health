# Configuration

The config is a `VAR=value` file, by default `/etc/default/server-health` (root, mode
600: it holds webhooks). Lines starting with `#` are comments; values may be quoted.
Any `SERVER_HEALTH_*` variable in the environment overrides the file, and `--config`
points to another file.

## Core settings

| Setting | Default | What |
|---|---|---|
| `SERVER_HEALTH_MODULES` | `auto` | which modules run: `auto`, a list (`host,docker`), or `auto,-ports` ([modules](modules.md)) |
| `SERVER_HEALTH_LANG` | `en` | language of the messages: `en` or `pt-BR` |
| `SERVER_HEALTH_LABEL` | the hostname | the server's name in the messages (`my-server-prod`) |
| `SERVER_HEALTH_TZ` | `UTC` | time zone of the messages, quiet hours, `active_hours` and the daily buckets |
| `SERVER_HEALTH_QUIET_HOURS` | empty (off) | `23-7`: warnings in this window wait and go out together at its end; critical always goes out |
| `SERVER_HEALTH_DISCORD_WEBHOOK` | empty | the server's Discord channel |
| `SERVER_HEALTH_DISCORD_WEBHOOK_<APP>` | | an app's own channel (`MY_APP` for `my-app`) |
| `SERVER_HEALTH_DISCORD_MENTION` | `@here` | mention on a new critical alert; `@everyone`, `<@&ROLE_ID>` or empty |
| `SERVER_HEALTH_WEBHOOK_URL` | empty | every message as JSON to this URL ([below](#generic-webhook)) |
| `SERVER_HEALTH_SENTRY_DSN` | empty | new alerts also recorded in Sentry |
| `SERVER_HEALTH_PING_URL` | empty | heartbeat URL called on every cron round (healthchecks.io) |
| `SERVER_HEALTH_DAILY_AT` | `09:00` | when the daily summary goes out, or `off` ([below](#reports)) |
| `SERVER_HEALTH_WEEKLY_AT` | `sat 07:00` | when the weekly report goes out, or `off` |
| `SERVER_HEALTH_WEEKLY_DASHBOARD` | `true` | attach the [dashboard](dashboard.md) to the weekly report on Discord |
| `SERVER_HEALTH_UPDATE_CHECK` | `true` | once a day, look for a newer server-health ([updates](modules.md#updates)) |
| `SERVER_HEALTH_UPDATE_URL` | the project's `versions.json` | where that list is read from |
| `SERVER_HEALTH_APPS_DIR` | `/etc/server-health/apps.d` | the apps' TOMLs |
| `SERVER_HEALTH_MODULES_DIR` | `/etc/server-health/modules.d` | third-party modules |
| `SERVER_HEALTH_STATE_DIR` | `/var/lib/server-health` | state, history, alerts |
| `SERVER_HEALTH_LOG` | `/var/log/server-health.log` | one line per round and per message |

## Module settings

| Setting | Module | What |
|---|---|---|
| `SERVER_HEALTH_BACKUP_STAMP` | backup | a file the backup script touches at the end; its age is the backup's |
| `SERVER_HEALTH_BACKUP_GLOB` | backup | the backup files (`/var/backups/my-app/*.dump`): the newest is compared with the usual size |
| `SERVER_HEALTH_SYSTEMD_IGNORE` | systemd | units that may fail, comma separated |
| `SERVER_HEALTH_IGNORE_APPS` | dokku | Dokku apps to skip, comma separated |
| `SERVER_HEALTH_HEALTH_PATH` | http | the health check path for Dokku apps (`/up`); an app's TOML can set its own |
| `SERVER_HEALTH_NGINX_LOG` | nginx | where each app's access log is, `{app}` replaced by its name (default Dokku's `/var/log/nginx/{app}-access.log`) |
| any name you choose | postgres | a database URL with its password, read by `database_url = "env:NAME"` in an app's TOML |
| `SERVER_HEALTH_TLS_DOMAINS` | tls | extra domains whose certificate expiry to check |

## Thresholds

Every threshold can be changed with the `SERVER_HEALTH_T_` prefix, for example
`SERVER_HEALTH_T_DB_WARN_GB=4`. The reasons behind the defaults are in
[alerts.md](alerts.md#thresholds-and-why).

| Module | Thresholds (default) |
|---|---|
| core | `REMIND_CRIT_MIN`=120, `REMIND_WARN_MIN`=1440 |
| updates | `UPDATE_CHECK_H`=24 |
| host | `MEM_AVAILABLE_WARN_PCT`=10, `MEM_AVAILABLE_CRIT_PCT`=5, `PSI_MEM_SOME_WARN`=10.0, `PSI_MEM_FULL_CRIT`=5.0, `MEM_WARN_PERSIST_MIN`=15, `SWAP_OUT_WARN_KBS`=50, `SWAP_OUT_CRIT_KBS`=200, `DISK_WARN_PCT`=75, `DISK_CRIT_PCT`=85, `DISK_FULL_WARN_DAYS`=14, `DISK_FULL_CRIT_DAYS`=3, `INODES_WARN_PCT`=80, `INODES_CRIT_PCT`=90, `LOAD_WARN_RATIO`=1.0, `LOAD_CRIT_RATIO`=2.0, `LOAD_WARN_PERSIST_MIN`=15, `LOAD_CRIT_PERSIST_MIN`=10 |
| backup | `BACKUP_WARN_H`=26, `BACKUP_CRIT_H`=50, `BACKUP_SHRINK_PCT`=50 |
| ssh | `SSH_KNOWN_DAYS`=30 |
| maintenance | `MAINT_STALE_DAYS`=7, `MAINT_CHECK_H`=6 |
| dokku | `APP_DOWN_PERSIST_MIN`=5, `DEPLOY_WINDOW_MIN`=30, `DEPLOY_5XX_RATIO`=3.0, `DEPLOY_5XX_MIN_PCT`=2.0, `DEPLOY_5XX_MIN_COUNT`=10, `DEPLOY_JOBS_RATIO`=3.0, `DEPLOY_JOBS_MIN`=5, `DEPLOY_UP_RATIO`=2.0, `DEPLOY_UP_MIN_MS`=300 |
| http | `UP_SLOW_WARN_S`=2.0, `UP_FAIL_PERSIST_MIN`=5 |
| nginx | `NGINX_5XX_WARN_COUNT`=10, `NGINX_5XX_WARN_PCT`=5.0, `NGINX_5XX_CRIT_COUNT`=50, `NGINX_5XX_CRIT_PCT`=20.0, `BOT_VOLUME_WARN`=300, `TRAFFIC_MIN_BASELINE`=50, `TRAFFIC_DROP_PCT`=20, `TRAFFIC_HISTORY_DAYS`=14 |
| postgres | `DB_WARN_GB`=2.0, `DB_CRIT_GB`=3.0, `DB_CONN_WARN_PCT`=80, `DB_CONN_CRIT_PCT`=95, `DB_LONG_TX_WARN_S`=300, `DB_LONG_TX_CRIT_S`=1800, `DB_DEAD_ROWS_MIN`=10000, `DB_VACUUM_STALE_DAYS`=7 |
| rails | `SQ_HEARTBEAT_WARN_S`=180, `SQ_HEARTBEAT_CRIT_S`=300, `SQ_HEARTBEAT_PERSIST_MIN`=5, `SQ_FAILED_WARN`=1, `SQ_FAILED_CRIT`=20, `SQ_QUEUE_AGE_WARN_S`=600, `SQ_QUEUE_AGE_CRIT_S`=1800, `JOB_ERROR_NEW_DAYS`=7, `TASK_GRACE_H`=2 |
| tls | `TLS_WARN_DAYS`=21, `TLS_CRIT_DAYS`=7, `TLS_CHECK_H`=6 |

## Channels

**Discord** is the main channel: one webhook for the server and, optionally, one per app
([installation](installation.md#2-discord-and-the-heartbeat)). A new critical alert
mentions `SERVER_HEALTH_DISCORD_MENTION`, so the phone rings.

### Generic webhook

With `SERVER_HEALTH_WEBHOOK_URL`, every message (alerts, reminders, summaries, weekly
reports and tests) is also posted as JSON:

```json
{
  "server": "my-server-prod",
  "type": "alert",
  "level": "CRIT",
  "route": null,
  "title": "Critical · Server · Memory",
  "text": "Critical · Server · Memory\n3,673/3,819 MB (96%) ...\n\n== Server\nMemory: ..."
}
```

`type` is `alert`, `reminder`, `summary`, `weekly` or `test`; `level` is `OK`, `WARN` or
`CRIT`; `route` is the app with its own Discord channel, or `null` for the server. It
is the bridge to anything else: [ntfy](https://ntfy.sh), a Slack workflow, n8n or a
script of yours.

### Sentry

With `SERVER_HEALTH_SENTRY_DSN`, each **new** alert (never a reminder, a "back to
normal" or a summary) becomes a Sentry event with the tags `app:infra`, `severity` and
`host`, so Alert Rules can filter them.

## Reports

The cron only runs the 5-minute round. The daily summary and the weekly report go out on
the first round after their time, in `SERVER_HEALTH_TZ`:

```bash
SERVER_HEALTH_TZ=America/Sao_Paulo
SERVER_HEALTH_DAILY_AT=18:30        # every day at 18:30
SERVER_HEALTH_WEEKLY_AT=mon 09:00   # Mondays at 09:00 (mon, tue, wed, thu, fri, sat, sun)
# SERVER_HEALTH_WEEKLY_AT=off       # no weekly report
```

Each goes out once on its day. A server that is off at that time sends it on its first
round later that same day; a day it never comes back to is skipped. `--dry-run` shows
when the next ones go out, and `--force` / `--force-weekly` send them now, marked as a
test.

## Quiet hours

With `SERVER_HEALTH_QUIET_HOURS="23-7"` (in `SERVER_HEALTH_TZ`), a new warning, one that
got worse to warning and a "back to normal" in this window wait in a queue and go out
together in the first round after it, each with the time it happened. Warning reminders
do not join the queue. Anything critical goes out at once.

## Muting

Planned maintenance (a reboot, a database migration) would trigger alerts nobody needs:

```bash
sudo server-health --mute 2h     # 90m, 2h, 1d
sudo server-health --unmute
```

While muted, rounds keep measuring and keeping state, but send no alert; summaries still
go out. Alerts still open at the end come back as reminders at their usual interval.
`--dry-run` shows the mute.
