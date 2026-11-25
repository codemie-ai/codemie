# EPMCDME-17677 One-row transfer refuses subsystem rows Implementation Plan

## Acceptance criteria

- [ ] `transfer_setting` on an `__internal__` row, a `codemie:project:` LiteLLM budget key row, a `Schedule_` data-source row, or the `project_member_budget_tracking_enabled` row fails with 422 "Reserved integration identifiers", and the row's `project_name` is unchanged.
- [ ] `transfer_setting` on an ordinary row (e.g. `jira-prod`, `llm-key`) moves as today.
- [ ] A mock-based regression test in `TestTransferSetting` covers the refusal cases and passes.

**Goal:** The one-row transfer applies the same subsystem-alias rule as the bulk transfer.

**Architecture:** Reuse `SettingsTransferService._is_subsystem_managed` and raise the same 422 `ExtendedHTTPException` shape as the bulk path. The guard sits right after `Settings.get_by_id`, before `_validate_projects` and `_apply`, so nothing is written. Both PUT routers re-raise it unchanged and their `transactional_session()` rolls back the preceding field update.

**Tech Stack:** Python, pytest, `unittest.mock`.

**Spec:** none (inline requirements, see Acceptance criteria).

## Global Constraints

- No explanatory code comments; no feature docs under `docs/`; no real-Postgres tests.
- Commit per task using the repository's existing convention. Do not stage `.ai-run/sdlc-factory/doctor.json` or `.codemie/codemie-cli.config.json`.
- Quality gates run by the calling flow (`.ai-run/guides/quality-gates.md`), not by this plan.

### Task 1: Guard `transfer_setting` against subsystem-managed aliases

**Files:**
- Modify: `src/codemie/service/settings/settings_transfer_service.py:390-391` (`transfer_setting`)
- Test: `tests/codemie/service/settings/test_settings_transfer_service.py:737` (`TestTransferSetting`)

**Test-first:** yes — parametrized test fails today because `transfer_setting` calls `_apply` and moves the row instead of raising 422.

- [ ] **Step 1: Write the failing test** in `TestTransferSetting`, `test_refuses_subsystem_managed_alias`, parametrized over `(alias, credential_type)`: `("__internal__IDE_abc", ...)`, `("codemie:project:source:category:premium_models", CredentialTypes.LITE_LLM)`, `("Schedule_my-datasource", ...)` (use the same types the bulk tests use at lines ~137-139), `("project_member_budget_tracking_enabled", ...)`. Patch `Settings.get_by_id`, `_validate_projects`, `_apply` as the neighbouring tests do; row from `_setting(alias, ..., id_="row-1")` with `project_name = "source"`. Assert:
  - `ExtendedHTTPException` raised with `code == 422` and `message == "Reserved integration identifiers"`
  - `mock_apply.assert_not_called()` and `mock_validate_projects.assert_not_called()`
  - `row.project_name == "source"`
- [ ] **Step 2:** Run `pytest tests/codemie/service/settings/test_settings_transfer_service.py::TestTransferSetting -v`; expect the new cases FAIL, existing five PASS.
- [ ] **Step 3:** In `transfer_setting`, after `Settings.get_by_id` and before `_validate_projects`, raise the 422 `ExtendedHTTPException` when `cls._is_subsystem_managed(setting.alias)`. Mirror lines 108-117: same message, details naming the alias (`... cannot be transferred: <alias>.`), help "Choose an integration that is not managed by an internal subsystem." Extract a small shared helper only if it avoids duplicating the bulk message text.
- [ ] **Step 4:** Re-run the same command plus the whole file; expect all PASS (bulk reserved-alias tests and ordinary-row tests `jira-prod` / `llm-key` unchanged).
- [ ] **Step 5:** Commit per the repository convention.

## Negative-constraint pass

- Nothing written on refusal: guard precedes `_validate_projects`/`_apply` (Task 1).
- No comments, no docs, no real-Postgres tests, dirty files excluded: Global Constraints, Task 1 tests are mock-based.
