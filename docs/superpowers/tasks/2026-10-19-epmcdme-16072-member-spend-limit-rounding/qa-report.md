# QA Gate Report — 2026-10-19-epmcdme-16072-member-spend-limit-rounding

**Branch**: EPMCDME-16072_member-spend-limit-rounding (backend `codemie` 95d924953, UI `codemie-ui` d7bb3b054)
**Runner**: poetry (backend), npm (UI)
**Started**: 2026-10-19
**Status**: PASSED (no failure attributable to this change; see unverified gates below)

## Backend — D:/Projects/codemie

| Gate | Source | Status | Command | Notes |
|------|--------|--------|---------|-------|
| lint | guide/hook | PASS | `poetry run ruff check` + `ruff format --check` | clean |
| license | hook | PASS | license header check | 2595 files |
| build | guide | PASS | `poetry build` | wheel + sdist built, then removed from `dist/` |
| unit (full suite) | guide | FAIL (env, pre-existing) | `poetry run pytest tests/ -o addopts="--import-mode=importlib"` | 202 failed, 8 errors, 20595 passed, 205 skipped (17:50). Classified below: none caused by this change |
| affected | guide | PASS | `pytest tests/codemie/service/budget tests/codemie/rest_api/routers/test_project_budget_router.py tests/enterprise/litellm/test_project_member_runtime_sync.py` | all green |
| secrets | hook | PASS | gitleaks | "no leaks found", 186s |
| commit-msg | hook | PASS | commit-msg prefix check | all 7 commits `EPMCDME-16072: ...` |
| openapi baseline | ci | UNVERIFIED | `tests/codemie/rest_api/router_guards` | app probe subprocess fails on Windows (WinError 10106). Baseline hand-updated with the generator's rendering of `zero_limit_member_count` (10 lines). Needs CI / `/sanity` |
| sonar | ci | SKIPPED | sonar-local | no `SONAR_TOKEN` locally; CI will run it |
| test harness (sanity-api) | ci | PASS | `poetry run codemie-test-harness run --marks "api and sanity" -n 2 --reruns 10` | 197 passed, 4 skipped, 2 rerun (17:08) against local stack rebuilt from both branches |
| test harness (sanity-ui) | ci | PASS | `poetry run codemie-test-harness run sanity-ui -n 2 --reruns 10` | 93 passed, 5 rerun (1:16:12). An earlier run stalled on a broken Vite dev server in the UI container (blank pages, `$RefreshSig$ is not defined`); fixed by recreating the container, not by test changes |

### Full-suite failure classification (HEAD vs merge-base 00b964548)

The 56 files holding failures (from the pytest `lastfailed` cache) were rerun single-process on a clean
merge-base worktree and on HEAD, with the venv on PATH:

| Tree | Failed | Errors | Passed |
|------|--------|--------|--------|
| merge-base | 196 | 7 | 1354 |
| HEAD | 197 | 7 | 1358 |

- All 203 merge-base failures/errors also fail on HEAD. Groups: git-hook script tests, `enterprise/mcp_auth`,
  `codemie_tools` code executor / file system, router-guard app probe (WinError 10106), others — Windows/env only.
- 1 HEAD-only: `tests/codemie_tools/file_analysis/pptx/test_pptx_toolkit.py::test_pptx_tool_query_text_with_text`.
  Cause: stale CRLF working copy of `test.pptx.md` in the main checkout (1283 bytes, July mtime) while
  `.gitattributes` sets `eol=lf`; clean checkout (1225 bytes) passes. Branch does not touch `codemie_tools`.
- Full run 210 bad vs rerun 204 bad — the 6 difference are tests that fail only under full-suite load or
  ordering and pass when rerun on HEAD:
  - code executor timing tests (`test_codemie_runtime_sdk.py::TestCallTools::*`, `test_tool_call_channel.py`
    withdrawn / deadline tests — 5 s thread deadlines);
  - `test_main_retrieval_backend_disabled.py` (fresh `main.py` import in a subprocess, 90 s timeout; imports the
    budget router, passes on HEAD);
  - `test_logout_token_cleanup_mock_tms.py` ERROR (skips standalone: `codemie_enterprise` not installed;
    errors in full run only via order-dependent module state).
  None touches budget code.
- Remaining `lastfailed` entries are stale (deleted files `test_index_faq.py`, `test_faq_trigger_wiring.py`,
  old collection-error file entries, `TestClient` class ids); the specific stale parametrized ids pass on both trees.

## UI — D:/Projects/codemie-ui

| Gate | Source | Status | Command | Notes |
|------|--------|--------|---------|-------|
| lint | guide | PASS | `npm run lint` | 380s |
| typecheck | guide | PASS | `npm run typecheck` | `tsc --noEmit` clean |
| unit | guide | PASS (2 flaky) | `npm run test:unit` | 7195 passed, 2 failed under load (`router.test.tsx` 61s timeout, `Popup.test.tsx` focus trap); both files 30/30 on rerun; untouched by diff |
| integration | guide | PASS | `npm run test:integration:slnt` | 50 files, 564 passed, 1 skipped (657s) |
| changed-file tests | guide | PASS | `vitest run ProjectBudgetsSection` | 9/9 |
| secrets | hook | PASS | pre-commit gitleaks | run with Rancher docker on PATH |
| license-check | hook/ci | FAIL (env) | `npm run license-check` | "@adobe/css-tools@4.5.0 is licensed under MIT which is not permitted by --onlyAllow" — MIT is on the allow list; Windows quoting of the script's `--onlyAllow` arg. No dependency change in diff. CI must confirm |
| ui (browser) | guide | SKIPPED | — | no configured UI e2e script; banner covered by component tests |

## Side effects of the QA run

- Backend venv synced to `poetry.lock` with `poetry install --no-root` (about 40 added, 29 upgraded, none removed).
- `dist/codemie-*` cleanup after the build gate may have removed pre-existing gitignored wheels (regenerable).

## Drift signal

no
