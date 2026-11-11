# EPMCDME-17061 Shared budget row at member add: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Member add creates a missing `<budget_id>:shared` row before the allocation. Activity-event inserts stop hiding the caller's DB failures. Admin add routes answer a fixed 500.
**Architecture:** Pure functions in `budget_models.py` own the shared row. The async creators and a new sync creator in `ProjectAssignmentService` both use them. `insert`/`async_insert` flush the caller's writes outside the savepoint. A context manager in `membership.py` maps `SQLAlchemyError`.
**Tech Stack:** SQLModel/SQLAlchemy 2.0, FastAPI, pytest/pytest-asyncio.
**Spec:** `docs/superpowers/tasks/2026-11-10-epmcdme-17061/spec.md`. `technical-analysis.md` is in the same directory.

Commit per task using the repository's existing convention. Run tests with `poetry run pytest <path> -q`.

## Global Constraints
- Admin message, verbatim: `The member could not be assigned; nothing was saved.` The status is exactly 500, because the handler hides `details` only at 500 (`main.py:1657`). The routes map it; `ProjectAssignmentService` does not.
- The shared row is a DB write only:
  - no provider call and no `ON CONFLICT`;
  - the allocation stays `SyncStatus.PENDING`;
  - an existing shared row is never modified.
- Untouched:
  - `insert_strict` and the events default;
  - `provisioning_budget_service.py` and the provisioning guide;
  - override child rows;
  - the guards at `project_budget_service.py:2171` and `:2627`.
  
  No migration.
- AC6: existing budget and member-add tests pass unchanged. In `test_activity_repository.py`, only the flush stubs and counts at `:65-74`, `:109-117` and `:133-144` change.
- Spec deviation: the amount rule is a sibling function in `budget_models.py`, not part of the row builder. Existing tests pin `_ensure_shared_child_budget`'s `per_member_soft_budget`/`per_member_max_budget` kwargs (e.g. `test_project_budget_service.py:99-105`), and AC6 wins.
- Left to the flow's verification stage: reproduce the ticket on a local stack with events on and off and `is_local` off (`main.py:1584`). This covers AC2, AC5 and the provisioning half of AC3.

## Review Focus
1. Two categories both missing their row: the second loop iteration's autoflush must not hit the FK (Task 3).
2. `actor_id=None` (bootstrap): `created_by` falls back, and NOT NULL holds (Task 3).
3. Events off: `insert` adds no flush (Task 2).
4. A commit that raises `PendingRollbackError` maps to the fixed 500, and `ExtendedHTTPException(400)` passes through (Task 4).
5. A repeated add or resend creates no second row (Task 3).

---

### Task 1: One owner for the shared row's contents

**Files:**
- Modify `src/codemie/service/budget/budget_models.py:34-52`.
- Modify `src/codemie/service/budget/project_budget_service.py:362-365, 388-481, 1021-1024, 1690-1693, 2177-2180, 2633-2634`.
- Modify `src/codemie/service/budget/budget_service.py:1649-1654`.
- Create the test `tests/codemie/service/budget/test_shared_project_budget_row.py`.

**Produces, in `budget_models.py`:**
```python
class EqualMemberShare(NamedTuple):  # read by member_effective_max_budget like an equal allocation
    allocated_soft_budget: float
    allocated_max_budget: float

def child_budget_name(main_budget: Budget, suffix: str) -> str  # moved from project_budget_service.py:362-365
def shared_project_budget_amounts(main_budget: Budget, equal_share: Any | None, enforce_limit: bool) -> tuple[float, float]
def build_shared_project_budget_row(main_budget: Budget, *, project_name: str, actor_id: str | None,
                                    soft_budget: float, max_budget: float) -> dict[str, Any]
```
- Amounts:
  - `None` → the main budget's `(soft, max)`;
  - otherwise `(equal_share.allocated_soft_budget, member_effective_max_budget(equal_share, main_budget, enforce_limit))`.
- Row:
  - `budget_id=build_shared_project_budget_id(...)` and `created_by=actor_id or main_budget.created_by`;
  - the fields of `project_budget_service.py:409-426`, with `budget_origin_type="shared_default"`, `owner_user_id=None` and `name=child_budget_name(main, "shared")`.

**Test-first: yes. The tests import the new functions, which do not exist yet, so they fail with ImportError.**

- [ ] Step 1: Write the tests. Main budget: `b1`, name `p-platform`, soft 80, max 100, created_by `admin`. Equal allocation: soft 20, max 25.
  - Amounts:
    - no share → `(80, 100)`;
    - enforced → `(20, 25)`;
    - unenforced → `(20, 100)`, both for the allocation and for `EqualMemberShare(20, 25)`.
  - Row, with actor `actor-1`:
    - `budget_id "b1:shared"`, `created_by "actor-1"`;
    - origin `shared_default`, parent `b1`, owner `None`, `name "p-platform [shared]"`;
    - duration, category and description come from the main budget;
    - `deleted_at` and `detached_at` are `None`.
  - Row with actor `None` → `created_by "admin"`.
  - Async, with `budget_repository` patched:
    - row missing → `_ensure_shared_child_budget` calls `insert` with a `Budget` built from the row;
    - row exists → it calls `update` with the row minus `budget_id` and `created_by`.
