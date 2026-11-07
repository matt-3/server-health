# Alerts

## When it alerts

An alert is a **change of state**. Its identity is the metric's key with its level
(`host.memory` at warning), never the measured value: 3,067 MB and 3,071 MB are the
same warning.

| Situation | Message |
|---|---|
| A metric goes from OK to warning or critical | at once (critical with `@here`) |
| Warning becomes critical, or critical goes down to warning | at once |
| Back to normal | "Back to normal", with how long it lasted |
| Still critical | a reminder every 2h |
| Still warning | a reminder every 24h |
| An occurrence (OOM kill, container restart, new job error, SSH from a new IP, new port, suspicious deploy) | one message per occurrence, no "back to normal" |
| Everything OK | nothing |
| Daily summary (`SERVER_HEALTH_DAILY_AT`, 09:00 by default) | always, with the color of the current state |
| Weekly report (`SERVER_HEALTH_WEEKLY_AT`, Saturday 07:00 by default) | always, with the dashboard attached |

**Persistence.** Some metrics only alert after some minutes at the same level: memory
(15 min at warning), load (15 min at warning, 10 at critical), an app without processes
and a failing `/up` (5 min), Solid Queue without a heartbeat (5 min), and the indicators
that ask for it (`persist_min`). The peak of a build or the restart of a deploy does not
become an alert. In the report, a metric in that window shows "waiting for
confirmation".

**A deploy running.** While a `git-hook`, `ps:rebuild` or `docker build` runs, memory,
swap and load warnings are ignored; processes, `/up` and the rest still count. Swap-out
measures the interval since the previous round, so it is also ignored in the round right
after a build or after a new app container appears. An alert already open stays open
during a deploy, silently: a deploy does not say the problem ended. It only closes when
the measure is back to normal.

**A metric that disappears** (an app removed, a failed collection) keeps its state for
24h silently, so there is no false "back to normal".

## The message

The server's name goes on top; the title says the level and what changed
(`🔴 Critical · Server · Memory`), and the text starts with the measured value and the
why. Below, the metric's section with each line marked (🔴 critical, 🟡 warning, 🟢 OK),
problems first and in bold, and "Open now" with everything still at warning or critical.
On a memory alert, "Who uses the memory" lists the hungriest containers. Discord does
not color text: the color is the side bar, and each line's state is its emoji.

Each app has its own sections: Dokku writes one nginx log per app, and everything that
belongs to the app (processes, `/up`, requests, bots, database, Solid Queue,
indicators) is in its section. Memory, disk, load and maintenance are the whole
server's.

## The daily summary

It opens with "Needs attention" when something is open, then every section with its
lines, the server's last 24 hours (RAM min/average/max, waiting for memory, swap-out,
load, OOM kills), each app's requests and bots of the day, the largest tables, and the
summary indicators ("numbers of the day").

## The weekly report

Sent at `SERVER_HEALTH_WEEKLY_AT` (Saturday at 07:00 by default), it covers the 7
previous days against the week before:

- **Alerts of the week:** how many opened, each with how many times and for how long,
  and the ones still open.
- **Per app:** availability (rounds with `/up` in 2xx, median and worst response),
  people's requests with the peak hour, 5xx, deploys, restarts outside a deploy, and
  failed jobs per class.
- **Bots, per app:** total and share of the requests, each category with its top bots,
  bots seen for the first time in 4 weeks, the pages most read by AI and search engines
  and the attack paths most tried.
- **Numbers of the week, per app:** the sum of the summary indicators of the 7 daily
  summaries.
- **Server, databases and maintenance:** RAM, waiting for memory, swap-out, load and OOM
  of the week, disk growth and forecast, each database's size at the start and the end,
  and pending maintenance.
- **The [dashboard](dashboard.md)**, as an HTML file attached to the message.

## What does not alert, on purpose

- **RAM in use.** 80% in use with 20% available and nobody waiting for memory is normal
  for a server with two apps. Occupancy shows in the report, next to its 7-day average,
  as information.
- **Swap in use.** It is the leftover of a past peak: the kernel only brings pages back
  when someone uses them, and the number does not go down by itself.
- **Swap-in.** It is the opposite of pressure: the machine leaving swap.

## Thresholds and why

