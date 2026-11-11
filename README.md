# MUSE Multi-Agent Orchestration

A lightweight, safety-gated orchestration system for delegating selected MUSE tasks to external CLI agents.

> **Release status:** Repository initialization. The reviewed R5 source snapshot has **not yet been published**. This repository is not yet an installable release.

## Architecture

MUSE remains the single orchestrator. Its routing layer can delegate eligible tasks to:

- **agy** — Japanese writing and selected content work.
- **Grok CLI** — public X/trend research within restricted tool permissions.
- **Codex CLI** — image-task mock workflow only; live image generation is not enabled.

The system uses task classification, approval/security gates, SQLite task state, adapters, output validation, receipts, and recovery controls.

## Verified project history

- **R3:** Local orchestration integration and test harness.
- **R4:** Skill activation, routing fixes, and fresh-session validation.
- **R4-G:** Japanese writing routed to agy in a real new session; simple Chinese editing handled directly by MUSE (per project acceptance report).
- **R5:** Public GitHub source-review and release preparation.

These are historical project-reported outcomes, not independent production certification.

## Safety boundaries

No real customer personal data may be sent to external CLIs. Real Codex image generation and production cron migration are not enabled. Existing production schedules must not be changed by this repository.

## Publication policy

Only reviewed source, sanitized examples, and relevant documentation will be published. Live databases, credentials, CLI authentication state, raw receipts, personal data, and potentially sensitive probe logs are excluded.

## Next steps

Publish the sanitized R5 source baseline, run portable tests, document environment-dependent CLI checks, then create the first tagged release. No release tag is assigned yet.
