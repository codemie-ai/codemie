# Project-Scoped Activity Trail (EPMCDME-14325) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attribute every `activity_events` row to a project (where one applies) and to a snapshot
of the actor's email/display name, then expose a new project-scoped read endpoint authorized for
that project's admins or platform admins/maintainers, leaving the existing global endpoint
untouched.

**Architecture:** Add three nullable columns (`project_name`, `actor_email`, `actor_display_name`)
to `ActivityEvent`/`ActivityEventCreate` via a new Alembic migration. Thread the two new snapshot
values through all 36 existing `ActivityEventCreate(...)` call sites across 10 files. Add a
`find_by_project` repository method (mirrors `find_all`'s conditions/count pattern, gated by
`ACTIVITY_EVENTS_ENABLED`), a `list_project_events` service method, a new `Depends`-based
authorization function modeled on `project_admin_or_admin_user_detail_access`, and a new router
under `/v1/projects/{project_name}/activity-events`.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy, Alembic, pytest/pytest-asyncio, `unittest.mock`.

**Spec:** `docs/superpowers/tasks/2026-09-17-project-scoped-activity-trail/spec.md`

**Commit per task using the repository's existing convention.**

## Global Constraints

- `project_name` is denormalized, nullable, no FK; null means "no project context" (never empty
  string).
- `actor_email`/`actor_display_name` are write-time snapshots; `actor_id`'s existing FK/null-on-delete
  behavior is untouched.
- The migration must not modify `domain`, `event_type`, `entity_id`, `actor_id`, or `created_at` on
  any existing row (AC13).
- The existing global endpoint (`/v1/admin/activity-events`) and its filters/authorization/response
  shape must not change (AC10).
- No backfill of pre-existing rows (Non-goal). No change to `attributes` JSON shape at any emit site
  (Non-goal). No derivation of project attribution via joins at read time (Non-goal).
- Personal-project creation always passes `project_name=None` — this is how AC9's exclusion is
  enforced (not a runtime filter).
- The two scheduler emit sites with no actor (`active_project_budget_restore_service.py`,
  `inactive_project_budget_stop_service.py`) must pass `None` for both actor-snapshot fields.

---

### Task 1: Add columns to `ActivityEvent` and `ActivityEventCreate`

**Files:**
- Modify: `src/codemie/service/activity/activity_models.py:91-163`
- Test: `tests/codemie/service/activity/test_activity_models.py`

**Interfaces:**
- Produces: `ActivityEvent.project_name: str | None`, `ActivityEvent.actor_email: str | None`,
  `ActivityEvent.actor_display_name: str | None`; same three fields (optional) on
  `ActivityEventCreate`; new index `ix_activity_events_project_name_created_at` on
  `(project_name, created_at)`.

Test-first: yes — a test asserting `ActivityEventCreate(domain=..., event_type=..., project_name="acme", actor_email="a@b.com", actor_display_name="A B")` constructs successfully and round-trips those three fields unchanged, and that omitting them defaults all three to `None`.

- [ ] **Step 1: Write the failing test** in `test_activity_models.py`:

```python
def test_activity_event_create_accepts_project_and_actor_snapshot_fields():
    event = ActivityEventCreate(
        domain=ActivityDomain.PROJECT_MANAGEMENT,
        event_type=ProjectManagementEvent.PROJECT_CREATED,
        project_name="acme",
        actor_email="a@b.com",
        actor_display_name="A B",
    )
    assert event.project_name == "acme"
    assert event.actor_email == "a@b.com"
    assert event.actor_display_name == "A B"


def test_activity_event_create_defaults_project_and_actor_snapshot_to_none():
    event = ActivityEventCreate(domain="d", event_type="e")
    assert event.project_name is None
    assert event.actor_email is None
    assert event.actor_display_name is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/activity/test_activity_models.py -v`
Expected: FAIL — `ActivityEventCreate` has no field `project_name`.

- [ ] **Step 3: Implement**

