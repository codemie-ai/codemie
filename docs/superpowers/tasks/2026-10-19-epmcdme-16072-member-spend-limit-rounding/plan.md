# EPMCDME-16072 Member Spend-Limit Rounding Implementation Plan

> **For agentic workers:** Executed inline by sdlc-standard (superpowers:test-driven-development). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EQUAL members get one uniform, non-negative, floor-to-cent limit whose total never exceeds the budget. When members end up at $0.00, a backend flag and a UI banner warn about it. Turning enforcement on recomputes stale shares.

**Architecture:**
- **Shared split helper.** One pure Decimal helper in `ProjectBudgetService` replaces the HALF_UP plus last-member-remainder logic in both split helpers.
- **Response flag.** Router builders derive `zero_limit_member_count` from stored rows.
- **Toggle-on recompute.** The enforcement toggle-on path calls a new service method that recomputes and persists shares before the existing LiteLLM push.
- **UI banner.** codemie-ui renders an `InfoWarning` per category from the new field.

**Tech Stack:** Python 3.12, FastAPI, SQLModel async, pytest + pytest-asyncio; React + TypeScript, vitest + Testing Library.

**Spec:** `docs/superpowers/tasks/2026-10-19-epmcdme-16072-member-spend-limit-rounding/spec.md`

## Global Constraints

- **Rounding rule.** EQUAL share = `floor_to_cent((budget − Σ FIXED) / equal_count)`, using Decimal `ROUND_DOWN` to `_CENT`. Every EQUAL member gets exactly that value; leftover cents stay unallocated.
- **Soft limit.** Per-member soft = `min(floor_to_cent(soft_remaining / equal_count), max_share)`.
- **FIXED validation.** FIXED override validation and its messages stay unchanged.
- **Zero-limit count.**
  - `zero_limit_member_count` = the count of active EQUAL rows with `allocated_max_budget <= 0`, but only when enforcement is on; otherwise 0.
  - FIXED rows are never counted.
- **Never block.** $0.00 shares never reject a save or the toggle.
- **Branches.** Backend: `D:/Projects/codemie` on `EPMCDME-16072_member-spend-limit-rounding`. UI: `D:/Projects/codemie-ui`, new branch `EPMCDME-16072_member-spend-limit-rounding` from `origin/main`.
- **Commits.** Message prefix: `EPMCDME-16072: `. Never commit `docker-compose.yml` or old task folders.
- **Backend tests.** Run with `poetry run pytest <path> -p no:xdist -q` (narrow scope).

## Review Focus

1. **Sub-cent input** (a group category amount such as `3.3 * 30 / 100`): the share must floor, never round up. Test in Task 1.
2. **FIXED overrides leave $0 remaining for EQUAL members:** EQUAL members get 0.0, there is no exception, and they are counted in `zero_limit_member_count`. Test in Task 1 and Task 3.
3. **Soft budget larger than max per member** (remaining soft > remaining max because of FIXED overrides): soft is capped at max. Test in Task 1.
4. **Toggle-on when FIXED overrides exceed the budget** (`ValidationException`): log and keep the stored rows; the toggle still syncs. Test in Task 4.
5. **Enforcement off with stale $0 rows:** the count is 0 and there is no banner. Test in Task 3.

---

### Task 1: Uniform floor-to-cent split helper

**Files:**
- Modify: `src/codemie/service/budget/project_budget_service.py:24` (import `ROUND_DOWN`), `:184-234` (`_allocate_equal`), `:246-285` (`_equal_amounts_with_fixed_overrides`)
- Test: `tests/codemie/service/budget/test_project_budget_allocation_rounding.py` (new)
- Modify test: `tests/codemie/service/budget/test_project_budget_service_lifecycle.py:583-589`. The existing remainder test keeps the expected `(15.0, 15.0)`; just confirm it still passes.

**Interfaces:**
- Produces:
  - `ProjectBudgetService._equal_share(amount: Decimal, count: int) -> Decimal`: a static helper. It returns `(amount / count).quantize(_CENT, ROUND_DOWN)` clamped at `Decimal("0")` minimum, and returns `Decimal("0")` when `count == 0`.
  - `_allocate_equal` and `_equal_amounts_with_fixed_overrides` keep their signatures. Each EQUAL member gets `(share_max, min(share_soft, share_max))`.

Test-first: yes — `$1 / 70` gives 70 identical 0.01 values (the current code gives 69 × 0.01 + 0.31), and `$0.99 / 65` currently yields one -0.31 value.