- [ ] Step 2: Run the file. Expected: FAIL with ImportError.
- [ ] Step 3: Add the symbols. Then, in `project_budget_service.py`:
  - Remove `_child_budget_name`; callers use `child_budget_name`.
  - Move the get → insert/update tail of `_upsert_child_budget` (`:401-444`, logs included) into `async _upsert_budget_row(self, session, row: dict[str, Any]) -> Budget`. Its update payload is the row minus `budget_id` and `created_by`.
  - `_upsert_child_budget` keeps its override field dict and calls the new method.
  - `_ensure_shared_child_budget` keeps its signature (with `actor_id: str | None`) and upserts `build_shared_project_budget_row(...)`.
- [ ] Step 4: Replace the five amount expressions with `shared_project_budget_amounts`:
  - `:1021-1024` and `budget_service.py:1649-1654`: `allocation_rows[0] if allocation_rows else None`.
  - `:2177-2180` and `:2633-2634`: `allocation_rows[0]`.
  - `:1690-1693`: `EqualMemberShare(correct_soft, correct_max)`, with its result reassigned to `correct_soft, correct_max`.
- [ ] Step 5: Run `poetry run pytest tests/codemie/service/budget tests/enterprise/litellm/test_unused_project_member_budget.py -q`. Expected: PASS.

### Task 2: `insert`/`async_insert` stop hiding the caller's failures

**Files:**
- Modify `src/codemie/service/activity/activity_repository.py:142-187`.
- Create `tests/codemie/sqlite_harness.py` and `tests/codemie/service/activity/test_activity_repository_sqlite.py`.
- Modify `tests/codemie/service/activity/test_activity_repository.py`.

**Produces:** `constrained_sqlite_engine() -> Engine` in `tests/codemie/sqlite_harness.py`; Task 3 uses it.
- Engine: in-memory sqlite with `StaticPool` and `check_same_thread=False`.
- On `connect`: set `isolation_level = None` and run `PRAGMA foreign_keys=ON`.
- On `begin`: `exec_driver_sql("BEGIN")`. This is SQLAlchemy's pysqlite SAVEPOINT recipe.
- Compile `JSONB`/`ARRAY` to `JSON`, as `tests/codemie/service/project/registration_db.py:27-30` does.

**Test-first: yes. With events on, a caller's pending row that violates an FK should make `insert` raise `IntegrityError`. Today `insert` returns `None` and the next commit raises `PendingRollbackError`.**

- [ ] Step 1: Write the tests.
  - sqlite tests: enable events by patching `activity_repository.config`. Probe tables: `parent(id PK)` and `child(id PK, parent_id FK)`.
    - `test_insert_propagates_callers_pending_failure`: a pending orphan `child` makes `insert` raise `IntegrityError`. After that, `rollback()` and `SELECT 1` succeed, and no `child` row exists.
    - `test_insert_swallows_event_row_failure_and_caller_commits`: there is no `activity_events` table and a `parent` row is pending. `insert` returns `None`, `commit()` succeeds, and the `parent` row is stored.
  - Mock tests:
    - `test_insert_disabled_does_not_flush`.
    - `test_async_insert_propagates_callers_flush_failure`: flush raises `IntegrityError`, so `async_insert` raises it and `begin_nested` is not called.
- [ ] Step 2: Run both files. Expected: the propagate tests FAIL.
- [ ] Step 3: In both methods, flush the caller's writes after the `ACTIVITY_EVENTS_ENABLED` check and before the `try`. Only the event row stays inside `begin_nested` and the catch-all. Update the existing stubs:
  - `:65`: flush count 2.
  - `:109`: the `insert` half uses `side_effect=[None, RuntimeError("db down")]` and still asserts `None`.
  - `:133`: flush await count 2.
- [ ] Step 4: Run `poetry run pytest tests/codemie/service/activity -q`. Expected: PASS.

### Task 3: Member add creates the missing shared row first

**Files:**
- Modify `src/codemie/service/project/project_assignment_service.py:46-51, 104-189`.
- Create the test `tests/codemie/service/project/test_member_added_shared_budget_sqlite.py`.

