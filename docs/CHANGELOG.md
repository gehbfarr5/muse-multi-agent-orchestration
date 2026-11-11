# Changelog

## v1.0.0-rc1, R7-E re-verification — 2026-11-11 (release candidate)

Same version label (`1.0.0-rc1`), same production baseline
(`6b84dbf`); candidate-only changes for the R7-E re-verification:

- External calls are now default-off at the formal entry (new
  `src/config.py`; `external_calls_enabled` in `config/config.json`,
  literal `true` only). Default, missing, and invalid configs
  produce zero agy/Grok/real-Codex calls; local handling and the
  simulated-image path are unaffected; the security gate and pinned
  version check still run when enabled. H5 real-image flag stays
  separately default-off.
- Pillow declared: full suite and real PNG verification need
  Pillow 12.2.0 (`requirements-test.txt`). Standard
  library only: 5 import errors (3 modules + 2 fixture tests);
  with Pillow 12.2.0: 220 tests, OK (skipped=3), 0 real agent calls.
- Final file tree fixed: root `SECURITY.md` carries the full policy
  (`docs/SECURITY.md` is now a pointer); `.gitignore` adds the
  `state/` and `work/` rules the remote lacks; remote historical
  docs (`docs/R5-SOURCE-REVIEW.md`, `docs/RELEASE-POLICY.md`)
  retained untouched; real production task IDs removed from public
  docs; new `docs/SOURCES.md` records Skill/third-party provenance
  and platform support (Linux verified; macOS not tested, residual
  scan degraded — no `/proc` for zombie detection).
- Regression 217 passed and 3 skipped (220 tests total) in a clean
  Linux environment with Pillow 12.2.0.

## v1.0.0-rc1 — 2026-11-11 (release candidate)

Public release candidate built from production HEAD `6b84dbf`.

- Two P1 natural-language routing fixes (R7-B1/B2): Chinese
  writing+review and X/Twitter research now route to agy/Grok from
  natural phrasing; composite research+writing requests fail closed.
- Live acceptance (R7-C): one real agy task and one real Grok task
  through the production entry, both adopted on the first attempt.
- Sanitized for publication: machine-specific paths removed,
  production data excluded, fresh Git history, public docs added.
- Regression 205/205 in production and in a clean environment.

## R7 — 2026-11-11

- R7-A: production operations review; Grok cron migration decided
  `KEEP_EXISTING_CRON`.
- R7-B1: isolated fix for ISSUE-01/02 (regression 207/207 in
  isolation, including ISSUE-03/04 changes).
- R7-B2: ISSUE-01/02 deployed to production (HEAD `6b84dbf`);
  ISSUE-03/04 stripped to separate patches, not deployed.
- R7-C: live acceptance `R7C_LIVE_ACCEPTANCE_PASS`.

## R6 — 2026-11-11

- R6-B: first authorized real Codex image generation; classifier
  gap found for English image prompts.
- R6-C: H5 wiring — image classifier, real-image adapter behind a
  default-off flag, snapshot discovery, PNG validation, image retry
  budget 0.
- R6-D: isolated end-to-end real generation passed.
- R6-E1: H5 code deployed to production, flag stays off.
- R6-E2: one controlled production generation passed (34.9 s);
  flag restored to off.
- R6-F: artifact verification (three PNG copies byte-identical;
  chat-transport re-encoding identified) and runtime policy
  (on-demand enable recommended; long-term enable needs six new
  capabilities plus three-level authorization).

## R5 — 2026-11-11

- R5-A: portability fixes (missing-CLI reporting, zombie-free
  residual scan); real breaker trip/recovery verified in isolation.
- R5-B: Codex CLI has no image switch; generation runs through the
  agent's internal image tool.
- R5-C: both Grok crons not equivalently executable by the
  orchestrator (130 s timeout; model and output differences).
- GitHub handoff package prepared (124 files); remote repository
  initialized with README, SECURITY.md, release policy, and R5 source
  review (source itself not yet published).

## R4 — 2026-11-11

- Formal Skill installed and activated; Grok Fetch-deny fix
  (`Fetch(*)` permanently denied; agent uses pre-approved
  web_search/web_fetch).
- Routing rule: task type, not length, decides delegation.
- Real-task verification: Japanese writing to agy; simple Chinese
  editing handled directly.

## R3 — 2026-11-11

- Unified entry, deterministic classifier, SQLite task store,
  three-state contracts, P0–P3 security model, adapters with
  deny-all / allowlist / sandbox controls.
- Version 0.1.0. Integration partial: Codex read boundary missing,
  isolation CLI-layer only.

## R1–R2 — 2026-11-11

- Feasibility: agy 1.5.5, Grok 1.0.77, Codex CLI verified;
  architecture A (native CLI + thin adapters) selected.
- Six-module design, task contracts, SQLite WAL, P0–P3 security
  model; low-privilege UID/userns verification blocked by the
  platform (R-6).

## Known issues carried forward

- R-1: Codex OS-level file-read boundary incomplete.
- R-6: low-privilege UID/userns verification incomplete.
- R-7: sensitive-information gate leakage risk remains.
- R-8: real 429 scenarios not fully covered.
- ISSUE-03: breaker-refusal receipt enhancement (separate patch).
- ISSUE-04: `real_image` receipt marker enhancement (separate patch).
- Chinese writing + review is not an independent two-stage review.
- Some Grok cited sources not individually verified.
- Grok cron stays on its existing scheme; H5 long-term auto-enable
  and quota management not implemented; N1–N8 migration nodes not
  started.
