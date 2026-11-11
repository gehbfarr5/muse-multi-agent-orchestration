"""R2-D security tests (deterministic; no live CLI calls except the
separate live_security.py runner).

Covers: agy deny-all settings + fail-closed, Grok name validation +
disallowed/deny controls, Codex work-dir + artifact guards, the unified
Approval Gate security screen (SECURITY_BLOCKED), and the desensitized
audit record. All data is fictional.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import adapter_agy, adapter_codex, adapter_grok, approval, router, run_task
from src.store import TaskStore

WS_WORK = os.path.expanduser("~/workspace/muse-orchestration-r2d/work")


def base_contract(**over):
    c = router.route("jp_copywriting", "把中文改写为自然日语",
                     "虚构样本：今天天气很好，我去公园散步。", task_seq=900)
    c.update(over)
    return c


class TestAgySettings(unittest.TestCase):
    def test_deny_all_settings_written_and_verified(self):
        with tempfile.TemporaryDirectory() as d:
            p = adapter_agy.build_settings(d, "deny_all")
            with open(p) as f:
                s = json.load(f)
            self.assertEqual(s["permissions"]["deny"], adapter_agy.DENY_ALL_RULES)
            self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")

    def test_strict_settings(self):
        with tempfile.TemporaryDirectory() as d:
            p = adapter_agy.build_settings(d, "strict")
            with open(p) as f:
                self.assertEqual(json.load(f)["toolPermission"], "strict")

    def test_scoped_mode_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                adapter_agy.build_settings(d, "scoped")

    def test_unknown_mode_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                adapter_agy.build_settings(d, "wide_open")

    def test_build_command_includes_settings(self):
        with tempfile.TemporaryDirectory() as d:
            argv, sp = adapter_agy.build_command(
                {"model": "gemini-3.8-flash-medium", "output_format": "text",
                 "timeout": {"soft_seconds": 110},
                 "security_profile": {"agy_mode": "deny_all"}}, "hello", d)
            self.assertIn("--settings", argv)
            self.assertEqual(argv[argv.index("--settings") + 1], sp)
            self.assertTrue(os.path.isfile(sp))

    def test_run_fail_closed_on_bad_mode(self):
        with tempfile.TemporaryDirectory() as d:
            raw = adapter_agy.run(
                {"model": "gemini-3.8-flash-medium", "output_format": "text",
                 "timeout": {"soft_seconds": 110, "hard_seconds": 120},
                 "security_profile": {"agy_mode": "scoped"}}, "hello", d)
            self.assertTrue(raw.get("security_fail_closed"))
            self.assertIsNone(raw.get("rc"))
            self.assertIn("SECURITY_FAIL_CLOSED", raw.get("stderr", ""))


class TestGrokControls(unittest.TestCase):
    def test_disallowed_unknown_name_refused(self):
        with self.assertRaises(ValueError):
            adapter_grok.build_command(
                {"tools": ["web_search"], "disallowed_tools_extra": ["web_serch"]}, "x")

    def test_meta_tools_removed_by_default(self):
        argv = adapter_grok.build_command({"tools": ["web_search"]}, "x")
        val = argv[argv.index("--disallowed-tools") + 1]
        self.assertIn("search_tool", val)
        self.assertIn("use_tool", val)
        self.assertIn("Agent", val)

    def test_default_deny_read_when_no_read_tool(self):
        argv = adapter_grok.build_command({"tools": ["web_search"]}, "x")
        self.assertIn("Read(*)", argv)

    def test_default_deny_fetch_always(self):
        # R4-F: Fetch(*) is denied even when read_file is granted: the
        # skill-load path uses Fetch, and Fetch cancels any headless run
        # (it needs approval that cannot be given). web_fetch URL opening
        # still works under the deny (probe 2026-11-11).
        argv = adapter_grok.build_command({"tools": ["web_search"]}, "x")
        self.assertIn("Fetch(*)", argv)
        argv_read = adapter_grok.build_command({"tools": ["read_file"]}, "x")
        self.assertIn("Fetch(*)", argv_read)
        self.assertNotIn("Read(*)", argv_read)

    def test_fetch_deny_does_not_remove_web_fetch(self):
        argv = adapter_grok.build_command(
            {"tools": ["web_search", "web_fetch"]}, "x")
        tools_val = argv[argv.index("--tools") + 1]
        self.assertIn("web_fetch", tools_val)

    def test_no_read_deny_when_read_tool_granted(self):
        argv = adapter_grok.build_command({"tools": ["read_file"]}, "x")
        self.assertNotIn("Read(*)", argv)

    def test_malformed_deny_rule_refused(self):
        with self.assertRaises(ValueError):
            adapter_grok.build_command(
                {"tools": ["web_search"], "deny_rules": ["Read("]}, "x")

    def test_reported_tools_parsing(self):
        text = "Available: `web_search`, `open_page` and `read_file`."
        self.assertEqual(adapter_grok.reported_tools(text),
                         ["open_page", "read_file", "web_search"])


class TestCodexGuards(unittest.TestCase):
    def test_tmp_work_dir_refused(self):
        with self.assertRaises(ValueError):
            adapter_codex.validate_work_dir("/tmp/r2d-accept/work")
        with self.assertRaises(ValueError):
            adapter_codex.validate_work_dir("/tmp")

    def test_workspace_work_dir_accepted(self):
        self.assertTrue(adapter_codex.validate_work_dir(
            os.path.join(WS_WORK, "t-0001")).startswith(
                os.path.expanduser("~/workspace")))

    def test_home_work_dir_refused(self):
        with self.assertRaises(ValueError):
            adapter_codex.validate_work_dir(os.path.expanduser("~"))

    def test_artifact_path_guard(self):
        wd = os.path.join(WS_WORK, "t-0001")
        self.assertTrue(adapter_codex.guard_artifact_path(
            os.path.join(wd, "out.png"), wd))
        with self.assertRaises(ValueError):
            adapter_codex.guard_artifact_path(
                os.path.expanduser("~/.codex/generated_images/x.png"), wd)
        with self.assertRaises(ValueError):
            adapter_codex.guard_artifact_path("/tmp/evil.png", wd)


class TestSecurityGate(unittest.TestCase):
    def test_fictional_phone_pattern_blocked(self):
        c = base_contract()
        d, reason = approval.gate(c, prompt="请联系 13800000000 确认")
        self.assertEqual(d, "security_blocked")
        self.assertNotIn("13800000000", reason)  # desensitized

    def test_fictional_id_pattern_blocked(self):
        c = base_contract()
        d, _ = approval.gate(c, prompt="身份证 110101199001011234 请核对")
        self.assertEqual(d, "security_blocked")

    def test_credential_name_blocked(self):
        c = base_contract()
        d, _ = approval.gate(c, prompt="请读取 auth.json 里的内容")
        self.assertEqual(d, "security_blocked")

    def test_production_path_blocked(self):
        c = base_contract()
        d, _ = approval.gate(
            c, prompt="请读取 /home/example-operator/workspace/goals/x-v/crons/daily/foo.md")
        self.assertEqual(d, "security_blocked")

    def test_allowed_fixture_path_passes(self):
        c = base_contract()
        d, _ = approval.gate(
            c, prompt="请读取 /tmp/r2d-accept/work/inside.txt 的第一行")
        self.assertEqual(d, "allow")

    def test_missing_agy_security_config_blocked(self):
        c = base_contract()
        c.pop("security_profile", None)
        self.assertEqual(approval.gate(c)[0], "security_blocked")

    def test_missing_grok_tools_blocked(self):
        c = router.route("x_trend_research", "o", "x", task_seq=901)
        c.pop("tools", None)
        self.assertEqual(approval.gate(c)[0], "security_blocked")

    def test_missing_codex_sandbox_blocked(self):
        c = router.route("image_generate", "o", "x", task_seq=902)
        c.pop("sandbox", None)
        self.assertEqual(approval.gate(c)[0], "security_blocked")

    def test_external_op_without_confirmation_blocked(self):
        c = base_contract()
        c["constraints"]["external_operation"] = True
        self.assertEqual(approval.gate(c)[0], "security_blocked")

    def test_system_permission_blocked(self):
        c = base_contract()
        c["constraints"]["requires_system_permission"] = True
        self.assertEqual(approval.gate(c)[0], "security_blocked")

    def test_clean_task_still_allowed(self):
        self.assertEqual(approval.gate(base_contract())[0], "allow")


class TestSecurityBlockedPipeline(unittest.TestCase):
    def test_blocked_task_never_calls_executor_and_writes_audit(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "t.db")
            store = TaskStore(db)
            receipts = os.path.join(d, "receipts")
            os.makedirs(receipts)
            work_root = os.path.join(d, "work")
            os.makedirs(work_root)
            calls = []

            def fake_executor(contract, prompt, cwd):
                calls.append(contract["task_id"])
                return {"rc": 0, "stdout": "x", "stderr": "",
                        "elapsed_ms": 1, "timed_out": False,
                        "killed_group": False, "spawn_error": None}

            c = base_contract()
            c["task_id"] = "t-20261111-0901"
            status, res = run_task.execute_task(
                store, c, "请联系 13800000000 确认", work_root, receipts,
                fake_executor, lambda e: ("1.5.5", True))
            self.assertEqual(status, "failed_final")
            self.assertEqual(res.get("error_category"), "SECURITY_BLOCKED")
            self.assertEqual(calls, [])  # executor never invoked
            audit_path = os.path.join(receipts, "t-20261111-0901.security.json")
            self.assertTrue(os.path.isfile(audit_path))
            with open(audit_path) as f:
                audit = json.load(f)
            self.assertEqual(audit["rule"], "PII_PATTERN")
            self.assertNotIn("13800000000", json.dumps(audit))
            store.close()


if __name__ == "__main__":
    unittest.main()
