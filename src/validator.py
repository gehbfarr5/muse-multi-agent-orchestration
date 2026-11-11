"""Result Validator: four-stage acceptance.

Stage order (R2A FAILURE-RECOVERY section 1):
  execution (exit code / timeout / spawn) -> output non-empty ->
  structure parse -> content rules.
agent_claimed_status is recorded only, never a success input.
"""
import json
import re

# 429 sniffing table (R2A section 2). Live 429 text per CLI is UNVERIFIED;
# these patterns come from generic server conventions + R1 F6 mock shape.
TRANSIENT_429 = re.compile(r"rate_limited|rate limit|too many requests|\b429\b", re.I)
QUOTA_429 = re.compile(r"quota|insufficient_quota|billing|limit exceeded|exceeded.*quota", re.I)
RETRY_AFTER = re.compile(r"retry-after[:\s]+(\d+)", re.I)
AUTH_PATTERNS = re.compile(r"not signed in|not authenticated|unauthorized|401|token expired|login required", re.I)


def _is_japanese(text):
    return any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in text)


def classify(raw, contract):
    """Return (error_category, output_status, execution_status).

    raw: adapter result dict with rc/stdout/stderr/timed_out/spawn_error/
    response_text or parsed payload, denied_actions, envelope.
    """
    if raw.get("spawn_error"):
        return "SPAWN_FAILED", "empty", "failed"
    if raw.get("timed_out"):
        return "TIMEOUT", "partial", "timed_out"
    rc = raw.get("rc")
    if rc is None:
        return "UNKNOWN_EXECUTION_STATE", "empty", "unknown"
    if rc < 0:
        # killed by signal (e.g. -15 SIGTERM, -9 SIGKILL)
        return "UNKNOWN_EXECUTION_STATE", "empty", "killed"
    blob = (raw.get("stdout", "") or "") + "\n" + (raw.get("stderr", "") or "")
    if QUOTA_429.search(blob):
        return "RATE_LIMITED", "empty", "failed"
    if TRANSIENT_429.search(blob):
        return "RATE_LIMITED", "empty", "failed"
    if AUTH_PATTERNS.search(blob):
        return "AUTH_REQUIRED", "empty", "failed"
    if rc != 0:
        return "NONZERO_EXIT", "empty", "failed"
    text = raw.get("response_text", raw.get("stdout", ""))
    if contract.get("executor") == "grok":
        text = (raw.get("parsed") or {}).get("text", "")
    if not (text or "").strip():
        if raw.get("denied_actions"):
            return "PERMISSION_DENIED", "empty", "completed"
        return "EMPTY_OUTPUT", "empty", "completed"
    return None, "non_empty", "completed"


