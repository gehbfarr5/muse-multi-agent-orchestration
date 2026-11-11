"""Store tests: idempotency, concurrency, crash recovery, transitions.

Crash recovery uses a real subprocess that is SIGKILLed mid-transaction;
the store must reopen cleanly with atomicity (row absent) and
integrity_check OK.
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.router import route
from src.store import DuplicateTask, IllegalTransition, TaskStore

CRASH_CHILD = r"""
import sqlite3, time
db = sys.argv[1]
conn = sqlite3.connect(db, timeout=30.0, isolation_level=None)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("BEGIN IMMEDIATE")
conn.execute("INSERT INTO tasks (task_id, capability, executor, executor_version_pinned,"
             " risk_level, approval_requirement, status, idempotency_key, contract_json,"
             " created_at, updated_at) VALUES ('t-crash-1','jp_copywriting','agy','1.5.5',"
             "'P1','auto_if_validated','queued','agy:jp_copywriting:crash','{}','2026-11-11','2026-11-11')")
time.sleep(30)  # parent kills us mid-transaction
"""


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r2b-store-")
        self.db = os.path.join(self.tmp, "tasks.db")
        self.store = TaskStore(self.db)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _contract(self, seq=1):
        return route("jp_copywriting", "虚构目标", "虚构输入。", task_seq=seq)

    def test_create_and_get(self):
        c = self._contract()
        self.store.create_task(c)
        row = self.store.get(c["task_id"])
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["idempotency_key"], c["idempotency_key"])
        self.assertEqual(len(self.store.history(c["task_id"])), 1)

    def test_duplicate_key_raises_with_original_id(self):
        c = self._contract()
        self.store.create_task(c)
        c2 = dict(c)
        c2["task_id"] = "t-20261111-9999"
        with self.assertRaises(DuplicateTask) as ctx:
            self.store.create_task(c2)
        self.assertEqual(ctx.exception.task_id, c["task_id"])

    def test_illegal_transition_rejected(self):
        c = self._contract()
        self.store.create_task(c)
        with self.assertRaises(IllegalTransition):
            self.store.transition(c["task_id"], "adopted")  # queued -> adopted illegal
        self.assertEqual(self.store.get(c["task_id"])["status"], "queued")

    def test_claim_is_atomic(self):
        c = self._contract()
        self.store.create_task(c)
        self.assertTrue(self.store.claim(c["task_id"]))
        self.assertFalse(self.store.claim(c["task_id"]))  # second claim fails

    def test_concurrent_claims_only_one_wins(self):
        c = self._contract()
        self.store.create_task(c)
        results = []

        def worker():
            s = TaskStore(self.db)
            results.append(s.claim(c["task_id"]))
            s.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(results), 1)

    def test_concurrent_duplicate_inserts_only_one_wins(self):
        c = self._contract()
        results = {"ok": 0, "dup": 0}
        lock = threading.Lock()

        def worker(i):
            cc = dict(c)
            cc["task_id"] = f"t-20261111-{1000+i}"
            s = TaskStore(self.db)
            try:
                s.create_task(cc)
                with lock:
                    results["ok"] += 1
            except DuplicateTask:
                with lock:
                    results["dup"] += 1
            finally:
                s.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results["ok"], 1)
        self.assertEqual(results["dup"], 7)

    def test_crash_mid_transaction_is_atomic(self):
        # pre-create schema so the child only does the crashing insert
        c = self._contract()
        self.store.create_task(c)
        self.store.close()
        code = CRASH_CHILD.replace("sys.argv", "sys.argv")
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys\n" + code, self.db],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # wait until the insert is visible as a WAL write, then SIGKILL
        import time
        for _ in range(50):
            time.sleep(0.1)
            wal = self.db + "-wal"
            if os.path.exists(wal) and os.path.getsize(wal) > 0:
                break
        child.kill()
        child.wait()
        store2 = TaskStore(self.db)
        self.assertEqual(store2.integrity_check(), "ok")
        # the killed transaction must NOT be visible (atomicity)
        self.assertIsNone(store2.get("t-crash-1"))
        # the earlier committed row survives
        self.assertIsNotNone(store2.get(c["task_id"]))
        store2.close()

    def test_backup_and_integrity(self):
        c = self._contract()
        self.store.create_task(c)
        dest = os.path.join(self.tmp, "backup.db")
        self.store.backup(dest)
        self.assertTrue(os.path.isfile(dest))
        conn = sqlite3.connect(dest)
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        conn.close()

    def test_breaker_trips_at_three(self):
        self.assertFalse(self.store.record_failure("agy"))
        self.assertFalse(self.store.record_failure("agy"))
        self.assertTrue(self.store.record_failure("agy"))
        self.assertEqual(self.store.breaker("agy")["tripped"], 1)
        self.store.record_success("agy")
        self.assertEqual(self.store.breaker("agy")["consecutive_failures"], 0)


if __name__ == "__main__":
    unittest.main()
