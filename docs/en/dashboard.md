# Dashboard

The dashboard is one self-contained HTML page (inline SVG, no script, no external file)
with the last 7 days, hourly: memory and waiting for memory, load, disk, swap-out, each
database's size, each app's requests (people, bots, 5xx) and health-check time, what is
open now and the recent alerts. It follows the system's light or dark theme.

It is built from what the monitor already keeps (`history.jsonl`, `alerts.jsonl`,
`state.json`): no process runs to serve it, no port opens and no login has to be kept.

## Three ways to see it

**Attached to the weekly report.** The weekly report arrives on Discord with the
page as a file (`server-health-<date>.html`): open it in the browser. Turn it off with
`SERVER_HEALTH_WEEKLY_DASHBOARD=false`.

**As a file, whenever you want:**

```bash
sudo server-health --dashboard /tmp/dashboard.html
scp deploy@<server>:/tmp/dashboard.html .   # from your computer, then open it
```

**Served on the server, through an SSH tunnel:**

```bash
# on the server
sudo server-health --serve            # 127.0.0.1:8730; Ctrl+C stops
# on your computer
ssh -L 8730:127.0.0.1:8730 deploy@<server>
# open http://localhost:8730 (every reload rebuilds the page)
```

It only listens on 127.0.0.1: nothing is exposed to the internet.

## The data

The history keeps one sample per round for 35 days; the page shows the last 7, averaged
per hour (requests are summed per hour). A chart with no data yet says so: the samples
start with the first cron round. The page carries server data (app names, sizes, alert
titles), the same as the alerts on Discord: share it like you would share those.
