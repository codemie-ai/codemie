# EPMCDME-14433 follow-up: personal spending panel project rows

## Goal

Make the personal spending panel (`GET /v1/analytics/budget_usage`, UI `SpendingCard.tsx`) show:

- which project is the user's default;
- rows for budgeted projects where member spend limits are not enforced;
- override limits;
- live project totals.

The admin spending endpoints and the CLI statusline must not change.

## Scope

Backend (`repos/codemie`):

- `routers/analytics/spending.py`: resolves the subject's email. For the admin view this is the target user's email.
- `budget_usage_service.py`
- `member_spend_service.py`
- `project_spend_tracking_repository.py`
- the budget provider protocol, the no-op provider and the LiteLLM adapter
- `spend_collector_service.py`: adds a lock only.

UI (`repos/codemie-ui`): `SpendingCard.tsx`.

## Design

**Default flag.** Every `/budget_usage` row gets a boolean `is_default`.

- The default is `resolve_membership_default(memberships, subject_email)` (`rest_api/security/user.py:49`), including its fallback.
- Project rows are flagged when their project is the default.
- Personal rows are flagged when the default is one of the subject's personal projects (`aget_personal_project_names`).
- `is_default` is not added to `columns`, and `project_name` labels do not change.

**Ordering.** The backend returns rows in this order:

1. default-project rows;
2. personal rows;
3. the remaining projects, sorted case-insensitively as today.

When the default is the personal project, the personal rows come first. Within a project, the category order stays as today. The UI keeps the server order.

**UI badge.** In table mode (`rowCount > 1`), `SpendingCard` renders `project_name` through `customRenderColumns`: the label, followed by a "Default" `DetailsBadge` when `is_default` is set. The badge is styled like `BudgetOverrideIndicator.tsx`. Doughnut mode shows no badge.

**Row selection and limits.** `get_user_project_rows` gets a new keyword-only flag. Only `get_budget_usage` passes it, so the admin callers keep today's code path. With the flag set:

- **Enforcement-on projects:** unchanged. These rows already use `allocated_max_budget`, which `member_effective_max_budget` also returns when enforcement is on. No cap is applied here, because shares and overrides add up to the project budget.
- **Enforcement-off projects with an active project budget:** one row per category that has an active budget.
  - `current_spending` is the member's own spend.
  - Non-override members (including a missing allocation, which counts as not FIXED): `budget_limit` is `member_effective_max_budget(allocation, budget, enforce_limit=False)` (`budget_models.py:44`), which is the project budget's `max_budget`. `total` is the project total divided by the project max, times 100.
  - FIXED-override members: `member_effective_max_budget` returns the override. That value is then capped by what is left in the project budget, because LiteLLM stops the member once the project budget runs out:
    `effective_limit = min(override, member_spend + max(0, project_max - project_total))`.
    Here `project_total` is the live project total described under "Live project totals". `budget_limit` is `effective_limit`, and `total` is `member_spend / effective_limit * 100`.
    - Over budget (`project_total >= project_max`): nothing is left, so the limit equals `member_spend` and the bar shows 100%.
    - Zero case (`member_spend` is 0 and nothing is left): the limit is $0 and is rendered as today's zero limit (0%). This is a known limitation.
  - The reset date comes from the budget that supplies the limit.
  - If there is no project total row yet, the project total counts as 0, so the percent of a non-override row is 0.
- **Enforcement-off projects without a budget:** hidden.

Worked examples:

| Enforcement | Setup | Member | Shown |
|---|---|---|---|
| off | budget $100; X spent $50, Y spent $0 | X | $50 / 50% |
| | | Y | $0 / 50% |
| off | budget $100; user1 override $40, spent $10; user2 no override, spent $80; total $90, $10 left | user1 | limit min(40, 10+10) = $20, $10 / 50% |
| | | user2 | $80 / 90% |
| off | budget $100; user1 override $40, spent $10; total $30 (cap not binding) | user1 | limit $40, $10 / 25% |
| off | budget $100; no overrides; spent $10 and $20 | both | 30% |
| on | budget $100; 2 members with $50 each; spent $10 and $20 | | 20% / 40% |
| on | user1 override $40, others $30 each; user1 spent $10, user2 spent $20 | user1 | 25% |
| | | user2 | 66.6% |

**Live project totals.** In the same request, before reading the project totals, the subject's enforcement-off budgeted projects are refreshed. A project is refreshed when its newest `project_budget` row is older than `BUDGET_MEMBER_SPEND_STALENESS_THRESHOLD_MS`, or when it has no row. Each refresh works as follows:

1. Take a shared try-lock on a new global project-refresh key, then a per-project try-lock (`pg_try_advisory_xact_lock`). If either lock is busy, skip that project and use the stored value.
2. Re-check staleness under the lock.
3. Call a new provider method, `collect_project_budget_spend_for_projects(project_names)`. It makes one `/key/info` call per project budget key. Settings reads (`get_project_litellm_creds_by_alias`) run via `asyncio.to_thread`. A failure in one project, including `InvalidSpendSnapshotError`, skips that project only. The no-op provider returns `[]`.
4. Insert a `project_budget` row only when the quantized total changed or `is_reset_transition` fires. The first row for a project budget is inserted even at zero, so staleness has a timestamp to read. `daily_spend` and `cumulative_spend` use the collector's delta helpers.
5. Otherwise, touch the newest row's `spend_date` with a new `touch_project_budget_spend_dates` method. Like `touch_member_spend_dates`, it skips a row when a newer row exists. A row at or before its reset cutoff is never touched or rewritten (`_is_unchanged_since` / `_is_hidden_and_unchanged` semantics).
6. Commit, roll back, then reload, as in the member refresh. Any exception rolls back and falls back to the stored values. The response never fails.

