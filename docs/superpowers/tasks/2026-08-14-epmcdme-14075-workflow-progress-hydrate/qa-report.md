# QA Gate Report — epmcdme-14075-workflow-progress-hydrate

**Branch**: EPMCDME-14075_workflow-progress-hydrate
**Runner**: poetry
**Started**: 2026-08-14T07:50:00Z
**Status**: BLOCKED

## Gates

| Gate | Status | Duration | Command | Notes |
|------|--------|----------|---------|-------|
| lint | PASS | 2.36s | `make ruff` | format unchanged; ruff check passed |
| build | PASS | 6.39s | `make build` | Built codemie-0.8.0 sdist and wheel |
| license | PASS | 1.27s | `make license-check` | 2040 files, 0 missing headers |
| gitleaks | FAIL | 28.75s | `make gitleaks` | Detected AZURE_OPENAI_API_KEY in local gitignored `.env` (not in this change) |
| unit | FAIL | 194.79s | `make test` | 71 failed, 14376 passed, 177 skipped, 23 errors. Changed tests passed: `test_dual_queue.py` (10) and `test_history_materializer.py` (18). Failures are unrelated (enterprise MCP auth, git wrappers, vsdx loader, missing `tree_sitter_languages`) |
| affected | SKIPPED | — | (n/a) | no affected-test command |
| ui | SKIPPED | — | (n/a) | no UI surface changed |
| coverage | SKIPPED | — | `make coverage` | user did not request coverage |
| sonar | SKIPPED | — | `make sonar-local` | credentials/network not assumed |
| test-harness | SKIPPED | — | `make test-harness` | not opening an MR in this stage |

## Failure detail

### gitleaks

Finding in `/workspace/.env` line 4 (`generic-api-key` / `AZURE_OPENAI_API_KEY`). `.env` is gitignored local config, not part of the EPMCDME-14075 commits.

### make test (summary)

```
= 71 failed, 14376 passed, 177 skipped, 189 warnings, 23 errors in 185.97s (0:03:05) =
```

Changed-file tests:

```
tests/codemie/core/test_dual_queue.py ..........                         [  7%]
tests/codemie/service/test_history_materializer.py ..................    [ 60%]
```

Sample unrelated failures: `ModuleNotFoundError: No module named 'tree_sitter_languages'`; `TypeError: 'PydanticDescriptorProxy' object is not callable` in git API wrapper fixtures; enterprise `mcp_auth` bridge tests.

## Drift signal

no