- [ ] **Step 1: Write failing tests** in the new file. Use the `_allocation` SimpleNamespace fake as in the lifecycle tests. Call `_allocate_equal(user_ids=[f"u{i:04d}" for i in range(n)], max_budget, soft_budget, budget_id="b", project_name="p", budget_category="platform", allocation_mode="equal", assigned_by="a")`.
  Parametrize `(max, soft, n, expected_max, expected_soft)` with:
  - `(1.0, 0.8, 70, 0.01, 0.01)`
  - `(0.99, 0.79, 65, 0.01, 0.01)`
  - `(1.09, 0.87, 70, 0.01, 0.01)`
  - `(0.30, 0.24, 70, 0.0, 0.0)`
  - `(100.0, 80.0, 7, 14.28, 11.42)`
  - `(200.0, 160.0, 3, 66.66, 53.33)`
  - `(1000.0, 800.0, 1000, 1.0, 0.8)`
  - `(0.99, 0.79, 1, 0.99, 0.79)`

  For each case assert:
  - every row has the same `allocated_max_budget == expected_max`;
  - every row has `allocated_soft_budget == expected_soft`;
  - every value is `>= 0`;
  - `Decimal(str(sum)) <= Decimal(str(max))` for both soft and max (use `sum(Decimal(str(r.allocated_max_budget)) ...)`);
  - soft `<=` max per row.

  Further tests:
  - `test_allocate_equal_floors_sub_cent_amount`: `max_budget=3.3*30/100` (0.99 with float noise), n=2 → 0.49 each.
  - `test_allocate_equal_caps_soft_at_max`: `max_budget=0.5, soft_budget=5.0`, n=1 → soft 0.5.
  - `test_equal_amounts_uniform_with_fixed_overrides`: FIXED `(10.0 max, 5.0 soft)` plus 3 EQUAL, budget max 10.01, soft 8.0 → each EQUAL gets `(0.0, 0.0)`.
  - `test_equal_amounts_uniform_remaining_split`: FIXED `(1.0, 0.5)` plus 70 EQUAL, max 2.0, soft 1.5 → each EQUAL gets `(0.01, 0.01)`, and the sum of all max values is `<= 2.0`.
  - `test_equal_amounts_soft_capped_at_max_with_fixed`: FIXED `(9.0 max, 0.0 soft)` plus 1 EQUAL, budget max 10, soft 8 → EQUAL gets `(1.0, 1.0)`.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/service/budget/test_project_budget_allocation_rounding.py -p no:xdist -q`. Expected: FAIL on the 70-member and 65-member cases.
- [ ] **Step 3: Implement** `_equal_share` and rewrite both helpers to use it. Remove the remainder and `is_last` logic. Update the `_allocate_equal` docstring to describe the floor rule, with leftover cents unallocated.
- [ ] **Step 4: Run** the new file plus `tests/codemie/service/budget/test_project_budget_service_lifecycle.py` and `tests/codemie/service/budget/`. Expected: PASS. Fix any test that asserted the old last-member remainder by updating its expected values to the floor rule.
- [ ] **Step 5: Commit** `EPMCDME-16072: floor equal member shares uniformly`.

### Task 2: Zero-share warning log + callsite seam tests

**Files:**
- Modify: `src/codemie/service/budget/project_budget_service.py`: `create_project_budget` (around L936-963), `create_project_budget_group` (around L1909-1928), `_create_new_group_category` (around L2292-2309), `_resync_member_allocations` (L1179-1216)
- Test: `tests/codemie/service/budget/test_project_budget_allocation_rounding.py`

**Interfaces:**
- Consumes: `_equal_share` (Task 1).
- Produces: `ProjectBudgetService._log_zero_equal_share(project_name: str, budget_id: str, budget_category: str, zero_count: int) -> None`, a static method. It logs `logger.warning(f"budget_event=member_share_below_minimum component=project_budget_service project_name=... budget_id=... budget_category=... zero_limit_member_count=...")` only when `zero_count > 0`.

Test-first: yes. Two failing tests:
- Resync of 70 EQUAL rows with a $0.30 budget logs `member_share_below_minimum`.
- Create of a $1/70 budget seeds the shared child with `per_member_max_budget=0.01`.

- [ ] **Step 1: Write failing tests.**
  - `test_create_project_budget_seeds_shared_child_with_uniform_share`: follow the existing `create_project_budget` test setup in `tests/codemie/service/budget/test_project_budget_service.py` (patch repositories, `SettingsService.get_enforce_member_spend_limits` → True, `patch.object(service, "_ensure_shared_child_budget", AsyncMock())`). Use 70 members and `max_budget=1.0`, `soft_budget=0.8`. Assert `_ensure_shared_child_budget` was awaited with `per_member_max_budget=0.01, per_member_soft_budget=0.01`.
  - `test_resync_logs_warning_when_equal_share_is_zero`: follow `test_resync_member_allocations_updates_shared_child_budget_for_equal_members` (test_project_budget_service.py L30-98) with 70 EQUAL allocations and `eff_max=0.30`. Patch `codemie.service.budget.project_budget_service.logger`. Assert that a warning call contains `member_share_below_minimum` and `zero_limit_member_count=70`.
- [ ] **Step 2: Run.** Expected: FAIL. The shared-child assert should already pass after Task 1; if it does, keep it as a seam regression and note GREEN-on-first-run. The warning test fails.
- [ ] **Step 3: Implement.**
  - Call `_log_zero_equal_share` after the split in the three create paths. `zero_count` = the count of rows with `allocated_max_budget <= 0`, only when `enforce_limit` is true.
  - In `_resync_member_allocations`, count EQUAL entries of `new_alloc_map` with max `<= 0` when `enforce_limit`.
- [ ] **Step 4: Run** `tests/codemie/service/budget/ -p no:xdist -q`. Expected: PASS.
- [ ] **Step 5: Commit** `EPMCDME-16072: warn when equal member share floors to zero`.

### Task 3: `zero_limit_member_count` in budget and group responses

**Files:**
- Modify: `src/codemie/rest_api/models/project_budget.py:71-95` (`ProjectBudgetResponse`), `:172-185` (`CategoryBudgetDetailResponse`)
- Modify: `src/codemie/rest_api/routers/project_budget_router.py:72-122` (`_build_project_budget_response`), `:453-470` (`_build_project_budget_group_response`)
- Test: `tests/codemie/rest_api/routers/test_project_budget_router_zero_limit.py` (new; first check `tests/codemie/rest_api/routers/` for an existing project budget router test module and add to it if one exists)

**Interfaces:**
- Produces:
  - Response field `zero_limit_member_count: int = 0` on both response models.
  - Router helper `_zero_limit_member_count(allocations: list[ProjectMemberBudgetAssignment], enforce_limit: bool) -> int`.
  - The group builder reads enforcement via `SettingsService.get_enforce_member_spend_limits(result.group.project_name)`, once per group.

Test-first: yes — a budget response built from 70 EQUAL rows at 0.0 with enforcement on has `zero_limit_member_count == 70`; today the field does not exist.

- [ ] **Step 1: Write failing tests.** Build the response directly from SimpleNamespace or model fakes:
  - enforcement on, 70 EQUAL rows at 0.0 → 70;
  - enforcement off, same rows → 0;
  - one FIXED row at 0.0 plus two EQUAL rows at 0.5 → 0;
  - one legacy EQUAL row at -0.31 plus 64 EQUAL rows at 0.02 → 1;
  - group builder with one category of 3 EQUAL rows at 0.0 and enforcement on → that category's count is 3.
- [ ] **Step 2: Run.** Expected: FAIL (AttributeError or validation error on the unknown field).
- [ ] **Step 3: Implement** the field, the helper and the builder wiring.
- [ ] **Step 4: Run** the new tests plus `tests/codemie/rest_api/` budget router tests (`-k budget`). Expected: PASS.
- [ ] **Step 5: Commit** `EPMCDME-16072: expose zero_limit_member_count on budget responses`.

### Task 4: Recompute shares on enforcement toggle-on

**Files:**
- Modify: `src/codemie/service/budget/project_budget_service.py`: new public method next to `_resync_member_allocations`
- Modify: `src/codemie/enterprise/litellm/project_member_runtime_sync.py:267-326` (`resync_project_member_allocations`)
- Test: `tests/codemie/service/budget/test_project_budget_allocation_rounding.py`, `tests/enterprise/litellm/test_project_member_runtime_sync.py`

**Interfaces:**
- Consumes: `_equal_amounts_with_fixed_overrides`, `_log_zero_equal_share`, `_ensure_shared_child_budget_after_resync(session, *, budget_id, budget, shared_sample)`, `project_member_budget_assignment_repository.update_allocation(session, allocation_id, allocated_max_budget, allocated_soft_budget)`.
- Produces: `async ProjectBudgetService.recompute_equal_shares(self, session: AsyncSession, project_name: str) -> None`.
  1. Load `get_active_by_project(session, project_name=project_name)` and group the rows by `project_budget_id`.
  2. For each group, load the budget with `budget_repository.get_by_id`; skip if it is None.
  3. Compute the map with `budget.max_budget` and `budget.soft_budget`.
  4. Call `update_allocation` only for rows whose `(max, soft)` changed.
  5. Call `_ensure_shared_child_budget_after_resync` with the first EQUAL `(max, soft)`.
  6. Call `_log_zero_equal_share`.
  7. On `ValidationException` for a budget: `logger.warning(f"budget_event=member_share_recompute_skipped ... reason=fixed_overrides_exceed_budget")` and continue.
  8. No provider calls and no commit; the caller commits.
- In `resync_project_member_allocations`, when `enforce_limit` is true, call `project_budget_service.recompute_equal_shares(session, project_name)` before `get_active_by_project`. Use a local import (`# noqa: PLC0415`) to avoid the enterprise ↔ service import cycle.

