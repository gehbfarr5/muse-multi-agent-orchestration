"""R3 orchestrator tests: unified entry, classifier, simulated image
lifecycle, rollback, health, receipts (deterministic + fake executors;
live CLI calls are exercised separately in the acceptance run).
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import classifier, image_approval, orchestrator
from src.store import TaskStore


def _fake_ok(contract, prompt, cwd):
    return {"rc": 0, "stdout": "はい、今日はいい天気です。",
            "stderr": "", "elapsed_ms": 10,
            "response_text": "はい、今日はいい天気です。",
            "argv": ["fake-agy"], "denied_actions": []}


def _fake_version(executor):
    return {"agy": "1.5.5", "grok": "1.0.77",
            "codex": "0.164.0-alpha.9"}[executor], True


class OrchestratorTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = self.tmp
        os.makedirs(os.path.join(self.base, "work"), exist_ok=True)
        os.makedirs(os.path.join(self.base, "state", "receipts"), exist_ok=True)
        # R7-E: delegation tests run with external calls explicitly
        # enabled; the default-off behavior is covered by
        # test_external_calls_default_off_r7e.py.
        os.makedirs(os.path.join(self.base, "config"), exist_ok=True)
        with open(os.path.join(self.base, "config", "config.json"), "w") as f:
            json.dump({"external_calls_enabled": True}, f)
        self.store = TaskStore(os.path.join(self.base, "state", "tasks.db"))

    def tearDown(self):
        self.store.close()


class TestVersionWrapper(unittest.TestCase):
    def test_wrapped_version_accepts_executor_arg(self):
        # run_task calls version_fn(executor); the wrapper must accept it.
        # R5-A: the wrapper is tested against a fake version function so
        # the suite does not depend on which CLIs are installed locally.
        fake = orchestrator._normalized_version(
            lambda: ("agy 1.5.5 (build xyz)", 0))
        ver, ok = fake("agy")
        self.assertEqual(ver, "1.5.5")
        self.assertTrue(ok)
        fake_codex = orchestrator._normalized_version(
            lambda: ("codex 0.164.0-alpha.9", 0))
        ver, ok = fake_codex("codex")
        self.assertEqual(ver, "0.164.0-alpha.9")
        self.assertTrue(ok)

    def test_wrapped_version_missing_binary(self):
        # A missing binary is reported as NOT_INSTALLED, not as an
        # empty string that would look like a version drift.
        fake = orchestrator._normalized_version(
            lambda: ("NOT_INSTALLED", None))
        ver, ok = fake("agy")
        self.assertEqual(ver, "NOT_INSTALLED")
        self.assertFalse(ok)


class TestClassifier(unittest.TestCase):
    def test_jp_routes_agy(self):
        c = classifier.classify("把这句中文改写为日语：今天天气很好。")
        self.assertEqual(c["capability"], "jp_copywriting")
        self.assertEqual(c["decision"], "delegate")

    def test_cn_routes_agy(self):
        c = classifier.classify("请写一段中文文案介绍公园音乐会。")
        self.assertEqual(c["capability"], "cn_copywriting")

    def test_review_routes_agy(self):
        c = classifier.classify("评审这段文案是否存在夸大表述。")
        self.assertEqual(c["capability"], "content_review")

    def test_trend_routes_grok(self):
        c = classifier.classify("研究一下虚构话题 Zorbania 风筝节的趋势。")
        self.assertEqual(c["capability"], "x_trend_research")

    def test_topic_discovery_routes_grok(self):
        c = classifier.classify("帮我做话题发现：找几个虚构城市节日话题。")
        self.assertEqual(c["capability"], "x_topic_discovery")

    def test_image_routes_codex(self):
        c = classifier.classify("给这段虚构文案生成图片配图。")
        self.assertEqual(c["capability"], "image_generate")

    def test_unknown_is_direct(self):
        c = classifier.classify("帮我算一下 12 * 34 等于多少。")
        self.assertEqual(c["decision"], "muse_direct")
        self.assertIsNone(c["capability"])


class TestSubmitFake(OrchestratorTestBase):
    def test_delegate_adopted(self):
        # Patch the real executors with fakes for a deterministic run.
        real = dict(orchestrator.EXECUTORS)
        orchestrator.EXECUTORS["agy"] = (_fake_ok, _fake_version)
        try:
            out = orchestrator.submit(
                "把这句中文改写为日语：今天天气很好。",
                input_text="今天天气很好。", store=self.store, base=self.base,
                task_seq=1)
        finally:
            orchestrator.EXECUTORS.update(real)
        self.assertEqual(out["status"], "adopted")
        self.assertEqual(out["handled_by"], "agy")
        self.assertTrue(out["receipt_reference"].endswith(".json"))
        row = self.store.conn.execute(
            "SELECT status FROM tasks WHERE task_id = ?",
            (out["task_id"],)).fetchone()
        self.assertEqual(row[0], "adopted")

    def test_gate_blocks_pii_zero_calls(self):
        calls = []

        def counting(contract, prompt, cwd):
            calls.append(contract["task_id"])
            return _fake_ok(contract, prompt, cwd)

        real = dict(orchestrator.EXECUTORS)
        orchestrator.EXECUTORS["agy"] = (counting, _fake_version)
        try:
            out = orchestrator.submit(
                "把这句中文改写为日语：请联系 13800000000 确认",
                input_text="虚构样本：无。", store=self.store, base=self.base,
                task_seq=2)
        finally:
            orchestrator.EXECUTORS.update(real)
        self.assertEqual(out["status"], "failed_final")
        self.assertEqual(calls, [])

    def test_blocked_resubmission_is_duplicate_not_crash(self):
        # R4 fix: a repeated SECURITY_BLOCKED submission used to raise
        # DuplicateTask out of run_task. It must return duplicate_of.
        real = dict(orchestrator.EXECUTORS)
        orchestrator.EXECUTORS["agy"] = (_fake_ok, _fake_version)
        try:
            first = orchestrator.submit(
                "把这句中文改写为日语：请联系 13800000000 确认",
                input_text="虚构样本：无。", store=self.store, base=self.base,
                task_seq=6)
            second = orchestrator.submit(
                "把这句中文改写为日语：请联系 13800000000 确认",
                input_text="虚构样本：无。", store=self.store, base=self.base,
                task_seq=7)
        finally:
            orchestrator.EXECUTORS.update(real)
        self.assertEqual(first["status"], "failed_final")
        self.assertEqual(second["status"], "duplicate_of")

    def test_duplicate_not_rerun(self):
        real = dict(orchestrator.EXECUTORS)
        orchestrator.EXECUTORS["agy"] = (_fake_ok, _fake_version)
        try:
            first = orchestrator.submit(
                "把这句中文改写为日语：今天天气很好。",
                input_text="今天天气很好。", store=self.store, base=self.base,
                task_seq=3)
            second = orchestrator.submit(
                "把这句中文改写为日语：今天天气很好。",
                input_text="今天天气很好。", store=self.store, base=self.base,
                task_seq=4)
        finally:
            orchestrator.EXECUTORS.update(real)
        self.assertEqual(first["status"], "adopted")
        self.assertEqual(second["status"], "duplicate_of")

    def test_direct_not_recorded(self):
        out = orchestrator.submit("帮我算一下 12 * 34 等于多少。",
                                  store=self.store, base=self.base)
        self.assertEqual(out["handled_by"], "muse_direct")
        rows = self.store.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        self.assertEqual(rows, 0)


class TestImageLifecycle(OrchestratorTestBase):
    def test_simulated_png_and_approval(self):
        work = os.path.join(self.base, "work", "t-img")
        os.makedirs(work, exist_ok=True)
        path = os.path.join(work, "t-img.png")
        image_approval.generate_simulated_png(path)
        check = image_approval.validate_png_header(path)
        self.assertTrue(check["ok"])
        self.assertEqual(check["size"], (64, 64))
        image_approval.record_draft(self.store.conn, "t-img", path)
        rec = image_approval.get(self.store.conn, "t-img")
        self.assertEqual(rec["state"], "draft_generated")
        self.assertFalse(image_approval.is_usable(rec["state"]))
        # Approve-before-shown is illegal.
        with self.assertRaises(ValueError):
            image_approval.transition(self.store.conn, "t-img", "approved_for_use")
        image_approval.transition(self.store.conn, "t-img", "shown_to_user")
        image_approval.transition(self.store.conn, "t-img", "approved_for_use")
        rec = image_approval.get(self.store.conn, "t-img")
        self.assertTrue(image_approval.is_usable(rec["state"]))

    def test_codex_simulated_run(self):
        from src import adapter_codex
        # Work dirs must live under ~/workspace, never /tmp (R2-D rule).
        base = os.path.expanduser("~/workspace/muse-orchestration-r3/work")
        work = os.path.join(base, "test-t-codex")
        os.makedirs(work, exist_ok=True)
        contract = {"task_id": "t-codex", "executor": "codex",
                    "expected_output": {"kind": "image"},
                    "timeout": {"hard_seconds": 130},
                    "sandbox": "workspace-write"}
        raw = adapter_codex.run(contract, "虚构配图提示", work)
        self.assertEqual(raw["rc"], 0)
        self.assertTrue(os.path.isfile(raw["artifact_path"]))
        self.assertTrue(raw["artifact_path"].startswith(work))
        self.assertEqual(raw["image_state"], "draft_generated")
        # Artifact outside the work dir is refused.
        with self.assertRaises(ValueError):
            adapter_codex.guard_artifact_path("/tmp/evil.png", work)


class TestRollbackAndHealth(OrchestratorTestBase):
    def test_rollback_refuses_then_enable_restores(self):
        orchestrator.set_disabled(self.base, "test rollback")
        out = orchestrator.submit("把这句中文改写为日语：今天天气很好。",
                                  store=self.store, base=self.base)
        self.assertEqual(out["handled_by"], "framework_disabled")
        self.assertTrue(orchestrator.clear_disabled(self.base))
        self.assertFalse(orchestrator.is_disabled(self.base))

    def test_health_gate_self_test(self):
        # R5-A: health is exercised with stubbed executors so the suite
        # does not depend on which CLIs are installed on this machine.
        # The gate self-test and DB integrity are what this test owns;
        # real version detection is covered by test_portability_r5.py.
        real = dict(orchestrator.EXECUTORS)
        # health() calls version_fn() with no argument, so each stub
        # must accept an optional executor (like the real wrapper).
        for name in orchestrator.EXECUTORS:
            orchestrator.EXECUTORS[name] = (
                _fake_ok, lambda _e=None, _n=name: _fake_version(_n))
        try:
            h = orchestrator.health(self.base)
        finally:
            orchestrator.EXECUTORS.update(real)
        self.assertTrue(h["checks"]["gate_self_test"]["ok"])
        self.assertTrue(h["checks"]["db_integrity"]["ok"])
        self.assertEqual(h["checks"]["agy_version"]["version"], "1.5.5")
        self.assertTrue(h["checks"]["agy_version"]["ok"])

    def test_receipt_query(self):
        real = dict(orchestrator.EXECUTORS)
        orchestrator.EXECUTORS["agy"] = (_fake_ok, _fake_version)
        try:
            out = orchestrator.submit(
                "把这句中文改写为日语：今天天气很好。",
                input_text="今天天气很好。", store=self.store, base=self.base,
                task_seq=5)
        finally:
            orchestrator.EXECUTORS.update(real)
        q = orchestrator.query_receipt(out["task_id"], self.base)
        self.assertTrue(q["ok"])
        self.assertEqual(q["receipt"]["task_id"], out["task_id"])
        self.assertNotIn("thought", json.dumps(q["receipt"]))


if __name__ == "__main__":
    unittest.main()
