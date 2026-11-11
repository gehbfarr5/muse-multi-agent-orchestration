# Security

## Threat model

This system delegates natural-language tasks to external CLI agents
that run with the operator's own credentials and filesystem access.
The gate and the CLI permission flags reduce risk; they do not remove
it. Treat every delegated task as handing the prompt — and anything
the agent can read — to a third-party model service.

## Sensitive-data gate

- The gate blocks phone numbers, ID numbers, credential file names
  (`id_rsa`, `id_ed25519`, `.pem`, `credentials.json`,
  `client_secret`, OAuth token files), path traversal, `~`/`$HOME`
  path forms, absolute paths outside the allowed roots, and indirect
  requests for sensitive files.
- Blocked values are never stored: audit records keep the rule id
  only.
- **Known gap (R-7):** the gate is pattern-based. It can miss
  sensitive data in unusual spellings, encoded forms, or languages
  outside its patterns. A clean gate result is not proof that a
  prompt is safe. Operators must still review prompts that may
  contain personal data.

## Agent permission boundaries

| Agent | Boundary |
|---|---|
| agy | Deny-all session settings; no tools, no network for text tasks. |
| Grok | `web_search`/`web_fetch` only; `Agent`, `search_tool`, `use_tool` disallowed; `Fetch(*)` and `Read(*)` denied; no subagents; max 3 turns. No account actions (post/like/follow) are ever requested. |
| Codex | Sandboxed execution; work dir must be inside the operator workspace. **Read boundary is NONE**: Codex can read files outside the work dir (R-1). Do not route tasks that reference sensitive files. |

## External-model call risks

- Prompts and retrieved public content are sent to the model
  provider. Never include customer data, credentials, or private
  internal addresses.
- Model output is validated (non-empty, schema, PNG checks) but not
  trusted: an agent's self-reported success is never used as the
  success signal.
- The 130 s hard timeout bounds a runaway call; a 429 is treated as
  a failure, not retried in a loop.

## CLI security limits

- CLI-level restrictions are not an OS sandbox. There is no
  kernel/user-namespace isolation in this release (R-6: low-privilege
  UID/userns verification was blocked by the platform and is
  unverified).
- Pinned CLI versions are enforced; a missing or mismatched CLI
  fails closed to MUSE handling instead of running an unknown binary.
- The image-generation flag is process-local and defaults to off;
  task parameters, missing config, or invalid config cannot enable it.

## Public deployment notes

- Run on a machine whose home directory contains no unrelated
  credentials in reachable paths, or tighten the allowed path roots
  in `src/approval.py` first.
- Keep `state/` (SQLite DB, receipts, work dirs) out of version
  control (see `.gitignore`) and out of any published artifact.
- Do not publish raw receipts or logs; they can contain prompt text.
- The Grok daily/weekly schedules are separate and must not be
  recreated from this repository without their own review.

## Reporting security issues

Open a GitHub issue describing the issue **without including any
secret, credential, token, customer data, or private path**. If the
issue involves a live credential, rotate it first through the
provider, then report.
