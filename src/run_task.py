"""Orchestration entry: Router -> Approval Gate -> Store -> Adapter ->
Validator -> Recovery, with receipts on disk.

This is the only module that wires the six components together.
`executor_fn(contract, prompt, cwd)` is injected so tests can substitute
a fake executor; production calls use the real adapter per executor.
"""
import json
import os
import time

from . import approval, recovery, validator
from .store import DuplicateTask


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())


def _scrub(obj):
    """Drop fields that must never be stored (Grok thought, secrets)."""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()
                if k not in ("thought",)}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def execute_task(store, contract, prompt, work_root, receipts_root,
                 executor_fn, version_fn, sleep_fn=time.sleep):
    """Run one task end to end. Returns (final_status, result_contract).

    version_fn(executor) -> (version_string, ok_bool). A drift refuses
    execution and routes to needs_human (VERSION_DRIFT).
    """
    task_id = contract["task_id"]
    work_dir = os.path.join(work_root, task_id)
    os.makedirs(work_dir, exist_ok=True)
    receipt_path = os.path.join(receipts_root, f"{task_id}.json")

    # 1. Approval gate (+ R2-D unified security screen on contract + prompt)
    decision, reason = approval.gate(contract, prompt=prompt)
    if decision != "allow":
        try:
            store.create_task(contract)
        except DuplicateTask as dup:
            # Same blocked submission twice: report the first receipt.
            return "duplicate_of", {"error_category": "DUPLICATE_REQUEST",
                                    "duplicate_of": dup.task_id,
                                    "gate_decision": decision,
                                    "gate_reason": reason}
        target = {"needs_human": "needs_human",
                  "forbidden": "failed_final",
                  "security_blocked": "failed_final"}.get(decision, "failed_final")
        if decision == "security_blocked":
            # Desensitized audit record: rule id + decision only. Never the
            # prompt, never the matched value.
            audit = {"task_id": task_id, "decision": "SECURITY_BLOCKED",
                     "rule": reason.split(":", 2)[1] if reason.startswith("SECURITY_BLOCKED:") else "UNKNOWN",
                     "executor": contract.get("executor"),
                     "capability": contract.get("capability"),
                     "at": _now()}
            audit_path = os.path.join(receipts_root, f"{task_id}.security.json")
            with open(audit_path, "w") as f:
                json.dump(audit, f, ensure_ascii=False, indent=2)
            os.chmod(audit_path, 0o600)
            store.transition(task_id, "running", "gate decision")
            store.transition(task_id, "failed", reason)
            store.transition(task_id, "failed_final", reason)
            return target, {"error_category": "SECURITY_BLOCKED",
                            "gate_decision": decision, "gate_reason": reason,
                            "security_audit": audit_path}
        if decision == "needs_human":
            # queued -> running -> unknown -> investigating -> needs_human
            store.transition(task_id, "running", "gate: awaiting human")
            store.transition(task_id, "unknown", reason)
            store.transition(task_id, "investigating", reason)
            store.transition(task_id, "needs_human", reason)
        else:
            store.transition(task_id, "running", "gate decision")
            store.transition(task_id, "failed", reason)
            store.transition(task_id, "failed_final", reason)
        return target, {"error_category": "PERMISSION_DENIED" if decision == "forbidden" else None,
                        "gate_decision": decision, "gate_reason": reason}

    # 2. Breaker check: a tripped executor refuses new work (manual probe only).
    # Must run BEFORE create_task (the task is created exactly once).
    br = store.breaker(contract["executor"])
    if br.get("tripped"):
        try:
            store.create_task(contract)
        except DuplicateTask as dup:
            return "duplicate_of", {"error_category": "DUPLICATE_REQUEST",
                                    "duplicate_of": dup.task_id,
                                    "gate_decision": "breaker_tripped",
                                    "gate_reason": "executor circuit breaker is tripped"}
        store.transition(task_id, "running", "breaker tripped: refused")
        store.transition(task_id, "failed", "circuit breaker tripped")
        store.transition(task_id, "fallback_to_muse",
                         "breaker tripped; MUSE takes over; manual probe required to reset")
        return "fallback_to_muse", {"error_category": None,
                                    "gate_decision": "breaker_tripped",
                                    "gate_reason": "executor circuit breaker is tripped"}

    # 3. Idempotency: duplicate submission returns the first receipt, no re-run.
    try:
        store.create_task(contract)
    except DuplicateTask as dup:
        first = store.get(dup.task_id)
        result = json.loads(first["result_json"]) if first and first["result_json"] else {}
        result["error_category"] = "DUPLICATE_REQUEST"
        result["duplicate_of"] = dup.task_id
        return "duplicate_of", result

    # 3. Version drift check before any execution.
    actual_version, _ok = version_fn(contract["executor"])
    if actual_version == "NOT_INSTALLED":
        # R5-A: a missing CLI binary is unavailability, not drift.
        # MUSE takes over instead of parking the task in needs_human.
        store.claim(task_id)  # queued -> running (claim already did this)
        store.transition(task_id, "failed", "executor binary not installed")
        store.transition(task_id, "fallback_to_muse",
                         "executor not installed; MUSE takes over")
        return "fallback_to_muse", {"error_category": "SPAWN_FAILED",
                                    "pinned": contract["executor_version_pinned"],
                                    "actual": actual_version}
    if actual_version != contract["executor_version_pinned"]:
        store.claim(task_id)  # queued -> running (claim already did this)
        store.transition(task_id, "unknown", "version drift")
        store.transition(task_id, "investigating", "version drift")
        store.transition(task_id, "needs_human",
                         f"VERSION_DRIFT pinned={contract['executor_version_pinned']} actual={actual_version}")
        return "needs_human", {"error_category": "VERSION_DRIFT",
                               "pinned": contract["executor_version_pinned"],
                               "actual": actual_version}

    # 4. Claim + execute with retry budget.
    if not store.claim(task_id):
        return store.get(task_id)["status"], {"error_category": "DUPLICATE_REQUEST"}
    retries_used = 0
    last_result = None
    attempt = 0
    while True:
        attempt += 1
        raw = executor_fn(contract, prompt, work_dir)
        raw["_blob"] = (raw.get("stdout", "") or "") + "\n" + (raw.get("stderr", "") or "")
        result = validator.validate(contract, raw)
        # R3: carry adapter extras (image artifact, response text) into the
        # result so the orchestrator summary and receipt can reference them.
        for key in ("artifact_path", "image_state", "image_check"):
            if key in raw:
                result[key] = raw[key]
        _resp = raw.get("response_text")
        if contract["executor"] == "grok":
            _resp = (raw.get("parsed") or {}).get("text", "")
        if _resp:
            result["response_text"] = _resp[:2000]
        # _blob is an internal diagnostic (raw stdout+stderr) used only by
        # recovery.decide(); it must never be persisted (it can contain
        # Grok `thought`). Strip it before receipt/DB writes.
        persist_result = {k: v for k, v in result.items() if k != "_blob"}
        # one receipt file per attempt (R2A FAILURE-RECOVERY s5)
        attempt_receipt = os.path.join(
            receipts_root, f"{task_id}.attempt{attempt}.json")
        result["receipt_reference"] = attempt_receipt
        receipt_path = attempt_receipt
        # receipt: full raw stdout/stderr, scrubbed of thought.
        # Grok stdout is a JSON envelope that may contain `thought`
        # (reasoning text); it is stripped before the receipt is written.
        receipt_stdout = raw.get("stdout", "")
        if contract["executor"] == "grok":
            try:
                env = json.loads((receipt_stdout or "").strip())
                env.pop("thought", None)
                receipt_stdout = json.dumps(env, ensure_ascii=False)
            except (json.JSONDecodeError, AttributeError):
                receipt_stdout = raw.get("stdout", "")
        receipt = {"task_id": task_id, "contract": _scrub(contract),
                   "argv": raw.get("argv"), "rc": raw.get("rc"),
                   "stdout": receipt_stdout, "stderr": raw.get("stderr", ""),
                   "elapsed_ms": raw.get("elapsed_ms", 0),
                   "result": _scrub(persist_result), "at": _now()}
        with open(receipt_path, "w") as f:
            json.dump(receipt, f, ensure_ascii=False, indent=2)
        os.chmod(receipt_path, 0o600)
        last_result = result
        store.set_result(task_id, persist_result, receipt_path)

        decision_r = recovery.decide(contract, result, retries_used, store)
        action = decision_r["action"]
        if action == "adopt":
            store.record_success(contract["executor"])
            store.transition(task_id, "succeeded", "validated")
            store.transition(task_id, "adopted", "MUSE adopted")
            return "adopted", result
        if action == "retry":
            retries_used += 1
            store.record_failure(contract["executor"])
            if decision_r["wait_seconds"]:
                sleep_fn(min(decision_r["wait_seconds"], 60))
            store.transition(task_id, "failed", f"{result.get('error_category')} retry {retries_used}")
            store.transition(task_id, "retry_queued", decision_r["reason"])
            store.transition(task_id, "running", "retry attempt")
            continue
        if action == "fallback_to_muse":
            store.transition(task_id, "failed", result.get("error_category") or "fallback")
            store.transition(task_id, "fallback_to_muse", decision_r["reason"])
            return "fallback_to_muse", result
        if action == "needs_human":
            if result.get("error_category") == "AUTH_REQUIRED":
                store.trip(contract["executor"], "auth required")
            store.transition(task_id, "failed", result.get("error_category") or "needs_human")
            store.transition(task_id, "failed_final", decision_r["reason"])
            return "needs_human", result
        if action == "fail_final":
            if result.get("error_category") == "RATE_LIMITED":
                store.trip(contract["executor"], "quota exhausted")
            store.transition(task_id, "failed", result.get("error_category") or "fail_final")
            store.transition(task_id, "failed_final", decision_r["reason"])
            return "failed_final", result
        # duplicate
        return "duplicate_of", result