Test-first: yes — after toggle-on, the provider receives `effective_max_budget=0.01` for an allocation stored as -0.31.

- [ ] **Step 1: Write failing tests.**
  - Service:
    - `test_recompute_equal_shares_updates_only_changed_rows`: 65 EQUAL rows (64 at 0.02 and 1 at -0.31) on a budget of 0.99/0.79. Assert `update_allocation` is awaited 65 times with `(0.01, 0.01)`, and the shared child sample is `(0.01, 0.01)`.
    - `test_recompute_equal_shares_skips_budget_when_fixed_exceeds`: FIXED max 2.0 on a budget of 1.0. No update; a warning is logged; no raise.
    - `test_recompute_equal_shares_noop_when_uniform`: rows already 0.01 → `update_allocation` is not awaited.
  - Runtime sync: `test_resync_recomputes_equal_shares_before_push_when_enforcement_on`. Patch `codemie.service.budget.project_budget_service.project_budget_service.recompute_equal_shares` with an AsyncMock that mutates the fake allocation's `allocated_max_budget` to 0.01. Assert it was awaited once, and that `sync_member_allocation` got `effective_max_budget=0.01`.
  - `test_resync_does_not_recompute_when_enforcement_off`: assert the recompute is not awaited.
- [ ] **Step 2: Run** both test files. Expected: FAIL (no attribute `recompute_equal_shares`).
- [ ] **Step 3: Implement** the method and the runtime-sync call. Existing `test_resync_*` tests with `enforce_limit=True` must patch `recompute_equal_shares` (AsyncMock) so their fakes don't need new attributes.
- [ ] **Step 4: Run** `tests/enterprise/litellm/test_project_member_runtime_sync.py tests/codemie/service/budget/ tests/codemie/service/project/ -p no:xdist -q`. Expected: PASS.
- [ ] **Step 5: Run** `make ruff` (or `poetry run ruff check src tests && poetry run ruff format --check src tests`). Expected: clean.
- [ ] **Step 6: Commit** `EPMCDME-16072: recompute equal shares when enforcement is turned on`.

