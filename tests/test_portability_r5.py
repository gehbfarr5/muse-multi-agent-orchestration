"""R5-A portability regressions: version detection must not depend on
which CLIs happen to be installed, and the residual-process scan must
not mistake a zombie (defunct) process for leftover work.

No real CLI model calls here. The "binary present" checks skip when the
binary is absent, so the suite stays portable across machines.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import adapter_agy, adapter_base, adapter_codex, adapter_grok
from src import orchestrator, router, run_task
from src.store import TaskStore

MISSING = "/nonexistent/muse-orchestration-test-binary"
VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?")


class TestVersionNotInstalled(unittest.TestCase):
    def test_version_missing_binary_reports_not_installed(self):
        # Each adapter reports a missing binary explicitly; it must not
        # shell out to a nonexistent path or return an empty string.
        for mod, attr in ((adapter_agy, "AGY_BIN"),
                          (adapter_grok, "GROK_BIN"),
                          (adapter_codex, "CODEX_BIN")):
            real = getattr(mod, attr)
            setattr(mod, attr, MISSING)
            try:
                out, rc = mod.version()
            finally:
                setattr(mod, attr, real)
            self.assertEqual(out, "NOT_INSTALLED")
            self.assertIsNone(rc)

    @unittest.skipUnless(adapter_base.check_binary_exists(
        os.path.expanduser("~/.local/bin/agy")), "agy not installed")
    def test_version_present_binary_parses(self):
        out, rc = adapter_agy.version()
        self.assertEqual(rc, 0)
        self.assertIsNotNone(VERSION_RE.search(out))

    @unittest.skipUnless(adapter_base.check_binary_exists(
        os.path.expanduser("~/.grok/bin/grok")), "grok not installed")
    def test_version_grok_present_binary_parses(self):
        out, rc = adapter_grok.version()
        self.assertEqual(rc, 0)
        self.assertIsNotNone(VERSION_RE.search(out))

    @unittest.skipUnless(adapter_base.check_binary_exists(
        os.path.expanduser("~/workspace/tools/codex-bin/codex")),
        "codex not installed")
    def test_version_codex_present_binary_parses(self):
        out, rc = adapter_codex.version()
        self.assertEqual(rc, 0)
        self.assertIsNotNone(VERSION_RE.search(out))


class TestNotInstalledFallsBack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r5-port-")
        self.store = TaskStore(os.path.join(self.tmp, "tasks.db"))
        self.work = os.path.join(self.tmp, "work")
        self.receipts = os.path.join(self.tmp, "receipts")
        os.makedirs(self.work)
        os.makedirs(self.receipts)

    def tearDown(self):
        self.store.close()

    def test_missing_binary_falls_back_to_muse(self):
        # A missing CLI is unavailability, not version drift: MUSE takes
        # over and the executor is never invoked.
        calls = []

        def executor_fn(contract, prompt, cwd):
            calls.append(contract["task_id"])
            raise AssertionError("executor must not be called")

        def version_fn(executor):
            return "NOT_INSTALLED", False

        c = router.route("jp_copywriting", "虚构测试目标",
                         "虚构输入：今天天气很好。", task_seq=1)
        status, result = run_task.execute_task(
            self.store, c, "虚构测试目标", self.work, self.receipts,
            executor_fn, version_fn)
        self.assertEqual(status, "fallback_to_muse")
        self.assertEqual(result["error_category"], "SPAWN_FAILED")
        self.assertEqual(calls, [])
        self.assertEqual(self.store.get(c["task_id"])["status"],
                         "fallback_to_muse")

    def test_health_reports_not_installed_without_raising(self):
        base = self.tmp
        os.makedirs(os.path.join(base, "work"), exist_ok=True)
        os.makedirs(os.path.join(base, "state", "receipts"), exist_ok=True)
        store = TaskStore(os.path.join(base, "state", "tasks.db"))
        store.close()
        real = dict(orchestrator.EXECUTORS)

        def _fake_ok(contract, prompt, cwd):
            return {"rc": 0, "stdout": "", "stderr": "", "elapsed_ms": 0,
                    "response_text": "", "argv": [], "denied_actions": []}

        def _not_installed(executor=None):
            return "NOT_INSTALLED", False

        for name in orchestrator.EXECUTORS:
            orchestrator.EXECUTORS[name] = (_fake_ok, _not_installed)
        try:
            h = orchestrator.health(base)
        finally:
            orchestrator.EXECUTORS.update(real)
        self.assertFalse(h["ok"])
        self.assertEqual(h["checks"]["agy_version"]["version"], "NOT_INSTALLED")
        self.assertFalse(h["checks"]["agy_version"]["ok"])
        # The gate self-test and DB integrity still run and pass.
        self.assertTrue(h["checks"]["gate_self_test"]["ok"])
        self.assertTrue(h["checks"]["db_integrity"]["ok"])


class TestZombieResidual(unittest.TestCase):
    def test_zombie_is_not_residual_but_live_process_is(self):
        # Fork a child that exits at once while the parent sleeps without
        # reaping: the child becomes a zombie in the same process group.
        # pgrep still lists it; the residual scan must not.
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        pgid = os.getpgid(0)
        try:
            deadline = time.time() + 5.0
            zombie_seen = False
            while time.time() < deadline:
                raw = subprocess.run(["pgrep", "-g", str(pgid)],
                                     capture_output=True, text=True)
                pids = raw.stdout.split()
                if str(pid) in pids:
                    zombie_seen = True
                    break
                time.sleep(0.1)
            self.assertTrue(zombie_seen, "zombie child was never listed")
            self.assertTrue(adapter_base._is_zombie(str(pid)))
            residual = adapter_base.residual_pids_in_group(pgid, "python")
            self.assertNotIn(str(pid), residual)
            # A live process in the same group is still reported.
            live = subprocess.Popen(["sleep", "30"], start_new_session=False)
            try:
                self.assertIn(str(live.pid),
                              adapter_base.residual_pids_in_group(
                                  os.getpgid(live.pid), "sleep"))
            finally:
                live.terminate()
                live.wait(timeout=5)
        finally:
            _, status = os.waitpid(pid, 0)
            self.assertTrue(os.WIFEXITED(status))

    def test_is_zombie_unknown_pid_is_conservative(self):
        # An unreadable / vanished pid is treated as live (counted),
        # never silently dropped.
        self.assertFalse(adapter_base._is_zombie("99999999"))


if __name__ == "__main__":
    unittest.main()
