# Changelog

Versions follow [Semantic Versioning](https://semver.org/). Each one is a `vX.Y.Z` git
tag, which `install.sh` downloads, and an entry in `versions.json`, which servers read
to know a newer version exists.

## v1.0.0 - 2026-11-07

First release.

- **The server:** memory and memory pressure (PSI), swap-out, disk and its forecast,
  inodes, load, OOM kills, failed systemd units, the clock, new listening ports, SSH
  logins from new IP ranges, pending security updates and reboots, backup age and size,
  TLS certificate expiry.
- **The apps (optional):** Docker containers; Dokku apps (processes, restarts, OOM-killed
  containers, a deploy that made things worse, storage mount and upload size limits);
  any app's health URL and nginx access log (people and bots, 5xx, a bot in volume, a
  traffic drop); Postgres (size, connections, long transactions, vacuum, largest tables)
  from Dokku or from a URL or a container; Rails' Solid Queue seen from outside (heartbeat,
  failed jobs, new job errors, queues, recurring tasks); SQL indicators declared by each
  app in a TOML file. Apps outside Dokku are declared in that TOML.
- **Alerts on state changes:** one message when a problem starts, reminders while it
  lasts, "back to normal" with its duration; persistence windows; deploys do not raise
  false alarms; quiet hours; a mute for planned maintenance.
- **Reports:** a daily summary and a weekly report with a dashboard attached, at the
  times set in the config (`SERVER_HEALTH_DAILY_AT`, `SERVER_HEALTH_WEEKLY_AT`).
- **Dashboard:** a self-contained HTML page with the charts of the last 7 days, also
  written by `--dashboard` or served on 127.0.0.1 by `--serve`.
- **Channels:** Discord (one channel for the server and one per app), a generic JSON
  webhook, Sentry.
- **Modules:** everything is a module, chosen by `SERVER_HEALTH_MODULES` (`auto` by
  default); a third-party module is one Python file in `/etc/server-health/modules.d`.
- **Messages in English and Portuguese.**
- **Update check:** once a day it reads `versions.json`; security and critical fixes
  become a warning.
- **Bounded data:** 35 days of history, 90 days of alerts, a 5 MB log.
- **`install.sh`:** installs a tag, checks the version and every app's TOML before
  changing anything, compiles the package once, keeps the previous version for a
  rollback, and is safe through `curl | bash`.
