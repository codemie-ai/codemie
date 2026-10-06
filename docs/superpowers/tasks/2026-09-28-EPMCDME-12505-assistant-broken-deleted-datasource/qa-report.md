# QA Gate Report — 2026-09-28-EPMCDME-12505

**Branch**: EPMCDME-12505 (changes uncommitted in the working tree)
**Runner**: guide-first (`.ai-run/guides/quality-gates.md`)
**Started**: 2026-09-28T09:47:38Z
**Status**: PASSED (no failure introduced by this change; see unit note)

## Gates

| Gate | Status | Duration | Command | Notes |
|------|--------|----------|---------|-------|
| lint | PASS | 2s | `make ruff` | All checks passed; no files reformatted |
| build | PASS | 4s | `make build` | Built codemie-0.8.0 wheel |
| license | PASS | 1s | `make license-check` | 2289 files, 0 missing headers |
| secrets | SKIPPED | — | `make gitleaks` | Docker daemon not running (guide: Skip if Docker unavailable). Unverified, not passed |
| unit | PASS (baseline-equal) | 123s | `LC_ALL=en_US.UTF-8 make test` | 16595 passed, 81 failed, 23 errors. The same 104 tests fail on clean origin/main `1ba7afa30` (clean worktree run of the same 18 files: 81 failed, 23 errors); set difference with vs without change = empty. None in touched areas |
| coverage / sonar-local / verify / test-harness | N/A | — | — | Not requested / no MR yet; test-harness required before MR |
| ui | SKIPPED | — | (n/a) | no UI surface changed |

## Failure detail

Pre-existing on main (environment-dependent): vsdx loader, permission models, oauth adapters/redis lazy init, MCP auth resolver, job lock, codemie_tools git wrappers, vertex tagging headers. Full log: /tmp/12505-gate-test.log; baseline: /tmp/12505-baseline.log.

## Drift signal

no