| Metric | Warning | Critical | Why |
|---|---|---|---|
| Available memory (`MemAvailable`) | ≤ 10% for 15 min | ≤ 5% | the real headroom for a peak; below 5% the OOM killer is near |
| Waiting for memory (PSI `some`, 5 min) | ≥ 10% for 15 min | - | processes stopped waiting for memory: slowness has started |
| Everything waiting (PSI `full`, 1 min) | - | ≥ 5% | every process stopped at once |
| Swap-out | 50 KB/s | 200 KB/s | the kernel pushing pages to disk right now |
| Disk | 75% | 85% | a full disk stops deploys, logs and Postgres at once |
| Disk forecast (7-day pace) | full in 14 days | in 3 days | time to clean up or grow the disk |
| Inodes | 80% | 90% | no inode, no new file, even with free space |
| Load (5 min, per vCPU) | 1.0 for 15 min | 2.0 for 10 min | a CPU queue forming |
| OOM kill | - | any | something already died for lack of memory |
| Database | 2 GB | 3 GB | a large database eats the machine's page cache |
| Postgres connections | 80% of the max | 95% | near the limit the app refuses requests |
| Open transaction | 5 min | 30 min | holds locks and the vacuum |
| Vacuum falling behind | a table with 10,000+ dead rows and no vacuum in 7 days | - | queries slow down and the table grows |
| App without processes | - | 5 min | a deployed app down |
| `/up` | above 2 s for 15 min | no 2xx for 5 min | slow or down, measured without the CDN |
| 5xx in nginx (per round) | 10 and 5% | 50 and 20% | errors in volume; both minimums avoid alarms with little traffic |
| Solid Queue without heartbeat | 3 min | 5 min (5 min in a row) | without a worker no job runs; without a scheduler no scheduled task |
| Failed jobs (30 min) | 1 | 20 | a job that failed and sits in the failed list |
| New job error (event) | a job class with an exception not seen in 7 days, or ever | - | Sentry's "new issue", for jobs |
| Suspicious deploy (event, 30 min after) | 5xx 3× higher with 2% and 10 errors; or job failures 3× with 5; or median `/up` 2× with 300 ms | - | the deploy that made something worse, with the commit |
| Stuck queue (oldest ready job) | 10 min | 30 min | worker stopped, slow or without a free thread |
| Recurring task (minutes and hours) | twice its usual interval | - | the task did not run at its rhythm |
| Daily, weekly or monthly task | its schedule's interval plus 2h | - | a purge or a daily reminder did not run |
| People in 30 min | below 20% of the same half hour's median over 7 days, for 10 min | - | DNS, CDN or redirect broken while `/up` answers; only with 14 days of history and a baseline of 50+ |
| Backup | 26 h | 50 h | the daily backup did not finish |
| Backup size | below 50% of the usual | - | a backup that ran and came out incomplete |
| Security update pending | for more than 7 days | - | a security fix waiting |
| Reboot pending | for more than 7 days | - | the updated kernel or library only counts after a reboot |
| server-health update | a newer version with a security or critical fix | - | the monitor itself needs the fix |
| SSH from a new IP range (event) | a /24 (IPv4) or /48 (IPv6) not seen in 30 days | - | access nobody on the team made shows up in the same minute |
| New listening port (event) | any | - | a service exposed that nobody opened on purpose |
| Failed systemd unit | for 10 min | - | something died outside Docker |
| Clock not synchronized | for 30 min | - | TLS, tokens with expiry and scheduling break |
| TLS certificate | expires in 21 days | in 7 days | an expired certificate takes the site down for every browser |
| Bot in volume (a single bot, not search/preview/uptime) | 300 requests in 5 min | - | an aggressive crawler weighing on the CPU |
| Storage mount / upload size (daily) | missing / below the TOML's | - | uploads lost on the next deploy / refused with 413 and no trace in the app log |

### The first week

Thresholds and quiet hours are tuned by what reached Discord. The warnings sent at night
come out of the log (here, 23:00 to 07:00 in São Paulo, 02:00 to 10:00 in the server's
UTC clock):

```bash
sudo zcat -f /var/log/server-health.log* \
  | awk '/Discord: sent \((WARNING|BACK TO NORMAL|STILL OPEN|CHANGES)\)/ { h = substr($2, 1, 2) + 0; if (h >= 2 && h < 10) print }'
```

The log writes the message kind in the language of the messages (`SERVER_HEALTH_LANG`):
in Portuguese the kinds are `AVISO`, `NORMALIZOU`, `SEGUE` and `MUDANÇAS`. A warning that
repeats at night without asking for action asks for a higher threshold or a longer
persistence (`SERVER_HEALTH_T_*`, or `persist_min` in the TOML). If the problem is only
the hour, set `SERVER_HEALTH_QUIET_HOURS="23-7"`.

## I got an alert

The message has what changed, the explanation and every metric of the affected part.

```bash
sudo server-health --dry-run           # the state now, complete
sudo tail -n 50 /var/log/server-health.log
```

