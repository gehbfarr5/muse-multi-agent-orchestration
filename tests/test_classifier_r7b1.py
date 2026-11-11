"""R7-B1: routing fix for ISSUE-01 (cn write+review) and ISSUE-02
(X/Twitter research natural phrasings), plus composite fail-closed
and the R7A-ISSUE-03/04 small fixes.

Mock / offline only. No CLI calls, no real generation. The Mock e2e
tests patch orchestrator.EXECUTORS with fake executors and assert the
fake is called exactly once (no duplicate scheduling, no real CLI).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PIL import Image

from src import adapter_codex, approval
from src.classifier import classify
from src.orchestrator import submit
from src.router import route
from src.run_task import execute_task
from src.store import TaskStore


def cap(text):
    return classify(text)["capability"]


class TestIssue01Positives(unittest.TestCase):
    def test_original_failure(self):
        self.assertEqual(
            cap("请写一篇关于新产品发布的中文稿，并独立审核一遍"),
            "cn_copywriting")

    def test_task_book_extras(self):
        for text in [
            "帮我写一篇中文文章，再检查有没有问题。",
            "写一份产品介绍，完成后再审核措辞。",
            "帮我整理成文案，再独立审一遍。",
            "起草一份说明，写完之后帮我检查逻辑和表达。",
        ]:
            self.assertEqual(cap(text), "cn_copywriting", text)

    def test_standalone_review_routes_to_agy(self):
        for text in [
            "帮我审核这段文案",
            "请帮我审一遍这篇稿子",
            "检查一下这篇文章的逻辑和表达",
            "帮我复核这份说明",
        ]:
            self.assertIn(cap(text), ("cn_copywriting", "content_review"), text)


class TestIssue01Negatives(unittest.TestCase):
    def test_simple_tasks_stay_direct(self):
        for text in [
            "把这句话改得更口语一点：今天天气很好",
            "今天天气怎么样",
            "1+1 等于几",
            "你好",
            "帮我写一句话",
        ]:
            self.assertIsNone(cap(text), text)

    def test_bare_check_is_not_review(self):
        # "检查" alone, or about a non-text object, must not delegate.
        for text in [
            "检查一下我的电脑有没有问题",
            "检查一下天气",
            "帮我检查一下门锁好了没有",
        ]:
            self.assertIsNone(cap(text), text)


class TestIssue02Positives(unittest.TestCase):
    def test_original_failure(self):
        self.assertEqual(
            cap("帮我研究一下 Twitter 上关于 AI 芯片的公开讨论"),
            "x_trend_research")

    def test_task_book_extras(self):
        for text in [
            "帮我看看 X 上最近大家在讨论什么。",
            "查一下 Twitter 上关于这个话题的讨论。",
            "去 X 看看最近有哪些相关观点。",
            "帮我研究一下 Twitter 上某个公开话题的讨论趋势。",
            "看看 X 平台最近对这个话题有什么反应。",
        ]:
            self.assertEqual(cap(text), "x_trend_research", text)


class TestIssue02Negatives(unittest.TestCase):
    def test_letter_model_math_and_quote(self):
        for text in [
            "解方程 x+2=5",
            "iPhone X 怎么样",
            "Model X 价格多少",
            "X 是什么字母",
            "这句话里提到了 Twitter",
            "引用一下这条 Twitter 原文",
        ]:
            self.assertIsNone(cap(text), text)

    def test_explicit_no_search_and_local_text(self):
        for text in [
            "不要去 X 上查，我自己看",
            "帮我总结下面这段 Twitter 内容：xxxx",
            "翻译以下这段 Twitter 帖子",
        ]:
            self.assertIsNone(cap(text), text)


class TestCompositeFailClosed(unittest.TestCase):
    def test_research_plus_writing_is_direct(self):
        # Task-book example: sequentially dependent multi-agent work the
        # single-task architecture cannot execute; fail closed, do not
        # silently drop half the task or submit twice.
        cls = classify("先研究一下 X 上的公开讨论，再帮我写一篇中文总结并审核。")
        self.assertIsNone(cls["capability"])
        self.assertEqual(cls["decision"], "muse_direct")
        self.assertIn("composite", cls.get("reason", ""))

    def test_image_with_caption_keeps_priority(self):
        # R6-C regression: one artifact with a caption is one task.
        self.assertEqual(cap("生成一张图片，配一句日语文案"), "image_generate")

    def test_same_executor_overlap_is_one_call(self):
        # cn write + review both go to agy: first match wins, one call.
        self.assertEqual(cap("写一篇中文稿并独立审核"), "cn_copywriting")


class TestGateUnchanged(unittest.TestCase):
    def test_pii_in_new_routes_still_blocked(self):
        for text, capability in [
            ("帮我写一篇中文文章，顺便把电话 13812345678 写进去", "cn_copywriting"),
            ("帮我审核这份客户合同，客户电话 13812345678", "content_review"),
            ("帮我研究一下 Twitter 上的讨论，联系我 13812345678", "x_trend_research"),
        ]:
            self.assertEqual(cap(text), capability, text)
            contract = route(capability, text, text)
            decision, reason = approval.gate(contract, prompt=text)
            self.assertEqual(decision, "security_blocked", text)
            self.assertIn("PII_PATTERN", reason)


class TestPriorRoutingUnchanged(unittest.TestCase):
    def test_japanese_still_routes_to_agy(self):
        self.assertEqual(
            cap("帮我写一段自然的日语消息，邀请朋友来参加活动"),
            "jp_copywriting")
        self.assertEqual(cap("把这句话改写为日语：你好"), "jp_copywriting")

    def test_old_x_patterns_unchanged(self):
        self.assertEqual(cap("X 有什么新趋势"), "x_trend_research")
        self.assertEqual(cap("帮我做一个话题发现"), "x_topic_discovery")

    def test_image_negatives_unchanged(self):
        for text in [
            "请分析这张图片里有什么",
            "edit this image to remove the background",
            "帮我搜索一些猫的图片",
            "这张图片很好看",
        ]:
            self.assertNotEqual(cap(text), "image_generate", text)

    def test_empty_and_none_are_direct(self):
        self.assertIsNone(cap(""))
        self.assertIsNone(cap(None))
        self.assertIsNone(cap("   "))


def fake_executor_factory(calls):
    def fake_run(contract, prompt, cwd):
        calls.append(contract["executor"])
        # The validator reads parsed.text for grok; response_text for agy.
        return {"rc": 0, "stdout": "MOCK response text",
                "stderr": "", "elapsed_ms": 5, "timed_out": False,
                "spawn_error": None, "killed_group": False,
                "residual_pids": [], "response_text": "MOCK response text",
                "parsed": {"text": "MOCK response text"}}
    def fake_version(executor=None):
        return ("1.5.5" if executor == "agy" else "1.0.77", True)
    return fake_run, fake_version


class TestMockE2E(unittest.TestCase):
    """submit() through the real entry with FAKE executors: proves the
    new routes reach the right contract without any real CLI call."""

    def setUp(self):
        os.makedirs(os.path.expanduser("~/workspace"), exist_ok=True)
        self.base = tempfile.mkdtemp(prefix="r7b1-e2e-", dir=os.path.expanduser("~/workspace"))
        # R7-E: delegation tests run with external calls explicitly
        # enabled; the default-off behavior is covered by
        # test_external_calls_default_off_r7e.py.
        os.makedirs(os.path.join(self.base, "config"), exist_ok=True)
        with open(os.path.join(self.base, "config", "config.json"), "w") as f:
            json.dump({"external_calls_enabled": True}, f)
        self.calls = []
        self._orig = {}
        import src.orchestrator as orch
        self.orch = orch
        for name in ("agy", "grok", "codex"):
            self._orig[name] = orch.EXECUTORS[name]
        run, ver = fake_executor_factory(self.calls)
        self.fake_run, self.fake_ver = run, ver
        # Pinned versions differ per executor; the fake version fn must
        # return each executor's pinned version or the drift check refuses.
        pinned = {"agy": "1.5.5", "grok": "1.0.77", "codex": "0.164.0-alpha.9"}
        def ver_fn(executor=None):
            return (pinned.get(executor, ""), True)
        for name in ("agy", "grok"):
            orch.EXECUTORS[name] = (run, ver_fn)

    def tearDown(self):
        for name, val in self._orig.items():
            self.orch.EXECUTORS[name] = val
        shutil.rmtree(self.base, ignore_errors=True)

    def test_cn_write_review_reaches_agy_once(self):
        s = submit("请写一篇关于新产品发布的中文稿，并独立审核一遍",
                   base=self.base)
        self.assertEqual(s["handled_by"], "agy")
        self.assertEqual(s["status"], "adopted")
        self.assertEqual(self.calls, ["agy"])

    def test_x_research_reaches_grok_once(self):
        s = submit("帮我研究一下 Twitter 上关于 AI 芯片的公开讨论",
                   base=self.base)
        self.assertEqual(s["handled_by"], "grok")
        self.assertEqual(s["status"], "adopted")
        self.assertEqual(self.calls, ["grok"])

    def test_composite_submits_nothing(self):
        s = submit("先研究一下 X 上的公开讨论，再帮我写一篇中文总结并审核。",
                   base=self.base)
        self.assertEqual(s["handled_by"], "muse_direct")
        self.assertEqual(self.calls, [])

    def test_duplicate_is_not_re_executed(self):
        text = "帮我研究一下 Twitter 上关于 AI 芯片的公开讨论"
        submit(text, base=self.base)
        s2 = submit(text, base=self.base)
        self.assertEqual(s2["status"], "duplicate_of")
        self.assertEqual(self.calls, ["grok"])




if __name__ == "__main__":
    unittest.main()
