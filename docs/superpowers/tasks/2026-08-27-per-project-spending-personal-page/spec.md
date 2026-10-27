# Spec: Per-Project Spending Rows on Personal Spending Panel

## Overview

`GET /v1/analytics/budget_usage` currently returns three rows for a user's own platform, CLI,
and premium-models budget allocations. Per-project member spending is available only to admins
via `GET /v1/analytics/user-project-spending`.

This change extends `budget_usage` to also include per-project rows for every project the
subject user is a member of. The self-service path (no `user_id`) and the admin path (`user_id`
given) share `BudgetUsageService.get_budget_usage` and both return the extended row set; the
only difference between them is which user is resolved as the subject.

## Goals

- Append per-project member spending rows to `budget_usage` on both the self-service and admin
  paths.
- Reuse `project_spend_tracking` (`spend_subject_type='member_budget'`) as the data source,
  identical to the admin endpoint.
- Pin personal rows first (sorted by category: platform → CLI → premium); sort per-project rows
  below them by project name ascending (case-insensitive), then category order.
- Return null for `budget_reset_at` and `time_until_reset` on per-project rows (see §Row
  Building for rationale).
- Fix the freshness latch in `MemberSpendService._ensure_fresh` so quiet tenants do not retrigger
  a global LiteLLM refresh on every request.

## Non-Goals

- `GET /v1/analytics/user-project-spending` behavior is not modified (its implementation is
  refactored into a wrapper; see §Row Production).
- No frontend changes — the backend returns rows in the existing `TabularResponse` schema.
- No new database tables or migrations.
- Populating `budget_reset_at` from `project_budget_id → Budget.budget_reset_at` for per-project
  rows is not in scope (see §Row Building).
- Cross-pod coalescing of member spend refreshes is not in scope; "another pod is refreshing →
  serve DB" is acceptable.
- Mutating `spend_date` on existing `member_budget` rows is explicitly out of scope (see
  §Freshness Latch Fix for why).

## Design

### Row Production — `_expand_project_rows`

Rather than re-implementing the loading steps of `get_user_project_spend`, `get_budget_usage`
calls an existing public method and reshapes its output:

1. `MemberSpendService` gains a new method `get_user_project_spend_rows(session, user_id) ->
   list[dict]` that contains the core loading logic currently in `get_user_project_spend`
   (including `_ensure_fresh`, membership loading, personal-project exclusion, spend map,
   reset-cutoff filtering, and `_build_spend_rows`). The existing `get_user_project_spend` becomes
   a thin wrapper: call `get_user_project_spend_rows` and prepend `_category_columns`.
2. A new pure function `_expand_project_rows(project_spend_rows: list[dict]) -> list[dict]` in
   `budget_usage_service.py` expands each project row, which carries one column per category,
   into one row per project and category by calling `_build_spending_row` for each:

```python
_CATEGORY_SUFFIX = {
    BudgetCategory.PLATFORM.value: "",
    BudgetCategory.CLI.value: " (cli)",
    BudgetCategory.PREMIUM_MODELS.value: " (premium)",
}

def _expand_project_rows(project_spend_rows: list[dict]) -> list[dict]:
    rows = []
    for project in sorted(project_spend_rows, key=lambda p: p["project_name"].casefold()):
        for category in BudgetCategory:
            spend = project[category.value]
            limit = project[f"{category.value}_limit"]
            if limit is None and not spend:
                continue
            rows.append(
                _build_spending_row(
                    f"{project['project_name']}{_CATEGORY_SUFFIX[category.value]}",
                    {"total_spend": spend, "max_budget": limit, "budget_reset_at": None},
                )
            )
    return rows
```

Every project row carries all three categories (`_build_row`,
`member_spend_service.py:88-95`), with `0` spend and `None` limit where the project has no
allocation for that category. The expansion skips those empty categories so the panel does not
show `"Project X (premium)"` at `$0.00` with no limit for a project that has no premium budget.
This mirrors the personal rows, which only exist for categories with an assignment. A project
with no allocation and no spend in any category therefore produces no rows; the admin endpoint
still shows it as a row of zeros.

This makes AC 2 structurally true for every row that is emitted: personal page and admin
endpoint share the same loading path (`get_user_project_spend_rows`), so value drift is
impossible. Sort order falls out of the sorted project rows combined with the category-ordered
inner loop.

Because `get_user_project_spend_rows` calls `_ensure_fresh` as its first statement (matching the
existing `get_user_project_spend` at `member_spend_service.py:199`), no separate
`ensure_member_spend_fresh` wrapper is needed.

