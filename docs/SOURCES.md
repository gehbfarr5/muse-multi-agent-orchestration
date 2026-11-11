# Sources, redistribution, and platforms

## Skills

### 1. `skills/muse_orchestration/` — included (own work)

- Source: written for this project (R4); the installed formal Skill
  is the same content.
- Files: `SKILL.md` (routing rules, task-type-over-length rule),
  `skill.yaml` (metadata).
- Redistribution: part of this repository; governed by the
  repository [MIT License](../LICENSE).

### 2. `agent-reach` Skill — NOT included (third-party, referenced only)

- Source: a third-party skill installed on the operator's machine at
  `~/.agents/skills/agent-reach/SKILL.md`. Its provenance was never
  verified (it is not this project's work and its author/license are
  unknown to this repository).
- Why it is mentioned: the R4-F root cause — Grok's headless run
  loaded this skill via `Fetch`, which always needs approval in
  headless mode. The fix was an adapter-side deny (`Fetch(*)` is
  always denied for Grok), not a change to the skill.
- Redistribution basis: **none**. The skill file is not copied into
  this repository, and no part of its content is reproduced here.
  Do not copy it into a public tree without its owner's permission
  and a license check.

### Codex built-in image skill — NOT included

- `adapter_codex.py` mentions the Codex agent's built-in image tool
  ("imagegen") only as a string match for failure classification.
  No skill text is included. The Codex CLI itself is not
  redistributed (see below).

## Third-party materials

| Material | Included? | Source / redistribution basis |
|---|---|---|
| Python standard library | n/a (runtime) | PSF license; no redistribution |
| Pillow 12.2.0 | No — installed by the user for tests and real PNG verification | PyPI, HPND license; `requirements-test.txt` pins the verified version |
| agy CLI 1.5.5 | No — operator installs it | Antigravity CLI, operator's own install; code only invokes it |
| Grok CLI 1.0.77 | No — operator installs it | Operator's own install; code only invokes it |
| Codex CLI 0.164.0-alpha.9 | No — operator installs it | Operator's own install; code only invokes it |
| `agent-reach` Skill | No (see above) | Third-party; not reproduced |
| GitHub remote baseline docs (`docs/R5-SOURCE-REVIEW.md`, `docs/RELEASE-POLICY.md`) | Yes — retained from the public baseline, unchanged | Pre-existing remote files; this candidate does not modify or republish them |

No third-party source code is copied into this repository.

## Supported platforms

| Platform | Status | Evidence |
|---|---|---|
| Linux x86_64, Python 3.12 | **Verified** | Full suite 220 tests, OK (skipped=3), in a clean Linux home with Pillow 12.2.0 (2026-11-11); also verified without Pillow (5 import errors, expected) |
| macOS | **Not tested — known degraded** | See below; no macOS run was performed for this candidate |

### macOS known failures (from code reading, not a macOS run)

- `_is_zombie()` in `src/adapter_base.py` reads
  `/proc/<pid>/stat` to tell a zombie (defunct) process from a live
  one. macOS has no `/proc`, so the read always fails and the
  function conservatively returns "not a zombie". Consequence: on
  macOS the residual-process scan counts zombie children as live
  leftovers (false positives), and the Issue #2 tests
  (`test_zombie_is_not_residual_but_live_process_is`,
  `test_process_group_kill_leaves_no_residual`) are expected to
  fail there. The kill itself (`os.killpg`, `SIGTERM`/`SIGKILL`)
  is POSIX and unaffected.
- The Codex binary path default (`~/workspace/tools/codex-bin/codex`)
  points at a Linux build; a macOS install needs its own binary.
- `pgrep -g` is used by the residual scan and exists on macOS, but
  combined with the `/proc` gap above the scan result is unreliable
  there.

Until a macOS run is done, treat macOS as unsupported for the
residual-scan behavior; the rest of the suite is expected to work but
is unverified there. Do not claim macOS support in release notes.

## Scrub record (R7-E; what was removed or kept, and why)

| Item | Disposition |
|---|---|
| Real production task IDs in `docs/RELEASE.md` freeze matrix (three IDs from the R7-C acceptance run) | **Removed** — replaced with generic labels ("R7-C agy acceptance task", "R7-C Grok acceptance task"). The full evidence stays in the private R7-C workspace, not in the public tree. |
| Literal operator home path in the release notes | **Reworded** to "machine-specific home paths" — the public doc no longer names the operator's home. |
| Blocked-path inputs in `tests/test_gate_hardening_r3.py` and `tests/test_security_r2d.py` | **Generalized** — the tests use the fictional `/home/example-operator` path and still assert that the gate refuses it. |
| Task-ID format `t-20261111-<seq>` in `src/router.py` and test fixtures (`t-20261111-0001`, …) | **Kept intentionally** — it is the ID *scheme* (the router generates it for every task), not a record of a real task; fixture values are synthetic and carry no linked content. |
| `git clone https://github.com/gehbfarr5/muse-multi-agent-orchestration` in `README.md` | **Kept** — that is the intended public remote URL, not a leak. |
| `gehbfarr5` as the GitHub account name in docs | **Kept** — it is the public repository owner (already public). |
