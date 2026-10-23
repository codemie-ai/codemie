# ServiceNow Budget Actions Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `set`, `adjust` and `cancel` their own handlers that check everything before writing anything, matching the agreed flow.

**Architecture:** `ServiceNowIntegrationService.apply_budget_request` becomes a dispatcher plus shared logging/response. Each action handler runs a read-only check phase (find project, validate names that must be created, read the budget group, compute target amounts) and then a write phase (sync transaction: provision project/cost center + assign admins; async transaction: create/update/delete the group). The pure amount math and the existing write helpers are reused.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLModel (sync `Session` + `AsyncSession`), pytest + anyio, `unittest.mock`.

**Spec:** `docs/superpowers/tasks/2026-10-19-servicenow-budget-integration/spec.md` (sections "Decisions", "API contract", "Resolution flow") and the diagram `budget-flow.md` next to it.

## Global Constraints

- Paths are relative to `repos/codemie`; run commands from there. Tests: `poetry run pytest <path> -q -p no:cacheprovider`. Lint: `poetry run ruff check <files>` and `poetry run ruff format --check <files>`.
- Commit subject: `EPMCDME-16127: <summary>`. One commit per task. Never push. Do not commit files under `docs/superpowers/tasks/` other than this plan's code and test files.
- The working tree already contains an uncommitted change (missing project → 404 for `adjust`/`cancel`, `PROJECT_NOT_FOUND_MESSAGE`/`PROJECT_NOT_FOUND_HELP`, test `test_missing_project_is_404_without_provisioning`). Task 2 supersedes it; include those files in Task 2's commit.
- Behavior to reach (exact):
  - `set`: upsert + replace. Missing project/cost center/group are created. Model rejects `set` without `platform` > 0 with 422.
  - `adjust`: upsert + merge. Missing project/cost center/group are created; merge base is the current amounts, or all zero when there is no group. Merged `platform` ≤ 0 → 400 `"Platform budget must be greater than 0"`.
  - `cancel`: never creates. Missing project → 404 `"Project '<name>' does not exist"` with help `"Send action 'set' to create the project and its budget."`. Group present → `project_budget_service.delete_project_budget_group`; absent → 200 no-op. `amounts` and `admin_emails` are ignored (not rejected); response admin lists are empty.
  - Admins are assigned for `set` and `adjust` only.
  - Project name is validated (`ProjectService.validate_shared_project_name`) only when the project is missing and about to be created; the cost center code (`CostCenterService.validate_name`) only when the project is missing and the names differ. An existing project is never rejected for its names.
  - Every 4xx is raised before the first write: no sync session commit, no `create_shared_project_in_session`, no `get_or_create`, no `bulk_assign_users_to_project`, no budget-service write call.
  - Existing project's `cost_center` in the response comes from `cost_center_repository.get_active_by_id` (not `get_by_id`).
  - 409 (deleted project) and 403 (personal project) carry help text: 409 `"Restore the project or use a different CodeMie project name."`, 403 `"Use a shared project."`.
- Keep unchanged: two-transaction write order (sync then async), `IntegrityError` retry of the sync write once, group-create conflict (409 or `IntegrityError`) → rollback, re-read, update once; soft-limit preservation; stored `Application.name` used for every budget call; log line formats (`servicenow_budget_request_started|completed|failed`, `servicenow_project_resolved`, `servicenow_sync_conflict_retry`).
- No comments by default; match the file's docstring density.

## Review Focus

1. A check-phase error must leave zero writes, including on `adjust` against an existing project whose merge drops `platform` to 0 (admins must not be granted).
2. `cancel` with `admin_emails` must not grant admin rights, and with `amounts` must not fail.
3. An existing project whose `epam_project_code` doesn't match the cost-center pattern must work for all three actions.
4. `adjust` on a missing project creates project + group from the request alone (merge with zero).
5. A concurrently created group between check and write still converges (create conflict → update).

---

### Task 1: Request model — `set` requires platform, `cancel` ignores payload fields

Test-first: yes — new model tests fail: `set` without platform is accepted, `cancel` with `amounts` raises.

**Files:**
- Modify: `src/codemie/rest_api/models/servicenow_integration.py` (`ServiceNowBudgetRequest._validate_action_payload`)
- Test: `tests/codemie/rest_api/routers/test_servicenow_integration_router.py` (class `TestServiceNowBudgetRequest`)

**Interfaces:**
- Produces: `ServiceNowBudgetRequest` where, after validation, a `cancel` request has `amounts is None` and `admin_emails == []` regardless of input; a `set` request without `platform` or with `platform == 0` raises `ValidationError` (`"platform must be greater than 0 for set"`); `set`/`adjust` with missing or empty `amounts` still raise (`"amounts is required for set and adjust"`).