| Alert | First step |
|---|---|
| **Memory** | "Who uses the memory" names the container. Web above normal: `dokku ps:restart <app>` relieves it at once (zero downtime). Growing week after week: time for a bigger plan |
| **Swap-out** | same as memory: the same lack of headroom, seen through the disk |
| **OOM kill / container killed for memory** | `sudo journalctl -k --since '1 hour ago' \| grep -i -A5 'killed process'` and `dokku ps:report <app>`. Web and worker restart by themselves; check `/up` |
| **Disk or disk forecast** | `sudo docker system df` and `sudo docker system prune`. A growing database: the largest tables are in the daily summary |
| **Inodes** | `sudo du --inodes -x / \| sort -n \| tail`: usually a folder with millions of small files |
| **Load** | `htop` and `sudo docker stats --no-stream`. Outside a deploy and sustained, the CPU is short |
| **Processes: none running** | `dokku ps:report <app>` and `dokku logs <app> -n 200` |
| **Restart outside a deploy** | the process died and Docker started it again: `dokku logs <app> -n 300` around that time |
| **Health (`/up`)** | failing with the processes up: `dokku logs <app> -n 200` and `dokku nginx:show-config <app>`. Slow: database or memory (see the other metrics in the message) |
| **Requests (5xx)** | `dokku logs <app> -n 300 \| grep -E 'Error\|500'` and the app's error tracker |
| **Database size** | the largest tables are in the daily summary. A large `solid_cache_entries`: clear the cache |
| **Connections** | `dokku postgres:connect <service>` and `SELECT state, count(*) FROM pg_stat_activity GROUP BY 1;` |
| **Longest transaction** | `SELECT pid, now() - xact_start, state, left(query, 80) FROM pg_stat_activity WHERE xact_start IS NOT NULL ORDER BY 2 DESC LIMIT 5;` |
| **Vacuum falling behind** | `SELECT relname, n_dead_tup, last_autovacuum FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 5;` then `VACUUM (ANALYZE) <table>;` at a quiet hour |
| **Solid Queue without heartbeat** | `dokku ps:report <app>` (is the `worker` process up?) and `dokku logs <app> -p worker -n 200` |
| **Failed jobs / new job error** | the app's jobs dashboard (Mission Control): the class and the error of each. The message never carries the error text (it may have personal data). On the first round after install the monitor learns the failures that already existed, silently |
| **Suspicious deploy** | the message has the commit and what got worse. The error tracker at that time and `dokku logs <app> -n 300`; rolling back is a deploy of the previous commit |
| **Stuck queue** | a queue with its oldest job waiting: worker without a free thread or stuck on a slow job. A queue with hundreds of jobs and the oldest days old: no worker listens to it (check `config/queue.yml`; a recurring task with `command` goes to `solid_queue_recurring` unless it has a `queue:`) |
| **Recurring / daily and weekly tasks** | the scheduler stopped, or the task is failing, hung or left `recurring.yml`: the jobs dashboard |
| **People in 30 min** | the site is down for the outside with the machine up: open it from a phone, check DNS and the CDN's rules and status. A holiday or an odd night lowers the number too |
| **Bot in volume** | the message names it. If it weighs and brings nothing (AI training crawler, SEO, a script), block its User-Agent at the CDN (on Cloudflare: Security → WAF → Custom rules) |
| **Indicators** | the message has the indicator's `explanation`, which says the first step |
| **Monitor config** | `sudo server-health --check-app /etc/server-health/apps.d/<app>.toml` shows the error |
| **SSH from a new IP** | the message has the user, the IP and the method. Someone on the team on a new network? If not: `sudo last -i`, `sudo journalctl -u ssh --since '1 hour ago'`, rotate the key and check `~/.ssh/authorized_keys` |
| **New port open** | `sudo ss -ltnp` shows the process. Close it or put it behind the firewall |
| **Failed service** | `systemctl status <unit>` and `journalctl -u <unit> -n 50` |
| **Clock** | `timedatectl timesync-status` and `sudo systemctl restart systemd-timesyncd` |
| **TLS certificate** | renew it (`dokku letsencrypt:auto-renew`, or your provider's) |
| **Backup / backup size** | the backup script's log; open the newest backup before trusting it |
| **Storage mount** | `dokku storage:ensure-directory <app>` and `dokku storage:mount <app> /var/lib/dokku/data/storage/<app>:/rails/storage`, then a restart |
| **Upload size** | `dokku nginx:set <app> client-max-body-size 50m` and `dokku config:set <app> MAX_REQUEST_BODY=<bytes>` |
| **Security update / reboot pending** | `sudo apt update && sudo apt upgrade`, and a reboot at a quiet hour |
| **server-health update** | read the version's notes in `CHANGELOG.md` and run the command in the message (the [upgrade](installation.md#upgrade)) |
| **Dokku or Docker without access** | does the monitor run as root? `sudo cat /etc/cron.d/server-health` |
