# Apps and their indicators

Each app can have a TOML file that says where its things are (when it is not a Dokku
app) and what only it knows matters: the flow people feel when it stops, a record stuck
in an intermediate state, an external call failing, and the numbers of the day for the
summary. Keep it in the app's repository (the SQL changes with its migrations); on the
server it lives in `/etc/server-health/apps.d/<app>.toml`. A commented example:
[`examples/my-app.toml`](../../examples/my-app.toml).

## An app outside Dokku

A Dokku app is found by itself. Any other app (Docker Compose, Kamal, a systemd service)
is monitored when its TOML declares at least one of these:

```toml
app = "shop"                                # its name in the messages and the metric keys
health_url = "https://shop.example.com/up"  # the http module checks it (and tls its certificate)
nginx_log = "/var/log/nginx/shop.access.log" # the nginx module reads it (combined format)
database_url = "env:SHOP_DATABASE_URL"      # the postgres, rails and indicators modules use it
# database_container = "shop-postgres-1"    # or: psql inside this container, with `docker exec`
```

`database_url` takes a `postgres://user@host:5432/db` URL. **Never put the password in
the TOML**: the file is readable by every user of the machine, and `--check-app` refuses
it. Put the whole URL in the config file (root only) and point to it:

```bash
# /etc/default/server-health
SHOP_DATABASE_URL=postgres://monitor:secret@127.0.0.1:5432/shop?sslmode=disable
```

A read-only user is enough (and better): the monitor only reads. With
`database_container`, psql runs inside the container as `postgres` (or as the user and
database of `database_url`, without a password), the way the official image allows on
its local socket.

## The TOML

```toml
app = "my-app"              # the app name in Dokku, or any name for an app outside it (required)
name = "My App"             # optional: messages show "My App (my-app)"
database = "my-app-db"      # Dokku Postgres service; default: the one linked to the app
health_path = "/up"         # optional: the http module's path for this app
jobs_dashboard = "/admin/jobs"  # optional: quoted in failed-job alerts
storage_mount = true        # optional: alert if the app has no Dokku storage mount
max_body_size = "50m"       # optional: alert if nginx or Thruster accept less
health_url = "https://..."  # outside Dokku: see above (also nginx_log, database_url, database_container)

[[indicator]]
key = "orders_stuck"        # lowercase letters, digits and _
label = "Orders stuck in processing"
sql = "SELECT count(*) FROM orders WHERE status = 'processing' AND updated_at < now() - interval '15 minutes'"
warn_at = 1                 # value >= 1 is a warning
critical_at = 5             # value >= 5 is critical
direction = "above"         # or "below": alerts when the value falls to the limit
when = "always"             # "always": every round, can alert; "summary": daily summary only, never alerts
persist_min = 0             # the alert only counts after this many minutes in a row
active_hours = "09-23"      # optional: outside these hours (SERVER_HEALTH_TZ) it never alerts
format = "number"           # number, bytes, duration (seconds) or cents
explanation = "what to do when it alerts (goes into the message)"
```

## Rules

- **The query returns one number**, starts with `SELECT` or `WITH` and has no `;` in
  the middle.
- **It runs in a read-only session** (`default_transaction_read_only`), with a 5 s
  timeout and 1 s lock wait. A query that fails becomes a warning for that indicator
  (after 10 min), and the rest goes on.
- **Counts and sums only.** No e-mail, name, user-written text, IP or per-user value:
  the result goes to Discord.
- **The time rule lives in the SQL** (`created_at < now() - interval '10 minutes'`).
  `persist_min` is for what oscillates (a job that runs at night), not a replacement
  for the window.
- **Rails `datetime` columns store UTC without a zone.** Comparing with a local hour
  needs `(created_at AT TIME ZONE 'UTC') AT TIME ZONE 'America/Sao_Paulo'`.
- **Summary indicators add up in the weekly report** (`number` and `cents` are summed;
  `bytes` and `duration` show the last value). A label that says "(24h)" loses it there.
- An error in the TOML (unknown key, `sql` that writes, a summary indicator with a
  threshold) drops only that indicator and shows as "Monitor config" in the report.

## Checking a TOML

On the server, before installing a change:

```bash
sudo server-health --check-app /tmp/my-app.toml
```

It validates the file and runs each query once, showing the value, the level and the
time, or Postgres's error. Without Dokku (your computer, CI) it only validates the file:
exit code 0 when valid, 1 or 3 when not.

To check it in the app's CI with the same version the servers run:

```yaml
# GitHub Actions
- uses: actions/setup-python@v5
  with: { python-version: "3.12" }
- run: |
    curl -fsSL https://github.com/matt-3/server-health/archive/refs/tags/v1.0.0.tar.gz | tar -xz -C /tmp
    SERVER_HEALTH_CONFIG=/dev/null PYTHONPATH=/tmp/server-health-1.0.0 \
      python3 -m server_health --check-app scripts/monitor/my-app.toml
```

```yaml
# GitLab CI
monitor_toml:
  image: python:3.12-slim
  variables:
    SERVER_HEALTH_VERSION: "1.0.0"
  script:
    - python3 -c "import urllib.request, tarfile, io; tarfile.open(fileobj=io.BytesIO(urllib.request.urlopen('https://github.com/matt-3/server-health/archive/refs/tags/v${SERVER_HEALTH_VERSION}.tar.gz').read())).extractall('/tmp')"
    - SERVER_HEALTH_CONFIG=/dev/null PYTHONPATH=/tmp/server-health-${SERVER_HEALTH_VERSION} python3 -m server_health --check-app scripts/monitor/my-app.toml
```
