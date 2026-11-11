# Operations

## Start and stop

There is no daemon. Each command is one process:

```bash
python3 muse_orchestrate.py health     # version + gate + DB checks
python3 muse_orchestrate.py run "..."  # submit one task
python3 muse_orchestrate.py tasks      # list recent tasks
python3 muse_orchestrate.py receipts <task_id>
```

Stop a run with Ctrl-C; the adapter kills the child process group,
so no orphan CLI keeps running. Verify with `ps` if a run was killed
externally.

## Enabling external calls (default off)

External CLI calls (agy, Grok, real Codex) are **off by default**.
With the template config — or with no `config/config.json` at all —
`run` handles simple tasks locally and refuses delegation with
`external_calls_disabled`. No agent is called, no task row is
recorded, and no receipt is written.

To enable delegation, copy the template and set the flag:

```bash
cp config/config.json.example config/config.json   # if not done yet
# edit config/config.json: "external_calls_enabled": true
```

Rules:

- Only a literal JSON `true` enables. `"true"`, `1`, a missing
  file, unreadable file, invalid JSON, or a non-object top level
  all mean off (fail-closed).
- Enabling this flag does **not** bypass the security gate or the
  pinned version check: both still run before any call. A PII
  prompt is still `security_blocked`; a version drift is still
  `needs_human` with `VERSION_DRIFT`.
- Local handling (`muse_direct`) and the Codex simulated-image path
  never need this flag.
- The pre-existing `state/DISABLED` rollback flag (`rollback` /
  `enable` commands) is separate and still refuses everything,
  including local handling, while set.
- Real Codex image generation needs **both** this flag and the
  separate real-image flag (`adapter_codex.configure_real_image()`),
  which is process-local and off by default (see below).

## Health checks

```bash
python3 muse_orchestrate.py health
python3 -m unittest discover -s tests        # offline suite, mocked
sqlite3 state/tasks.db "PRAGMA integrity_check;"
sqlite3 state/tasks.db "SELECT executor, status, COUNT(*) FROM tasks GROUP BY 1, 2;"
```

## Reading task state and receipts

- `tasks` lists task id, capability, executor, and status
  (`adopted`, `failed_final`, `fallback_to_muse`, `security_blocked`,
  `duplicate_of`).
- Receipts live in `state/receipts/<task_id>.attempt<N>.json` and hold
  the contract, argv, exit code, elapsed time, validation result, and
  the (truncated) response text. The full agent stdout is kept in the
  receipt's `stdout` field when the CLI returns a JSON envelope.
- Receipts are append-only: never edit or delete them to "fix" a task.

## Failure troubleshooting

| Symptom | Meaning | Action |
|---|---|---|
| `external_calls_disabled` | External calls are off (default; missing/invalid config, or flag not literally `true`) | Set `"external_calls_enabled": true` in `config/config.json` if delegation is intended. Local handling and simulated images are unaffected. |
| `security_blocked` | Gate hit (PII, credential path, production path, external send) | Do not rephrase to evade the gate. Remove the sensitive content. |
| `TIMEOUT` after 130 s | CLI exceeded the hard timeout | Check the task is small enough; the orchestrator cannot run the ~240 s Grok daily discovery job. |
| `429` / rate limit | Provider throttled the call | Wait; do not retry in a loop. Breaker state is visible in the DB. |
| `fallback_to_muse` | Agent failed and MUSE answered directly | Read the receipt's error category before resubmitting. |
| `duplicate_of` | Same idempotency key already ran | Find the original task id; do not force a re-run. |

## Backups and rollback

```bash
# Back up code + config + database before any change
cp -a src config muse_orchestrate.py VERSION <backup-dir>/
sqlite3 state/tasks.db ".backup <backup-dir>/tasks.db"
sha256sum <backup-dir>/* > <backup-dir>/SHA256SUMS
```

Rollback is code-only: restore the previous code/config/Skill files
and keep the current database (schema is stable across these
versions; tasks recorded by the newer code remain valid). Never
restore an old database over a newer one to "undo" a deployment.

## Upgrades

1. Back up as above. 2. Replace code. 3. Run the offline test suite.
4. Run `health`. 5. Submit one small direct task before any real
agent task.

## Logs

Runtime logs are local files only. Never publish raw logs: receipts
and logs can contain prompt text. Rotate by deleting old
`state/receipts` files only after exporting a sanitized summary, and
never delete the database to hide a failure.

## Real image generation (Codex H5, controlled use only)

Real generation is **off by default** and stays off after every run.
It additionally requires `external_calls_enabled: true` in
`config/config.json` (see "Enabling external calls" above). To run
one controlled generation:

1. Confirm the request is approved and contains no real personal data.
2. Set `"external_calls_enabled": true` in `config/config.json`.
3. Enable the flag for that single process invocation only
   (see `adapter_codex.configure_real_image()`; the flag is a
   process-local variable, never persisted).
4. Verify the receipt shows one attempt, a validated PNG, and the
   flag back to off afterwards.

There is no long-term enable mode in this release: no persisted
quota, no concurrency limit, and no auto-retry of real generation.
Long-term enablement needs the six capabilities listed in
`docs/RELEASE.md` plus a separate authorization.

## Boundaries

- Do not auto-retry a failed real agent call. One task, one
  submission; a second attempt needs a new, explicit decision.
- Do not switch executors after a failure (no silent agy↔Grok
  fallback).
- Do not edit the pinned CLI versions in `src/router.py` without
  re-running the full suite.
- Do not point the orchestrator at a production database from a
  test checkout, and never run tests against the live database.