The member spend refresh (`_refresh_user_member_spend`) also covers the enforcement-off projects that are now selected.

**Nightly safety.** `_collect_provider_project_budgets` takes the global project-refresh advisory lock exclusively, waiting for it. It holds the lock from before `collect_project_budget_spend()` until `insert_project_budget_entries` commits. Without the lock, a live insert or touch made during the nightly's fetch window could be excluded from its "latest before snapshot" baseline, and that delta would be counted twice. Rows the collector selects and writes do not change. Reset cutoffs keep coming from `get_project_reset_cutoffs_for_projects` over all `project_budget` rows, live ones included.

## Acceptance criteria

- AC1: Every `/budget_usage` row has a boolean `is_default`. Labels are unchanged, and the `"<email> (cli)"` personal row is unchanged.
- AC2: Default-project rows come first, then personal rows, then the remaining projects case-insensitively. Personal rows are flagged when the resolved default (fallback included) is the subject's personal project.
- AC3: The admin view (`?user_id=`) flags and orders rows by the target user's memberships and email.
- AC4: `SpendingCard` table mode shows a "Default" badge on `is_default` rows, styled like `BudgetOverrideIndicator`. Doughnut mode shows no badge.
- AC5: Each budgeted category of an enforcement-off project gets a row: `current_spending` is the member's spend, and `total` is the project total over the project max. The worked example gives 50% for both members.
- AC6: Enforcement-off projects without a budget show no row.
- AC7: A FIXED-override member of an enforcement-on project has `budget_limit` equal to the override (via `member_effective_max_budget`), with no cap. A FIXED-override member of an enforcement-off project has `budget_limit = min(override, member_spend + max(0, project_max - project_total))`. In both cases `total` is member spend over `budget_limit`.
- AC8: Rows of enforcement-on projects for non-override members are identical to today.
- AC9: Stale totals of the subject's enforcement-off budgeted projects are refreshed live, with one `/key/info` call per project key. Fresh projects and enforcement-on projects are not fetched.
- AC10: If the per-project lock or the global lock is busy, there is no fetch and no write, and the stored value is shown.
- AC11: A row is inserted only on a changed total, a reset transition, or a project budget's first row. An unchanged total touches `spend_date`. Repeated page views add no rows. A row at or before its reset cutoff is never touched.
- AC12: Settings reads in the live path run off the event loop. A failing project does not affect the others. Any refresh failure returns the stored values with HTTP 200.
- AC13: `/user-project-spending` and `/project-member-spending` responses are byte-identical to today's for the same DB state.
- AC14: The nightly project-budget step holds the global lock exclusively from fetch to commit, and its inserted rows are unchanged. Lifetime spend (the sum of `daily_spend`) is not double-counted by live rows.
- AC15: Enforcement-off FIXED cap cases:
  - binding cap: user1 $20 limit / 50%, user2 90%;
  - non-binding cap: $40 limit / 25%;
  - over budget: limit equals member spend, 100%;
  - zero spend with nothing left: $0 limit rendered as 0%.
- AC16: Results for the external team's cases:
  - off, no override, $10 and $20 of $100: 30% for both;
  - on, $50 shares, $10 and $20 spent: 20% / 40%;
  - on, user1 override $40 and others $30: user1 $10 is 25%, user2 $20 is 66.6%.

## Non-goals

- No "Overridden" badge, no "Shared" hint, no change to zero-limit behaviour.
- No change to `/user-project-spending` or `/project-member-spending` code or output.
- No new DB columns, tables or migrations; no Redis or in-process freshness markers.
- No changes to `repos/codemie-code`.
- No live refresh for enforcement-on projects or for projects the subject is not a member of.
- No refactor of the global loader `_load_project_key_spend_from_litellm`: its synchronous settings read and all-or-nothing error handling stay.
- No change to the member-budget nightly/touch interaction.
- No new badge or other UI change in doughnut mode or in other spending views.
- No remaining-budget cap on enforcement-on rows.

## Risks

- A touch moves a `project_budget` row's date on any per-date project spend history. This is required by the task and matches the member-row precedent.
- Live reset detection can set a reset cutoff before the nightly run would. That changes what `/project-member-spending` hides for the project, which follows from keeping cutoffs consistent.
- The merged member refresh has the same nightly-window double-count exposure. It is out of scope.
- A manual collector backfill with a past target date can still diff against live rows dated after it.
- Known limitation: a FIXED member of an enforcement-off project with no spend and no budget left gets a $0 limit and shows 0%, not "exhausted". Zero-limit rendering is unchanged by decision.
- The FIXED cap uses the project total and the member spend as last refreshed. When either is stale, for example because a lock was busy, the cap is off by the same amount.
