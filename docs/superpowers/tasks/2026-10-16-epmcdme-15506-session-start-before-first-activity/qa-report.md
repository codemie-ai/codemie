# QA Gate Report — epmcdme-15506-session-start-before-first-activity

**Branch**: EPMCDME-15506_session-start-before-first-activity
**Runner**: guide-first (`.ai-run/guides/quality-gates.md`, Makefile targets)
**Started**: 2026-10-16
**Status**: BLOCKED (both failing gates fail identically on `main`; nothing in the failures comes from this branch)

## Gates

| Gate | Status | Duration | Command | Notes |
|------|--------|----------|---------|-------|
| lint | PASS | 2s | `make ruff` | format + `ruff check` clean |
| build | PASS | 4s | `make build` | sdist and wheel built into ignored `dist/` |
| license | PASS | 1s | `make license-check` | 2595 files, 0 missing headers |
| secrets | FAIL | 9s | `make gitleaks` | 2 `private-key` hits in `.kube-local/kubeconfig.yaml` and `.kube-local/kubeconfig-internal.yaml`: local, gitignored (`.gitignore:141`), not part of the change. None of the 6 changed files is flagged |
| unit | FAIL | 119s | `make test` | 56 failed, 1 error, 20686 passed, 198 skipped. All 57 are outside CLI analytics (`tests/enterprise/mcp_auth/*` 52, `service/oauth` 2, `service/mcp` 2, `security/test_logout_token_cleanup_mock_tms.py` 1 error, `router_guards/test_openapi_snapshot.py` 1). The same files on `main` (080fda7a2): 56 failed, 231 passed, 1 skipped, identical to the branch. CLI analytics suites: 1467 passed |
| coverage | SKIPPED | — | `make coverage` | not requested |
| static analysis | SKIPPED | — | `make sonar-local` | Sonar credentials not configured locally |
| full verification | N/A | — | `make verify` | its parts (ruff, license, gitleaks, test) ran individually above |
| test harness | SKIPPED | — | `make test-harness` | user asked not to run it; not opening an MR in this run |
| ui | SKIPPED | — | (n/a) | no UI surface changed |

## Failure detail

- `make gitleaks`: `RuleID: private-key`, files `/workspace/.kube-local/kubeconfig.yaml:18` and `/workspace/.kube-local/kubeconfig-internal.yaml:18`; `leaks found: 2`. The `dir` scan reads the whole working tree, ignored files included.
- `make test`: `56 failed, 20686 passed, 198 skipped, 259 warnings, 1 error in 118.64s`. Reproduced on `main` with the same failing files: `56 failed, 231 passed, 1 skipped`.

## Drift signal

no
