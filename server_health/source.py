"""Where every reading comes from: the real host, or a folder of fixtures (tests).

Modules never call `open`, `subprocess` or the clock directly: they go through the
source, so the whole monitor runs against `tests/fixtures/` without a server."""

from __future__ import annotations

import glob as globlib
import json
import os
import socket
import ssl
import subprocess
import tempfile
import time
from pathlib import Path


class SystemSource:
    """Reads the real host. Every external call goes through here, with a timeout."""

    def read(self, path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def read_from(self, path: str, offset: int, limit: int) -> tuple[bytes, int, int] | None:
        """Reads from an offset. Returns (data, inode, size)."""
        try:
            st = os.stat(path)
            with open(path, "rb") as fh:
                start = offset if 0 <= offset <= st.st_size else 0
                fh.seek(start)
                return fh.read(limit), st.st_ino, st.st_size
        except OSError:
            return None

    def stat(self, path: str) -> os.stat_result | None:
        try:
            return os.stat(path)
        except OSError:
            return None

    def glob(self, pattern: str) -> list[tuple[str, int, float]]:
        """(path, size, mtime) of each file matching the pattern, newest first."""
        out = []
        for p in globlib.glob(pattern):
            try:
                st = os.stat(p)
            except OSError:
                continue
            if os.path.isfile(p):
                out.append((p, st.st_size, st.st_mtime))
        return sorted(out, key=lambda x: -x[2])

    def run(self, key: str, argv: list[str], timeout: int = 30, stdin: str | None = None,
            env: dict | None = None) -> tuple[int, str, str]:
        # Without input, the child gets /dev/null: when the monitor runs from a pipe
        # (`curl | bash`), an inherited stdin would be the rest of someone's script.
        # Secrets go in `env`, never in argv: argv shows up in `ps` for every user.
        try:
            p = subprocess.run(argv, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=timeout,
                               env={**os.environ, **env} if env else None)
            return p.returncode, p.stdout, p.stderr
        except FileNotFoundError:
            return 127, "", f"{argv[0]} not found"
        except subprocess.TimeoutExpired:
            return 124, "", f"{argv[0]} took longer than {timeout}s"

    def which(self, name: str) -> bool:
        from shutil import which
        return which(name) is not None

    def fetch(self, url: str, timeout: int = 10) -> str | None:
        """A small GET (the update check). None on any failure."""
        import urllib.request
        from . import __version__
        req = urllib.request.Request(url, headers={"User-Agent": f"server-health/{__version__}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(256 * 1024).decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return None

    def disk(self, path: str) -> tuple[int, int, int]:
        st = os.statvfs(path)
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        avail = st.f_bavail * st.f_frsize
        return used + avail, used, avail

    def inodes(self, path: str) -> tuple[int, int]:
        """(total, free) inodes. Some filesystems report 0 total: no inode limit."""
        st = os.statvfs(path)
        return st.f_files, st.f_ffree

    def cert_expiry(self, host: str, port: int, server_name: str, timeout: float = 10) -> float | None:
        """Expiry (epoch) of the certificate served for `server_name`, read without
        validating it: an expired or self-signed certificate still has a date."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                with ctx.wrap_socket(sock, server_hostname=server_name) as tls:
                    der = tls.getpeercert(binary_form=True)
        except (OSError, ssl.SSLError):
            return None
        if not der:
            return None
        # The stdlib only decodes a certificate it validated, or one in a file.
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as fh:
            fh.write(ssl.DER_cert_to_PEM_cert(der))
            path = fh.name
        try:
            info = ssl._ssl._test_decode_cert(path)  # noqa: SLF001
            return ssl.cert_time_to_seconds(info["notAfter"])
        except (AttributeError, KeyError, ValueError, OSError):
            return None
        finally:
            os.unlink(path)

    def cpu_count(self) -> int:
        return os.cpu_count() or 1

    def now(self) -> float:
        return time.time()


class FixtureSource(SystemSource):
    """Reads everything from a folder. fs/ mirrors the filesystem; cmd/<key>.txt is
    each command's output (with optional cmd/<key>.rc and cmd/<key>.err); host.json
    has the disk, inodes, CPUs, certificates and the clock."""

    def __init__(self, root: str):
        self.root = Path(root)
        host = self.root / "host.json"
        self.host = json.loads(host.read_text()) if host.exists() else {}
        self.calls: list[str] = []

    def _fs(self, path: str) -> Path:
        return self.root / "fs" / path.lstrip("/")

    def read(self, path):
        return super().read(str(self._fs(path)))

    def read_from(self, path, offset, limit):
        return super().read_from(str(self._fs(path)), offset, limit)

    def stat(self, path):
        return super().stat(str(self._fs(path)))

    def glob(self, pattern):
        prefix = str(self.root / "fs")
        return [(p[len(prefix):], size, mtime) for p, size, mtime in super().glob(str(self._fs(pattern)))]

    def run(self, key, argv, timeout=30, stdin=None, env=None):
        self.calls.append(key)
        self.envs = getattr(self, "envs", {})
        self.envs[key] = env
        out = self.root / "cmd" / f"{key}.txt"
        if not out.exists():
            return 127, "", f"missing fixture: cmd/{key}.txt"
        rc_file = self.root / "cmd" / f"{key}.rc"
        rc = int(rc_file.read_text().strip()) if rc_file.exists() else 0
        err_file = self.root / "cmd" / f"{key}.err"
        err = err_file.read_text() if err_file.exists() else ""
        return rc, out.read_text(), err

    def which(self, name):
        return name in self.host.get("commands", ["docker", "dokku", "journalctl", "systemctl", "ss",
                                                  "timedatectl", "fail2ban-client", "apt-get"])

    def disk(self, path):
        total, used, avail = self.host.get("disk", [40 * 1024 ** 3, 10 * 1024 ** 3, 30 * 1024 ** 3])
        return total, used, avail

    def inodes(self, path):
        total, free = self.host.get("inodes", [2_621_440, 2_400_000])
        return total, free

    def fetch(self, url, timeout=10):
        return self.host.get("urls", {}).get(url)

    def cert_expiry(self, host, port, server_name, timeout=10):
        value = self.host.get("certs", {}).get(server_name)
        return float(value) if value is not None else None

    def cpu_count(self):
        return int(self.host.get("cpu_count", 2))

    def now(self):
        return float(self.host.get("now", time.time()))