In `ActivityEvent` (after `attributes`), add:
```python
    project_name: Optional[str] = Field(default=None, sa_column=Column(VARCHAR(255), nullable=True))
    actor_email: Optional[str] = Field(default=None, sa_column=Column(VARCHAR(255), nullable=True))
    actor_display_name: Optional[str] = Field(default=None, sa_column=Column(VARCHAR(255), nullable=True))
```
Add to `__table_args__`:
```python
        Index("ix_activity_events_project_name_created_at", "project_name", "created_at"),
```
On `ActivityEventCreate`, add the same three optional fields (`project_name`, `actor_email`,
`actor_display_name: Optional[str] = None`) alongside `attributes`.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/service/activity/test_activity_models.py -v` — Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 2: Alembic migration + migration-safety test

**Files:**
- Create: `src/external/alembic/versions/<new_revision>_add_project_and_actor_snapshot_to_activity_events.py`
- Test: `tests/external/alembic/test_activity_events_project_actor_migration.py` (new file — no
  existing migration test suite was found for this table)

**Interfaces:**
- Consumes: `ActivityEvent` columns from Task 1 (must match what the migration creates).
- Produces: revision id `<new_revision>`, `down_revision = "s9t0u1v2w3x4"` (the confirmed current
  head on the branch touching `activity_events` — NOT `38255069bfab`, which has a downstream
  migration).

Test-first: yes — a test that runs `upgrade()`/`downgrade()` against a throwaway table state (or asserts via `op` call inspection) that only `project_name`, `actor_email`, `actor_display_name` columns and the new index are added, and that no `ALTER`/`UPDATE` touches `domain`, `event_type`, `entity_id`, `actor_id`, or `created_at`.

- [ ] **Step 1: Write the failing test** — patch `alembic.op` and assert the migration module's
  `upgrade()` calls only `add_column` (x3, nullable) and `create_index`, never `alter_column`,
  `execute` with UPDATE, or any op touching the five protected columns:

```python
from unittest.mock import MagicMock, patch

import importlib


