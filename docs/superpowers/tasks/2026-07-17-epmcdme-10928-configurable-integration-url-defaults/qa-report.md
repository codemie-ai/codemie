# QA Gate Report — epmcdme-10928-configurable-integration-url-defaults

**Branch**: EPMCDME-10928_configurable-integration-url-defaults
**Runner**: poetry
**Started**: 2026-07-20T07:45:00Z
**Status**: PASSED

## Gates

| Gate    | Status  | Command                | Notes |
|---------|---------|------------------------|-------|
| lint    | PASS    | `make ruff`            | 1 file reformatted, all checks passed |
| build   | PASS    | `make build`           | codemie-0.8.0 built successfully |
| license | PASS    | `make license-check`   | 1931 files checked, 0 missing headers |
| secrets | PASS    | `make gitleaks`        | No leaks found (26.55 MB scanned) |
| unit    | PASS    | `make test`            | 13141 passed, 129 skipped. 46 pre-existing failures in enterprise/mcp_auth (ModuleNotFoundError: codemie_enterprise not installed locally); 0 failures in branch-touched files. |
| ui      | SKIPPED | (n/a)                  | No UI surface changed |

## Failure detail

46 pre-existing test failures in `tests/enterprise/mcp_auth/` and `tests/codemie/service/mcp/` due to `ModuleNotFoundError: No module named 'codemie_enterprise'`. These tests require the `codemie_enterprise` package which is not installed in this local environment. None of the failing tests are in files touched by EPMCDME-10928. This is an environment-local issue, not a regression introduced by this branch.

## Drift signal

no
