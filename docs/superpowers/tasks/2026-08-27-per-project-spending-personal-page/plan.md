# Per-Project Spending Rows — Personal Spending Panel

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `GET /v1/analytics/budget_usage` to include per-project member spending rows below the user's personal rows, with sorting and a freshness latch fix in `MemberSpendService`.

**Architecture:** `get_budget_usage` calls a new `get_user_project_spend_rows` on `MemberSpendService` first (before the personal lazy-refresh), reshapes the columnar output into row-per-category via `_expand_project_rows`, then appends to sorted personal rows. The admin `user_id` path receives the same extension automatically because both paths share `get_budget_usage`. A dual-marker latch (`_last_checked_at` + `asyncio.Lock`) is added to `MemberSpendService` to prevent repeated LiteLLM calls when no spend has changed.

**Tech Stack:** Python, FastAPI, SQLAlchemy `AsyncSession`, `asyncio.Lock`, pytest + `AsyncMock`

**Spec:** `docs/superpowers/tasks/2026-08-27-per-project-spending-personal-page/spec.md`

## Global Constraints

- No DB migrations — `project_spend_tracking` and `project_member_budget_assignment` tables already exist.
- No frontend changes — backend only; existing `TabularResponse` schema is unchanged.
- `GET /v1/analytics/user-project-spending` must return the same data it did before; its implementation becomes a thin wrapper only.
- Personal projects (`project_type='personal'`) remain excluded from per-project rows.
- No `spend_date` on `member_budget` rows is mutated by any code path introduced here.
- Commit per task using the repository's existing convention.

---

### Task 1: Batched reset-cutoff repository method

**Files:**
- Modify: `src/codemie/repository/project_spend_tracking_repository.py:988-1030` (add method after `get_project_reset_cutoffs`)
- Test: `tests/codemie/repository/test_project_spend_tracking_repository.py`

**Interfaces:**
- Produces: `ProjectSpendTrackingRepository.get_project_reset_cutoffs_for_projects(session, project_names: list[str]) -> dict[str, dict[str, datetime]]` — outer key is `project_name`, inner key is `budget_category`, value is cutoff `datetime`. Returns `{}` for unknown projects.

**Test-first: yes — `get_project_reset_cutoffs_for_projects` returns the same per-project cutoffs as calling `get_project_reset_cutoffs` once per project, and emits a single query for a multi-project input**

- [ ] Write failing tests

This file uses no database. Follow the existing cutoff tests (`test_project_spend_tracking_repository.py:630-706`): `_session(rows)` returns an `AsyncMock` whose `execute().all()` yields `SimpleNamespace` rows, and query shape is asserted with `_compile_sql(session.execute.await_args.args[0])`. The new rows need a `project_name` attribute, so extend the local `_row` helper (or add a sibling) rather than reusing it unchanged.

```python
@staticmethod
def _prow(project: str, category: str, period_spend: str, spend_date: datetime):
    return SimpleNamespace(
        project_name=project,
        budget_category=category,
        budget_period_spend=Decimal(period_spend),
        spend_date=spend_date,
    )

@pytest.mark.asyncio
async def test_cutoffs_are_grouped_per_project(self):
    repo = ProjectSpendTrackingRepository()
    rows = [
        self._prow("proj-a", "platform", "3221.46", datetime(2026, 7, 31, 23, tzinfo=timezone.utc)),
        self._prow("proj-a", "platform", "38.13", datetime(2026, 8, 2, 23, tzinfo=timezone.utc)),
        self._prow("proj-b", "cli", "10.00", datetime(2026, 7, 31, 23, tzinfo=timezone.utc)),
        self._prow("proj-b", "cli", "12.00", datetime(2026, 8, 2, 23, tzinfo=timezone.utc)),
    ]
    got = await repo.get_project_reset_cutoffs_for_projects(self._session(rows), ["proj-a", "proj-b"])
    assert got == {"proj-a": {"platform": datetime(2026, 7, 31, 23, tzinfo=timezone.utc)}}

@pytest.mark.asyncio
async def test_projects_do_not_share_decrease_detection(self):
    """A drop in one project must not create a cutoff in another."""
    repo = ProjectSpendTrackingRepository()
    rows = [
        self._prow("proj-a", "platform", "100.00", datetime(2026, 8, 1, 23, tzinfo=timezone.utc)),
        self._prow("proj-b", "platform", "5.00", datetime(2026, 8, 2, 23, tzinfo=timezone.utc)),
    ]
    got = await repo.get_project_reset_cutoffs_for_projects(self._session(rows), ["proj-a", "proj-b"])
    assert got == {}

@pytest.mark.asyncio
async def test_empty_input_issues_no_query(self):
    repo = ProjectSpendTrackingRepository()
    session = self._session([])
    assert await repo.get_project_reset_cutoffs_for_projects(session, []) == {}
    session.execute.assert_not_awaited()

@pytest.mark.asyncio
async def test_one_query_for_many_projects(self):
    repo = ProjectSpendTrackingRepository()
    session = self._session([])
    await repo.get_project_reset_cutoffs_for_projects(session, ["proj-a", "proj-b"])
    assert session.execute.await_count == 1
    sql = _compile_sql(session.execute.await_args.args[0])
    assert "project_budget" in sql
    assert "proj-a" in sql and "proj-b" in sql
```

