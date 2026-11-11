"""Fault-injection regression tests: R1 F1-F10 + R2-A F11-F15.

All executors are fakes injected into run_task.execute_task; no real CLI
is called. Every test writes receipts to a temp dir and asserts the
classification, the retry behaviour, and the final task state.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import router, run_task
from src.store import TaskStore

PINNED = {"agy": "1.5.5", "codex": "0.164.0-alpha.9", "grok": "1.0.77"}


def version_fn(executor):
    return PINNED[executor], True


def fake_raw(**kw):
    d = {"rc": 0, "stdout": "", "stderr": "", "elapsed_ms": 10,
         "timed_out": False, "killed_group": False, "spawn_error": None,
         "residual_pids": [], "response_text": "", "denied_actions": []}
    d.update(kw)
    return d


class FaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r2b-test-")
        self.store = TaskStore(os.path.join(self.tmp, "tasks.db"))
        self.work = os.path.join(self.tmp, "work")
        self.receipts = os.path.join(self.tmp, "receipts")
        os.makedirs(self.work)
        os.makedirs(self.receipts)
        self.seq = 0
        self.calls = 0

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def contract(self, kind="text", executor="agy", capability="jp_copywriting"):
        self.seq += 1
        c = router.route(capability, "虚构测试目标", "虚构输入：今天天气很好。",
                         kind=kind, task_seq=self.seq)
        if executor != "agy":
            c["executor"] = executor
            c["executor_version_pinned"] = PINNED[executor]
            c["idempotency_key"] = router.idempotency_key(
                executor, capability, "虚构测试目标", "虚构输入：今天天气很好。", kind)
        return c

    def run_case(self, contract, executor_fn):
        return run_task.execute_task(
            self.store, contract, "虚构提示词", self.work, self.receipts,
            executor_fn, version_fn, sleep_fn=lambda s: None)

    def count_executor_calls(self):
        return self.calls

    # ---- F1-F10 ----
    def test_f1_spawn_failed(self):
        def ex(c, p, w):
            self.calls += 1
            return fake_raw(spawn_error="No such file", rc=None)
        status, res = self.run_case(self.contract(), ex)
        self.assertEqual(status, "fallback_to_muse")
        self.assertEqual(res["error_category"], "SPAWN_FAILED")
        self.assertEqual(self.calls, 1)  # never retried

    def test_f2_timeout(self):
        def ex(c, p, w):
            self.calls += 1
            return fake_raw(timed_out=True, killed_group=True, rc=None)
        status, res = self.run_case(self.contract(), ex)
        self.assertEqual(res["error_category"], "TIMEOUT")
        self.assertEqual(status, "fallback_to_muse")
        self.assertEqual(self.calls, 2)  # 1 retry within budget, then fallback

    def test_f3_nonzero_exit(self):
        def ex(c, p, w):
            self.calls += 1
            return fake_raw(rc=3, stderr="AGY_ERROR something failed")
        status, res = self.run_case(self.contract(), ex)
        self.assertEqual(res["error_category"], "NONZERO_EXIT")
        self.assertEqual(status, "fallback_to_muse")
        self.assertEqual(self.calls, 1)

    def test_f4_empty_output(self):
        def ex(c, p, w):
            self.calls += 1
            return fake_raw(rc=0, stdout="", response_text="")
        status, res = self.run_case(self.contract(), ex)
        self.assertEqual(res["error_category"], "EMPTY_OUTPUT")
        self.assertEqual(status, "fallback_to_muse")
        self.assertEqual(self.calls, 2)

    def test_f5_bad_json(self):
        def ex(c, p, w):
            self.calls += 1
            return fake_raw(rc=0, stdout='{"score_0_to_10": ', response_text='{"score_0_to_10": ')
        status, res = self.run_case(self.contract(kind="json"), ex)
        self.assertEqual(res["error_category"], "INVALID_OUTPUT")
        self.assertEqual(self.calls, 2)

    def test_f6_transient_429_retries_then_succeeds(self):
        state = {"n": 0}

        def ex(c, p, w):
            state["n"] += 1
            if state["n"] == 1:
                return fake_raw(rc=1, stderr="429 rate_limited; Retry-After: 5")
            return fake_raw(rc=0, stdout="今日は天気がいいです。",
                            response_text="今日は天気がいいです。")
        status, res = self.run_case(self.contract(), ex)
        self.assertEqual(status, "adopted")
        self.assertTrue(res["success"])
        self.assertEqual(state["n"], 2)

    def test_f6b_quota_exhausted_never_retries_and_trips_breaker(self):
        def ex(c, p, w):
            self.calls += 1
            return fake_raw(rc=1, stderr="insufficient_quota: billing limit reached")
        status, res = self.run_case(self.contract(), ex)
        self.assertEqual(res["error_category"], "RATE_LIMITED")
        self.assertEqual(status, "failed_final")
        self.assertEqual(self.calls, 1)  # zero retries
        self.assertEqual(self.store.breaker("agy")["tripped"], 1)

    def test_f7_truncated_json(self):
        def ex(c, p, w):
            self.calls += 1
            return fake_raw(rc=0, stdout='{"score_0_to_10": 7, "note": "trun',
                            response_text='{"score_0_to_10": 7, "note": "trun')
        status, res = self.run_case(self.contract(kind="json"), ex)
        self.assertEqual(res["error_category"], "INVALID_OUTPUT")

    def test_f8_validation_failed_score_out_of_range(self):
        def ex(c, p, w):
            self.calls += 1
            return fake_raw(rc=0, stdout='{"score_0_to_10": 99}',
                            response_text='{"score_0_to_10": 99}')
        status, res = self.run_case(self.contract(kind="json"), ex)
        self.assertEqual(res["error_category"], "VALIDATION_FAILED")
        self.assertEqual(status, "fallback_to_muse")

    def test_f9_unknown_state_never_blind_retried(self):
        def ex(c, p, w):
            self.calls += 1
            return fake_raw(rc=-9, stdout="", response_text="")
        status, res = self.run_case(self.contract(), ex)
        self.assertEqual(res["error_category"], "UNKNOWN_EXECUTION_STATE")
        self.assertEqual(status, "needs_human")
        self.assertEqual(self.calls, 1)  # exactly one attempt, no blind retry

    def test_f10_duplicate_returns_first_receipt_without_rerun(self):
        def ex(c, p, w):
            self.calls += 1
            return fake_raw(rc=0, stdout="今日は天気がいいです。",
                            response_text="今日は天気がいいです。")
        c1 = self.contract()
        status1, _ = self.run_case(c1, ex)
        self.assertEqual(status1, "adopted")
        c2 = dict(c1)
        c2["task_id"] = "t-20261111-9999"  # new id, SAME idempotency key
        status2, res2 = self.run_case(c2, ex)
        self.assertEqual(status2, "duplicate_of")
        self.assertEqual(res2["error_category"], "DUPLICATE_REQUEST")
        self.assertEqual(self.calls, 1)  # executor never re-invoked

    # ---- F11-F15 (R2-A additions) ----
    def test_f11_agy_fake_success_intercepted(self):
        def ex(c, p, w):
            self.calls += 1
            env = {"status": "SUCCESS", "response": "",
                   "denied_actions": [{"action": "command"}]}
            return fake_raw(rc=0, stdout=json.dumps(env),
                            response_text="", envelope=env,
                            denied_actions=[{"action": "command"}])
        status, res = self.run_case(self.contract(), ex)
        self.assertFalse(res["success"])
        self.assertEqual(res["agent_claimed_status"], "SUCCESS")
        self.assertEqual(res["error_category"], "PERMISSION_DENIED")
        self.assertEqual(status, "fallback_to_muse")

    def test_f12_grok_thought_never_in_receipt(self):
        marker = "THOUGHT_SECRET_MARKER_12345"

        def ex(c, p, w):
            self.calls += 1
            env = {"text": "虚构话题总结。", "stopReason": "end_turn",
                   "thought": marker, "total_cost_usd": 0.007,
                   "sessionId": "s1", "requestId": "r1", "cwd": "/tmp"}
            raw = fake_raw(rc=0, stdout=json.dumps(env))
            from src import adapter_grok
            raw["parsed"] = adapter_grok.parse_output(raw["stdout"], "")
            return raw
        c = self.contract(executor="grok", capability="x_trend_research")
        status, res = self.run_case(c, ex)
        self.assertEqual(status, "adopted")
        receipt_files = os.listdir(self.receipts)
        self.assertTrue(receipt_files)
        blob = ""
        for f in receipt_files:
            blob += open(os.path.join(self.receipts, f)).read()
        self.assertNotIn(marker, blob)
        self.assertNotIn('"thought":', blob)

    def test_f13_external_send_blocked_before_executor(self):
        c = self.contract()
        c["capability"] = "publish_x"
        c["executor"] = "grok"
        c["executor_version_pinned"] = PINNED["grok"]

        def ex(cc, p, w):
            self.calls += 1
            return fake_raw(rc=0, stdout="ok", response_text="ok")
        status, res = self.run_case(c, ex)
        self.assertEqual(status, "failed_final")
        self.assertEqual(self.calls, 0)

    def test_f14_version_drift_refuses_execution(self):
        def bad_version(executor):
            return "9.9.9", False

        def ex(c, p, w):
            self.calls += 1
            return fake_raw(rc=0, stdout="x", response_text="x")
        status, res = run_task.execute_task(
            self.store, self.contract(), "虚构提示词", self.work,
            self.receipts, ex, bad_version, sleep_fn=lambda s: None)
        self.assertEqual(status, "needs_human")
        self.assertEqual(res["error_category"], "VERSION_DRIFT")
        self.assertEqual(self.calls, 0)

    def test_f15_breaker_trips_and_refuses_new_work(self):
        def ex(c, p, w):
            self.calls += 1
            return fake_raw(rc=0, stdout="", response_text="")
        # three DISTINCT failing tasks (different input -> different key)
        for i in range(3):
            c = router.route("jp_copywriting", "虚构测试目标",
                             f"虚构输入序号 {i}：今天天气很好。", task_seq=100 + i)
            self.run_case(c, ex)
        self.assertEqual(self.store.breaker("agy")["tripped"], 1)
        before = self.calls
        status, res = self.run_case(self.contract(), ex)
        self.assertEqual(status, "fallback_to_muse")
        self.assertEqual(res["gate_decision"], "breaker_tripped")
        self.assertEqual(self.calls, before)  # executor never called


if __name__ == "__main__":
    unittest.main()
