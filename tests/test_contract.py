"""Contract / router / approval / adapter-command tests (no real CLI calls)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import adapter_agy, adapter_codex, adapter_grok, approval, router


def base_contract(**over):
    c = router.route("jp_copywriting", "把中文改写为自然日语",
                     "虚构样本：今天天气很好，我去公园散步。", task_seq=1)
    c.update(over)
    return c


class TestRouter(unittest.TestCase):
    def test_route_jp(self):
        c = base_contract()
        self.assertEqual(c["executor"], "agy")
        self.assertEqual(c["risk_level"], "P1")
        self.assertTrue(c["idempotency_key"].startswith("agy:jp_copywriting:"))

    def test_idempotency_key_deterministic(self):
        k1 = router.idempotency_key("agy", "jp_copywriting", "o", "in", "text")
        k2 = router.idempotency_key("agy", "jp_copywriting", "o", "in", "text")
        k3 = router.idempotency_key("agy", "jp_copywriting", "o", "in2", "text")
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)

    def test_fact_check_stays_muse(self):
        r = router.route("fact_check", "核查数字", "虚构数字 123")
        self.assertTrue(r.get("muse_direct"))

    def test_pii_blocked(self):
        r = router.route("jp_copywriting", "o", "x", has_real_customer_pii=True)
        self.assertTrue(r.get("security_blocked"))

    def test_unknown_capability_blocked(self):
        r = router.route("no_such_cap", "o", "x")
        self.assertTrue(r.get("security_blocked"))


class TestApproval(unittest.TestCase):
    def test_p1_allow(self):
        d, _ = approval.gate(base_contract())
        self.assertEqual(d, "allow")

    def test_p2_needs_human_then_allow(self):
        c = base_contract(risk_level="P2", approval_requirement="human_confirm")
        self.assertEqual(approval.gate(c)[0], "needs_human")
        self.assertEqual(approval.gate(c, human_confirmed=True)[0], "allow")

    def test_p3_forbidden(self):
        self.assertEqual(approval.gate(base_contract(risk_level="P3"))[0], "forbidden")

    def test_send_capability_forbidden(self):
        c = base_contract(capability="publish_x")
        self.assertEqual(approval.gate(c)[0], "forbidden")

    def test_pii_security_blocked(self):
        c = base_contract()
        c["constraints"]["has_real_customer_pii"] = True
        self.assertEqual(approval.gate(c)[0], "security_blocked")

    def test_production_write_blocked(self):
        c = base_contract()
        c["constraints"]["writes_production"] = True
        self.assertEqual(approval.gate(c)[0], "security_blocked")


class TestAdapterCommands(unittest.TestCase):
    def test_agy_model_before_prompt(self):
        # R2-D: build_command now returns (argv, settings_path).
        argv, _ = adapter_agy.build_command(
            {"model": "gemini-3.8-flash-medium", "output_format": "text",
             "timeout": {"soft_seconds": 110}}, "hello")
        self.assertEqual(argv[1:3], ["--model", "gemini-3.8-flash-medium"])
        self.assertLess(argv.index("--model"), argv.index("-p"))
        self.assertIn("--settings", argv)

    def test_agy_rejects_json_schema_and_argon(self):
        with self.assertRaises(ValueError):
            adapter_agy.build_command(
                {"model": "gemini-3.8-flash-medium", "output_format": "json",
                 "timeout": {"soft_seconds": 110}}, "--json-schema")
        with self.assertRaises(ValueError):
            adapter_agy.build_command(
                {"model": "gemini-4-argon", "output_format": "text",
                 "timeout": {"soft_seconds": 110}}, "x")

    def test_grok_requires_tools(self):
        with self.assertRaises(ValueError):
            adapter_grok.build_command({"tools": []}, "x")

    def test_grok_rejects_unknown_tool(self):
        with self.assertRaises(ValueError):
            adapter_grok.build_command({"tools": ["web_serch"]}, "x")

    def test_grok_allows_known_tools_and_adds_guards(self):
        argv = adapter_grok.build_command(
            {"tools": ["web_search", "web_fetch"], "max_turns": 3}, "x")
        self.assertIn("--no-subagents", argv)
        # R2-D: disallowed list extended to Agent + MCP meta-tools.
        self.assertIn("Agent,search_tool,use_tool", argv)
        # R2-D: default deny Read(*) when read_file is not granted.
        self.assertIn("--deny", argv)
        self.assertIn("Read(*)", argv)
        self.assertNotIn("--sandbox", argv)
        self.assertNotIn("strict", argv)

    def test_codex_model_pinned(self):
        # R2-D: work dir must be under ~/workspace (never /tmp).
        ws = os.path.expanduser("~/workspace/muse-orchestration-r2d/work/t-test")
        with self.assertRaises(ValueError):
            adapter_codex.build_command({"model": "gpt-5", "sandbox": "read-only"}, "x", ws)
        argv = adapter_codex.build_command(
            {"model": "gpt-6.1-sol", "sandbox": "read-only"}, "x", ws)
        self.assertIn("gpt-6.1-sol", argv)
        self.assertNotIn("danger-full-access", argv)

    def test_codex_image_run_refused(self):
        with self.assertRaises(ValueError):
            adapter_codex.run(
                {"model": "gpt-6.1-sol", "sandbox": "workspace-write",
                 "expected_output": {"kind": "image"},
                 "timeout": {"hard_seconds": 60}}, "x", "/tmp")


if __name__ == "__main__":
    unittest.main()