- [ ] Run tests — expect `AttributeError` (method absent)
- [ ] Add `get_project_reset_cutoffs_for_projects` after `get_project_reset_cutoffs` in the repository class. The implementation is the same WHERE / ORDER logic as `get_project_reset_cutoffs` with `ProjectSpendTracking.project_name.in_(project_names)` replacing the `== project_name` filter, and a `ProjectSpendTracking.project_name` added to the SELECT list, the ORDER BY (before `budget_category`), and the outer grouping dict. The decrease-detection `previous` dict must be keyed by `(project_name, budget_category)`, not by category alone, or a drop in one project will be attributed to the next. Return early with `{}` for an empty `project_names`. The 90-day window is unchanged.
- [ ] Run tests — expect PASS
- [ ] Commit

---

### Task 2: Extract `get_user_project_spend_rows` + use batched cutoff

**Files:**
- Modify: `src/codemie/service/analytics/handlers/member_spend_service.py:185-217`
- Test: `tests/codemie/service/analytics/handlers/test_member_spend_service.py`

**Interfaces:**
- Consumes: `ProjectSpendTrackingRepository.get_project_reset_cutoffs_for_projects` (Task 1)
- Produces: `MemberSpendService.get_user_project_spend_rows(session, user_id: str) -> list[dict]` — one dict per project, keys: `project_name`, `platform`, `cli`, `premium_models`, `platform_limit`, `cli_limit`, `premium_models_limit`. Calls `_ensure_fresh` as its first statement. `get_user_project_spend` becomes a thin wrapper returning `(_category_columns("project_name", "Project"), await self.get_user_project_spend_rows(session, user_id))`.

**Test-first: yes — `get_user_project_spend` returns the same rows after the refactor; `get_user_project_spend_rows` calls `get_project_reset_cutoffs_for_projects` once for multi-project users**

- [ ] Write failing test

```python
@pytest.mark.asyncio
async def test_get_user_project_spend_rows_calls_batched_cutoff(mock_session):
    svc = MemberSpendService()
    with patch.object(svc, "_ensure_fresh", new=AsyncMock()), \
         patch("...project_spend_tracking_repository.get_project_reset_cutoffs_for_projects",
               new=AsyncMock(return_value={})) as batched, \
         patch("...project_spend_tracking_repository.get_project_reset_cutoffs",
               new=AsyncMock()) as single:
        await svc.get_user_project_spend_rows(mock_session, "user-1")
    batched.assert_called_once()
    single.assert_not_called()

@pytest.mark.asyncio
async def test_get_user_project_spend_still_returns_columns_and_rows(mock_session):
    """AC 7: the public admin-endpoint contract is unchanged by the extraction."""
    svc = MemberSpendService()
    rows = [{"project_name": "proj-a"}]
    with patch.object(svc, "get_user_project_spend_rows", new=AsyncMock(return_value=rows)):
        columns, got = await svc.get_user_project_spend(mock_session, "user-1")
    assert columns == _category_columns("project_name", "Project")
    assert got == rows
```

- [ ] Run tests — expect FAIL (method absent)
- [ ] Extract the body of `get_user_project_spend` (lines 198-217) into `get_user_project_spend_rows`; replace the per-project cutoff loop (`lines 211-214`) with a single `await project_spend_tracking_repository.get_project_reset_cutoffs_for_projects(session, project_names)` call; rewrite `get_user_project_spend` as the two-line wrapper.
- [ ] Run tests — expect PASS
- [ ] Commit

---

### Task 3: MemberSpendService freshness latch fix

**Files:**
- Modify: `src/codemie/service/analytics/handlers/member_spend_service.py:262-304`
- Test: `tests/codemie/service/analytics/handlers/test_member_spend_service.py`