def test_upgrade_only_adds_nullable_columns_and_index_never_touches_protected_columns():
    migration = importlib.import_module(
        "external.alembic.versions.<new_revision>_add_project_and_actor_snapshot_to_activity_events"
    )
    with patch("alembic.op.add_column") as add_column, patch("alembic.op.create_index") as create_index, \
         patch("alembic.op.alter_column") as alter_column, patch("alembic.op.execute") as execute:
        migration.upgrade()
    assert add_column.call_count == 3
    for call in add_column.call_args_list:
        assert call.args[1].nullable is True
    assert create_index.call_count == 1
    alter_column.assert_not_called()
    execute.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/external/alembic/test_activity_events_project_actor_migration.py -v`
Expected: FAIL — module does not exist yet.

- [ ] **Step 3: Implement the migration** following the existing
  `38255069bfab_create_activity_events_table.py` file's header/`revision`/`down_revision` shape.
  `upgrade()`:
```python
def upgrade() -> None:
    op.add_column("activity_events", sa.Column("project_name", sa.VARCHAR(255), nullable=True))
    op.add_column("activity_events", sa.Column("actor_email", sa.VARCHAR(255), nullable=True))
    op.add_column("activity_events", sa.Column("actor_display_name", sa.VARCHAR(255), nullable=True))
    op.create_index(
        "ix_activity_events_project_name_created_at",
        "activity_events",
        ["project_name", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_activity_events_project_name_created_at", table_name="activity_events")
    op.drop_column("activity_events", "actor_display_name")
    op.drop_column("activity_events", "actor_email")
    op.drop_column("activity_events", "project_name")
```
Set `revision`/`down_revision = "s9t0u1v2w3x4"` per the header conventions in the reference file.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/external/alembic/test_activity_events_project_actor_migration.py -v`
Expected: PASS. Also run `alembic heads` to confirm this migration is now the sole head on this
branch (no fork introduced).

- [ ] **Step 5: Commit**

---

### Task 3: Emit sites — project domain (`project_service.py`, `personal_project_service.py`)

**Files:**
- Modify: `src/codemie/service/project/project_service.py:160-166,243-249,543-...` (3 sites:
  `PROJECT_CREATED`, `PROJECT_UPDATED`, `PROJECT_DELETED`)
- Modify: `src/codemie/service/project/personal_project_service.py:84-90` (1 site)
- Test: `tests/codemie/service/project/test_project_service.py`,
  `tests/codemie/service/project/test_personal_project_service.py`

**Interfaces:**
- Consumes: `ActivityEventCreate(project_name=..., actor_email=..., actor_display_name=...)` from
  Task 1.

Test-first: yes — for each of the 3 `project_service.py` sites, a test asserting the emitted `ActivityEventCreate.project_name` equals the project name used at that call (`validated_name`/`project.name`/`project_name`) and `actor_email`/`actor_display_name` equal `user.email`/`user.name`; for `personal_project_service.py`, a test asserting `project_name is None` on the emitted event despite `entity_id=user_email`.

- [ ] **Step 1: Write the failing tests** (one per site) patching `activity_event_repository` and
  inspecting the `ActivityEventCreate` passed to `insert`/`async_insert`, e.g.:

```python
def test_create_project_emits_project_name_and_actor_snapshot(mock_activity_repo, ...):
    project_service.create_project(..., user=user)
    event = mock_activity_repo.insert.call_args.args[0]
    assert event.project_name == validated_name
    assert event.actor_email == user.email
    assert event.actor_display_name == user.name


def test_personal_project_creation_emits_null_project_name(mock_activity_repo, ...):
    personal_project_service.create_personal_project(..., user_id=user_id, user_email=user_email)
    event = mock_activity_repo.insert.call_args.args[0]
    assert event.project_name is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/project/test_project_service.py tests/codemie/service/project/test_personal_project_service.py -v`
Expected: FAIL — `ActivityEventCreate` calls don't pass the new kwargs yet, so assertions on
`None`/missing attribute mismatch expected values.

- [ ] **Step 3: Implement.** At each `project_service.py` site add
  `project_name=<validated_name|project.name|project_name>, actor_email=user.email,
  actor_display_name=user.name`. At the `personal_project_service.py` site add
  `project_name=None, actor_email=<actor's email if a user object is in scope>,
  actor_display_name=<actor's display name>` — keep `project_name=None` unconditionally.

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2 — Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 4: Emit sites — project membership (`project_assignment_service.py`, 5 sites)

**Files:**
- Modify: `src/codemie/service/project/project_assignment_service.py:419-425,497-503,620-626,730-736,799-805`
- Test: `tests/codemie/service/project/test_project_assignment_service.py`

**Interfaces:**
- Consumes: same `ActivityEventCreate` kwargs as Task 1. `actor: User` is already in scope at
  every site (per `assign_user_to_project`/`update_project_role`/`bulk_assign_users_to_project`/
  `bulk_remove_users_from_project`/`remove_user_from_project` signatures); `project_name` is already
  computed at each site (it is already written into `attributes["project_name"]`).

Test-first: yes — for each of the 5 sites, a test asserting the emitted event's `project_name` equals the value already present in `attributes["project_name"]`, and `actor_email`/`actor_display_name` equal `actor.email`/`actor.name`, while `attributes` itself is unchanged (backward compatibility).

- [ ] **Step 1: Write the failing tests**, extending the existing activity-emission assertions in
  `test_bulk_assign_upsert_existing`/`test_bulk_remove_success` and adding equivalents for the three
  single-user methods:

```python
def test_assign_user_to_project_emits_project_name_matching_attributes(mock_activity_repo, ...):
    ProjectAssignmentService.assign_user_to_project(session, project_name, user_id, actor=actor, ...)
    event = mock_activity_repo.insert.call_args.args[0]
    assert event.project_name == project_name == event.attributes["project_name"]
    assert event.actor_email == actor.email
    assert event.actor_display_name == actor.name
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/project/test_project_assignment_service.py -v`
Expected: FAIL

- [ ] **Step 3: Implement.** At each of the 5 sites, add
  `project_name=project_name, actor_email=actor.email, actor_display_name=actor.name` alongside the
  existing `attributes={"project_name": project_name, ...}` (leave `attributes` untouched).

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2 — Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 5: Emit sites — budget domain with project context (`project_budget_service.py`, 11 sites)

**Files:**
- Modify: `src/codemie/service/budget/project_budget_service.py:988-994,1254-1260,1300-1306,1408-1414,
  1472-1478,1563-1569,1739-1745,1934-1940,2108-2114,2348-2354,2384-2390`
- Test: `tests/codemie/service/budget/test_budget_service_activity.py` (extend) or a new
  `tests/codemie/service/budget/test_project_budget_service_activity.py`

**Interfaces:**
- Consumes: `ProjectBudgetAssignment.project_name`, `ProjectBudgetGroup.project_name`,
  `ProjectMemberBudgetAssignment.project_name` (`budget_models.py:104,212,259,306` — already loaded
  at every one of these 11 sites via `budget`/`group`/`data` objects).

Test-first: yes — for each event type, a test asserting the emitted event's `project_name` equals the owning model's `project_name` (not just the 2 sites that already had it in `attributes`), and `actor_display_name` equals the existing `actor_name` parameter already threaded through this service (actor_email is not available at this layer — pass `None`, since no email is threaded through and the spec forbids adding a new query).

- [ ] **Step 1: Write the failing tests**, one assertion group per event type, e.g.:

```python
def test_project_budget_rebalanced_emits_project_name_from_budget(mock_activity_repo, ...):
    await project_budget_service.rebalance_project_budget(session, budget_id, actor_id, actor_name)
    event = mock_activity_repo.async_insert.call_args.args[0]
    assert event.project_name == budget.project_name
    assert event.actor_display_name == actor_name
    assert event.actor_email is None
```
Repeat for `PROJECT_BUDGET_CREATED/UPDATED/DELETED/REBALANCED/RESET`,
`PROJECT_BUDGET_GROUP_CREATED/UPDATED/RESET/REBALANCED`,
`MEMBER_ALLOCATION_OVERRIDDEN/OVERRIDE_CLEARED`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/budget/ -k project_budget -v`
Expected: FAIL

- [ ] **Step 3: Implement.** At each of the 11 sites add
  `project_name=<budget|group|data>.project_name, actor_display_name=actor_name, actor_email=None`.
  Leave existing `attributes` dicts (including the 2 sites already setting
  `attributes={"project_name": ...}`) unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2 — Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 6: Emit sites — budget domain without project context (`budget_service.py`, 7 sites; scheduler files, 3 sites)

**Files:**
- Modify: `src/codemie/service/budget/budget_service.py:261-267,350-356,717-723,1125-1131,1147-1153,
  1229-1235,1242-1248`
- Modify: `src/codemie/service/budget/active_project_budget_restore_service.py:93-99` (no actor)
- Modify: `src/codemie/service/budget/inactive_project_budget_stop_service.py:53-58` (no actor)
- Modify: `src/codemie/service/budget/inactive_cost_center_budget_stop_service.py:58-64` (no actor)
- Test: `tests/codemie/service/budget/test_budget_service_activity.py` (extend)

**Interfaces:**
- Consumes: same `ActivityEventCreate` kwargs as Task 1.

Test-first: yes — for the 7 `budget_service.py` sites, a test asserting `project_name is None` (these are global-budget/user-budget-assignment events with no project) and `actor_display_name == actor_name` (already-threaded param), `actor_email is None`; for the 3 scheduler sites, a test asserting `project_name is None` and both actor-snapshot fields are `None` (no actor at all).

- [ ] **Step 1: Write the failing tests**, e.g.:

```python
def test_create_budget_emits_null_project_name(mock_activity_repo, ...):
    await budget_service.create_budget(session, data, actor_id, actor_name)
    event = mock_activity_repo.async_insert.call_args.args[0]
    assert event.project_name is None
    assert event.actor_display_name == actor_name
    assert event.actor_email is None


def test_restore_project_budget_emits_null_actor_snapshot(mock_activity_repo, ...):
    await active_project_budget_restore_service.restore(...)
    event = mock_activity_repo.async_insert.call_args.args[0]
    assert event.project_name is None
    assert event.actor_email is None
    assert event.actor_display_name is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/budget/test_budget_service_activity.py -v`
Expected: FAIL

- [ ] **Step 3: Implement.** At each of the 7 `budget_service.py` sites add
  `project_name=None, actor_email=None, actor_display_name=actor_name`. At each of the 3 scheduler
  sites add `project_name=None, actor_email=None, actor_display_name=None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2 — Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 7: Emit sites — user management, auth, and logout (6 files, 10 sites, no project context)

**Files:**
- Modify: `src/codemie/service/user/user_management_service.py` (3 sites: `USER_CREATED`,
  `USER_UPDATED`, `USER_DEACTIVATED`)
- Modify: `src/codemie/service/user/registration_service.py` (1 site: `USER_CREATED`,
  `actor_id=None`)
- Modify: `src/codemie/service/user/authentication_service.py` (4 sites: `USER_CREATED` x2,
  `USER_LOGIN` x2)
- Modify: `src/codemie/rest_api/routers/local_auth_router.py:243-249` (1 site: `USER_LOGOUT`)
- Test: existing test files for each of these services (extend in place)

**Interfaces:**
- Consumes: same `ActivityEventCreate` kwargs as Task 1.

Test-first: yes — one test per file asserting the emitted event's `project_name is None` (none of these 10 sites have project context) and, where a user/actor object is already in scope, `actor_email`/`actor_display_name` match it; where `actor_id=None` (registration_service.py), both snapshot fields are also `None`.

- [ ] **Step 1: Write the failing tests**, e.g.:

```python
def test_user_login_emits_null_project_name_and_actor_snapshot(mock_activity_repo, ...):
    authentication_service.login(..., user=user)
    event = mock_activity_repo.insert.call_args.args[0]
    assert event.project_name is None
    assert event.actor_email == user.email
    assert event.actor_display_name == user.name


def test_registration_emits_null_actor_snapshot_when_no_actor(mock_activity_repo, ...):
    registration_service.register(...)
    event = mock_activity_repo.insert.call_args.args[0]
    assert event.actor_email is None
    assert event.actor_display_name is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/user/ tests/codemie/rest_api/routers/test_local_auth_router.py -v`
Expected: FAIL

- [ ] **Step 3: Implement.** At each of the 10 sites add `project_name=None`. Where a `user`/`actor`
  object with `.email`/`.name` is already in scope, add `actor_email=user.email,
  actor_display_name=user.name`; at the `registration_service.py` site (`actor_id=None`), add
  `actor_email=None, actor_display_name=None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2 — Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 8: Repository — `find_by_project` with `ACTIVITY_EVENTS_ENABLED` gating

**Files:**
- Modify: `src/codemie/service/activity/activity_repository.py:51-343`
- Test: `tests/codemie/service/activity/test_activity_repository.py`

**Interfaces:**
- Consumes: `ActivityEvent.project_name`/`actor_email`/`actor_display_name` (Task 1);
  `config.ACTIVITY_EVENTS_ENABLED` (existing).
- Produces:
  `find_by_project(self, project_name: str, *, domain: list[str] | None = None, event_type: list[str] | None = None, actor_id: str | None = None, from_dt: datetime | None = None, to_dt: datetime | None = None, sort_dir: str = "desc", limit: int, offset: int, session: Session) -> tuple[list[ActivityEventRow], int]`
  added to both `ActivityEventRepository` (abstract) and `SQLActivityEventRepository` (concrete).

Test-first: yes — a test asserting `find_by_project` filters strictly on `project_name` plus the optional filters, orders `created_at DESC, id DESC` (tie-break), returns `(rows, total)` with `total` computed via the same `conditions` list as the paginated query, prefers the snapshot `actor_email`/`actor_display_name` over the `UserDB` join when present and falls back to the join when the snapshot is null, and returns `([], 0)` immediately when `config.ACTIVITY_EVENTS_ENABLED` is `False` without touching the session.

- [ ] **Step 1: Write the failing tests**:

```python
def test_find_by_project_filters_and_paginates(session_with_events):
    rows, total = SQLActivityEventRepository().find_by_project(
        "acme", limit=10, offset=0, session=session_with_events
    )
    assert all(r.project_name == "acme" for r in rows)  # extend ActivityEventRow with project_name
    assert total == expected_count


def test_find_by_project_returns_empty_when_disabled(monkeypatch, session):
    monkeypatch.setattr(config, "ACTIVITY_EVENTS_ENABLED", False)
    rows, total = SQLActivityEventRepository().find_by_project("acme", limit=10, offset=0, session=session)
    assert rows == [] and total == 0


def test_find_by_project_prefers_snapshot_over_join(session_with_mixed_rows):
    rows, _ = SQLActivityEventRepository().find_by_project("acme", limit=10, offset=0, session=session_with_mixed_rows)
    assert rows[0].actor_email == "snapshot@x.com"  # row has non-null actor_email column
    assert rows[1].actor_email == "joined@x.com"    # row has null actor_email, falls back to UserDB join
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/activity/test_activity_repository.py -v`
Expected: FAIL — `find_by_project` does not exist.

- [ ] **Step 3: Implement.** Add `project_name`, `actor_email`, `actor_display_name` fields to
  `ActivityEventRow` (dataclass, `activity_repository.py:38-48`). Add the abstract method to
  `ActivityEventRepository`. Implement `find_by_project` on `SQLActivityEventRepository` following
  `find_all`'s exact shape (`activity_repository.py:232-294`): build `conditions` starting with
  `ActivityEvent.project_name == project_name`, add the optional filters, reuse `conditions` for
  both the `count()` statement and the paginated `select()`; order by
  `desc(ActivityEvent.created_at), desc(ActivityEvent.id)` (or `asc(...)` variants) for deterministic
  tie-break; select
  `func.coalesce(ActivityEvent.actor_email, UserDB.email).label("actor_email")` and the equivalent
  `coalesce` for `actor_display_name`/`UserDB.name`, still via the existing `outerjoin`; guard the
  whole method with `if not config.ACTIVITY_EVENTS_ENABLED: return [], 0` before touching the
  session.

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2 — Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 9: Service — `list_project_events`

**Files:**
- Modify: `src/codemie/service/activity/activity_event_service.py:26-81`
- Test: `tests/codemie/service/activity/test_activity_event_service.py`

**Interfaces:**
- Consumes: `SQLActivityEventRepository.find_by_project(...)` (Task 8); `ActivityEventListItem`
  (existing, needs `project_name` field — see Task 10).
- Produces:
  `ActivityEventService.list_project_events(self, project_name: str, *, domain=None, event_type=None, actor_id=None, from_dt=None, to_dt=None, sort_dir="desc", limit=50, offset=0) -> tuple[list[ActivityEventListItem], int]`

Test-first: yes — a test asserting `list_project_events` opens a session, calls `activity_event_repository.find_by_project` with the given `project_name` and filters, and maps each returned `ActivityEventRow` (including its `project_name`) to an `ActivityEventListItem`.

- [ ] **Step 1: Write the failing test**, mirroring the existing `test_list_events` test's
  `@patch` structure:

```python
def test_list_project_events_delegates_to_find_by_project(mock_repo, mock_session):
    mock_repo.find_by_project.return_value = ([row], 1)
    items, total = activity_event_service.list_project_events("acme", limit=10, offset=0)
    mock_repo.find_by_project.assert_called_once()
    assert mock_repo.find_by_project.call_args.kwargs["project_name"] == "acme" or \
        mock_repo.find_by_project.call_args.args[0] == "acme"
    assert items[0].project_name == row.project_name
    assert total == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/activity/test_activity_event_service.py -v`
Expected: FAIL — `list_project_events` does not exist.

- [ ] **Step 3: Implement.** Add `list_project_events` to `ActivityEventService`, mirroring
  `list_events` (`activity_event_service.py:27-69`): open `Session(PostgresClient.get_engine())`,
  call `activity_event_repository.find_by_project(project_name, ...)`, map rows to
  `ActivityEventListItem(..., project_name=r.project_name)`.

- [ ] **Step 4: Run test to verify it passes**

Run: same command as Step 2 — Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 10: API models — add `project_name` to `ActivityEventListItem`

**Files:**
- Modify: `src/codemie/rest_api/models/activity_event.py`
- Test: `tests/codemie/rest_api/models/test_activity_event_models.py`

**Interfaces:**
- Produces: `ActivityEventListItem.project_name: str | None` (new optional field, default `None`,
  so the existing global endpoint's response is unaffected when the field is absent from its rows —
  AC10).

Test-first: yes — a test asserting `ActivityEventListItem` accepts and serializes a `project_name` value, and that constructing one without it still succeeds with `project_name=None` (preserving the global endpoint's existing behavior).

- [ ] **Step 1: Write the failing test**:

```python
def test_activity_event_list_item_accepts_optional_project_name():
    item = ActivityEventListItem(id="1", domain="d", event_type="e", entity_type=None,
                                  entity_id=None, actor_id=None, actor_email=None, actor_name=None,
                                  attributes=None, created_at=datetime.now(timezone.utc),
                                  project_name="acme")
    assert item.project_name == "acme"

    item2 = ActivityEventListItem(id="1", domain="d", event_type="e", entity_type=None,
                                   entity_id=None, actor_id=None, actor_email=None, actor_name=None,
                                   attributes=None, created_at=datetime.now(timezone.utc))
    assert item2.project_name is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/models/test_activity_event_models.py -v`
Expected: FAIL — unexpected keyword argument `project_name`.

- [ ] **Step 3: Implement.** Add `project_name: str | None = None` to `ActivityEventListItem`.

- [ ] **Step 4: Run test to verify it passes**

Run: same command as Step 2 — Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 11: Authorization dependency — project-admin-or-platform-admin

**Files:**
- Modify: `src/codemie/rest_api/security/authentication.py` (add new function near
  `project_admin_or_admin_user_detail_access`, `authentication.py:255-329`)
- Test: `tests/codemie/rest_api/security/test_authentication.py` (create the test module if none
  exists for this file — analysis found no test file for the closest precedent)

**Interfaces:**
- Consumes: `request.state.user` (existing `User`), `request.path_params["project_name"]`,
  `user_project_repository.is_admin(session, user_id, project_name) -> bool` (existing,
  `user_project_repository.py:195`).
- Produces: `async def project_admin_or_admin_or_maintainer_access(request: Request) -> None` —
  returns `None` if authorized, raises `ExtendedHTTPException(code=403, ...)` otherwise.

Test-first: yes — three tests: (1) an admin/maintainer user passes without a repository call; (2) a user who is project admin of the path's `project_name` (`is_admin` returns `True`) passes; (3) a user who is neither is denied with a 403 `ExtendedHTTPException` carrying `ACCESS_DENIED_MESSAGE`, and the denial path never queries for event existence/counts.

- [ ] **Step 1: Write the failing tests**:

```python
@pytest.mark.asyncio
async def test_admin_or_maintainer_passes_without_project_check():
    request = make_request(user=make_user(is_admin_or_maintainer=True))
    await project_admin_or_admin_or_maintainer_access(request)  # no exception


@pytest.mark.asyncio
async def test_project_admin_of_path_project_passes(monkeypatch):
    monkeypatch.setattr(user_project_repository, "is_admin", lambda session, uid, name: True)
    request = make_request(user=make_user(is_admin_or_maintainer=False), path_params={"project_name": "acme"})
    await project_admin_or_admin_or_maintainer_access(request)  # no exception


@pytest.mark.asyncio
async def test_non_admin_non_project_admin_denied(monkeypatch):
    monkeypatch.setattr(user_project_repository, "is_admin", lambda session, uid, name: False)
    request = make_request(user=make_user(is_admin_or_maintainer=False), path_params={"project_name": "acme"})
    with pytest.raises(ExtendedHTTPException) as exc_info:
        await project_admin_or_admin_or_maintainer_access(request)
    assert exc_info.value.code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/security/test_authentication.py -v`
Expected: FAIL — function does not exist.

- [ ] **Step 3: Implement**, modeled directly on `project_admin_or_admin_user_detail_access`:

```python
async def project_admin_or_admin_or_maintainer_access(request: Request):
    """Admin/maintainer, or the project admin of the path's project_name; else 403."""
    from codemie.clients.postgres import get_session
    from codemie.repository.user_project_repository import user_project_repository

    user = request.state.user
    if user.is_admin_or_maintainer:
        return

    project_name = request.path_params.get("project_name")
    if project_name:
        with get_session() as session:
            if user_project_repository.is_admin(session, user.id, project_name):
                return

    logger.warning(f"access_denied_project_scope: actor_user_id={user.id}, domain=activity_events")
    raise ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message=ACCESS_DENIED_MESSAGE,
        details="This action requires administrator, maintainer, or project admin privileges for this project.",
        help="If you believe you should have access to this project's activity trail, contact your administrator.",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2 — Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 12: Router — project-scoped activity-events endpoint

**Files:**
- Create: `src/codemie/rest_api/routers/project_activity_events_router.py`
- Modify: wherever routers are registered onto the FastAPI app (find via existing registration of
  `activity_events_router`, e.g. `src/codemie/rest_api/app.py` or equivalent router-inclusion
  module — locate the exact `include_router(activity_events_router...)` call and add a matching one
  for the new router)
- Test: `tests/codemie/rest_api/routers/test_project_activity_events_router.py`

**Interfaces:**
- Consumes: `ActivityEventService.list_project_events(...)` (Task 9),
  `project_admin_or_admin_or_maintainer_access` (Task 11), `ActivityEventListItem`/
  `PaginationData`/`PaginatedListResponse` (existing).
- Produces: `GET /v1/projects/{project_name}/activity-events` returning
  `PaginatedListResponse[ActivityEventListItem]`.

Test-first: yes — a test calling the router function directly (per the existing `test_activity_events_router.py` pattern of testing router functions, not `TestClient`) asserting it delegates to `activity_event_service.list_project_events` with the path's `project_name` and query filters, and builds `PaginationData` identically to the existing endpoint's `page`/`per_page`/`total`/`pages` formula.

- [ ] **Step 1: Write the failing test**:

```python
def test_list_project_activity_events_delegates_to_service(mock_service):
    mock_service.list_project_events.return_value = ([item], 1)
    result = list_project_activity_events(project_name="acme", limit=50, offset=0)
    mock_service.list_project_events.assert_called_once()
    assert result.pagination.total == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_project_activity_events_router.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**, mirroring `activity_events_router.py:53-94` but with
  `prefix="/v1/projects/{project_name}/activity-events"`, `Depends(project_admin_or_admin_or_maintainer_access)`
  in place of `Depends(maintainer_access_only)`, no `entity_type`/`entity_id` query params, and
  `project_name: str` bound as a path parameter (not a query param) passed straight to
  `activity_event_service.list_project_events`. Reuse the same `PaginationData` construction
  (`page=offset // limit`, `pages=math.ceil(total/limit)`) and the same
  `ExtendedHTTPException`/generic-`Exception` handling as the existing endpoint.

- [ ] **Step 4: Run test to verify it passes**

Run: same command as Step 2 — Expected: PASS. Also register the new router alongside the existing
`activity_events_router` registration and confirm the app still starts
(`poetry run python -c "from codemie.rest_api.app import app"` or the project's existing
app-import smoke check).

- [ ] **Step 5: Commit**

---

### Task 13: Cross-cutting regression — global endpoint unchanged, personal-project exclusion, pagination tie-break

**Files:**
- Test: `tests/codemie/rest_api/routers/test_activity_events_router.py` (extend),
  `tests/codemie/service/activity/test_activity_repository.py` (extend)

**Interfaces:**
- Consumes: `find_all` (unchanged, Task 8 does not touch it), `find_by_project` (Task 8).

Test-first: yes — (1) a regression test asserting `find_all`'s query/response shape is byte-for-byte identical before and after this change (same conditions, same join, no `project_name`/snapshot columns referenced) — AC10; (2) a test asserting a personal-project row (`project_name=None`, `entity_id=<email>`) never appears in `find_by_project` results for any project name; (3) a test with two rows sharing the same `created_at` asserting `find_by_project` returns them in a stable, non-repeating, non-skipped order across two paginated calls (`limit=1, offset=0` then `offset=1`) — AC12.

- [ ] **Step 1: Write the failing tests** as described above, using the existing
  `session_with_events` fixture pattern from `test_activity_repository.py`, inserting one personal-
  project row and two same-`created_at` project rows with distinct `id` values.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/activity/test_activity_repository.py tests/codemie/rest_api/routers/test_activity_events_router.py -v`
Expected: FAIL for the tie-break test if Task 8's `id DESC` secondary sort was omitted; the other
two should already pass if Tasks 3–8 were implemented correctly — if they fail, that indicates a
regression to fix before proceeding.

- [ ] **Step 3: Fix any regression found** (most likely: add the `ActivityEvent.id` secondary
  sort key to `find_by_project` if missing).

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2 — Expected: PASS

- [ ] **Step 5: Commit**

---

## Negative-Constraint Check

- **"No derivation via joins at read time"** (spec Non-goals) — Task 8 selects the snapshot columns
  directly and only falls back to the `UserDB` join via `COALESCE` for pre-migration rows; it never
  re-derives `project_name` from a join. Honored.
- **"No backfill of pre-existing rows"** — no task writes `project_name`/actor-snapshot values onto
  existing rows; Task 2's migration only adds nullable columns. Honored.
- **"No change to `attributes` JSON shape"** — Tasks 4–7 explicitly leave every existing
  `attributes={...}` dict untouched, adding only the new top-level kwargs. Honored.
- **"Existing global endpoint's behavior/filters/authorization must not change"** (AC10) — Task 8
  adds `find_by_project` as a new method; `find_all` is never modified. Task 12 registers a
  separate router at a separate prefix with a separate authorization dependency; `activity_events_router.py`
  is never modified. Task 13 adds an explicit regression test for this. Honored.
- **"Personal projects must not appear in project-scoped results"** (AC9) — Task 3 hard-codes
  `project_name=None` at the personal-project emit site (not a runtime filter), and Task 13 adds a
  regression test. Honored.
- **"Migration must not modify existing row values"** (AC13) — Task 2's test asserts `upgrade()`
  never calls `alter_column`/`execute` and only adds nullable columns/an index. Honored.
- **"Denial (AC7) returns no events, counts, or existence info"** — Task 11's dependency raises a
  403 before any call into `list_project_events`/`find_by_project`; no partial data path exists.
  Honored.
- No other "not"/"must not"/"without" constraints or Non-goals items were found unaddressed.
