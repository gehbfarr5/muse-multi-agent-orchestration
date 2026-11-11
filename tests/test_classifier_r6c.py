"""F1: image classifier (R6-C).

Positives (EN + CN), negatives (edit / analyze / search / mere mention),
conflict cases, and R4-G routing regressions (jp / Grok / direct).
Mock / offline only. No CLI calls, no real generation.
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.classifier import classify
from src.approval import gate


def cap(text):
    return classify(text)["capability"]


class TestImagePositives(unittest.TestCase):
    def test_english_phrases(self):
        for text in [
            "generate an image of a blue cup",
            "create a picture of a cat",
            "draw a picture",
            "generate a PNG of a logo",
            "Generate a PNG image",
            "make an illustration for a story",
        ]:
            self.assertEqual(cap(text), "image_generate", text)

    def test_chinese_phrases(self):
        for text in [
            "生成一张图片",
            "帮我画一张图",
            "创建一张插画",
            "生成 PNG 图片",
            "画一只猫",
            "生成图片",
            "请给这篇文章配图",
            "做一张图",
        ]:
            self.assertEqual(cap(text), "image_generate", text)

    def test_r6b_prompt(self):
        # The exact R6-B prompt must route to image generation.
        self.assertEqual(
            cap("Create one original PNG image of a small blue ceramic "
                "coffee cup on a plain white background, viewed from a "
                "slightly elevated angle. Minimal studio product "
                "photography, soft natural shadows, no text, no logo, "
                "no people."),
            "image_generate")

    def test_case_insensitive(self):
        self.assertEqual(cap("GENERATE AN IMAGE"), "image_generate")
        self.assertEqual(cap("Draw A Picture"), "image_generate")


class TestImageNegatives(unittest.TestCase):
    def test_edit_is_not_generation(self):
        for text in [
            "请帮我编辑这张图片，把背景换掉",
            "修改这张图片的亮度",
            "edit this image to remove the background",
            "EDIT THIS IMAGE",
            "crop this image",
        ]:
            self.assertNotEqual(cap(text), "image_generate", text)

    def test_analyze_is_not_generation(self):
        for text in [
            "请分析这张图片里有什么",
            "解析这张图片",
            "analyze this image",
            "describe this image",
            "what is in this image?",
            "这张图片是什么？",
        ]:
            self.assertNotEqual(cap(text), "image_generate", text)

    def test_search_is_not_generation(self):
        for text in [
            "帮我搜索一些猫的图片",
            "找一张猫的图片",
            "search for images of dogs",
            "find pictures of sunsets",
        ]:
            self.assertNotEqual(cap(text), "image_generate", text)

    def test_mere_mention_is_not_generation(self):
        for text in [
            "这张图片很好看",
            "这张配图不错",
            "图片里的人是谁",
        ]:
            self.assertNotEqual(cap(text), "image_generate", text)


class TestRoutingRegressions(unittest.TestCase):
    def test_japanese_still_routes_to_agy(self):
        # R4-G acceptance A: one-sentence Japanese invitation.
        self.assertEqual(
            cap("帮我写一段自然的日语消息，邀请朋友来参加活动"),
            "jp_copywriting")
        self.assertEqual(cap("把这句话改写为日语：你好"), "jp_copywriting")

    def test_grok_routes_unchanged(self):
        self.assertEqual(cap("X 有什么新趋势"), "x_trend_research")
        self.assertEqual(cap("帮我做一个话题发现"), "x_topic_discovery")

    def test_simple_tasks_stay_direct(self):
        # R4-G acceptance B: simple Chinese rewrite is MUSE-direct.
        self.assertIsNone(cap("把这句话改得更口语一点：今天天气很好"))
        self.assertIsNone(cap("今天天气怎么样"))
        self.assertIsNone(cap("1+1 等于几"))

    def test_image_outranks_japanese(self):
        # Priority list: explicit image generation (2) beats jp (3).
        self.assertEqual(
            cap("生成一张图片，配一句日语文案"), "image_generate")

    def test_gate_still_blocks_pii_before_delegation(self):
        # Classifier may match image, but the Approval Gate is
        # authoritative: a phone number in the text blocks delegation.
        from src.router import route
        text = "生成一张图片，上面写我的电话 13812345678"
        self.assertEqual(cap(text), "image_generate")
        contract = route("image_generate", "image", text, kind="image")
        decision, reason = gate(contract, prompt=text)
        self.assertEqual(decision, "security_blocked")
        self.assertIn("PII_PATTERN", reason)


if __name__ == "__main__":
    unittest.main()
