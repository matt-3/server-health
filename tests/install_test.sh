#!/usr/bin/env bash
#
# Tests install.sh for real: it installs into /usr/local, so it runs as root on a
# disposable machine (CI, or a container):
#
#   sudo bash tests/install_test.sh
#   docker run --rm -v "$PWD":/src -w /src debian:stable-slim \
#     sh -c 'apt-get update -qq && apt-get install -y -qq python3 curl >/dev/null && bash tests/install_test.sh'
#
# The "tags" are .tar.gz files in a local folder (file://), shaped like GitHub's.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(cd "$ROOT" && python3 -B -c 'import server_health; print(server_health.__version__)')"
TAG="v${VERSION}"
BIN=/usr/local/bin/server-health
LIB=/usr/local/lib/server-health

[[ $EUID -eq 0 ]] || { echo "run as root (sudo): install.sh installs into /usr/local" >&2; exit 2; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/archive" "$work/apps.d" "$work/bin" "$work/build"

# archive <tag> <version the code reports>
archive() {
  local tag="$1" reports="$2" dir="$work/build/server-health-${1#v}"
  rm -rf "$dir"
  mkdir -p "$dir"
  cp -R "$ROOT/server_health" "$dir/"
  find "$dir" -name '__pycache__' -prune -exec rm -rf {} +
  sed -i.bak "s/^__version__ = .*/__version__ = \"$reports\"/" "$dir/server_health/__init__.py"
  rm -f "$dir/server_health/__init__.py.bak"
  tar -czf "$work/archive/$tag.tar.gz" -C "$work/build" "server-health-${1#v}"
}
archive "$TAG" "$VERSION"
archive v0.0.1 "$VERSION"       # a tag whose code reports another version
archive v9.0.1 9.0.1
archive v9.0.2 9.0.2

printf 'app = "x"\n[[indicador]]\nchave = "old"\nsql = "SELECT 1"\n' > "$work/invalid.toml"
# A dokku that reads stdin to the end: with `curl | bash`, it would eat the rest of the script.
printf '#!/bin/sh\ncat >/dev/null\nexit 1\n' > "$work/bin/dokku"
chmod 755 "$work/bin/dokku"

export SERVER_HEALTH_ARCHIVE_URL="file://$work/archive"
export SERVER_HEALTH_APPS_DIR="$work/apps.d"
export SERVER_HEALTH_CONFIG="$work/server-health.conf"
export PATH="$work/bin:$PATH"

failures=0
check() {  # check <description> <expected rc> <command...>
  local desc="$1" want="$2"
  shift 2
  "$@" >"$work/out" 2>&1
  local rc=$?
  if [[ $rc -eq $want ]]; then
    echo "ok    $desc"
  else
    echo "FAIL  $desc (rc $rc, expected $want)"
    sed 's/^/      /' "$work/out" | tail -8
    failures=$((failures + 1))
  fi
}
expect() {  # expect <description> <condition...>
  local desc="$1"
  shift
  if "$@"; then echo "ok    $desc"; else echo "FAIL  $desc"; failures=$((failures + 1)); fi
}
reports() { [[ -x "$BIN" && "$("$BIN" --version </dev/null)" == "server-health $1" ]]; }
out_has() { grep -q "$1" "$work/out"; }

rm -rf "$LIB" "$BIN"
echo "SERVER_HEALTH_DISCORD_WEBHOOK=https://discord.test/api/webhooks/1/x" > "$SERVER_HEALTH_CONFIG"

check "installs with a TOML" 0 \
  bash "$ROOT/install.sh" "$TAG" "$ROOT/examples/my-app.toml"
expect "the command reports the version" reports "$VERSION"
expect "the TOML is installed" test -f "$work/apps.d/my-app.toml"
expect "current points to the version" test "$(readlink "$LIB/current")" = "$VERSION"
expect "the package is compiled once, at install" test -d "$LIB/$VERSION/server_health/__pycache__"

rm -rf "$LIB" "$BIN"
check "installs through a pipe, with a command that reads stdin" 0 \
  bash -c "cat '$ROOT/install.sh' | bash -s -- '$TAG'"
expect "the pipe installed the command" reports "$VERSION"

check "refuses an invalid TOML" 1 bash "$ROOT/install.sh" "$TAG" "$work/invalid.toml"
expect "it names the problem" out_has "unknown key"
expect "the refused TOML was not installed" test ! -e "$work/apps.d/invalid.toml"

check "refuses a download that reports another version" 1 bash "$ROOT/install.sh" v0.0.1
check "refuses a tag that does not exist" 1 bash "$ROOT/install.sh" v8.8.8
check "refuses a version out of the format" 1 bash "$ROOT/install.sh" 1.0
check "refuses a TOML that does not exist" 1 bash "$ROOT/install.sh" "$TAG" "$work/missing.toml"
check "asks for the version" 1 bash "$ROOT/install.sh"
expect "the command still reports the installed version after the refusals" reports "$VERSION"

check "upgrades to a newer version" 0 bash "$ROOT/install.sh" v9.0.1
expect "current points to the newer version" test "$(readlink "$LIB/current")" = "9.0.1"
expect "the previous version stays for a rollback" test -d "$LIB/$VERSION"
sleep 1
check "upgrades again" 0 bash "$ROOT/install.sh" v9.0.2
expect "only the active and the previous versions stay" test ! -d "$LIB/$VERSION" -a -d "$LIB/9.0.1" -a -d "$LIB/9.0.2"
expect "the package belongs to root and nobody else can write it" \
  test "$(stat -c '%U %a' "$LIB/9.0.2/server_health/cli.py")" = "root 644"

echo
if [[ $failures -gt 0 ]]; then
  echo "$failures failure(s)"
  exit 1
fi
echo "all good"
