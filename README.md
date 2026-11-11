# MUSE Multi-Agent Orchestration

A lightweight, safety-gated orchestration system for delegating selected
natural-language tasks to external CLI agents — `agy` (content), Grok
(public X/Twitter research), and Codex (image tasks) — with task
classification, an approval/security gate, SQLite task state, adapters,
output validation, receipts, and failure recovery.

> **Release status:** `v1.0.0-rc1` is a public release candidate. See
> [release notes](docs/RELEASE.md) and the [MIT License](LICENSE).

## What it does

MUSE stays the single orchestrator. For each natural-language task it:

1. Classifies the capability (direct handling, Japanese/Chinese writing,
   content review, public X research, or image generation).
2. Runs a deterministic security screen (PII, credential paths,
   production paths, external-send intents) before any delegation.
3. Routes to the matching agent through a fixed contract (pinned CLI
   version, tool allowlist, timeout, retry budget).
4. Executes the CLI, validates the output, records the task state and
   a receipt, and returns the result — or falls back to MUSE on failure.

## Verified capabilities

| Capability | Maturity |
|---|---|
| Direct MUSE handling of simple tasks | PROVEN_LIVE |
| agy Japanese writing / Chinese writing + review (single call) | PROVEN_LIVE |
| Grok public X/Twitter research | PROVEN_LIVE |
| Codex image tasks (simulated by default; real generation off) | PROVEN_LIVE (simulated) / MOCK_VERIFIED (real path, one controlled run) |
| Task contracts, state machine, receipts, dedup, breaker | PROVEN_LIVE |
| Sensitive-information gate | PROVEN_LIVE (leakage surface not fully covered — see R-7) |

See `docs/RELEASE.md` for the full freeze matrix and `docs/CHANGELOG.md`
for the R1–R7 history. "Chinese writing + review" is one agy call that
contains both instructions, not a two-stage pipeline.

## Requirements

- Python 3.12+. Text orchestration and simulated images use the Python
  standard library.
- **Full test suite and real PNG verification need Pillow 12.2.0**
  (see `requirements-test.txt`). Without it the
  suite reports 5 import errors in the image tests; with it the
  suite runs 220 tests. Install with
  `pip install -r requirements-test.txt`, or offline from a local
  wheel: `pip install --no-index --find-links <wheel-dir>
  "Pillow==12.2.0"`.
- One or more of the external CLIs, only if you want to run real tasks:
  `agy` 1.5.5, `grok` 1.0.77, `codex` 0.164.0-alpha.9. They are **not**
  bundled; the code pins these versions and refuses to run an agent
  whose CLI is missing or reports a different version.
- No network access is required for the test suite (mocked).
- **Supported platform: Linux x86_64, Python 3.12 (verified).**
  macOS is not tested and has a known degraded residual-process scan
  (no `/proc` for zombie detection — see `docs/SOURCES.md`).

## Quick start

```bash
git clone https://github.com/gehbfarr5/muse-multi-agent-orchestration
cd muse-multi-agent-orchestration
cp config/config.json.example config/config.json   # edit paths only
python3 muse_orchestrate.py health
python3 muse_orchestrate.py run "把这句中文改写为日语：今天天气很好，我去公园散步。"
python3 muse_orchestrate.py tasks
python3 muse_orchestrate.py receipts <task_id>
```

**External calls are off by default.** With the template config (or
no `config/config.json` at all), `run` handles simple tasks locally
and refuses delegation with `external_calls_disabled` — no agent is
called. To enable delegation, set `"external_calls_enabled": true`
in `config/config.json`. Enabling it does not bypass the security
gate, the pinned version check, or the separate real-image flag.
See `docs/OPERATIONS.md`.

Image tasks produce a **simulated** local PNG by default. Real Codex
image generation is off by default and requires an explicit,
per-task, operator-controlled enable — see `docs/OPERATIONS.md`.
There is no automatic retry of real generation.

## Tests

```bash
pip install -r requirements-test.txt   # Pillow 12.2.0
python3 -m unittest discover -s tests
```

The suite is offline and mocked (no real agent calls, no production
credentials, no network). Expected result with Pillow 12.2.0: **220
tests, OK (skipped=3)** — the 3 skipped tests are the CLI
version-parse tests, which skip by design when the pinned CLI is not
installed. Without Pillow the suite reports 5 import errors in the
image tests (3 modules fail to import, 2 fixture tests fail); the
production baseline ran 205/205; the candidate's own
clean-environment result is recorded in `docs/RELEASE.md` and may
differ if the environment lacks the pinned CLIs — version-dependent
tests report `NOT_INSTALLED` instead of passing silently.

## Safety boundaries (read first)

- Never send real customer personal data to an external CLI. The gate
  blocks phone numbers, ID numbers, credential paths, and production
  paths, but its coverage is not complete (R-7).
- CLI-level permission restrictions are **not** an OS sandbox. Codex
  has no read boundary (R-1); low-privilege UID/userns isolation is
  unverified (R-6).
- Existing production schedules (Grok crons) are out of scope and must
  not be changed by this repository.
- Do not commit tokens, OAuth credentials, CLI login state, production
  SQLite databases, receipts, or raw runtime logs (see `.gitignore`).

Full details: `SECURITY.md`, `docs/ARCHITECTURE.md`,
`docs/OPERATIONS.md`, `docs/SOURCES.md`.
