# Architecture

## Entry point

`muse_orchestrate.py` is the single CLI entry. It parses a command
(`run`, `health`, `tasks`, `receipts`, `approve-image`), builds a task
contract, and hands it to `src/orchestrator.py`. There is no daemon and
no server; every run is a short-lived process.

## Pipeline

```
natural-language task
  -> src/classifier.py   (deterministic keyword rules; image intent first)
  -> src/router.py       (capability -> executor, pinned version, contract)
  -> src/approval.py     (P0–P3 gate + R2-D security screen)
  -> src/run_task.py     (execute_task: run adapter, validate, receipt)
  -> src/adapter_*.py    (agy / grok / codex CLI invocation)
  -> src/validator.py    (output rules: non-empty, schema, image PNG)
  -> src/store.py        (SQLite task state + idempotency)
  -> receipt JSON        (state/receipts/<task_id>.attempt<N>.json)
```

## Classifier

Deterministic keyword matching, no model call. Order matters: image
intent is checked first, then Japanese writing, Chinese writing/review,
X research, and finally direct handling. Composite requests that match
both a research intent and a writing intent are **fail-closed** to
direct handling with a `composite` reason instead of silently executing
only half. Rules cover natural phrasing (e.g. "写一篇…再检查一下",
"看看 X 上在讨论什么") but only a finite set of expressions; an
unmatched composite may still execute a single leg.

## Security gate (`approval.py`)

- P0 auto, P1 auto-if-validated, P2 needs human confirmation,
  P3 forbidden. External send/publish capabilities are never
  executable by an adapter.
- The R2-D screen scans the contract and the prompt with fixed
  patterns: phone/ID numbers, credential file names, path traversal,
  `~`/`$HOME` forms, absolute paths outside the allowed roots, and
  indirect requests for sensitive files ("read the private key").
- A hit returns `security_blocked` with a rule id only; the audit
  record never stores the matched value.
- Allowed path roots are computed from the operator's home directory
  at import time; everything else absolute is blocked.

## Router and contracts

`router.py` maps a capability to an executor and builds a
`TaskContract`: pinned CLI version, objective, constraints, timeout
(soft 110 s / hard 130 s), retry budget (max 1 automatic retry for
text tasks, **0 for image tasks**), and an idempotency key
(`<executor>:<capability>:<hash>`). A duplicate submission returns
`duplicate_of` instead of re-executing.

## Adapters

Each adapter builds a fixed argv, runs the CLI as a subprocess with a
process group, enforces a timeout, and parses the result without
trusting the agent's self-reported status.

- `adapter_agy.py` — deny-all session settings (0600 file, read back
  and verified); no tools, no network for text tasks.
- `adapter_grok.py` — `--tools web_search,web_fetch` allowlist,
  `--disallowed-tools Agent,search_tool,use_tool`, `--no-subagents`,
  `--max-turns 3`, and a permanent `--deny Fetch(*)` (headless Fetch
  needs approval and was cancelling runs) plus `--deny Read(*)` when
  no file input is given.
- `adapter_codex.py` — sandboxed execution; work dir must be under
  the operator's workspace; real image generation behind a
  process-local flag that defaults to **off** and cannot be enabled
  by task parameters, missing config, or invalid config. Generated
  images are discovered by directory snapshot and validated as PNGs
  (RGB/RGBA); the file is copied with a hash comparison.

## Validation, state, and recovery

- `validator.py` enforces per-capability rules (non-empty text,
  image PNG checks). A failed validation is a failed task, not a
  success with a warning.
- `store.py` keeps tasks in SQLite (WAL). History rows record every
  state transition. Receipts are append-only JSON files.
- `recovery.py` decides retry vs fallback vs breaker-trip from the
  error category. A tripped breaker routes later tasks to MUSE until
  manually reset; breaker state is read-only inspected, never
  auto-reset by a task.
- `image_approval.py` gates the use of a generated image draft: a
  draft is never shown to a user as final without explicit approval.

## What this is not

- **Not an OS sandbox.** CLI-level permission flags are the only
  isolation. Codex can read files outside the work dir (R-1).
  Low-privilege UID/userns isolation was attempted and blocked by
  the platform; it is unverified (R-6).
- **Not a cron replacement.** The Grok daily/weekly schedules are
  separate scripts and are intentionally not migrated here: the
  orchestrator cannot meet the daily job's ~240 s runtime inside its
  130 s hard timeout, and the weekly review uses a different model
  and output format.
- **Not a two-stage review pipeline.** "Chinese writing + review"
  is a single agy call whose prompt contains both instructions.
