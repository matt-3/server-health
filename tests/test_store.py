"""The files in the state directory."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import T0
from server_health.store import Store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="server-health-store-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.store = Store(str(self.dir))

    def write(self, name: str, text: str):
        (self.dir / name).write_text(text)

    def test_missing_or_broken_files_load_empty(self):
        self.assertEqual(self.store.load_state(), {})
        self.assertEqual(self.store.load_history(), [])
        self.assertEqual(self.store.load_alerts(), [])
        self.assertEqual(self.store.load_bots(), {})
        self.write("state.json", "{broken")
        self.assertEqual(self.store.load_state(), {})

    def test_a_broken_line_is_skipped_not_fatal(self):
        self.write("history.jsonl", json.dumps({"ts": 1}) + "\nnot json\n" + json.dumps({"ts": 2}) + "\n")
        self.assertEqual([r["ts"] for r in self.store.load_history()], [1, 2])

    def test_pruning_keeps_the_recent_rows(self):
        now = T0
        self.store.append_history({"ts": now - 40 * 86400})
        self.store.append_history({"ts": now - 10 * 86400})
        self.store.append_alerts([{"ts": now - 100 * 86400, "kind": "new"}, {"ts": now - 5 * 86400, "kind": "new"}])
        self.store.prune_history(now)
        self.store.prune_alerts(now)
        self.assertEqual([r["ts"] for r in self.store.load_history()], [now - 10 * 86400])
        self.assertEqual(self.store.load_alerts(), [{"ts": now - 5 * 86400, "kind": "new"}])

    def test_state_round_trip(self):
        state = {"metrics": {"host.memory": {"alerted": "WARN"}}, "ports": {"*:22": T0}}
        self.store.save_state(state)
        self.assertEqual(self.store.load_state(), state)


if __name__ == "__main__":
    unittest.main()