**Interfaces:**
- Produces: `MemberSpendService.__init__` sets `self._last_checked_at: datetime | None = None` and `self._refresh_lock = asyncio.Lock()`. `_needs_refresh(newest_spend_date, in_memory_at: datetime | None = None)` gains a second parameter **with a default**, so the five existing one-argument call sites (`test_member_spend_service.py:146, 150, 154, 158, 163`) keep passing unchanged. `_ensure_fresh` acquires the lock, re-evaluates after acquisition, and sets `_last_checked_at` after any provider round-trip.

**Test-first: yes — no-change refresh sets `_last_checked_at`; second `_ensure_fresh` within TTL makes no provider call even when `max(spend_date)` is stale; a fresh DB marker still short-circuits on its own; concurrent coroutines coalesce to one provider call; a no-change refresh writes nothing**

- [ ] Write failing tests

`_ensure_fresh` returns early unless `_is_litellm_enabled()` is true (`member_spend_service.py:278`), and it is false under test. Every test below must patch it, matching the existing tests at `test_member_spend_service.py:189-224`; without the patch these fail with `call_count == 0` and look like the latch already works.

```python
_NEWEST = ("codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
           "get_newest_member_spend_date")

@pytest.mark.asyncio
async def test_latch_skips_provider_after_no_change_refresh(self):
    """The DB marker never advances on a no-change refresh; _last_checked_at must."""
    svc = MemberSpendService()
    session = MagicMock()
    with (
        patch.object(svc, "_refresh_all_member_spend", new=AsyncMock()) as refresh,
        patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
        patch(_NEWEST, new=AsyncMock(return_value=None)),
    ):
        await svc._ensure_fresh(session)
        await svc._ensure_fresh(session)
    refresh.assert_awaited_once()
    assert svc._last_checked_at is not None

@pytest.mark.asyncio
async def test_fresh_db_marker_still_skips_on_its_own(self):
    """The two markers are OR-ed: a fresh DB row short-circuits a cold process."""
    svc = MemberSpendService()
    fresh = datetime.now(timezone.utc) - timedelta(minutes=1)
    with (
        patch.object(svc, "_refresh_all_member_spend", new=AsyncMock()) as refresh,
        patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
        patch(_NEWEST, new=AsyncMock(return_value=fresh)),
    ):
        await svc._ensure_fresh(MagicMock())
    refresh.assert_not_awaited()
    assert svc._last_checked_at is None

@pytest.mark.asyncio
async def test_concurrent_requests_coalesce(self):
    svc = MemberSpendService()
    calls = 0

    async def slow_refresh(_session):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)

    with (
        patch.object(svc, "_refresh_all_member_spend", side_effect=slow_refresh),
        patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
        patch(_NEWEST, new=AsyncMock(return_value=None)),
    ):
        await asyncio.gather(*[svc._ensure_fresh(MagicMock()) for _ in range(5)])
    assert calls == 1

@pytest.mark.asyncio
async def test_no_change_refresh_writes_nothing_but_still_latches(self):
    """AC 10: the latch must not be implemented by touching member rows."""
    svc = MemberSpendService()
    insert_mock = AsyncMock()
    with (
        patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
        patch(_NEWEST, new=AsyncMock(return_value=None)),
        _patch_refresh_deps([], insert_mock=insert_mock),
    ):
        await svc._ensure_fresh(MagicMock())
    insert_mock.assert_not_awaited()
    assert svc._last_checked_at is not None
```

- [ ] Run tests — expect FAIL
- [ ] Add `__init__` to `MemberSpendService` with `self._last_checked_at: datetime | None = None` and `self._refresh_lock = asyncio.Lock()`. Constructing an `asyncio.Lock()` at module import is correct on Python 3.10+ — it no longer binds to an event loop at construction — so the module-level singleton (`member_spend_service.py:349`) needs no lazy initialization. Update `_needs_refresh` to accept `in_memory_at: datetime | None = None` and return `False` when *either* marker is within the TTL. Update `_ensure_fresh` to: acquire `self._refresh_lock`, re-read the DB marker and re-check `_needs_refresh` after acquisition, call the provider only if still stale, and set `self._last_checked_at = datetime.now(timezone.utc)` after any provider call attempt (including a failed one — a provider outage must not turn into a per-request retry storm).
- [ ] Run tests — expect PASS
- [ ] Run the existing `test_member_spend_service.py` suite unchanged — the default on `in_memory_at` keeps the five one-argument `_needs_refresh` call sites valid, and `test_skips_zero_delta_rows_like_the_collector` / `test_no_snapshots_writes_nothing` must still pass (AC 10).
- [ ] Verify that `get_project_member_spend` (line 219) still works — it calls `_ensure_fresh` and shares the same latch transparently.
- [ ] Commit