**Consumes:** Task 1's functions and Task 2's `constrained_sqlite_engine`.
**Produces, static methods on `ProjectAssignmentService`:**
- `_first_equal_allocation(session, project_name, budget_category, project_budget_id) -> Optional[ProjectMemberBudgetAssignment]`. This is the query now at `:119-127`; `_get_member_added_allocation_amounts` reuses it.
- `_ensure_shared_budget_row(session: Session, budget: Budget, project_name: str, actor_id: Optional[str]) -> None`.

**Test-first: yes. On a constrained sqlite session, the first member add to an assignment with no `b1:shared` row fails with a FOREIGN KEY error.**

- [ ] Step 1: Write the tests.
  - Fixture:
    - FK parent tables `users` and `applications`: raw-DDL stubs, or model tables as `registration_db.py` creates them, whichever lets `create_all` resolve the FKs.
    - `create_all` for `Budget`, `ProjectBudgetGroup`, `ProjectBudgetAssignment` and `ProjectMemberBudgetAssignment`.
    - Seed project `p`; users `u0` and `u1`; main budget `b1` (`p-platform`, soft 80, max 100, created_by `admin`) and its assignment.
    - Patch `SettingsService.get_enforce_member_spend_limits`.
  - Each test calls `_sync_project_budget_member_added(session, "p", "u1", actor)`, then `commit()`.
    - `test_first_member_creates_shared_row_then_allocation` (actor `actor-1`): row `b1:shared` matches Task 1, with soft 80, max 100 and `created_by "actor-1"`. The allocation has `shared_budget_id == effective_budget_id == "b1:shared"` and `PENDING`.
    - `test_bootstrap_without_actor_uses_main_creator` (actor `None`) → `created_by "admin"`.
    - `test_existing_shared_row_left_untouched`: a pre-seeded row with soft 10, max 20 keeps those values.
    - `test_shared_row_amounts_follow_member_limits`, parametrized on enforce True/False. `u0` has an equal allocation (soft 30, max 40, `shared_budget_id=None`). Expected soft 30; max 40 when enforced, 100 when not.
    - `test_two_categories_each_get_a_shared_row`: a second assignment `b2` is also missing its row. Both rows are created and the commit succeeds.
    - `test_repeat_add_is_idempotent`: a second call leaves one row and one allocation.
- [ ] Step 2: Run the file. Expected: the first test FAILS with `IntegrityError`.
- [ ] Step 3: Implement `_ensure_shared_budget_row`:
  - First call `session.get(Budget, build_shared_project_budget_id(...))`. If a row is found, return before any other query; this keeps the mock `exec` sequences in `test_project_assignment_service.py` intact.
  - Otherwise take the amounts from `_first_equal_allocation` and `SettingsService.get_enforce_member_spend_limits`. Import `SettingsService` locally, as `project_budget_service.py:1263` does.
  - Call `session.add(Budget(**row))`, then an explicit `session.flush()`, because no `relationship()` orders the inserts.
  - Log `budget_event=project_shared_budget_created` at info.
  - Call `_ensure_shared_budget_row` right after the main-budget check (`:166`).
- [ ] Step 4: Run `poetry run pytest tests/codemie/service/project tests/codemie/service/user tests/codemie/service/test_provisioning_service.py tests/codemie/service/test_provisioning_budget_service.py -q`. Expected: PASS.

### Task 4: Admin add routes answer the fixed 500

**Files:**
- Modify `src/codemie/rest_api/routers/projects/membership.py:15-63, 116-146, 179-216`.
- Test: `tests/codemie/rest_api/routers/test_projects_router.py`.

**Produces, module-private in `membership.py`:**
- `MEMBER_ASSIGNMENT_FAILED_MESSAGE`: the Global Constraints string.
- `@contextmanager _member_assignment_errors() -> Iterator[None]`. It maps `SQLAlchemyError as exc` to `raise ExtendedHTTPException(code=500, message=MEMBER_ASSIGNMENT_FAILED_MESSAGE, details=str(exc)) from exc`.

**Test-first: yes. When the service raises `IntegrityError`, `assign_user_to_project` should raise `ExtendedHTTPException(500, MEMBER_ASSIGNMENT_FAILED_MESSAGE)`. Today the raw error escapes.**

- [ ] Step 1: Write the tests, following the pattern at `:1586-1653` (patch `membership.get_session` and the service). Cover single assign, bulk assign and CSV import, parametrized over two failures: the service raises `IntegrityError`, or `commit` raises `PendingRollbackError`. Expected: `code == 500`, the fixed message, and `__cause__` set to the original exception. Add one case where the service raises `ExtendedHTTPException(400)`; it passes through unchanged.
- [ ] Step 2: Run the tests. Expected: FAIL.
- [ ] Step 3: In those three routes, wrap the service call and `session.commit()` in `with _member_assignment_errors():`, inside `with get_session()`. The update and remove routes do not change.
- [ ] Step 4: Run `poetry run pytest tests/codemie/rest_api/routers/test_projects_router.py -q`. Expected: PASS.
