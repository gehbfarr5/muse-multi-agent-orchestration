"""Task Router: capability routing + risk pre-classification (R2A ARCH s1.3).

MUSE-internal decision only; never calls a CLI.
"""
import hashlib
import json

ROUTING = {
    "jp_copywriting": ("agy", "P1", "auto_if_validated"),
    "cn_copywriting": ("agy", "P1", "auto_if_validated"),
    "content_review": ("agy", "P1", "auto_if_validated"),
    "image_generate": ("codex", "P1", "auto_if_validated"),
    "x_trend_research": ("grok", "P1", "auto_if_validated"),
    "x_topic_discovery": ("grok", "P1", "auto_if_validated"),
    "x_creative": ("grok", "P1", "auto_if_validated"),
    "fact_check": ("muse_direct", "P0", "auto"),
}

PINNED_VERSIONS = {"agy": "1.5.5", "codex": "0.164.0-alpha.9", "grok": "1.0.77"}


def idempotency_key(executor, capability, objective, input_text, kind):
    canonical = json.dumps(
        {"objective": objective, "input_text": input_text, "kind": kind},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{executor}:{capability}:{digest}"


def route(capability, objective, input_text, kind="text", has_real_customer_pii=False,
          writes_production=False, task_seq=1):
    """Returns a Task Contract dict, or a SECURITY_BLOCKED dict."""
    if has_real_customer_pii or writes_production:
        return {"security_blocked": True,
                "reason": "real customer PII or production write; never delegate"}
    if capability not in ROUTING:
        return {"security_blocked": True, "reason": f"unknown capability {capability}"}
    executor, risk, approval = ROUTING[capability]
    if executor == "muse_direct":
        return {"muse_direct": True, "capability": capability,
                "reason": "fact-checking stays with MUSE + evidence layer"}
    task_id = f"t-20261111-{task_seq:04d}"
    # R2-D: every contract carries its executor's required security config.
    # The Approval Gate blocks the task (SECURITY_BLOCKED) when it is missing.
    # Grok has no zero-tool mode: omitting --tools means full tool access,
    # so the least-privilege floor is a single harmless tool; x_creative gets
    # web_search only, with the default Read(*) deny (no read_file granted).
    security_profile = {}
    extra = {}
    if executor == "agy":
        security_profile = {"agy_mode": "deny_all"}
    elif executor == "grok":
        extra["tools"] = (["web_search", "web_fetch"] if capability == "x_trend_research"
                          else ["web_search"])
        extra["max_turns"] = 3
    elif executor == "codex":
        extra["sandbox"] = "workspace-write" if capability == "image_generate" else "read-only"
    contract = {
        "task_id": task_id,
        "capability": capability,
        "executor": executor,
        "executor_version_pinned": PINNED_VERSIONS[executor],
        "security_profile": security_profile,
        "objective": objective,
        "context": {"input_text": input_text, "input_refs": [], "prior_task_ids": []},
        "constraints": {
            "no_tools": capability in ("jp_copywriting", "cn_copywriting"),
            "no_network": capability in ("jp_copywriting", "cn_copywriting"),
            "no_customer_pii": True,
            "cwd": f"work/{task_id}",
            "allowed_params": [],
            "forbidden_params": [],
        },
        "risk_level": risk,
        "approval_requirement": approval,
        "timeout": {"soft_seconds": 110, "hard_seconds": 130},
        # R6-C: image tasks never auto-retry a real generation. A failed
        # real generation falls back to MUSE; the user decides the next
        # move. The mock image path is deterministic, so a retry adds
        # nothing there either.
        "retry_budget": {"max_auto_retries": 0 if capability == "image_generate" else 1,
                         "retryable_categories": ["TIMEOUT", "EMPTY_OUTPUT", "INVALID_OUTPUT"]},
        "idempotency_key": idempotency_key(executor, capability, objective, input_text, kind),
        "expected_output": {"kind": kind, "schema_ref": None, "min_chars": 1, "max_chars": 2000},
        "validation_rules": [{"rule": "non_empty", "severity": "fail"}],
        "created_at": "2026-11-11T11:00:00+08:00",
        "created_by": "muse-router",
    }
    contract.update(extra)
    if capability == "jp_copywriting":
        contract["validation_rules"].append({"rule": "language_is_japanese", "severity": "fail"})
    if kind == "json":
        contract["validation_rules"].append({"rule": "score_range", "severity": "fail"})
    return contract