---

### Task 4: `_expand_project_rows` + extend `get_budget_usage`

**Files:**
- Modify: `src/codemie/service/analytics/handlers/budget_usage_service.py`
- Test: `tests/codemie/service/analytics/handlers/test_budget_usage_service.py`

**Interfaces:**
- Consumes: `MemberSpendService.get_user_project_spend_rows` (Task 2)
- Produces: module-level `_expand_project_rows(project_spend_rows: list[dict]) -> list[dict]` — pure function; `get_budget_usage` returns `category_sorted(personal_rows) + project_rows`.

**Test-first: yes — `_expand_project_rows` sort order; category with spend but null limit emits row; category with null limit and zero spend is skipped; empty input yields no rows; personal rows are category-sorted regardless of assignment order; `get_budget_usage` calls `get_user_project_spend_rows` before `_load_from_db`**

- [ ] Write failing unit tests for `_expand_project_rows`

```python
def _project_row(name, **overrides):
    base = {
        "project_name": name,
        "platform": 1.0, "platform_limit": 10.0,
        "cli": 0.5, "cli_limit": 5.0,
        "premium_models": 0.0, "premium_models_limit": None,
    }
    return {**base, **overrides}

def test_expand_sorts_by_project_name_case_insensitive():
    # premium is skipped in _project_row (zero spend, null limit) -> 2 rows per project
    rows = _expand_project_rows([_project_row("Zebra"), _project_row("alpha")])
    names = [r["project_name"] for r in rows]
    assert names == ["alpha", "alpha (cli)", "Zebra", "Zebra (cli)"]

def test_expand_emits_all_three_categories_when_all_allocated():
    row = _project_row("p", premium_models=0.0, premium_models_limit=2.0)
    names = [r["project_name"] for r in _expand_project_rows([row])]
    assert names == ["p", "p (cli)", "p (premium)"]

def test_expand_empty_input():
    assert _expand_project_rows([]) == []

def test_expand_skips_null_limit_zero_spend():
    row = _project_row("p", premium_models=0.0, premium_models_limit=None)
    result = _expand_project_rows([row])
    assert all("premium" not in r["project_name"] for r in result)

def test_expand_emits_row_for_spend_with_null_limit():
    row = _project_row("p", platform=2.5, platform_limit=None)
    result = _expand_project_rows([row])
    platform_row = next(r for r in result if r["project_name"] == "p")
    assert platform_row["current_spending"] == 2.5
    assert platform_row["budget_limit"] is None
    assert platform_row["total"] == 0.0

def test_expand_null_reset_fields():
    result = _expand_project_rows([_project_row("p")])
    for r in result:
        assert r["budget_reset_at"] is None
        assert r["time_until_reset"] is None
```

- [ ] Run tests — expect `ImportError` / `NameError`
- [ ] Add `_CATEGORY_SUFFIX`, a shared `_category_rank(category: str) -> int` derived from `BudgetCategory` enum order, and `_expand_project_rows` to `budget_usage_service.py` after `_build_spending_row`. The function body is as shown in the spec (§Row Production).
- [ ] Sort personal rows by category. `_build_budget_usage_rows` (`budget_usage_service.py:163`) iterates `assignments` in DB order, which has none (`get_user_category_assignments`, `budget_repository.py:296`, has no `ORDER BY`). Sort inside that function — `for assignment in sorted(assignments, key=lambda a: (_category_rank(a.category), a.category))` — so both row blocks share `_category_rank` per spec §Sorting. The `a.category` tiebreaker keeps unknown categories deterministic.
- [ ] Extend `get_budget_usage` (`budget_usage_service.py:320-354`): before the existing `_load_from_db` call, add `project_rows = _expand_project_rows(await member_spend_service.get_user_project_spend_rows(session, subject_user_id))`. Import the module-level singleton `member_spend_service` (`member_spend_service.py:349`), not the class, and do it function-locally — `member_spend_service` imports `_compute_spend_delta` from this module at line 32, so a module-level import here is a circular import at startup. The final return becomes `columns, personal_rows + project_rows`.
- [ ] Write integration test for `get_budget_usage`

