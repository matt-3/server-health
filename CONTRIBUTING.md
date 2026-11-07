# Contributing

English | [Português](CONTRIBUTING.pt-BR.md)

Thank you for helping. Bug reports, fixes, better messages, new modules, new notifiers
and translations are all welcome.

## Before you start

- **A bug:** open an issue with the server (distribution, Python version), the command,
  what you expected and what happened. `sudo server-health --dry-run` and the last lines
  of `/var/log/server-health.log` help a lot. Remove webhooks, IPs and anything private.
- **Something big** (a new module, a change in how alerts work, a new dependency): open
  an issue first, so we agree on the shape before you write it.
- **Small fixes** (a typo, a message, a wrong threshold in the docs) can go straight to
  a pull request.

## The rules that keep it what it is

- **Standard library only.** No pip dependency: the monitor is installed on servers by a
  shell script, and must run on a fresh Ubuntu.
- **Light.** It runs every 5 minutes on small machines. No daemon, no long command in
  every round (if something is slow or changes slowly, cache it in the state, like
  `tls` and `updates` do), at most one database session per app.
- **It only reads.** No command changes the server, the apps or the databases; SQL runs
  in read-only sessions.
- **Alerts must be worth it.** A new alert needs a reason a person would act on, a
  threshold that does not ring on a normal day, and a `detail` that says what to do.
- **No personal data leaves the server.** Counts and sums, never e-mails, names, IPs of
  people or user-written text.
- **Every text in English and Portuguese.** Use `t()` with keys in the module's
  `strings` for `en` and `pt-BR`; the tests check both catalogs have the same keys.
- **Code, comments and names in English.**

## Running the tests

```bash
python3 -m unittest discover -s tests
```

The tests run the whole monitor against `tests/fixtures/basic` (fake `/proc`, command
outputs and logs), so no server is needed. A new module or check comes with tests: copy
the fixture in a test (see `tests/helpers.py`), write the command outputs it reads in
`cmd/`, and assert on the report or on what was sent.

The installer test installs for real, so it runs in CI or in a container:

```bash
docker run --rm -v "$PWD":/src -w /src debian:stable-slim \
  sh -c 'apt-get update -qq && apt-get install -y -qq python3 curl >/dev/null && bash tests/install_test.sh'
```

CI runs both on Python 3.11, 3.12 and 3.13.

## Writing a module

See [docs/en/writing-a-module.md](docs/en/writing-a-module.md). In short: a subclass of
`Module` in `server_health/modules/`, listed in `server_health/modules/__init__.py`, with
`detect`, the phases it needs, its thresholds and its strings; plus a line in the
modules table of the READMEs and in `docs/*/modules.md`.

## Pull requests

- One subject per pull request, with tests and the docs it changes (both languages; if
  you do not write Portuguese, say so and we translate).
- A line in `CHANGELOG.md` under an "Unreleased" heading.
- Releases (version, tag, `versions.json`) are done by the maintainers:
  [docs/en/releasing.md](docs/en/releasing.md).

By contributing you agree that your contribution is licensed under the [MIT](LICENSE)
license of the project.
