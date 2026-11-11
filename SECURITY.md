# Security and privacy

This project is intended for controlled, low-risk task delegation.

- Do not place customer names, phone numbers, insurance records, or other personal data in prompts to external CLI agents.
- Do not commit tokens, OAuth credentials, CLI login state, production SQLite databases, receipts, or raw runtime logs.
- CLI-level permission restrictions are not an OS sandbox.
- Codex live image generation is not enabled in the current verified baseline.
- Production cron migration requires separate authorization.

For security concerns, avoid posting secrets or private customer information in public GitHub issues.