```python
@pytest.mark.asyncio
async def test_get_budget_usage_personal_rows_first_then_project(mock_session, mock_user):
    project_rows_raw = [{"project_name": "proj-a", "platform": 1.0, "platform_limit": 10.0,
                          "cli": 0.0, "cli_limit": None, "premium_models": 0.0, "premium_models_limit": None}]
    with patch("...MemberSpendService.get_user_project_spend_rows",
               new=AsyncMock(return_value=project_rows_raw)), \
         patch.object(BudgetUsageService, "_load_from_db", ...), \
         patch.object(BudgetUsageService, "_refresh_from_litellm", new=AsyncMock()):
        _, rows = await BudgetUsageService().get_budget_usage(mock_session, mock_user.id, mock_user.username)
    personal = [r for r in rows if r["project_name"].startswith(mock_user.username)]
    project = [r for r in rows if "proj-a" in r["project_name"]]
    assert rows.index(personal[-1]) < rows.index(project[0])

@pytest.mark.asyncio
async def test_personal_rows_are_category_sorted(mock_session, mock_user):
    """Assignments come back from the DB unordered; the response must not."""
    assignments = [_assignment("premium_models"), _assignment("cli"), _assignment("platform")]
    ...  # patch _load_from_db to return these, patch get_user_project_spend_rows -> []
    _, rows = await BudgetUsageService().get_budget_usage(mock_session, mock_user.id, mock_user.username)
    assert [r["project_name"] for r in rows] == [
        mock_user.username, f"{mock_user.username} (cli)", f"{mock_user.username} (premium)",
    ]
```

- [ ] Run all `test_budget_usage_service.py` — expect PASS
- [ ] Commit

---

### Task 5: Router-level tests for combined response

**Files:**
- Modify: `tests/codemie/rest_api/routers/test_analytics.py`

**Interfaces:**
- Consumes: `BudgetUsageService.get_budget_usage` returning combined `(columns, rows)` including project rows (Task 4)

**Test-first: yes — per-project rows present in self-service response; per-project rows present in admin `user_id` path; personal rows precede all project rows**

There is no `TestClient`/`client` fixture in this file. `TestGetUserKeySpending` (`test_analytics.py:1841`) awaits the router function directly, patches `codemie.clients.postgres.get_async_session` with its own `_mock_session_ctx()` helper, patches `BudgetUsageService.get_budget_usage`, and reads rows from `response["data"]["rows"]` — not `response["rows"]`. Follow that pattern; the existing `test_returns_grouped_key_spending_success` already includes a `"demo"` project row in its fixture and is the closest model.

- [ ] Add test cases to `TestGetUserKeySpending` without changing existing assertions.

```python
@pytest.mark.asyncio
async def test_budget_usage_includes_project_rows(self, mock_user):
    from codemie.rest_api.routers.analytics import get_user_budget_usage
    from codemie.service.analytics.handlers.budget_usage_service import _get_key_spending_columns

    rows = [
        {"project_name": mock_user.email, "current_spending": 1.0, "budget_reset_at": None,
         "time_until_reset": None, "budget_limit": 10.0, "total": 10.0},
        {"project_name": "proj-x", "current_spending": 2.0, "budget_reset_at": None,
         "time_until_reset": None, "budget_limit": 20.0, "total": 10.0},
    ]
    with patch("codemie.clients.postgres.get_async_session", return_value=self._mock_session_ctx()):
        with patch(
            "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
            new_callable=AsyncMock,
            return_value=(_get_key_spending_columns(), rows),
        ):
            response = await get_user_budget_usage(user=mock_user, user_id=None)

    names = [r["project_name"] for r in response["data"]["rows"]]
    assert names.index(mock_user.email) < names.index("proj-x")
```

- [ ] Add the admin-path case: same structure, but `user_id="target-user-id"`, with `_resolve_admin_budget_subject` patched to return `("target-user-id", "target-username")`. Assert project rows are present — the admin path returns the same extended row set (AC 1); there is no self-service-only branch.
- [ ] Run `tests/codemie/rest_api/routers/test_analytics.py` — expect PASS
- [ ] Commit

---

## Negative-constraint pass

| Constraint | Honoring task |
|---|---|
| `GET /v1/analytics/user-project-spending` behavior not modified | Task 2 — wrapper returns same `(columns, rows)` as before |
| No frontend changes | No task touches routers or schemas beyond what `TabularResponse` already carries |
| No new DB tables / migrations | No migration task exists; all queries hit existing tables |
| `budget_reset_at` not derived from `project_budget_id` (out of scope) | Task 4 — `_expand_project_rows` unconditionally passes `budget_reset_at: None` |
| Cross-pod refresh coalescing not in scope | Task 3 — only in-memory `_last_checked_at` added; no distributed lock |
| `spend_date` on `member_budget` rows must not be mutated | No task calls `touch_budget_spend_dates` or any UPDATE on `project_spend_tracking` for `member_budget` rows |
