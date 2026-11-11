"""Recovery Controller: fault state machine, retry budget, circuit breaker.

Rules (R2A FAILURE-RECOVERY):
- Total auto-retries per task <= 2 across all categories.
- Transient 429: exponential backoff, max 2 retries, counted in budget.
- Quota-exhausted 429: NEVER retry; trip breaker immediately.
- UNKNOWN_EXECUTION_STATE: NEVER blind-retry; investigate first.
- Breaker trips at 3 consecutive failures per executor; recovery is
  manual-probe only, never automatic.
"""
import re
import time

from .validator import RETRY_AFTER, QUOTA_429

MAX_TOTAL_AUTO_RETRIES = 2
BREAKER_THRESHOLD = 3

# categories allowed one auto-retry each (still capped by total budget)
RETRYABLE_ONCE = {"TIMEOUT", "EMPTY_OUTPUT", "INVALID_OUTPUT", "VALIDATION_FAILED"}
# NONZERO_EXIT retries only for known-transient text
TRANSIENT_NONZERO = re.compile(r"temporar|transient|econnreset|econnaborted|dns|timeout", re.I)

# Backoff schedule for transient 429 (seconds). Caller sleeps; tests use 0.
BACKOFF_SECONDS = [5, 15]


def is_quota_exhausted(blob):
    return bool(QUOTA_429.search(blob or ""))


def retry_after_seconds(blob):
    m = RETRY_AFTER.search(blob or "")
    if m:
        return int(m.group(1))
    return None


def decide(contract, result, retries_used, store):
    """Decide the next action. Returns dict:
    action: adopt | retry | fallback_to_muse | fail_final | needs_human | duplicate
    wait_seconds: backoff before a retry (0 = none)
    reason: human-readable

    Retry budget: min(contract retry_budget.max_auto_retries, global cap 2).
    The contract is the tighter authority; the global cap is the ceiling.
    """
    cat = result.get("error_category")
    executor = contract["executor"]
    budget = min((contract.get("retry_budget") or {}).get("max_auto_retries", 2),
                 MAX_TOTAL_AUTO_RETRIES)
    if result.get("success"):
        return {"action": "adopt", "wait_seconds": 0, "reason": "validated success"}
    if cat == "DUPLICATE_REQUEST":
        return {"action": "duplicate", "wait_seconds": 0,
                "reason": "idempotency key hit; return first receipt"}
    if cat == "UNKNOWN_EXECUTION_STATE":
        return {"action": "needs_human", "wait_seconds": 0,
                "reason": "unknown execution state; investigate before any retry"}
    if cat == "AUTH_REQUIRED":
        return {"action": "needs_human", "wait_seconds": 0,
                "reason": "auth required; user must re-login; executor tripped"}
    if cat == "VERSION_DRIFT":
        return {"action": "needs_human", "wait_seconds": 0,
                "reason": "executor version drift; user decides whether to accept"}
    if cat == "SPAWN_FAILED":
        return {"action": "fallback_to_muse", "wait_seconds": 0,
                "reason": "executor unavailable; MUSE takes over"}
    if cat == "RATE_LIMITED":
        blob = result.get("_blob", "")
        if is_quota_exhausted(blob):
            return {"action": "fail_final", "wait_seconds": 0,
                    "reason": "quota exhausted; never retry; breaker tripped"}
        ra = retry_after_seconds(blob)
        if ra is not None and ra > 300:
            return {"action": "fail_final", "wait_seconds": 0,
                    "reason": f"Retry-After {ra}s > 300s; treat as quota exhausted"}
        if retries_used < budget:
            wait = ra if ra is not None else BACKOFF_SECONDS[min(retries_used, len(BACKOFF_SECONDS) - 1)]
            return {"action": "retry", "wait_seconds": wait,
                    "reason": "transient rate limit; backoff retry"}
        return {"action": "fallback_to_muse", "wait_seconds": 0,
                "reason": "transient 429 budget exhausted; MUSE takes over"}
    if cat in RETRYABLE_ONCE:
        if retries_used < budget:
            return {"action": "retry", "wait_seconds": 0,
                    "reason": f"{cat} within retry budget"}
        return {"action": "fallback_to_muse", "wait_seconds": 0,
                "reason": f"{cat} budget exhausted; MUSE takes over"}
    if cat == "NONZERO_EXIT":
        blob = result.get("_blob", "")
        if TRANSIENT_NONZERO.search(blob or "") and retries_used < budget:
            return {"action": "retry", "wait_seconds": 0,
                    "reason": "transient nonzero exit; one retry"}
        return {"action": "fallback_to_muse", "wait_seconds": 0,
                "reason": "nonzero exit; MUSE takes over"}
    if cat == "PERMISSION_DENIED":
        return {"action": "fallback_to_muse", "wait_seconds": 0,
                "reason": "permission denied; prompt/whitelist change needs a new task"}
    return {"action": "fail_final", "wait_seconds": 0,
            "reason": f"unhandled category {cat}"}


def investigate_unknown(store, task_id, work_dir, receipt_path):
    """Read-only recovery checks (ADR section 7). Returns dict of findings.
    Never retries, never mutates. Caller decides the outcome.
    """
    import os
    findings = {}
    findings["receipt_exists"] = bool(receipt_path and os.path.isfile(receipt_path))
    if findings["receipt_exists"]:
        try:
            import json
            with open(receipt_path) as f:
                rec = json.load(f)
            findings["receipt_complete"] = bool(rec.get("result") is not None)
        except Exception:
            findings["receipt_complete"] = False
    else:
        findings["receipt_complete"] = False
    findings["work_dir_exists"] = os.path.isdir(work_dir)
    findings["artifact_exists"] = bool(
        work_dir and os.path.isdir(work_dir)
        and any(os.scandir(work_dir)))
    # residual scan: any leftover child processes of this task are found by
    # the adapter at kill time; here we report none known.
    findings["residual_processes"] = []
    task = store.get(task_id) if store else None
    findings["task_status"] = task["status"] if task else None
    if findings["receipt_complete"] and findings["artifact_exists"]:
        findings["recommendation"] = "recover_to_succeeded"
    elif not findings["artifact_exists"] and not findings["receipt_complete"]:
        findings["recommendation"] = "retry_queued_or_failed_final"
    else:
        findings["recommendation"] = "needs_human"
    return findings