- [ ] **Step 1: Write the failing tests**

```python
    @pytest.mark.parametrize("amounts", [{"cli": 10.0}, {"platform": 0.0, "cli": 10.0}])
    def test_set_without_positive_platform_is_rejected(self, amounts):
        with pytest.raises(ValidationError, match="platform must be greater than 0 for set"):
            ServiceNowBudgetRequest(**_payload(action="set", amounts=amounts))

    def test_adjust_without_platform_is_accepted(self):
        assert ServiceNowBudgetRequest(**_payload(action="adjust", amounts={"cli": 10.0})).amounts

    def test_cancel_ignores_amounts_and_admin_emails(self):
        request = ServiceNowBudgetRequest(**_payload(action="cancel", amounts={"cli": 1.0}, admin_emails=["a@epam.com"]))
        assert request.amounts is None
        assert request.admin_emails == []
```

Remove the existing parametrized case that expects `cancel` with `amounts` to be rejected.

- [ ] **Step 2: Run to verify failure** — `poetry run pytest tests/codemie/rest_api/routers/test_servicenow_integration_router.py -k ServiceNowBudgetRequest -q -p no:cacheprovider` → the three new tests FAIL.
- [ ] **Step 3: Implement** in `_validate_action_payload`: for `cancel` set `self.amounts = None` and `self.admin_emails = []`; for `set`/`adjust` keep the non-empty check; for `set` require `self.amounts.get(BudgetCategory.PLATFORM, 0) > 0`.
- [ ] **Step 4: Run to verify pass** — same command → PASS; then the whole file → PASS.
- [ ] **Step 5: Commit** — `git add src/codemie/rest_api/models/servicenow_integration.py tests/codemie/rest_api/routers/test_servicenow_integration_router.py && git commit -m "EPMCDME-16127: Require platform for set and ignore payload fields on cancel"`

---

### Task 2: Separate action handlers with check-then-write

Test-first: yes — new handler tests fail against the current shared pipeline (cancel grants admins, adjust on a missing project returns 404, a failing check still provisions).

**Files:**
- Modify: `src/codemie/service/servicenow_integration_service.py`
- Test: `tests/codemie/service/test_servicenow_integration_service.py`

**Interfaces:**
- Consumes: Task 1 model guarantees (`cancel` has no amounts/admins; `set` has platform > 0).
- Produces (module-level pure functions, replacing `resolve_target_amounts`):
  - `replace_amounts(requested: dict[BudgetCategory, float]) -> dict[BudgetCategory, float]` — all three categories, missing → 0.
  - `merge_amounts(requested: dict[BudgetCategory, float], current: dict[BudgetCategory, float]) -> dict[BudgetCategory, float]` — all three categories, `current` overlaid with `requested`, missing in both → 0.
  - `build_category_specs` unchanged.
