"""Natural-language task classifier (R3 unified entry, step 2).

Deterministic keyword rules only — no model call, no guessing. A task that
matches no capability is MUSE-direct (MUSE handles it in conversation);
the classifier never invents a delegation. PII / production-write / send
intent is NOT decided here: the Approval Gate (step 5) is authoritative
and blocks those even when the classifier matched a capability.
"""
import re

# (capability, [keyword patterns]) — order matters: first match wins.
# R6-C: an explicit image-generation request outranks the other
# delegation rules (priority 2, after the Approval Gate, which runs
# later in run_task and is authoritative for PII/production writes).
# Image EDIT / ANALYSIS / SEARCH and mere mentions of an image must NOT
# match: see IMAGE_EXCLUSIONS, checked before the image rule.
RULES = [
    ("image_generate", [
        # English (R6-C)
        r"generate an image", r"create a picture", r"draw a picture",
        r"generate a png", r"make an illustration",
        r"(create|generate|draw|make).{0,40}\bpng\b.{0,10}\bimage\b",
        # Chinese (R6-C additions + R3 originals)
        r"生成一张图片", r"帮我画一张图", r"创建一张插画",
        r"生成\s*PNG\s*图片", r"画一只猫",
        r"生成图片", r"配图", r"插图", r"画一张", r"做一张图",
    ]),
    ("jp_copywriting", [r"日语", r"日本語", r"改写为日语", r"翻译成日语", r"日文"]),
    # R7-B1 (ISSUE-01): natural Chinese writing phrasings. A writing task
    # that also asks for a review ("写完再审核") still maps to the single
    # cn_copywriting contract: the prompt itself carries both instructions
    # and one agy call handles both. There is no separate two-phase
    # generation-then-review workflow in the current architecture; see
    # R7B1-IMPLEMENTATION.md for that recorded limitation.
    ("cn_copywriting", [
        r"中文创作", r"写一段中文", r"中文文案", r"写中文",
        r"写一篇", r"写一份", r"写篇", r"起草", r"整理成文案",
        r"写完.*(检查|审核|审)", r"写.*(文章|文稿|文案|介绍|说明|稿件)",
    ]),
    ("content_review", [
        r"评审", r"审校", r"检查.*夸大", r"检查.*表述", r"审查文案",
        # R7-B1 (ISSUE-01): natural review phrasings. "检查" alone is NOT
        # a pattern: it must name a text object (逻辑/表达/措辞/文章…)
        # so "检查一下电脑" stays MUSE-direct.
        r"审核", r"独立审", r"审一遍", r"复核",
        r"检查.*(逻辑|表达|措辞)", r"检查.*(文章|文案|稿|说明|文字|内容)",
    ]),
    # R7-B1 (ISSUE-02): natural X/Twitter research phrasings. The platform
    # name alone is NOT a pattern: a research verb or a discussion noun
    # must accompany it (see X_EXCLUSIONS for letter/model/quote cases).
    ("x_trend_research", [
        r"趋势", r"研究资料", r"话题.*研究", r"X 趋势",
        r"(X|Twitter)\s*上.*(讨论|观点|反应|话题|趋势|热议|热门)",
        r"研究.{0,20}(Twitter|X\b)",
        r"(看看|查一下|去).{0,12}(X|Twitter)\b",
        r"(Twitter|X)\s*平台",
    ]),
    ("x_topic_discovery", [r"话题发现", r"发现话题", r"找话题"]),
    ("fact_check", [r"事实核查", r"查证", r"是否属实", r"核实.*说法"]),
]

# Capabilities that need an external research step, and capabilities that
# need a writing step. A request matching one of each is a composite,
# sequentially dependent multi-agent task ("先研究 X 再写中文总结并审核"):
# the single-task architecture cannot execute both, so it fails closed
# to muse_direct (clarify / split) instead of silently dropping half.
# Image combos are excluded: an image request with a caption is one
# artifact, and the existing priority rule (image outranks the rest)
# already covers it (test_image_outranks_japanese).
_RESEARCH_CAPS = {"x_trend_research", "x_topic_discovery"}
_WRITING_CAPS = {"jp_copywriting", "cn_copywriting", "content_review"}