def validate(contract, raw):
    """Full validation. Returns a Result Contract dict (R2A TASK-CONTRACT s2)."""
    executor = contract["executor"]
    error_category, output_status, execution_status = classify(raw, contract)
    details = []
    validation_status = "not_run"
    kind = contract.get("expected_output", {}).get("kind", "text")

    if error_category in ("SPAWN_FAILED", "TIMEOUT", "NONZERO_EXIT",
                          "RATE_LIMITED", "AUTH_REQUIRED",
                          "UNKNOWN_EXECUTION_STATE"):
        validation_status = "not_run"
    elif error_category in ("EMPTY_OUTPUT", "PERMISSION_DENIED"):
        validation_status = "failed"
        details.append({"rule": "non_empty", "result": "fail",
                        "note": "empty output" + ("; denied_actions present" if raw.get("denied_actions") else "")})
    else:
        # structure + content checks
        text = raw.get("response_text", raw.get("stdout", ""))
        if executor == "grok":
            parsed = raw.get("parsed") or {}
            if parsed.get("parse_error"):
                error_category = "INVALID_OUTPUT"
                output_status = "invalid_json"
                details.append({"rule": "envelope_json", "result": "fail",
                                "note": parsed["parse_error"]})
            elif parsed.get("stop_reason") not in (None, "end_turn"):
                error_category = "VALIDATION_FAILED"
                details.append({"rule": "stop_reason_end_turn", "result": "fail",
                                "note": f"stopReason={parsed.get('stop_reason')}"})
            text = parsed.get("text", "")
        if error_category is None and kind == "json":
            try:
                data = json.loads((text or "").strip())
                output_status = "valid_json"
                details.append({"rule": "json_parse", "result": "pass"})
                for rule in contract.get("validation_rules", []):
                    if rule.get("rule") == "score_range":
                        v = data.get("score_0_to_10")
                        ok = isinstance(v, int) and 0 <= v <= 10
                        details.append({"rule": "score_range", "result": "pass" if ok else "fail",
                                        "note": f"score_0_to_10={v}"})
                        if not ok:
                            error_category = "VALIDATION_FAILED"
            except (json.JSONDecodeError, AttributeError) as e:
                error_category = "INVALID_OUTPUT"
                output_status = "invalid_json"
                details.append({"rule": "json_parse", "result": "fail", "note": str(e)})
        if error_category is None:
            for rule in contract.get("validation_rules", []):
                name = rule.get("rule")
                if name == "non_empty":
                    ok = bool((text or "").strip())
                    details.append({"rule": name, "result": "pass" if ok else "fail"})
                    if not ok:
                        error_category = "EMPTY_OUTPUT"
                elif name == "language_is_japanese":
                    ok = _is_japanese(text or "")
                    details.append({"rule": name, "result": "pass" if ok else "fail"})
                    if not ok:
                        error_category = "VALIDATION_FAILED"
                elif name == "max_chars":
                    limit = rule.get("limit", 2000)
                    ok = len(text or "") <= limit
                    details.append({"rule": name, "result": "pass" if ok else "fail"})
                    if not ok:
                        error_category = "VALIDATION_FAILED"
                elif name == "no_unverified_facts":
                    # Code can only warn on new entities; final call is MUSE's.
                    details.append({"rule": name, "result": "warn",
                                    "note": "code check is heuristic only; MUSE must confirm"})
        # R6-C image-artifact rule: for image tasks, a validated artifact
        # is the success criterion. Exit code 0 plus agent words is not
        # enough — if the adapter reports image_check not ok, the result
        # is INVALID_OUTPUT. Only fires when no earlier category (TIMEOUT,
        # NONZERO_EXIT, RATE_LIMITED, AUTH_REQUIRED, ...) already applies.
        if error_category is None and kind == "image":
            chk = raw.get("image_check") or {}
            if chk and not chk.get("ok"):
                error_category = "INVALID_OUTPUT"
                details.append({"rule": "image_artifact_valid", "result": "fail",
                                "note": chk.get("error") or "no valid image artifact"})
        if error_category is None:
            validation_status = "passed"
        else:
            validation_status = "failed"

    if raw.get("denied_actions") and validation_status == "passed":
        details.append({"rule": "denied_actions_empty", "result": "warn",
                        "note": f"denied_actions={raw['denied_actions']}"})

    success = (execution_status == "completed" and output_status in ("non_empty", "valid_json")
               and validation_status == "passed")
    return {
        "task_id": contract["task_id"],
        "executor": executor,
        "executor_version": contract.get("executor_version_pinned"),
        "execution_status": execution_status,
        "process_exit_code": raw.get("rc"),
        "output_status": output_status,
        "validation_status": validation_status,
        "artifact_reference": None,
        "error_category": error_category,
        "retryable": False,  # filled by recovery.decide()
        "elapsed_ms": raw.get("elapsed_ms", 0),
        "receipt_reference": None,  # filled by run_task
        "process_evidence": {
            "stdout_bytes": len(raw.get("stdout", "") or ""),
            "stderr_bytes": len(raw.get("stderr", "") or ""),
            "timed_out": raw.get("timed_out", False),
            "killed_process_group": raw.get("killed_group", False),
            "residual_processes": len(raw.get("residual_pids", []) or []),
            "denied_actions": raw.get("denied_actions", []),
        },
        "agent_claimed_status": _claimed(raw),
        "validation_details": details,
        "success": success,
        "_blob": raw.get("_blob", ""),
    }


def _claimed(raw):
    env = raw.get("envelope")
    if isinstance(env, dict):
        return env.get("status")
    parsed = raw.get("parsed")
    if isinstance(parsed, dict):
        return parsed.get("stop_reason")
    return None
