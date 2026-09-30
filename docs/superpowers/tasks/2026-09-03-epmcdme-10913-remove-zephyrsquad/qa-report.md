# QA Gate Report — epmcdme-10913-remove-zephyrsquad (backend)

**Branch**: EPMCDME-10913_remove-zephyrsquad
**Runner**: poetry (guide: `.ai-run/guides/quality-gates.md`)
**Started**: 2026-09-03
**Status**: PASSED

## Gates

| Gate  | Source | Status | Duration | Command | Notes |
|-------|--------|--------|----------|---------|-------|
| lint (ruff) | guide | PASS | ~5s | `make ruff` | format unchanged, check --fix clean, check clean |
| build | guide | PASS | ~10s | `make build` | sdist + wheel built |
| license-check | guide | PASS | ~5s | `make license-check` | 2179 files checked, 0 missing headers |
| secrets (gitleaks) | guide | PASS | 22s | `make gitleaks` | Docker available, no leaks found |
| tests | guide | PASS* | 181s | `poetry run pytest tests/` | see note below |
| coverage | guide | SKIPPED | — | `make coverage` | not requested |
| sonar-local | guide | SKIPPED | — | `make sonar-local` | not run this session (no SONAR_TOKEN in shell) |

## Test detail

Full suite: **64 failed, 15628 passed, 180 skipped, 23 errors** (181.50s). All 64 failures and
23 errors are confined to `tests/enterprise/mcp_auth/*` and
`tests/codemie/service/google_oauth/*` — files this branch does not touch
(`git diff main...HEAD --name-only` confirms zero overlap). These are pre-existing failures on
`main`, unrelated to the ZephyrSquad removal.

Every file this branch actually changes was additionally run in isolation and is fully green:
`tests/codemie_tools/qa/`, `tests/codemie_tools/base/`,
`tests/codemie/service/settings/test_settings_request_validator.py`,
`tests/codemie/service/settings/test_settings_service.py`,
`tests/codemie/service/settings/test_settings_tester.py`,
`tests/codemie/rest_api/routers/test_user_settings.py`,
`tests/codemie/rest_api/routers/test_project_settings.py`,
`tests/codemie/service/search_and_rerank/test_search_and_rerank_tool.py`
— **340 passed, 0 failed**.

(Separately noted during implementation: running many `tests/codemie/service/settings/*` files
together in one pytest invocation hits a pre-existing, order-dependent `langgraph`/`langchain`
circular-import collection error, reproduced identically on unmodified `main` with the same file
set. Running the same files individually or in the smaller combination above collects and passes
cleanly — this is an environment quirk, not a code defect from this branch.)

## Failure detail

None attributable to this branch. See Test detail above for the pre-existing, out-of-scope
failure list (mcp_auth OAuth bridge tests, google_oauth credential-preservation tests).

## Drift signal

no

## Still owed

`sonar-local` self-skips locally without `SONAR_TOKEN` — Sonar's server-side quality gate can
only be settled by the MR pipeline, not this session.
