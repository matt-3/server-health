"""State, history, alerts and bot detail on disk (/var/lib/server-health)."""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

HISTORY_KEEP_DAYS = 35
ALERTS_KEEP_DAYS = 90
LOG_MAX_BYTES = 5 * 1024 * 1024


class Store:
    def __init__(self, state_dir: str):
        self.dir = Path(state_dir)
        self.state_file = self.dir / "state.json"
        self.history_file = self.dir / "history.jsonl"
        self.bots_file = self.dir / "bots.json"
        self.alerts_file = self.dir / "alerts.jsonl"

    def ensure(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass

    @staticmethod
    def _write(path: Path, text: str):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(path)

    @staticmethod
    def _lines(path: Path) -> list[dict]:
        out = []
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return out

    def load_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def save_state(self, state: dict):
        self._write(self.state_file, json.dumps(state, ensure_ascii=False))

    def load_history(self) -> list[dict]:
        return self._lines(self.history_file)

    def append_history(self, row: dict):
        with open(self.history_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def load_bots(self) -> dict:
        try:
            return json.loads(self.bots_file.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def save_bots(self, data: dict):
        self._write(self.bots_file, json.dumps(data, ensure_ascii=False))

    def load_alerts(self) -> list[dict]:
        return self._lines(self.alerts_file)

    def append_alerts(self, rows: list[dict]):
        if not rows:
            return
        with open(self.alerts_file, "a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def prune_alerts(self, now: float):
        rows = [r for r in self.load_alerts() if r.get("ts", 0) >= now - ALERTS_KEEP_DAYS * 86400]
        self._write(self.alerts_file, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    def prune_history(self, now: float):
        rows = [r for r in self.load_history() if r.get("ts", 0) >= now - HISTORY_KEEP_DAYS * 86400]
        self._write(self.history_file, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def make_logger(path: str, fallback_dir: str, echo: bool):
    from datetime import datetime
    target = path
    try:
        with open(target, "a"):
            pass
    except OSError:
        target = str(Path(fallback_dir) / "server-health.log")
    # A cap of its own, so the log stays bounded without logrotate: above LOG_MAX_BYTES
    # the file becomes <log>.1 (replacing the previous one) and starts again.
    try:
        if os.path.getsize(target) > LOG_MAX_BYTES:
            os.replace(target, target + ".1")
    except OSError:
        pass

    def log(msg: str):
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        if echo:
            import sys
            print(line, file=sys.stderr)
        try:
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
    return log


def acquire_lock(state_dir: str, wait: int):
    fh = open(Path(state_dir) / "lock", "w")
    deadline = time.time() + wait
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            if time.time() >= deadline:
                fh.close()
                return None
            time.sleep(2)