`member_spend_service.py` already imports from `budget_usage_service` at line 32. The reverse
call from `budget_usage_service.get_budget_usage` must use a function-local import (matching the
prevailing style in that file) to avoid a circular import at startup.

### Session Lifecycle

`get_budget_usage` is extended with the project step before the existing personal logic:

```
a. project_rows = _expand_project_rows(
       await member_spend_service.get_user_project_spend_rows(session, subject_user_id)
   )
b. _load_from_db → personal refresh → rollback() → reload  (unchanged today)
c. return category_sorted(personal_rows) + project_rows
```

`project_rows` are plain dicts produced before any personal load. The subsequent `rollback()`
that follows the personal refresh cannot affect them.

`_ensure_fresh` must precede every read on the session (its commit expires loaded ORM objects),
so a user with zero memberships still goes through the member freshness check before the
membership read that would reveal there is nothing to load. With the latch fix below this
costs one `max(spend_date)` query per request, not a provider call.

### Sorting

`get_user_category_assignments` (`budget_repository.py:296`) has no `ORDER BY`, so personal
rows have no guaranteed category order. Personal rows are sorted explicitly by category after
they are built, before being prepended to `project_rows`. The rank is derived from
`BudgetCategory` enum order (`service/budget/budget_enums.py:20`: platform, cli,
premium_models) and shared with `_expand_project_rows`, so both blocks use one definition.

The final response is `category_sorted(personal_rows) + project_rows`.

### Row Building — `budget_reset_at`

`ProjectMemberBudgetAssignment.budget_reset_at` (`budget_models.py:323`) exists but is
inconsistently populated: it is set on the member-added path
(`project_assignment_service.py:255`) but not on the equal-distribution path
(`project_budget_service.py:220-231`). Per-project rows pass `budget_reset_at: None` to
`_build_spending_row`. Deriving it from `project_budget_id → Budget.budget_reset_at` is possible
but is out of scope.

### Freshness Latch Fix

`MemberSpendService._ensure_fresh` uses `max(spend_date)` over `member_budget` rows as its
staleness marker (`project_spend_tracking_repository.py:679`). A refresh only writes rows whose
spend changed (`_snapshot_to_row` returns `None` on zero delta, `member_spend_service.py:154`),
so a no-change refresh leaves the marker where it was and the full global LiteLLM call fires on
every request until any member anywhere spends. On a busy tenant the marker advances often
enough that the TTL appears to hold; on nights, weekends, and small tenants it does not. This
change elevates the impact from admin-only screens to every personal page load.

**Touching existing rows is not an option.** `_drop_rows_before_cutoff`
(`member_spend_service.py:101-116`) hides pre-reset member rows by comparing
`row.spend_date <= cutoff`. After a project budget resets, a member who has not spent yet gets
no new row (LiteLLM reports 0, delta is 0), so their latest row still carries the pre-reset
`budget_period_spend`; the cutoff comparison is what keeps it off the screen. Advancing that
row's `spend_date` — as `touch_budget_spend_dates` does for personal rows — would move it past
the cutoff and resurface the stale spend. The personal path can touch because it detects resets
through `budget_reset_at`, not through `spend_date`.

Two changes are required, both confined to `MemberSpendService`:

**1. In-memory check marker.** Add an instance attribute `_last_checked_at: datetime | None`
on the service singleton. `_ensure_fresh` sets it to `now` after every provider round trip,
whether or not rows were written. `_needs_refresh` returns `False` when either marker is within
the TTL:

```
needs_refresh = db_marker_stale(newest_spend_date) and in_memory_marker_stale(_last_checked_at)
```

The DB marker still lets a pod benefit from rows written by another pod or by the collector; the
in-memory marker covers the no-change case and the zero-row case (allocations present, nobody
has spent yet). Cost after this change: at most one global refresh per TTL per process.

**2. Per-process lock.** Add an `asyncio.Lock` instance attribute on the singleton.
`_ensure_fresh` acquires it, re-evaluates `needs_refresh` after acquisition (another coroutine
may have refreshed while this one waited), and only then calls the provider. Concurrent
requests on one process coalesce to a single in-flight refresh.

Both attributes are created in `__init__`, not as class variables, so tests that instantiate
`MemberSpendService()` do not share state.

These fixes also apply to `GET /v1/analytics/user-project-spending` and
`GET /v1/analytics/project-member-spending`, which share the same `_ensure_fresh` path.

### Reset-Cutoff Batching

