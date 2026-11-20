# Gate bulk budget tests (EPMCDME-17403) Implementation Plan

> **For agentic workers:** use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Make the bulk project-budget router tests and the attribution drift test pass or skip cleanly without codemie-enterprise.

**Architecture:** Test-only change in two files. No production code. Commit per task using the repository's existing convention (message prefix `EPMCDME-17403: `), staging only the named test files. Do not touch `docs/stories/` or `.ai-run/sdlc-factory/doctor.json`. Push nothing to Jira.

**Spec:** none (requirements inline); analysis in `technical-analysis.md` beside this file.

## Acceptance criteria

- Without enterprise, the 5 bulk tests (`test_bulk_http_returns_results_and_budgets` and 4 ids of `test_bulk_422_for_zero_items_over_1000_and_negative_amounts`) pass.
- Without enterprise, `test_the_mirrored_project_composer_matches_its_source` is skipped, and the rest of `test_attribution.py` still runs.
- With enterprise installed, all 157 tests in the two files pass.
- `test_router_endpoint_rejects_when_budget_management_disabled` is unchanged and still passes.
- No production code, `docs/stories/` or `doctor.json` change. One commit stages only the two test files.

## Task 1: Pin budgetManagement in `send_bulk_request`

**Files:** Modify `tests/codemie/rest_api/routers/test_project_budget_router.py:1359-1370`.

**Test-first:** yes — the 5 bulk tests return 403 instead of 200/422 without enterprise, because the flag is unset.

- [ ] Step 1: Reproduce the failure. Run `PYTHONPATH=~/no_ent poetry run pytest tests/codemie/rest_api/routers/test_project_budget_router.py tests/codemie/service/spend/test_attribution.py -p no_ent -n0 -q -W ignore`. Expect exactly 5 bulk failures (403) plus the drift test erroring on `ModuleNotFoundError`.
- [ ] Step 2: In the `send` closure of `send_bulk_request`, wrap the `client.post(_BULK_PATH, json=body)` call in `with patch.object(CustomerConfig, "is_feature_enabled", return_value=True):`, mirroring `send_member_request` (lines 1074-1077). Leave the disabled-guard tests and the `budget_management_app` fixture alone.
- [ ] Step 3: Rerun the command from Step 1. Expect the 5 bulk tests to pass.

## Task 2: Skip the drift test without enterprise

**Files:** Modify `tests/codemie/service/spend/test_attribution.py:79-91`.

**Test-first:** yes — `test_the_mirrored_project_composer_matches_its_source` raises `ModuleNotFoundError` without enterprise.

- [ ] Step 1: Add `pytest.importorskip("codemie_enterprise.litellm.service")` as the first statement inside the test body, before the `from codemie_enterprise.litellm.service import LiteLLMService` line. Never put it at module level. Add `import pytest` only if the file lacks it.
- [ ] Step 2: Verify without enterprise using the Task 1 command. Expect the drift test skipped and no failures in either file.
- [ ] Step 3: Verify with enterprise installed: run `poetry run pytest` on the two files. Expect 157 passed. Then commit both files by name.

## Negative-constraint pass

- No production code: both tasks touch tests only.
- Module-level `importorskip` forbidden: Task 2 puts it inside the body.
- Keep the disabled-guard test unchanged: neither task edits it.
- Do not touch `docs/stories/` or `doctor.json`: header constraint; stage by name.
- Nothing pushed to Jira: no task does this.
