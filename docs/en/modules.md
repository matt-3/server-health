# Modules

Everything the monitor measures is a module: one file in `server_health/modules/`.
`SERVER_HEALTH_MODULES` decides which ones run:

| Value | Runs |
|---|---|
| `auto` (default) | every module whose `detect` finds what it needs on this machine; a module with nothing to watch (no app, no domain) does nothing and costs nothing |
| `host` | only memory, swap, disk, inodes, load and OOM |
| `host,docker,ssh` | exactly those |
| `auto,-ports,-maintenance` | auto, without these |
| `auto,url_check` | auto, plus a [third-party module](writing-a-module.md) |

A module listed without the module it needs does not run, and `--dry-run` says why.
`server-health --list-modules` shows every module and which ones are on; `--dry-run`
prints the active list at the top.

## The modules

The **key** is the identity of each metric (what the anti-spam compares and what routes
an app's metric to its channel).

### `host` (Linux)

| Key | Measures | Alerts |
|---|---|---|
| `host.memory` | used, available, 7-day average, waiting for memory (PSI) | available ≤ 10% for 15 min (warning), ≤ 5% (critical); waiting `some` ≥ 10% (warning); `full` ≥ 5% (critical) |
| `host.swap` | swap in use and **swap-out** rate since the last round | swap-out ≥ 50 KB/s (warning), ≥ 200 (critical) |
| `host.disk` | use, growth per day (7 days) and when it fills up | ≥ 75% / 85%; full in 14 / 3 days |
| `host.inodes` | inodes in use | ≥ 80% / 90% |
| `host.load` | 1 and 5 min load per vCPU | 1.0 for 15 min / 2.0 for 10 min |
| `host.oom` | processes killed by the kernel for lack of memory (event) | any |

### `backup`

On when `SERVER_HEALTH_BACKUP_STAMP` or `SERVER_HEALTH_BACKUP_GLOB` is set.
`host.backup`: age of the stamp file (26h / 50h). `host.backup_size`: the newest backup
file against the median of the previous ones; below 50% is a warning (a backup that
ran and came out incomplete).

### `ssh` (journald)

`host.ssh_new` (event): a login from an IP range (/24, /48 on IPv6) that has not logged
in for 30 days. The first round learns the last 30 days silently.

### `maintenance` (Debian/Ubuntu)

`mnt.security_age` and `mnt.reboot_age`: security updates or a reboot pending for more
than 7 days. In the daily summary only: pending updates, Fail2Ban bans, SSH logins in
24h and the space Docker could reclaim.

### `updates`

`mnt.update`: once a day it reads the project's `versions.json` (one small GET; nothing
about the server is sent). A newer version with a security or critical fix is a warning,
repeated every 24h until the upgrade; one with features or ordinary fixes only shows in
the daily summary. `SERVER_HEALTH_UPDATE_CHECK=false` turns it off.

### `systemd`

`host.systemd`: failed units (`systemctl --failed`) for 10 min. Units expected to fail go
in `SERVER_HEALTH_SYSTEMD_IGNORE`.

### `clock`

`host.clock`: the clock not synchronized (`timedatectl`) for 30 min.

### `ports`

`host.ports_new` (event): a TCP port started listening outside loopback (`ss -ltn`).
The first round learns what is open.

### `docker`

No metric of its own (only `host.docker` when `docker ps` fails): it reads the
containers and their memory for the other modules and for "who uses the memory".

### Where the apps come from