`get_user_project_spend` fetches cutoffs one project at a time in a sequential loop
(`member_spend_service.py:211-214`), so latency grows with the number of projects. Wrapping the
loop in `asyncio.gather` would not help: SQLAlchemy's asyncpg adapter serializes every statement
on a connection behind `_execute_mutex` (`sqlalchemy/dialects/postgresql/asyncpg.py:502, 714`),
so gathered queries on one `AsyncSession` still run one after another.

Replace the loop with a single batched repository method:

```
get_project_reset_cutoffs_for_projects(session, project_names: list[str])
    -> dict[str, dict[str, datetime]]   # project_name -> category -> cutoff
```

It is the same statement as `get_project_reset_cutoffs` with `project_name IN (...)` and
`project_name` added to the ordering and the per-category scan. `get_project_reset_cutoffs`
stays for `get_project_member_spend`, which needs one project.

### Performance — Refresh Costs

**Member refresh** (`_ensure_fresh`): GET `/customer/list` + POST `/budget/info` via the
LiteLLM enterprise client (`codemie-enterprise/src/codemie_enterprise/litellm/service.py:1277`,
`:1351`), 30 s list timeout, httpx transport retries = 3. `/customer/list` returns all
customers; the page/size parameters the client sends are ignored by LiteLLM and there is no
truncation. The call is preceded by a full-table read of active allocations in a separate
session (`budget_provider_adapter.py:193`) and a full read of `budgets`. After the latch fix:
at most once per TTL per process. Without it: on every request until any member spends.

**Personal refresh** (`_refresh_from_litellm`): up to 6 LiteLLM calls per `budget_usage`
request — for each of the three budget categories: GET `/customer/info` + POST `/budget/info`
(`service.py:1238`, `:1248`) — uncached. This cost is unchanged by this feature.

### Column Schema

No columns are added or removed. Per-project rows reuse the existing six-column schema. Null in
`budget_reset_at` and `time_until_reset` for per-project rows is tolerated by
`_calculate_time_until_reset`, which already returns `None` when `budget_reset_at` is falsy.

## Acceptance Criteria

1. `GET /v1/analytics/budget_usage`, with or without `user_id`, returns the subject user's
   personal rows plus one row per (project, category) for every non-personal project the user is
   a member of where that category has an allocation or non-zero spend.
2. Per-project `current_spending` equals the corresponding category column from
   `GET /v1/analytics/user-project-spending` for the same user and project+category;
   `budget_limit` equals `{category}_limit`; `total` is the locally derived percentage. Values
   match after a project budget reset (reset-cutoff filtering applied).
3. Personal rows (platform, CLI, premium) appear before all per-project rows, sorted by category
   order (platform → CLI → premium).
4. Per-project rows are sorted by project name (ascending, case-insensitive), then category order
   (platform → CLI → premium).
5. `budget_reset_at` and `time_until_reset` are null for all per-project rows.
6. When the user is not a member of any non-personal project, or none of their projects has an
   allocation or spend in any category, only the personal rows are returned.
7. `GET /v1/analytics/user-project-spending` returns the same data it returned before this
   change.
8. After a member spend refresh that writes no rows, a second `_ensure_fresh` on the same process
   within the TTL makes no provider call.
9. Concurrent requests on one process that all find member spend stale result in at most one
   in-flight provider refresh.
10. No `member_budget` row's `spend_date` is modified by any code path introduced here.

## Tests Required

- `test_budget_usage_service.py` — unit tests for pure `_expand_project_rows`: populated
  project rows with spend and limits; a category with spend but null limit (row emitted, limit
  null, total 0.0); a category with null limit and zero spend (row skipped); a project with
  nothing in any category (no rows); empty input; sort order (project name case-insensitive
  ascending, platform → CLI → premium within each project). No session mock needed: pass project
  row dicts directly.
- `test_budget_usage_service.py` — integration test for `BudgetUsageService.get_budget_usage`:
  personal rows appear first sorted by category regardless of assignment order; per-project rows
  follow sorted by project name then category; `get_user_project_spend_rows` is called before
  `_load_from_db`.
- `test_analytics.py` — router-level tests: per-project rows appear when the user has
  memberships on both the self-service and `user_id` paths; personal rows appear first.
- `test_member_spend_service.py` — freshness latch: a refresh that writes no rows sets
  `_last_checked_at`; a second `_ensure_fresh` within the TTL makes no provider call even when
  `max(spend_date)` is stale or absent; a stale in-memory marker with a fresh DB marker also skips
  the provider; concurrent coroutines coalesce to one provider call; no insert or update is
  issued on a no-change refresh.
- `test_project_spend_tracking_repository.py` — `get_project_reset_cutoffs_for_projects` returns
  the same cutoffs as per-project `get_project_reset_cutoffs` for each project, and is called once
  for a multi-project user.
