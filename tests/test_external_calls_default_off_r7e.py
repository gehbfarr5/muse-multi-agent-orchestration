"""R7-E: external calls are default-off at the formal entry.

The `run` entry previously never read `config/config.json`, so a
version-matched agy was dispatched with no config at all. These tests
drive `orchestrator.submit()` with counting doubles (no real CLI) and
assert, for agy / Grok / real Codex, that default, missing, and
invalid configs produce ZERO executor calls, that local handling and
the simulated-image path still work, and that an explicit enable still
passes through the security gate and the pinned version check.

Boundary note: the guarantee covers task dispatch (executor run
invocations). `health()` version probes (`--version`) are local binary
queries, not agent executions, and are not gated by this flag.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import adapter_codex, config as entry_config, orchestrator
from src.store import TaskStore

PINNED = {"agy": "1.5.5", "grok": "1.0.77", "codex": "0.164.0-alpha.9"}


def _fake_run_ok(contract, prompt, cwd):
    if contract["executor"] == "grok":
        return {"rc": 0, "stdout": "{}", "stderr": "", "elapsed_ms": 5,
                "response_text": "", "parsed": {"text": "虚构趋势摘要。",
                                                "stop_reason": "end_turn"},
                "argv": ["fake-grok"], "denied_actions": []}
    return {"rc": 0, "stdout": "はい、今日はいい天気です。",
            "stderr": "", "elapsed_ms": 5,
            "response_text": "はい、今日はいい天気です。",
            "argv": ["fake-" + contract["executor"]], "denied_actions": []}


def _fake_version_pinned(executor):
    return PINNED[executor], True


class _Counting:
    """Wraps a fake executor run and records every invocation."""

    def __init__(self):
        self.calls = []

    def run(self, contract, prompt, cwd):
        self.calls.append(contract["task_id"])
        return _fake_run_ok(contract, prompt, cwd)


class ExternalCallsDefaultOffTestBase(unittest.TestCase):
    JP = "把这句中文改写为日语：今天天气很好。"
    GROK_NATURAL = "帮我看看 X 上最近大家在讨论什么。"  # R7-B1 natural wording
    IMAGE = "给这段虚构文案生成图片配图。"
    DIRECT = "帮我算一下 12 * 34 等于多少。"
    PII = "把这句中文改写为日语：请联系 13800000000 确认"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = self.tmp
        os.makedirs(os.path.join(self.base, "work"), exist_ok=True)
        os.makedirs(os.path.join(self.base, "state", "receipts"), exist_ok=True)
        self.store = TaskStore(os.path.join(self.base, "state", "tasks.db"))
        self.counters = {name: _Counting() for name in ("agy", "grok", "codex")}
        self.real_exec = dict(orchestrator.EXECUTORS)
        for name, counter in self.counters.items():
            orchestrator.EXECUTORS[name] = (counter.run, _fake_version_pinned)
        self._real_image_before = adapter_codex.is_real_image_enabled()

    def tearDown(self):
        orchestrator.EXECUTORS.update(self.real_exec)
        adapter_codex.configure_real_image(self._real_image_before)
        self.store.close()

    def _write_config_raw(self, raw):
        os.makedirs(os.path.join(self.base, "config"), exist_ok=True)
        with open(os.path.join(self.base, "config", "config.json"), "w") as f:
            f.write(raw)

    def _write_config(self, obj):
        self._write_config_raw(json.dumps(obj, ensure_ascii=False))

    def _total_calls(self):
        return sum(len(c.calls) for c in self.counters.values())

    def _task_rows(self):
        return self.store.conn.execute(
            "SELECT COUNT(*) FROM tasks").fetchone()[0]

    def _submit(self, text, **kw):
        return orchestrator.submit(text, store=self.store, base=self.base,
                                   **kw)


class TestDefaultOffNoConfig(ExternalCallsDefaultOffTestBase):
    def test_agy_zero_calls_no_config_file(self):
        out = self._submit(self.JP, task_seq=1)
        self.assertEqual(out["handled_by"], "external_calls_disabled")
        self.assertEqual(out["status"], "refused")
        self.assertEqual(self._total_calls(), 0)
        self.assertEqual(self._task_rows(), 0)

    def test_grok_zero_calls_no_config_file(self):
        out = self._submit(self.GROK_NATURAL, task_seq=2)
        self.assertEqual(out["handled_by"], "external_calls_disabled")
        self.assertEqual(self._total_calls(), 0)
        self.assertEqual(self._task_rows(), 0)

    def test_codex_real_image_zero_calls_no_config_file(self):
        adapter_codex.configure_real_image(True)
        out = self._submit(self.IMAGE, task_seq=3)
        self.assertEqual(out["handled_by"], "external_calls_disabled")
        self.assertEqual(self._total_calls(), 0)
        self.assertEqual(self._task_rows(), 0)


class TestDefaultOffInvalidConfig(ExternalCallsDefaultOffTestBase):
    def test_invalid_configs_all_zero_calls(self):
        # Missing key, false, string "true", number 1, malformed JSON,
        # and a non-object top level: none of these enable anything.
        cases = [
            ("empty object", "{}"),
            ("explicit false", '{"external_calls_enabled": false}'),
            ("string true", '{"external_calls_enabled": "true"}'),
            ("number one", '{"external_calls_enabled": 1}'),
            ("malformed json", '{"external_calls_enabled": true'),
            ("top-level list", '["external_calls_enabled"]'),
        ]
        for label, raw in cases:
            with self.subTest(label=label):
                self._write_config_raw(raw)
                for counter in self.counters.values():
                    counter.calls.clear()
                out = self._submit(self.JP, task_seq=10)
                self.assertEqual(out["handled_by"],
                                 "external_calls_disabled", label)
                self.assertEqual(self._total_calls(), 0, label)


class TestPreservedLocalPaths(ExternalCallsDefaultOffTestBase):
    def test_direct_handling_needs_no_config(self):
        out = self._submit(self.DIRECT)
        self.assertEqual(out["handled_by"], "muse_direct")
        self.assertEqual(self._total_calls(), 0)

    def test_simulated_image_needs_no_config(self):
        # Real-image flag off (default): the simulated path makes no
        # external call, so the enable check must not block it.
        self.assertFalse(adapter_codex.is_real_image_enabled())
        out = self._submit(self.IMAGE, task_seq=4)
        self.assertEqual(out["handled_by"], "codex")
        self.assertEqual(out["status"], "adopted")
        self.assertEqual(len(self.counters["codex"].calls), 1)


class TestExplicitEnable(ExternalCallsDefaultOffTestBase):
    def test_enable_dispatches_agy(self):
        self._write_config({"external_calls_enabled": True})
        out = self._submit(self.JP, task_seq=5)
        self.assertEqual(out["handled_by"], "agy")
        self.assertEqual(out["status"], "adopted")
        self.assertEqual(len(self.counters["agy"].calls), 1)

    def test_enable_dispatches_grok_and_real_codex(self):
        self._write_config({"external_calls_enabled": True})
        out = self._submit(self.GROK_NATURAL, task_seq=6)
        self.assertEqual(out["handled_by"], "grok")
        self.assertEqual(out["status"], "adopted")
        adapter_codex.configure_real_image(True)
        out = self._submit(self.IMAGE, task_seq=7)
        self.assertEqual(out["handled_by"], "codex")
        self.assertEqual(out["status"], "adopted")
        self.assertEqual(len(self.counters["grok"].calls), 1)
        self.assertEqual(len(self.counters["codex"].calls), 1)

    def test_enable_still_passes_security_gate(self):
        self._write_config({"external_calls_enabled": True})
        out = self._submit(self.PII, task_seq=8)
        self.assertEqual(out["status"], "failed_final")
        self.assertEqual(self._total_calls(), 0)

    def test_enable_still_enforces_version_check(self):
        self._write_config({"external_calls_enabled": True})
        real = dict(orchestrator.EXECUTORS)
        orchestrator.EXECUTORS["agy"] = (
            self.counters["agy"].run, lambda e: ("9.9.9", True))
        try:
            out = self._submit(self.JP, task_seq=9)
        finally:
            orchestrator.EXECUTORS.update(real)
        self.assertEqual(out["status"], "needs_human")
        self.assertEqual(out.get("error_category"), "VERSION_DRIFT")
        self.assertEqual(self._total_calls(), 0)

    def test_rollback_flag_still_refuses_everything(self):
        # The pre-existing global disable (state/DISABLED) is untouched
        # and still refuses even with external calls enabled.
        self._write_config({"external_calls_enabled": True})
        orchestrator.set_disabled(self.base, "r7e test rollback")
        try:
            out = self._submit(self.JP, task_seq=11)
        finally:
            orchestrator.clear_disabled(self.base)
        self.assertEqual(out["handled_by"], "framework_disabled")
        self.assertEqual(self._total_calls(), 0)


class TestConfigLoader(unittest.TestCase):
    def test_load_missing_file_is_disabled_with_reason(self):
        base = tempfile.mkdtemp()
        cfg, err = entry_config.load_config(base)
        self.assertEqual(cfg, {})
        self.assertIn("missing", err)
        self.assertFalse(entry_config.external_calls_enabled(cfg))

    def test_load_invalid_json_and_non_object(self):
        base = tempfile.mkdtemp()
        os.makedirs(os.path.join(base, "config"), exist_ok=True)
        path = os.path.join(base, "config", "config.json")
        for raw in ('{"external_calls_enabled": true', '[1, 2]', '"x"'):
            with open(path, "w") as f:
                f.write(raw)
            cfg, err = entry_config.load_config(base)
            self.assertEqual(cfg, {})
            self.assertIsNotNone(err)
            self.assertFalse(entry_config.external_calls_enabled(cfg))

    def test_only_literal_true_enables(self):
        for value, expected in [
                (True, True), (False, False), ("true", False),
                (1, False), (None, False)]:
            self.assertEqual(
                entry_config.external_calls_enabled(
                    {"external_calls_enabled": value}),
                expected, repr(value))
        self.assertFalse(entry_config.external_calls_enabled({}))
        self.assertFalse(entry_config.external_calls_enabled(None))

    def test_requires_external_call_matrix(self):
        agy = {"executor": "agy", "expected_output": {"kind": "text"}}
        grok = {"executor": "grok", "expected_output": {"kind": "text"}}
        img = {"executor": "codex", "expected_output": {"kind": "image"}}
        txt = {"executor": "codex", "expected_output": {"kind": "text"}}
        self.assertTrue(entry_config.requires_external_call(agy))
        self.assertTrue(entry_config.requires_external_call(grok))
        self.assertFalse(entry_config.requires_external_call(img, False))
        self.assertTrue(entry_config.requires_external_call(img, True))
        self.assertTrue(entry_config.requires_external_call(txt, False))
        self.assertTrue(entry_config.requires_external_call(
            {"executor": "unknown"}, False))


if __name__ == "__main__":
    unittest.main()
