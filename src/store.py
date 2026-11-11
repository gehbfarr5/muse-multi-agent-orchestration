"""Task Store: SQLite-backed task state (R2A ADR S2).

Implements: UNIQUE idempotency key, transactional create+claim,
validated state transitions with history in the same transaction,
WAL + synchronous FULL, user_version=1, circuit breaker table.
Receipt bodies live in files, never in the DB.
"""
import json
import os
import sqlite3
import time

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  task_id            TEXT PRIMARY KEY,
  capability         TEXT NOT NULL,
  executor           TEXT NOT NULL,
  executor_version_pinned TEXT NOT NULL,
  risk_level         TEXT NOT NULL,
  approval_requirement TEXT NOT NULL,
  status             TEXT NOT NULL,
  idempotency_key    TEXT NOT NULL UNIQUE,
  contract_json      TEXT NOT NULL,
  result_json        TEXT,
  receipt_reference  TEXT,
  started_at         TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_executor ON tasks(executor);
CREATE TABLE IF NOT EXISTS task_history (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id     TEXT NOT NULL REFERENCES tasks(task_id),
  from_status TEXT,
  to_status   TEXT NOT NULL,
  reason      TEXT,
  at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_task ON task_history(task_id);
CREATE TABLE IF NOT EXISTS circuit_breakers (
  executor       TEXT PRIMARY KEY,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  rate_limited_count_1h INTEGER NOT NULL DEFAULT 0,
  tripped        INTEGER NOT NULL DEFAULT 0,
  tripped_at     TEXT,
  last_failure_at TEXT
);
"""

# Allowed transitions (ADR section 4). Terminal states have no outgoing edges.
ALLOWED = {
    "queued": {"running", "duplicate_of"},
    "running": {"succeeded", "failed", "unknown"},
    "succeeded": {"adopted", "fallback_to_muse"},
    "failed": {"retry_queued", "failed_final", "fallback_to_muse"},
    # C5 deviation (recorded in README): ADR diagram only shows
    # succeeded -> fallback_to_muse, but FAILURE-RECOVERY requires MUSE
    # takeover after failure to end in fallback_to_muse ("MUSE 接管完成").
    # failed_final is reserved for "no takeover, terminal failure".
    "retry_queued": {"running"},
    "unknown": {"investigating"},
    "investigating": {"failed_final", "retry_queued", "needs_human", "succeeded"},
    "adopted": set(),
    "fallback_to_muse": set(),
    "failed_final": set(),
    "needs_human": set(),
    "duplicate_of": set(),
}
TERMINAL = {"adopted", "fallback_to_muse", "failed_final", "needs_human", "duplicate_of"}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())


class DuplicateTask(Exception):
    """Idempotency key already exists; carries the original task_id."""

    def __init__(self, task_id):
        super().__init__(f"duplicate idempotency key, original task {task_id}")
        self.task_id = task_id


class IllegalTransition(Exception):
    pass


class TaskStore:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30.0, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.conn.executescript(SCHEMA)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def close(self):
        self.conn.close()

    def create_task(self, contract):
        """Insert a task in status queued. On duplicate key, raise DuplicateTask."""
        now = _now()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute(
                "INSERT INTO tasks (task_id, capability, executor,"
                " executor_version_pinned, risk_level, approval_requirement,"
                " status, idempotency_key, contract_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (contract["task_id"], contract["capability"], contract["executor"],
                 contract["executor_version_pinned"], contract["risk_level"],
                 contract["approval_requirement"], "queued",
                 contract["idempotency_key"], json.dumps(contract, ensure_ascii=False),
                 now, now),
            )
            self.conn.execute(
                "INSERT INTO task_history (task_id, from_status, to_status, reason, at)"
                " VALUES (?,?,?,?,?)",
                (contract["task_id"], None, "queued", "created", now),
            )
            self.conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            self.conn.execute("ROLLBACK")
            row = self.conn.execute(
                "SELECT task_id FROM tasks WHERE idempotency_key=?",
                (contract["idempotency_key"],)).fetchone()
            raise DuplicateTask(row[0] if row else "?")
        return contract["task_id"]

    def claim(self, task_id):
        """Atomically queued -> running. Returns True if claimed."""
        now = _now()
        self.conn.execute("BEGIN IMMEDIATE")
        cur = self.conn.execute(
            "UPDATE tasks SET status='running', started_at=?, updated_at=?"
            " WHERE task_id=? AND status='queued'", (now, now, task_id))
        if cur.rowcount == 1:
            self.conn.execute(
                "INSERT INTO task_history (task_id, from_status, to_status, reason, at)"
                " VALUES (?,?,?,?,?)", (task_id, "queued", "running", "claimed", now))
            self.conn.execute("COMMIT")
            return True
        self.conn.execute("ROLLBACK")
        return False

    def transition(self, task_id, to_status, reason=""):
        """Validated transition + history row in one transaction."""
        now = _now()
        self.conn.execute("BEGIN IMMEDIATE")
        row = self.conn.execute(
            "SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            self.conn.execute("ROLLBACK")
            raise KeyError(task_id)
        from_status = row[0]
        if to_status not in ALLOWED.get(from_status, set()):
            self.conn.execute("ROLLBACK")
            raise IllegalTransition(f"{from_status} -> {to_status} not allowed")
        self.conn.execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
            (to_status, now, task_id))
        self.conn.execute(
            "INSERT INTO task_history (task_id, from_status, to_status, reason, at)"
            " VALUES (?,?,?,?,?)", (task_id, from_status, to_status, reason, now))
        self.conn.execute("COMMIT")
        return to_status

    def set_result(self, task_id, result_json, receipt_reference):
        self.conn.execute(
            "UPDATE tasks SET result_json=?, receipt_reference=?, updated_at=?"
            " WHERE task_id=?",
            (json.dumps(result_json, ensure_ascii=False), receipt_reference, _now(), task_id))
        self.conn.commit()

    def get(self, task_id):
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self.conn.execute(
            "SELECT * FROM tasks LIMIT 0").description]
        return dict(zip(cols, row))

    def get_by_key(self, idempotency_key):
        row = self.conn.execute(
            "SELECT task_id, status, result_json FROM tasks WHERE idempotency_key=?",
            (idempotency_key,)).fetchone()
        return row

    def history(self, task_id):
        return self.conn.execute(
            "SELECT from_status, to_status, reason, at FROM task_history"
            " WHERE task_id=? ORDER BY id", (task_id,)).fetchall()

    def integrity_check(self):
        return self.conn.execute("PRAGMA integrity_check").fetchone()[0]

    # ---- circuit breaker ----
    def record_failure(self, executor):
        """Increment consecutive failures; trip at >=3. Returns tripped bool."""
        now = _now()
        self.conn.execute("BEGIN IMMEDIATE")
        self.conn.execute(
            "INSERT INTO circuit_breakers (executor, consecutive_failures, last_failure_at)"
            " VALUES (?,?,?) ON CONFLICT(executor) DO UPDATE SET"
            " consecutive_failures=consecutive_failures+1, last_failure_at=excluded.last_failure_at",
            (executor, 1, now))
        row = self.conn.execute(
            "SELECT consecutive_failures FROM circuit_breakers WHERE executor=?",
            (executor,)).fetchone()
        tripped = row[0] >= 3
        if tripped:
            self.conn.execute(
                "UPDATE circuit_breakers SET tripped=1, tripped_at=? WHERE executor=?",
                (now, executor))
        self.conn.execute("COMMIT")
        return tripped

    def record_success(self, executor):
        self.conn.execute(
            "INSERT INTO circuit_breakers (executor, consecutive_failures)"
            " VALUES (?,0) ON CONFLICT(executor) DO UPDATE SET consecutive_failures=0",
            (executor,))
        self.conn.commit()

    def breaker(self, executor):
        row = self.conn.execute(
            "SELECT consecutive_failures, tripped, tripped_at FROM circuit_breakers"
            " WHERE executor=?", (executor,)).fetchone()
        if row is None:
            return {"consecutive_failures": 0, "tripped": 0, "tripped_at": None}
        return {"consecutive_failures": row[0], "tripped": row[1], "tripped_at": row[2]}

    def reset_breaker_manual(self, executor, reason):
        """Manual probe-based reset only; never called automatically."""
        self.conn.execute(
            "UPDATE circuit_breakers SET tripped=0, consecutive_failures=0,"
            " tripped_at=NULL WHERE executor=?", (executor,))
        self.conn.commit()
        return True

    def trip(self, executor, reason):
        """Force-trip the breaker (auth failure / quota exhausted)."""
        now = _now()
        self.conn.execute(
            "INSERT INTO circuit_breakers (executor, consecutive_failures, tripped, tripped_at, last_failure_at)"
            " VALUES (?,?,1,?,?) ON CONFLICT(executor) DO UPDATE SET"
            " tripped=1, tripped_at=excluded.tripped_at,"
            " last_failure_at=excluded.last_failure_at",
            (executor, 1, now, now))
        self.conn.commit()
        return True

    def backup(self, dest_path):
        """Atomic backup via VACUUM INTO."""
        self.conn.execute("VACUUM INTO ?", (dest_path,))
        try:
            os.chmod(dest_path, 0o600)
        except OSError:
            pass
        return dest_path
