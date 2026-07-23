# QA Gate Report — collect-member-spend-before-removal

**Branch**: EPMCDME-13619
**Runner**: poetry
**Started**: 2026-07-22T00:00:00Z
**Status**: PASSED

## Gates

| Gate | Status | Duration | Command | Notes |
|------|--------|----------|---------|-------|
| lint | PASS | ~5s | `make ruff` | 2165 files unchanged; all checks passed |
| build | PASS | ~5s | `make build` | codemie-0.8.0.tar.gz + .whl built |
| license | PASS | ~3s | `make license-check` | 1932 files, 0 missing headers |
| secrets | SKIPPED | — | `make gitleaks` | 144 hits all in gitignored untracked `codemie-storage/test_multiprocessing/*.html` (Jira CSRF tokens, false positives). Zero hits in diff. Pre-existing condition. |
| unit | PASS | ~270s | `make test` | 13299 passed, 10 pre-existing failures in `test_hedged_handler.py` (EPMCDME-12879, not in diff) |
| ui | SKIPPED | — | n/a | no UI surface changed |

## Failure detail

None in diff. Pre-existing test failures:

```
FAILED tests/codemie/rest_api/handlers/test_hedged_handler.py::test_hedged_response - assert 200 == 206
FAILED tests/codemie/rest_api/handlers/test_hedged_handler.py::test_hedged_response_with_broken_upstream - assert 200 == 206
... (10 total, all same pattern, all same file)
```

Last commit to that file: `a2ea43338 EPMCDME-12879: Request hedging` — not this branch.

## Drift signal

no