### Task 5: UI banner for zero member limits (codemie-ui)

**Files:**
- Branch: in `D:/Projects/codemie-ui`, check `git status --porcelain` is clean (stash nothing without asking), then `git checkout -b EPMCDME-16072_member-spend-limit-rounding origin/main`.
- Modify: `src/types/entity/projectBudget.ts` (`ProjectBudget`: add `zero_limit_member_count?: number`), `src/types/entity/projectBudgetGroup.ts` (`CategoryBudgetDetail`: same field)
- Modify: `src/pages/settings/administration/projectsManagement/ProjectBudgetsSection.tsx`:
  - `BudgetCardData` gets `zeroLimitMemberCount: number`;
  - the `budgetCards` mapping (L477-503) uses `scope?.zero_limit_member_count ?? budget?.zero_limit_member_count ?? 0`;
  - the banner list renders above the "Budget categories" header (around L580).
- Test: `src/pages/settings/administration/projectsManagement/__tests__/ProjectBudgetsSection.test.tsx`

**Interfaces:**
- Consumes: backend field `zero_limit_member_count` (Task 3).
- Produces: for each card with `zeroLimitMemberCount > 0`, an `<InfoWarning type={InfoWarningType.WARNING} className="mb-3" message={...} />` with `data-testid` not required. The exact message copy:
  `` `${getBudgetCategoryLabel(category)} budget is too small to split across members: ${count} ${count === 1 ? 'member has' : 'members have'} a $0.00 limit and cannot spend. Increase the budget or turn off Enforce member spend limits.` ``

Test-first: yes — rendering with a platform category whose `zero_limit_member_count: 70` shows the text "70 members have a $0.00 limit"; it currently fails.

- [ ] **Step 1: Write failing tests** in the existing test file, using its store mock setup:
  - count 70 → the banner text is present;
  - count 1 → "1 member has a $0.00 limit";
  - count 0 or field missing → no "$0.00 limit" text.
- [ ] **Step 2: Run** `npx vitest run src/pages/settings/administration/projectsManagement/__tests__/ProjectBudgetsSection.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** the types, the mapping and the banner rendering.
- [ ] **Step 4: Run** the same test file, then `npm run typecheck` and `npx eslint <changed files>`. Expected: PASS and clean.
- [ ] **Step 5: Commit** in codemie-ui: `EPMCDME-16072: warn when project members get $0.00 spend limit`.
