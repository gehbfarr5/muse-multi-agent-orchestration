"""R3 unified orchestration entry: the single interface MUSE calls.

Pipeline (R3 §三, 10 steps):
  1. receive a natural-language task
  2. classify capability + risk (classifier, deterministic)
  3. decide MUSE-direct vs delegate (no match -> direct; no executor
     may choose another executor)
  4. select agy / Grok / Codex via the router
  5. pre-execution authorization + security gate (approval.py)
  6. record the task in SQLite
  7. call the adapter
  8. validate the result
  9. handle failure / breaker (recovery.py)
  10. return the final result + traceable receipt reference

Rollback: when state/DISABLED exists, the entry refuses delegation and
reports framework_disabled. The framework is additive — disabling it
restores the old direct-CLI path, which is never modified.
"""
import json
import os

from . import adapter_agy, adapter_codex, adapter_grok, approval, image_approval
from .classifier import classify
from .router import route
from .run_task import execute_task
from .store import TaskStore

BASE = os.path.expanduser("~/workspace/muse-orchestration-r3")
DEFAULT_DB = os.path.join(BASE, "state", "tasks.db")
DEFAULT_WORK = os.path.join(BASE, "work")
DEFAULT_RECEIPTS = os.path.join(BASE, "state", "receipts")
DISABLED_FLAG = os.path.join(BASE, "state", "DISABLED")

EXECUTORS = {
    "agy": (adapter_agy.run, adapter_agy.version),
    "grok": (adapter_grok.run, adapter_grok.version),
    "codex": (adapter_codex.run, adapter_codex.version),
}


