#!/usr/bin/env bash
#
# Installs or upgrades server-health on a server, from the tag of a version.
#
# Usage (on the server, as root):
#   install.sh vX.Y.Z                    install that version
#   install.sh vX.Y.Z /tmp/app.toml ...  also install (or replace) each app's TOML
#
# From the internet, without cloning anything:
#   curl -fsSL https://raw.githubusercontent.com/matt-3/server-health/vX.Y.Z/install.sh \
#     | sudo bash -s -- vX.Y.Z /tmp/my-app.toml
#
# Nothing changes if something fails: the downloaded code must report the requested
# version, and every TOML (the installed ones and the new ones) goes through the new
# version's --check-app before anything is installed. Each version lives in its own
# folder under /usr/local/lib/server-health, and `current` points to the active one:
# going back is pointing it at the previous folder. It never touches the config
# (/etc/default/server-health) or the cron: their first setup is in docs/.

set -euo pipefail

readonly ARCHIVE_URL="${SERVER_HEALTH_ARCHIVE_URL:-https://github.com/matt-3/server-health/archive/refs/tags}"
readonly LIB="${SERVER_HEALTH_LIB:-/usr/local/lib/server-health}"
readonly BIN="${SERVER_HEALTH_BIN:-/usr/local/bin/server-health}"
readonly APPS_DIR="${SERVER_HEALTH_APPS_DIR:-/etc/server-health/apps.d}"
readonly CONFIG="${SERVER_HEALTH_CONFIG:-/etc/default/server-health}"

die() { echo "ERROR: $*" >&2; exit 1; }

# Everything runs inside main, called on the last line: with `curl | bash`, bash reads
# this script from stdin bit by bit, and only starts main after reading the whole file.
# Every command here gets </dev/null for the same reason: a process that read stdin
# would eat the rest of the script, and the install would end silently.
main() {
  [[ $# -ge 1 ]] || die "usage: install.sh vX.Y.Z [file.toml ...]"
  local tag="$1"
  shift
  [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version '$tag' is not in the vX.Y.Z format"
  local version="${tag#v}"
  [[ $EUID -eq 0 ]] || die "run as root (sudo)"

  command -v curl >/dev/null || die "curl is not installed"
  command -v tar >/dev/null || die "tar is not installed"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' </dev/null 2>/dev/null \
    || die "needs python3 3.11 or newer (tomllib arrived in 3.11)"

  local toml
  for toml in "$@"; do
    [[ -f "$toml" ]] || die "$toml does not exist"
    [[ "$toml" == *.toml ]] || die "$toml does not end in .toml"
  done

  tmp=$(mktemp -d)  # global: the trap runs after main has returned
  trap 'rm -rf "$tmp"' EXIT

  echo "Downloading server-health ${tag}..."
  curl -fsSL "${ARCHIVE_URL}/${tag}.tar.gz" -o "$tmp/release.tar.gz" </dev/null \
    || die "could not download ${ARCHIVE_URL}/${tag}.tar.gz (does the tag exist?)"
  mkdir "$tmp/src"
  tar -xzf "$tmp/release.tar.gz" -C "$tmp/src" </dev/null || die "the download is not a valid .tar.gz"
  local pkg
  pkg=$(find "$tmp/src" -maxdepth 3 -type f -path '*/server_health/__init__.py' </dev/null | head -n 1)
  [[ -n "$pkg" ]] || die "the download has no server_health package"
  local root
  root=$(dirname "$(dirname "$pkg")")

  run_new() { python3 -I -B -c "import sys; sys.path.insert(0, '$root'); sys.argv[0] = 'server-health'; \
from server_health.__main__ import run; run()" "$@" </dev/null; }

  local got
  got=$(run_new --version 2>/dev/null || true)
  [[ "$got" == "server-health ${version}" ]] || die "the download reports '${got:-nothing}', not server-health ${version}"

  # A new TOML replaces the installed one with the same name: only the new one is checked.
  declare -A checks=()
  local f
  if [[ -d "$APPS_DIR" ]]; then
    for f in "$APPS_DIR"/*.toml; do
      [[ -e "$f" ]] && checks["$(basename "$f")"]="$f"
    done
  fi
  for toml in "$@"; do
    checks["$(basename "$toml")"]="$toml"
  done

  local name
  for name in "${!checks[@]}"; do
    echo
    echo "== --check-app ${name}"
    run_new --check-app "${checks[$name]}" \
      || die "${name} did not pass ${tag}'s --check-app: nothing was changed. Fix it (or pass the new file to this command) and run again"
  done

  echo
  install -d -m 755 "$LIB"
  rm -rf "${LIB:?}/${version}.new"
  install -d -m 755 "$LIB/${version}.new"
  cp -R "$root/server_health" "$LIB/${version}.new/"
  find "$LIB/${version}.new" -name '__pycache__' -prune -exec rm -rf {} + </dev/null
  # Compiled once here, so each 5-minute round loads bytecode instead of compiling.
  python3 -I -m compileall -q "$LIB/${version}.new/server_health" </dev/null >/dev/null \
    || die "could not compile the package"
  chown -R root:root "$LIB/${version}.new"
  chmod -R go-w "$LIB/${version}.new"
  rm -rf "${LIB:?}/${version}"
  mv "$LIB/${version}.new" "$LIB/${version}"
  ln -sfn "$version" "$LIB/current.new" && mv -T "$LIB/current.new" "$LIB/current"

  install -d -m 755 "$(dirname "$BIN")"
  cat > "$BIN.new" <<EOF
#!/bin/sh
# server-health: the active version is $LIB/current (see install.sh).
exec python3 -I -c 'import sys; sys.dont_write_bytecode = True; sys.path.insert(0, "$LIB/current"); sys.argv[0] = "server-health"; from server_health.__main__ import run; run()' "\$@"
EOF
  chmod 755 "$BIN.new"
  mv "$BIN.new" "$BIN"

  if [[ $# -gt 0 ]]; then
    install -d -m 755 "$APPS_DIR"
    for toml in "$@"; do
      install -o root -g root -m 644 "$toml" "$APPS_DIR/$(basename "$toml")"
      echo "Installed: $APPS_DIR/$(basename "$toml")"
    done
  fi

  # Keep the active version and the previous one, for a quick rollback.
  local keep
  keep=$(ls -1t "$LIB" </dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | head -n 2 | tr '\n' ' ')
  for f in "$LIB"/*; do
    local base
    base=$(basename "$f")
    if [[ "$base" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && " $keep " != *" $base "* ]]; then
      rm -rf "${f:?}"
    fi
  done

  echo "Installed: $BIN ($("$BIN" --version </dev/null))"
  [[ -e "$CONFIG" ]] || echo "WARNING: $CONFIG does not exist; without it no alert goes out (docs, installation)."
  [[ -e /etc/cron.d/server-health ]] || echo "WARNING: /etc/cron.d/server-health does not exist; the monitor does not run by itself (docs, installation)."
  echo "The cron picks up the new version on its next round. Check with: sudo server-health --dry-run"
}

main "$@"
