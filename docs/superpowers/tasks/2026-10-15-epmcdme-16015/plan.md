# Platform Budget Hard Limit > 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject budget-group create/update requests whose Platform hard limit is 0 (EPMCDME-16015, backend part).

**Architecture:** The platform hard limit is `total_amount * platform.pct / 100`, and `total_amount > 0` is already enforced, so the limit is 0 exactly when the platform pct is 0. Add one check to the shared validator `ProjectBudgetService._validate_group_categories`. Create and update both call it before any insert or provider call. No request or response model changes.

**Tech Stack:** Python, FastAPI, SQLModel, pytest (`unittest.mock`, `SimpleNamespace`).

**Spec:** inline requirements (no spec file); research in `docs/superpowers/tasks/2026-10-15-epmcdme-16015/technical-analysis.md`.

Commit per task using the repository's existing convention.

## Acceptance criteria

- [ ] POST /groups with `categories.platform.pct == 0` returns 400 naming `platform`, and no group or budget is created (AC7).
- [ ] PUT /groups/{group_id} with `categories` containing platform pct 0 returns 400 and persists nothing, including for a legacy group whose saved platform budget is 0 (AC5, AC7).
- [ ] CLI pct 0, Premium Models pct 0 and any `soft_budget` of 0 (including platform) still save (AC6).
- [ ] Create/update with platform pct > 0 behaves as before (AC8).
- [ ] PUT without `categories` on a legacy platform-0 group is still accepted.
- [ ] GET/list group responses still load platform-0 groups; response models are unchanged.

## Global Constraints

- No explanatory code comments.
- Error convention: `ExtendedHTTPException(code=400, message=...)` with the category key `'platform'` in the message.
- Do not touch: `CategoryBudgetSpec` / `CategoryBudgetSpecUpdate` / response models, the single-category budget flow, CLI/premium/soft-limit rules, or data migrations.
- Keep the update gate `if data.categories is not None` (`project_budget_service.py:2100`).

## Review Focus

- Platform pct a tiny positive value (e.g. 0.01) must pass, because "greater than 0" sets no other minimum. Pinned in Task 1.
- A negative platform pct must still produce the existing ">= 0 and finite" message, so the new check runs after the range loop. Pinned by the existing parametrized test.
- Update on a legacy group with no platform row and payload platform pct 0 must not reach `_update_group_categories`. Pinned in Task 1.
- On rejection, the router must not commit (`project_budget_router.py:573-576`, where the exception precedes `session.commit()`). Existing behaviour; Task 1 asserts the service never awaits `session.commit`.
- Platform `soft_budget=0` with pct > 0 must pass. Pinned in Task 1.

## Negative-constraint pass

- "do not add bounds to response models": no task edits `rest_api/models/project_budget.py`.
- "validate ONLY when payload includes categories": Task 1 leaves `update_project_budget_group` untouched, and the existing `test_update_project_budget_group_logs_on_scalar_only_edit` keeps partial updates covered.
- "out of scope: CLI/premium/soft restrictions": the new check reads only the `platform` key. Task 1's tests assert that cli/premium pct 0 and soft 0 pass.
- "out of scope: migration, single-category flow": no task touches them.
- "no explanatory code comments": stated in Global Constraints.

---

### Task 1: Enforce platform hard limit > 0 in the group validator

**Files:**
- Modify: `src/codemie/service/budget/project_budget_service.py:1788` (`_validate_group_categories`)
- Test: `tests/codemie/service/budget/test_project_budget_service_group_creation.py`

**Interfaces:**
- Consumes: `ProjectBudgetService._validate_group_categories(categories: dict, total_amount: float) -> None`, `create_project_budget_group(session, data, actor_id)`, `update_project_budget_group(session, group_id, data, actor_id)`
- Produces: no new symbols. The validator raises 400 for platform pct <= 0.

Test-first: yes — `_validate_group_categories` with platform pct 0 raises 400 with `'platform'` in the message, and create/update with platform pct 0 raise before any persistence call. All of these currently pass through without an error.

- [ ] **Step 1: Write the failing tests.** In `TestValidateGroupCategoriesWithZeroPct`:
  - Rename `test_platform_zero_passes_validation` (L50-59) to `test_platform_zero_fails_validation` and assert `pytest.raises(ExtendedHTTPException)` with `code == 400` and `"platform" in message`.
  - Add passing cases: platform `pct=0.01` with cli `99.99`, and platform `pct=60, soft_budget=0` with cli `pct=40, soft_budget=0` and premium `pct=0, soft_budget=0`.

  Add a new async class `TestGroupWritesRejectPlatformZero`, modelled on `test_update_project_budget_group_logs_on_scalar_only_edit` in `test_project_budget_service_lifecycle.py:877-917`:
  - Create: `data = SimpleNamespace(budget_duration="30d", total_amount=100.0, project_name="proj-a", categories={"platform": SimpleNamespace(pct=0.0, soft_budget=None), "cli": SimpleNamespace(pct=100.0, soft_budget=None)})`. Patch `service._ensure_project_exists` and `project_budget_group_repository.get_active_by_project` with `AsyncMock`. Assert a 400 is raised, both mocks are not awaited, and `session.add` / `session.commit` are not called.
  - Update on a legacy platform-0 group: patch `project_budget_group_repository.get_by_id` to return the group and `_update_group_scalar_fields` with `AsyncMock()`. Make `_load_group_full_result` return `SimpleNamespace(categories=[], total_amount=100.0)`, and patch `_update_group_categories` with `AsyncMock()`. Pass `data` with `total_amount=None` and the same zero-platform categories (other fields `None`). Assert a 400 is raised, `_update_group_categories` is not awaited, and `session.commit` is not awaited.
- [ ] **Step 2: Run them and confirm they fail.** `poetry run pytest tests/codemie/service/budget/test_project_budget_service_group_creation.py -v`. Expected: the new rejection tests FAIL with "DID NOT RAISE".
- [ ] **Step 3: Implement.** In `_validate_group_categories`, after the per-key range loop (after L1787) and before the sum check, raise `ExtendedHTTPException(code=400, message=f"Category 'platform' hard limit must be greater than 0, got pct {categories[platform_key].pct}")` when `categories[platform_key].pct <= 0`.
- [ ] **Step 4: Run the tests and confirm they pass.** Run the same file plus `tests/codemie/service/budget/test_project_budget_service_lifecycle.py` and `tests/codemie/rest_api/routers/test_project_budget_router_schema.py`. Expected: all PASS.
