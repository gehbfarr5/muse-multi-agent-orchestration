# Release policy

## R5 baseline

The uploaded R5 handoff archive is the source of truth for release review. A historical report describes 126 passing tests, while a separate portable-container execution reported one environment-sensitive CLI-version test failure. Until reproduced and resolved, the public repository must not claim fully portable 126/126 results.

## Gates before publishing source

1. Check archive integrity and file inventory.
2. Review every proposed file for credentials, customer data, local machine identifiers, and internal runtime logs.
3. Publish source and safe documentation without unreviewed evidence dumps.
4. Run available unit tests and clearly separate mocked from live CLI tests.
5. Verify the resulting GitHub tree and only then create a version tag.

## Versioning

Do not backdate tags or claim historical commits that were not imported. The first public release version will be selected after source publication and validation.