def _normalized_version(version_fn):
    """Wrap an adapter version() so it returns (bare_version, ok_bool).

    Adapter version()s return (full stdout, rc); the pinned comparison in
    run_task needs the bare number (e.g. "1.5.5", "0.164.0-alpha.9").
    """
    import re as _re

    def wrapped(_executor=None):
        raw, rc = version_fn()
        m = _re.search(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?", raw or "")
        return (m.group(0) if m else (raw or "").strip(), rc == 0)

    return wrapped


# EXECUTORS maps to (run, normalized_version) — run_task compares the bare
# version against the pinned version in the contract.
EXECUTORS = {name: (run_fn, _normalized_version(ver_fn))
             for name, (run_fn, ver_fn) in EXECUTORS.items()}


def is_disabled(base=BASE):
    return os.path.exists(os.path.join(base, "state", "DISABLED"))


def set_disabled(base=BASE, reason="manual rollback"):
    path = os.path.join(base, "state", "DISABLED")
    with open(path, "w") as f:
        f.write(reason + "\n")
    os.chmod(path, 0o600)
    return path


def clear_disabled(base=BASE):
    path = os.path.join(base, "state", "DISABLED")
    if os.path.exists(path):
        os.remove(path)
    return not os.path.exists(path)


def submit(text, input_text="", store=None, base=BASE, task_seq=None):
    """Submit one natural-language task. Returns a summary dict.

    task_seq defaults to (tasks already in the store + 1) so two CLI
    submissions never share a task_id. Direct tasks return
    handled_by=muse_direct and are NOT recorded in the task DB (nothing
    was delegated). Delegated tasks are recorded with a receipt reference.
    """
    work_root = os.path.join(base, "work")
    receipts_root = os.path.join(base, "state", "receipts")
    os.makedirs(work_root, exist_ok=True)
    os.makedirs(receipts_root, exist_ok=True)
    own_store = store is None
    if own_store:
        store = TaskStore(os.path.join(base, "state", "tasks.db"))
    try:
        if is_disabled(base):
            return {"handled_by": "framework_disabled",
                    "status": "refused",
                    "reason": "orchestration entry is disabled (rollback flag); "
                              "use the direct CLI invocation instead"}
        cls = classify(text)
        if cls["decision"] == "muse_direct":
            return {"handled_by": "muse_direct",
                    "status": "direct",
                    "capability": cls["capability"],
                    # R7-B1: surface the classifier's own reason (e.g. a
                    # research + writing composite) instead of the generic
                    # no-match text, so the refusal is traceable.
                    "reason": cls.get("reason") or "no delegation rule matched; MUSE handles it directly"}
        capability = cls["capability"]
        kind = "image" if capability == "image_generate" else "text"
        if task_seq is None:
            task_seq = store.conn.execute(
                "SELECT COUNT(*) FROM tasks").fetchone()[0] + 1
        contract = route(capability, text, input_text or text, kind=kind,
                         task_seq=task_seq)
        if contract.get("security_blocked"):
            return {"handled_by": "security_blocked", "status": "refused",
                    "capability": capability,
                    "reason": contract.get("reason")}
        if contract.get("muse_direct"):
            return {"handled_by": "muse_direct", "status": "direct",
                    "capability": capability,
                    "reason": contract.get("reason")}
        # R7-E: external calls are default-off. The config file is read
        # here, at the formal entry, before any executor is invoked.
        # Local handling already returned above; the simulated-image
        # path makes no external call and is not gated by this flag.
        # When enabled, execute_task still runs the security gate and
        # the pinned version check before any call.
        from . import config as _config
        from .adapter_codex import is_real_image_enabled as _real_image
        cfg, cfg_error = _config.load_config(base)
        if _config.requires_external_call(contract, _real_image()):
            if not _config.external_calls_enabled(cfg):
                return {"handled_by": "external_calls_disabled",
                        "status": "refused",
                        "capability": capability,
                        "reason": ("external calls are disabled by default; "
                                   "set \"external_calls_enabled\": true in "
                                   "config/config.json to enable them"
                                   + (f" ({cfg_error})" if cfg_error else ""))}
        executor_fn, version_fn = EXECUTORS[contract["executor"]]
        status, result = execute_task(
            store, contract, text, work_root, receipts_root,
            executor_fn, version_fn)
        summary = {"handled_by": contract["executor"],
                   "status": status,
                   "task_id": contract["task_id"],
                   "capability": capability,
                   "receipt_reference": result.get("receipt_reference"),
                   "error_category": result.get("error_category"),
                   "response_text": (result.get("response_text") or "")[:500]}
        # Image lifecycle: an adopted draft is recorded and marked shown;
        # it becomes usable only after explicit user approval.
        if capability == "image_generate" and status == "adopted":
            artifact = result.get("artifact_path")
            if artifact:
                image_approval.record_draft(store.conn, contract["task_id"], artifact)
                image_approval.transition(store.conn, contract["task_id"], "shown_to_user")
                summary["artifact_path"] = artifact
                summary["image_state"] = "shown_to_user"
                summary["image_usable"] = False
                summary["note"] = ("draft shown to user; approve explicitly with "
                                   "approve-image --confirm before use")
        return summary
    finally:
        if own_store:
            store.close()


def approve_image(task_id, store=None, base=BASE):
    """Approve a shown image draft. Requires the draft to be shown first."""
    own_store = store is None
    if own_store:
        store = TaskStore(os.path.join(base, "state", "tasks.db"))
    try:
        rec = image_approval.get(store.conn, task_id)
        if not rec:
            return {"ok": False, "error": f"no image draft for task {task_id}"}
        image_approval.transition(store.conn, task_id, "approved_for_use")
        return {"ok": True, "task_id": task_id,
                "artifact_path": rec["artifact_path"],
                "state": "approved_for_use",
                "usable": True}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    finally:
        if own_store:
            store.close()


def health(base=BASE):
    """Health check: executor versions, DB integrity, gate self-test, dirs."""
    checks = {}
    for name, (run_fn, version_fn) in EXECUTORS.items():
        try:
            ver, ok = version_fn()
            checks[f"{name}_version"] = {"version": ver, "ok": bool(ok)}
        except Exception as e:  # noqa: BLE001 - health must report, not raise
            checks[f"{name}_version"] = {"version": None, "ok": False, "error": str(e)}
    db_path = os.path.join(base, "state", "tasks.db")
    try:
        store = TaskStore(db_path)
        integrity = store.conn.execute("PRAGMA integrity_check").fetchone()[0]
        checks["db_integrity"] = {"ok": integrity == "ok", "detail": integrity}
        store.close()
    except Exception as e:  # noqa: BLE001
        checks["db_integrity"] = {"ok": False, "error": str(e)}
    # Gate self-test: a clean prompt passes, a PII prompt is blocked.
    from .router import route as _route
    clean = _route("jp_copywriting", "把中文改写为自然日语",
                   "虚构样本：今天天气很好。", task_seq=999)
    blocked = _route("jp_copywriting", "虚构联系测试", "虚构样本：无。", task_seq=998)
    d_clean, _ = approval.gate(clean, prompt="把这句中文改写为自然日语：今天天气很好。")
    d_block, _ = approval.gate(blocked, prompt="请联系 13800000000 确认")
    checks["gate_self_test"] = {"clean_allows": d_clean == "allow",
                                "pii_blocks": d_block == "security_blocked",
                                "ok": d_clean == "allow" and d_block == "security_blocked"}
    for d in ("work", "state/receipts"):
        p = os.path.join(base, d)
        checks[f"dir_{d}"] = {"ok": os.path.isdir(p) and os.access(p, os.W_OK)}
    checks["framework_disabled"] = {"disabled": is_disabled(base)}
    overall = all(v.get("ok", True) for v in checks.values()
                  if isinstance(v, dict))
    return {"ok": overall, "checks": checks}


def query_receipt(task_id, base=BASE):
    """Return the latest receipt (or security audit) for a task id."""
    receipts_root = os.path.join(base, "state", "receipts")
    matches = sorted(f for f in os.listdir(receipts_root)
                     if f.startswith(task_id + "."))
    if not matches:
        return {"ok": False, "error": f"no receipt for {task_id}"}
    latest = matches[-1]
    with open(os.path.join(receipts_root, latest)) as f:
        return {"ok": True, "file": latest, "receipt": json.load(f)}