The app modules (`http`, `nginx`, `postgres`, `rails`, `indicators`) work on a list of
apps. With Dokku, its apps join the list by themselves. Any other app (Docker Compose,
Kamal, a systemd service) joins it by declaring where its things are in its
[TOML](app-indicators.md#an-app-outside-dokku): `health_url`, `nginx_log`, `database_url`
or `database_container`.

### `dokku` (needs `docker`)

| Key | Measures |
|---|---|
| `app.<app>.ps` | processes per type, uptime, deploy (commit and date), memory; critical when a deployed app has no container for 5 min |
| `app.<app>.restart` | a restart outside a deploy (event) |
| `app.<app>.oom` | a container killed for memory (event) |
| `app.<app>.deploy` | suspicious deploy (event): 30 min after a deploy, 5xx, job failures or `/up` time much worse than the hour before |
| `app.<app>.storage` | daily: the TOML says `storage_mount = true` and Dokku has no storage mount |
| `app.<app>.body_size` | daily: nginx's `client-max-body-size` or Thruster's `MAX_REQUEST_BODY` below the TOML's `max_body_size` |

While a deploy runs (`git-hook`, `ps:rebuild`, `docker build`), memory, swap and load
warnings are ignored, and the swap-out of the round right after it too.

### `http`

`app.<app>.up`: the health check. For a Dokku app, `/up` (or `health_path` in the TOML)
asked by the machine itself (`curl --resolve <domain>:443:127.0.0.1`), without the CDN in
the middle; for another app, its `health_url` as given.
Critical with no 2xx for 5 min; warning above 2 s for 15 min.

### `nginx`

Reads each app's access log in nginx's combined format: Dokku's by default, or the
`nginx_log` of the TOML, or the pattern in `SERVER_HEALTH_NGINX_LOG`. Without a log it
reads nothing.

| Key | Measures |
|---|---|
| `app.<app>.http` | requests since the last round: people, bots by category, 5xx (warning: 10 and 5%; critical: 50 and 20%) |
| `app.<app>.bots` | a single bot (not a search engine, link preview or uptime monitor) with 300+ requests in 5 min |
| `app.<app>.traffic` | people in the last 30 min below 20% of the usual for that half hour (needs 14 days of history) |

Bots are classified by User-Agent and path (`BOT_RULES` in the module): search engines,
AI training, AI search and assistants, link previews, SEO, uptime monitors, scanners,
scripts, other bots. The weekly report shows each category's top bots, new bots, the
pages most read by AI and search engines and the attack paths most tried. Paths are
stored with identifiers replaced by `:id`; no IP is stored.

### `postgres`

The databases come from Dokku (`dokku postgres:connect`) and from the TOMLs:
`database_url` (psql on the machine; the password goes in `PGPASSWORD`, never on the
command line, which `ps` shows to every user) or `database_container` (psql inside that
container, through `docker exec`).

| Key | Measures |
|---|---|
| `db.<service>.size` | size and growth per day (2 GB / 3 GB) |
| `db.<service>.conn` | connections against `max_connections` (80% / 95%) |
| `db.<service>.long_tx` | the longest open transaction (5 min / 30 min) |
| `db.<service>.vacuum` | tables with 10,000+ dead rows and no vacuum for 7 days |
| `db.<service>.cache_hit` | daily summary: cache hit ratio |
| `db.<service>.tables` | daily summary: the 6 largest tables and their growth |

All queries run in a read-only session with a 5 s statement timeout. Each app costs one
connection per round: the `rails` and `indicators` queries share it.

### `rails` (needs `postgres`)

Runs only where the Solid Queue tables exist.

| Key | Measures |
|---|---|
| `app.<app>.sq` | heartbeat of worker, dispatcher and scheduler (3 min / 5 min) |
| `app.<app>.sq_failed` | failed jobs in 30 min (1 / 20) and in 24h, top classes |
| `app.<app>.job_error` | a job class failing with an exception not seen in 7 days (event) |
| `app.<app>.sq_queues` | per queue, the oldest ready job (10 min / 30 min); scheduled jobs late |
| `app.<app>.sq_recurring` | recurring tasks (minutes, hours) twice their usual interval late |
| `app.<app>.sq_long_tasks` | daily, weekly and monthly tasks past their schedule plus 2h |

### `indicators` (needs `postgres`)

`ind.<app>.<key>`: each app's SQL indicators, from its [TOML](app-indicators.md).

### `tls`

`app.<app>.tls` and `host.tls`: the soonest certificate expiry among the domains (21 /
7 days), read from the machine itself on port 443 with each domain's name. The domains
come from Dokku, from each app's `https` `health_url` and from
`SERVER_HEALTH_TLS_DOMAINS`. Checked every 6 hours; a domain served by another machine
is skipped.