- Produces (classmethods on `ServiceNowIntegrationService`):
  - `apply_budget_request(actor, request) -> ServiceNowBudgetResult` — logs started/completed/failed and dispatches via `{SET: _handle_set, ADJUST: _handle_adjust, CANCEL: _handle_cancel}`; no other action logic.
  - `_handle_set(actor, request) -> ServiceNowBudgetResult` and `_handle_adjust(actor, request) -> ServiceNowBudgetResult` — share `_upsert(actor, request, compute_amounts)` where `compute_amounts(requested, current) -> dict` is `lambda r, c: replace_amounts(r)` / `merge_amounts`.
  - `_handle_cancel(actor, request) -> ServiceNowBudgetResult`.
  - `_find_project(session, project_name) -> Application | None` — read-only; soft-deleted → 409 (with help), personal → 403 (with help), else the row or `None`.
  - `_check_upsert(actor, request, compute_amounts) -> _UpsertPlan` — check phase: `_find_project`; if `None`, validate project name and (names differ) cost center code; read the group (by stored name, or none when the project is missing) and its current budgets; compute target amounts and `build_category_specs` (raises 400). Writes nothing.
  - `@dataclass class _UpsertPlan: project: Application | None; project_name: str; group_id: str | None; total: float; specs: dict[str, CategoryBudgetSpec]`.
  - Write phase reuses the existing sync transaction (`_resolve_and_assign`/`_sync_once`, adapted to take the `project` found in the check so it doesn't re-validate or re-look-up), then `_write_group(actor, plan)` which creates (with the existing conflict fallback) or updates using the precomputed `total`/`specs`.
  - Remove `_project_exists`, `NO_BUDGET_TO_ADJUST_MESSAGE`/`HELP`, `resolve_target_amounts`, and the action branching inside `_apply_budget`/`_create_group`/`_update_group` (fold them into `_write_group` and `_delete_group`).
- `_reuse_project` reads the cost center with `cost_center_repository.get_active_by_id`.

- [ ] **Step 1: Write the failing tests** (replace the superseded ones: `test_missing_project_is_404_without_provisioning`, `test_missing_project_checks_existence_read_only`, any `adjust`-without-group-404 test, any test calling `resolve_target_amounts`)

```python
class TestBudgetMath:
    def test_replace_amounts_zero_fills(self):
        assert replace_amounts({P: 500.0}) == {P: 500.0, C: 0.0, M: 0.0}

    def test_merge_amounts_overlays_current(self):
        assert merge_amounts({C: 200.0}, {P: 500.0, C: 100.0}) == {P: 500.0, C: 200.0, M: 0.0}

class TestHandlers:  # anyio; patch collaborators at codemie.service.servicenow_integration_service.*
    async def test_dispatch_routes_each_action_to_its_handler(self, actor): ...  # patch.object the three handlers; one call each

    async def test_cancel_missing_project_is_404_and_writes_nothing(self, actor): ...
        # _find_project -> None; expect 404 message/help above; _resolve_and_assign, delete never called

    async def test_cancel_never_assigns_admins(self, actor): ...
        # existing project + group; delete_project_budget_group awaited with group id; _assign_admins/bulk_assign not called; result admin lists == []

    async def test_cancel_existing_project_without_group_is_noop(self, actor): ...
        # 200 result, budget_group None, no delete call, no commit

    async def test_adjust_missing_project_creates_project_and_group_from_request(self, actor): ...
        # _find_project -> None; create_shared_project_in_session called; create_project_budget_group called with total == sum(request) and pct derived from request only

    async def test_adjust_merge_dropping_platform_is_400_and_writes_nothing(self, actor): ...
        # existing project, group with platform 500; request {platform: 0}; expect 400; no sync commit, no bulk_assign, no budget write

    async def test_invalid_cost_center_code_on_missing_project_is_400_and_writes_nothing(self, actor): ...
        # names differ, code "bad--code"; expect 400; no writes

    @pytest.mark.parametrize("action", ["set", "adjust", "cancel"])
    async def test_existing_project_ignores_invalid_cost_center_code(self, actor, action): ...
        # existing project, epam code "bad--code" != project name; no 400; CostCenterService.validate_name not called

    @pytest.mark.parametrize(("action", "expected_total"), [("set", 500.0), ("adjust", 550.0)])
    async def test_set_replaces_and_adjust_merges_on_existing_group(self, actor, action, expected_total): ...
        # current group P=500, C=100, M=50; request {platform: 300, cli: 200}
        # set -> update with total 500 and premium_models pct 0 (removed); adjust -> total 550 (premium_models kept at 50)

    async def test_group_created_concurrently_between_check_and_write_converges(self, actor): ...
        # check sees no group; create raises IntegrityError; rollback awaited; re-read returns group; update awaited once with the precomputed specs

    async def test_reused_project_reports_only_active_cost_center(self, session, actor): ...
        # cost_center_repository.get_active_by_id returns None for a deleted cc -> cost_center None

    async def test_deleted_and_personal_projects_carry_help(self, session): ...
        # 409 help == "Restore the project or use a different CodeMie project name."; 403 help == "Use a shared project."
```

Keep and adapt the existing tests for: soft-limit preservation, percentages summing to 100 / derive-back within 0.01, `IntegrityError` retry of the sync write, admins committed before the budget write, cache invalidation after commit, failure log carrying status/message, stored project name used for budget calls, `list_projects`.

- [ ] **Step 2: Run to verify failure** — `poetry run pytest tests/codemie/service/test_servicenow_integration_service.py -q -p no:cacheprovider` → new tests FAIL (import errors for `replace_amounts`/`merge_amounts` count as the expected failure).
- [ ] **Step 3: Implement** the handlers and helpers per the Interfaces block. The check phase may open its own read-only sync session for `_find_project` (run via `asyncio.to_thread`) and an async session to read the group; neither commits.
- [ ] **Step 4: Run to verify pass** — the service test file → PASS; then `poetry run pytest tests/codemie/service/test_servicenow_integration_service.py tests/codemie/rest_api/routers/test_servicenow_integration_router.py tests/codemie/rest_api/security/test_servicenow_access.py -q -p no:cacheprovider` → PASS; ruff check + format check on both files → clean.
- [ ] **Step 5: Commit** — `git add src/codemie/service/servicenow_integration_service.py tests/codemie/service/test_servicenow_integration_service.py && git commit -m "EPMCDME-16127: Split ServiceNow budget actions into check-then-write handlers"`
