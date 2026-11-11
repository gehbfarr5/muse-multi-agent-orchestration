#!/usr/bin/env python3
"""MUSE multi-agent orchestration — unified CLI entry (R3).

Usage:
    python3 muse_orchestrate.py run "把这句中文改写为自然日语：今天天气很好。"
    python3 muse_orchestrate.py health
    python3 muse_orchestrate.py receipts <task_id>
    python3 muse_orchestrate.py tasks
    python3 muse_orchestrate.py approve-image <task_id> --confirm
    python3 muse_orchestrate.py rollback --reason "..."
    python3 muse_orchestrate.py enable

The entry never replaces the production CLI calls: it is an additive
layer. `rollback` disables delegation (state/DISABLED); the old direct
invocation path keeps working. `enable` removes the flag (manual, by the
user or MUSE only after the cause is fixed).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import orchestrator
from src.store import TaskStore


def _out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(prog="muse_orchestrate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="submit a natural-language task")
    p_run.add_argument("text", help="natural-language task (fictional data in tests)")
    p_run.add_argument("--input-text", default="")

    sub.add_parser("health", help="executor versions, DB integrity, gate self-test")

    p_rec = sub.add_parser("receipts", help="show the latest receipt for a task")
    p_rec.add_argument("task_id")

    sub.add_parser("tasks", help="list tasks in the SQLite store")

    p_appr = sub.add_parser("approve-image", help="approve a shown image draft")
    p_appr.add_argument("task_id")
    p_appr.add_argument("--confirm", action="store_true",
                        help="explicit user confirmation (required)")

    p_rb = sub.add_parser("rollback", help="disable framework delegation")
    p_rb.add_argument("--reason", default="manual rollback")

    sub.add_parser("enable", help="re-enable framework delegation after a rollback")

    args = ap.parse_args()
    base = os.path.dirname(os.path.abspath(__file__))

    if args.cmd == "run":
        _out(orchestrator.submit(args.text, input_text=args.input_text, base=base))
    elif args.cmd == "health":
        _out(orchestrator.health(base))
    elif args.cmd == "receipts":
        _out(orchestrator.query_receipt(args.task_id, base))
    elif args.cmd == "tasks":
        store = TaskStore(os.path.join(base, "state", "tasks.db"))
        rows = store.conn.execute(
            "SELECT task_id, executor, capability, status FROM tasks "
            "ORDER BY task_id").fetchall()
        store.close()
        _out([{"task_id": r[0], "executor": r[1], "capability": r[2], "status": r[3]}
              for r in rows])
    elif args.cmd == "approve-image":
        if not args.confirm:
            print("refusing: approve-image requires --confirm (explicit user approval)",
                  file=sys.stderr)
            sys.exit(2)
        _out(orchestrator.approve_image(args.task_id, base=base))
    elif args.cmd == "rollback":
        path = orchestrator.set_disabled(base, args.reason)
        _out({"ok": True, "disabled_flag": path, "reason": args.reason,
              "note": "delegation refused until `enable`; direct CLI calls unaffected"})
    elif args.cmd == "enable":
        _out({"ok": orchestrator.clear_disabled(base)})


if __name__ == "__main__":
    main()
