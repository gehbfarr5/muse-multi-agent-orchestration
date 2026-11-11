# Release notes — v1.0.0-rc1

## Baseline

- Candidate version: `1.0.0-rc1`, published under the [MIT License](../LICENSE).
- Production baseline this candidate was built from: Git HEAD
  `6b84dbf`, regression 205/205, 32 production tasks, 32 receipts
  (2026-11-11).
- The candidate is a sanitized copy of that baseline: machine-specific
  paths replaced with home-relative ones, stale test artifacts
  removed, production data excluded, and a fresh Git history (the
  production history was not imported).

## What changed vs the production tree (candidate-only)

| Change | Reason |
|---|---|
| `src/approval.py`: allowed path roots computed from `~` | Remove machine-specific home paths |
| `tests/test_gate_hardening_r3.py`: allowed-path test uses `~` | Same; blocked-path fixtures unchanged |
| `tests/test_sandbox_probe.py`: mock fixture home `→ /home/example` | Remove operator home reference |
| `config/config.json.example`: `base_dir` generalized | Template, not a production path |
| `VERSION`: `0.1.0` → `1.0.0-rc1` | Candidate version label |
| Added: `README.md`, `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, `docs/SECURITY.md`, `docs/RELEASE.md`, `docs/CHANGELOG.md`, `.gitignore`, `skills/muse_orchestration/` | Public documentation and Skill |
| Excluded: `state/`, `work/`, `__pycache__/`, `tests/TEST-RESULTS.log`, R3-era internal reports, ISSUE-03/04 patches | Production data / stale / out of scope |

## What changed in the R7-E re-verification (candidate-only)

| Change | Reason |
|---|---|
| New `src/config.py`: `config/config.json` loading, `external_calls_enabled` (literal `true` only), `requires_external_call()` | The formal entry never read the config; external calls are now default-off (R7-E item 2) |
| `src/orchestrator.py`: explicit enable check in `submit()` before any executor runs | Default/missing/invalid config → `external_calls_disabled`, zero calls; local handling and simulated image unaffected; gate and version check still run when enabled |
| `config/config.json.example`: `external_calls_enabled: false` + explanation | Template documents the new flag |
| `SECURITY.md` moved to the repository root (full policy); `docs/SECURITY.md` is now a pointer | GitHub reads the root `SECURITY.md` as the security policy; the remote root file is replaced by this full policy (see the R7-E release diff) |
| New `requirements-test.txt`: `Pillow==12.2.0` | Full suite and real PNG verification need Pillow (R7-E item 1) |
| New `docs/SOURCES.md` | Skill + third-party provenance, platform support, scrub record |
| `docs/RELEASE.md` freeze matrix: real production task IDs removed | Public doc must not carry real task IDs; evidence stays in the private R7-C workspace |
| Tests: `tests/test_external_calls_default_off_r7e.py` (15 tests); `setUp` of `test_orchestrator.py`, `test_classifier_r7b1.py`, `test_image_e2e_mock_r6c.py` now writes an explicit enable config | Delegation tests opt in; default-off behavior is covered by the new test file |

No production behavior was changed by these edits; the candidate's
test suite is the acceptance check for equivalence.

## Capability freeze matrix

| Capability | Maturity | Evidence |
|---|---|---|
| MUSE direct handling (simple tasks) | PROVEN_LIVE | Production tasks + regression |
| agy Japanese writing | PROVEN_LIVE | R7-C agy acceptance task |
| agy Chinese writing + review (one call) | PROVEN_LIVE | R7-C agy acceptance task (draft + 3 review points + final) |
| Grok public X/Twitter research | PROVEN_LIVE | R7-C Grok acceptance task (3 viewpoints, 13 sources; 2 sampled links verified) |
| Codex image tasks, simulated | PROVEN_LIVE | Default path, used in production tasks |
| Codex real image generation | MOCK_VERIFIED | One controlled production run (R6-E2, 34.9 s); flag off by default; no long-term enablement |
| Task contracts, state, receipts, dedup | PROVEN_LIVE | 32/32 tasks and receipts, integrity ok |
| Breaker and failure fallback | MOCK_VERIFIED | Isolated trip/recovery verified; no live trip in production |
| Sensitive-information gate | PROVEN_LIVE | Blocks PII/credential/production paths in production; leakage surface not fully covered (R-7) |
| Grok cron migration | BLOCKED | KEEP_EXISTING_CRON: orchestrator cannot meet the ~240 s daily runtime in its 130 s limit; weekly review model/output differ |
| Low-privilege UID/userns isolation | UNVERIFIED | Platform-blocked; do not retry without new authorization (R-6) |
| Real 429 handling | UNVERIFIED | No complete real 429 sample (R-8) |
| Two-stage writing+review pipeline | DOCUMENTED_ONLY | Does not exist; the capability is one agy call |

## Clean-environment test result (verified 2026-11-11)

Two isolated runs, both from a copy of this candidate (no production
credentials, no production database, mocked agents, 0 real model calls):

- **Variant A — strict clean:** fresh home directory (no CLIs, no
  login state, no `~/workspace`), minimal environment.
  `python3 -m unittest discover -s tests` → **205 tests, OK
  (skipped=3)**. The 3 skipped tests are the CLI version-parse tests,
  which skip by design when the pinned CLI is not installed.
- **Variant B — isolated directory, operator home:** pinned CLIs
  present (agy 1.5.5, grok 1.0.77, codex 0.164.0-alpha.9).
  → **205 tests, 205 passed, 0 failed, 0 skipped.**

Candidate-only test-harness fixes were needed for Variant A (they do
not change production code or any test assertion): the suite now
creates `~/workspace` if missing, and the R6-C image E2E mock tests
stub the Codex version check through `orchestrator.EXECUTORS` — the
version check otherwise refuses with `SPAWN_FAILED` before the mocked
process runs when the CLI binary is absent. Before these fixes the
strict-clean run failed (34 setUp errors + 12 image-test failures).
One environment constraint remains by design: the operator home must
not be under `/tmp` (the Codex work-dir guard forbids it).

Note: an earlier independent R5 review (126 tests, 2 failures on a
different machine — a CLI-version test and a process-group residual
test) is superseded by this run for the current 205-test suite, but
the two underlying issues remain open on GitHub (#1, #2) until the
maintainer confirms them fixed in the published tree.

## Clean-environment test result, R7-E re-verification (2026-11-11)

Same candidate line, after the R7-E changes (default-off external
calls, Pillow declared, 15 new tests — 220 tests total):

- **Standard library only (no Pillow):** fresh home, minimal
  environment, isolated venv → **155 tests ran, FAILED
  (errors=5, skipped=3)**. The 5 errors are all `ModuleNotFoundError:
  No module named 'PIL'`: 3 test modules fail to import
  (`test_adapter_codex_real_r6c`, `test_classifier_r7b1`,
  `test_image_e2e_mock_r6c` — top-level `from PIL import Image`) and
  2 fixture tests in `test_sandbox_probe` fail at their lazy import.
  Production code itself never imports Pillow at module level
  (its two uses are lazy, inside PNG-validation functions, and
  return an error dict instead of raising).
- **With Pillow 12.2.0** (installed offline from the local wheel
  `pillow-12.2.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl`,
  `pip install --no-index --find-links <wheel-dir> "Pillow==12.2.0"`):
  same clean environment → **220 tests, OK (skipped=3)** — 217
  passed, 3 skipped. The 3 skipped tests are the CLI version-parse
  tests (`test_version_present_binary_parses`,
  `test_version_grok_present_binary_parses`,
  `test_version_codex_present_binary_parses`), which skip by design
  when the pinned CLI is not installed.
- **Real agent calls in both runs: 0** (mocked doubles only; the
  version probes in the health check are local `--version` queries,
  not agent executions).

The R7-D Variant B result (205/205 with pinned CLIs installed)
still stands for the pre-R7-E tree; the R7-E suite is a superset
(205 + 15 new default-off tests).

## Known limitations (see also docs/CHANGELOG.md)

R-1 Codex read boundary none; R-6 UID/userns unverified; R-7 gate
leakage surface; R-8 real 429 sample missing; ISSUE-03 (breaker-refusal
receipts) and ISSUE-04 (`real_image` receipt marker) are separate
candidate patches, not in this release; composite fail-closed covers
only keyword-matched research+writing combinations; Grok's 11 of 13
cited sources in R7-C were not individually verified; H5 long-term
enablement and quota management not implemented; N1–N8 migration
nodes not authorized.

## Compatibility and rollback

- Python 3.12+. Full tests and real PNG verification require
  Pillow 12.2.0 (`requirements-test.txt`). Pinned CLIs: agy 1.5.5,
  grok 1.0.77, codex 0.164.0-alpha.9.
- Supported platform: Linux x86_64, Python 3.12 (verified). macOS
  is not tested and has a known degraded residual-process scan (no
  `/proc` for zombie detection — see `docs/SOURCES.md`).
- External calls are off by default (`external_calls_enabled` in
  `config/config.json`, literal `true` only). Missing, unreadable,
  or invalid config also means off. The pre-existing
  `state/DISABLED` rollback flag is unchanged and still refuses
  all delegation, including local handling.
- Database schema is stable across the R3–R7 line; rolling back to
  the previous code keeps the current database (do not restore an old
  database over a newer one).
- This release candidate is intended for review and testing.