# Phrases that look image-related but are NOT generation requests.
# Checked only against the image rule: an exclusion match means the
# text falls through to the later rules (usually muse_direct).
IMAGE_EXCLUSIONS = [
    # edit an existing image
    r"编辑.*(图片|图|照片)", r"修改.*(图片|图|照片)", r"改.*(这张图|这张图片)",
    r"\b(edit|modify|crop|resize|rotate|retouch)\b.{0,20}\b(image|picture|photo|png)\b",
    r"\b(image|picture|photo|png)\b.{0,20}\b(edit|modify|crop|resize|rotate|retouch)\b",
    # analyze / describe an existing image
    r"分析.*(图片|图|照片)", r"解析.*(图片|图|照片)", r"描述.*(这张图|这张图片|图片)",
    r"\banalyz\w*\b.{0,20}\b(image|picture|photo)\b",
    r"\bdescribe\b.{0,20}\b(image|picture|photo)\b",
    r"what is in (this|the) (image|picture|photo)",
    r"what's in (this|the) (image|picture|photo)",
    r"这张(图|图片|照片).*(是什么|是谁|怎么样|如何|好看|吗)",
    # search for images
    r"搜索.*(图片|图|照片)", r"找.*(图片|图)\b", r"搜一张图",
    r"\bsearch\b.{0,20}\b(images?|pictures?|photos?)\b",
    r"\bfind\b.{0,20}\b(images?|pictures?|photos?)\b",
    # mere mention of an image (no generation verb)
    r"这张(图|图片|配图|插图).*(好看|不错|喜欢|怎么样|如何)",
    r"图片.*(是什么|是谁|在哪里|怎么样)",
]


def classify(text):
    """Return {capability, executor, risk, confidence} or a direct verdict.

    confidence is 'high' when a rule matched, 'none' when nothing did.
    A research + writing composite returns muse_direct with a reason.
    """
    text = (text or "").strip()
    # Image keeps its existing priority: an explicit generation request
    # (with a caption, if any) is one artifact and outranks the rest.
    for pat in RULES[0][1]:
        if re.search(pat, text, re.IGNORECASE):
            if _is_excluded(text):
                break
            return {"capability": "image_generate",
                    "decision": "delegate",
                    "confidence": "high",
                    "matched_pattern": pat}
    # Collect every other matching capability (order = RULES order).
    matched = []
    for capability, patterns in RULES[1:]:
        if capability in _RESEARCH_CAPS and _is_x_excluded(text):
            continue
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                matched.append((capability, pat))
                break
    if not matched:
        return {"capability": None, "decision": "muse_direct",
                "confidence": "none", "matched_pattern": None}
    caps = {c for c, _ in matched}
    if caps & _RESEARCH_CAPS and caps & _WRITING_CAPS:
        return {"capability": None, "decision": "muse_direct",
                "confidence": "none", "matched_pattern": None,
                "reason": "composite: research + writing in one request; "
                          "single-task architecture cannot execute both; "
                          "ask the user to split or clarify"}
    capability, pat = matched[0]
    return {"capability": capability,
            "decision": "delegate",
            "confidence": "high",
            "matched_pattern": pat}


def _is_excluded(text):
    return any(re.search(pat, text, re.IGNORECASE) for pat in IMAGE_EXCLUSIONS)


# Phrases that look like X/Twitter research but are NOT: a bare letter,
# a product model, a math variable, a quoted mention, an explicit
# do-not-search instruction, or a request to summarize text the user
# already supplied. Checked only against the research rules: an
# exclusion match suppresses the research capability, not the others.
X_EXCLUSIONS = [
    # explicit do-not-search
    r"不要.*(X|Twitter)", r"不用.*(搜索|查).*(X|Twitter)",
    # summarize text already supplied locally
    r"(总结|概括|翻译|改写).*(下面|以下|这段|这篇|附上).*(Twitter|X)",
    # mere mention / quotation
    r"提到.*(Twitter|X)", r"(引用|引述).*(Twitter|X)",
    # math variable / letter question
    r"\b[xX]\s*[+\-*/=<>^]", r"解.*方程",
    r"[xX]\s*是(什么|啥)", r"(字母|字符).*[xX]",
    # product model
    r"(iPhone|Model|特斯拉|Tesla|小米|华为|OPPO|vivo)\s*[xX]\b",
]


def _is_x_excluded(text):
    return any(re.search(pat, text, re.IGNORECASE) for pat in X_EXCLUSIONS)
