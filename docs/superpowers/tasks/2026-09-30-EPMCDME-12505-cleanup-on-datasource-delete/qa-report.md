# QA Gate Report — 2026-09-30-EPMCDME-12505-cleanup-on-datasource-delete

**Branch**: EPMCDME-12505 (HEAD 52ec3a838)
**Runner**: guide-first (`.ai-run/guides/quality-gates.md`)
**Started**: 2026-09-30
**Status**: PASSED (no failure introduced by this change; see unit note)

## Gates

| Gate | Status | Duration | Command | Notes |
|------|--------|----------|---------|-------|
| lint | PASS | 2s | `make ruff` | All checks passed; no files reformatted |
| build | PASS | 4s | `make build` | Built codemie-0.8.0 wheel |
| license | PASS | 1s | `make license-check` | 2291 files, 0 missing headers |
| secrets | SKIPPED | — | `make gitleaks` | Docker daemon not running (guide: Skip if Docker unavailable). Unverified, not passed |
| unit | PASS (baseline-equal) | 131s | `LC_ALL=en_US.UTF-8 make test` | 16605 passed, 81 failed, 23 errors. The failing set is identical by name to clean origin/main `1ba7afa30` (baseline /tmp/12505-baseline.log, 28.09): set difference = empty both ways. 16605 = 16595 (28.09) + 10 new tests |
| coverage / sonar-local / verify | N/A | — | — | Not requested; verify needs Docker |
| test-harness | N/A | — | `make test-harness` | Required before undrafting MR !4351; needs Docker |
| ui | SKIPPED | — | (n/a) | no UI surface changed |

## Failure detail

Pre-existing on main (environment-dependent), none in touched areas: vsdx loader, permission models, oauth adapters/redis lazy init, MCP auth resolver, job lock, codemie_tools git wrappers, vertex tagging headers.

## Drift signal

no
