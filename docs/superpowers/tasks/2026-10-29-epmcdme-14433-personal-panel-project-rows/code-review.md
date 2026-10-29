---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: aa652e19c39ed5662924ea21fd174b6d5bc5ac50
---

# Code review — 2026-10-29-epmcdme-14433-personal-panel-project-rows

**Decision:** approve (check round, confidence final high · check high)  
**Reviewed head:** `aa652e1` · **Rounds:** final → check

**Closed:** 6 fixed · 0 moot · 0 decided

Targeted check of all 6 prior blocking findings against the source at HEAD 8aeb1f35 plus the uncommitted fix-up (diff signatures verified): all 6 are resolved, none are unresolved and no new findings were sought. The user-requested DEFAULT badge restyle in codemie-ui (signature verified) only changes the project_name renderer in table mode and keeps the label text and the doughnut mode as they were, so it introduces no regression. Residual note on CR-004: a project with no stored total, or one whose only total is hidden by the reset cutoff, is still fetched on every load, as the recommendation allowed. 0 deferred, 0 dismissed.

## Open — 0

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| AC1: every /budget_usage row has boolean is_default; columns and labels unchanged | pass | _build_spending_row always sets is_default; columns untouched |
| AC2: default rows first, then personal, then remaining; personal rows flagged when default is personal | pass | default_rows + personal_rows + other_rows; resolve_membership_default incl. fallback |
| AC3: admin view flags and orders by target user's memberships and email | pass | _resolve_admin_budget_subject returns target email |
| AC4: SpendingCard table mode Default badge; doughnut mode no badge | pass | customRenderColumns only when rowCount > 1 |
| AC5: enforcement-off budgeted category row; current_spending = member spend; total = project total / project max | pass | _apply_unenforced_limits sets percent_spend to project total |
| AC6: enforcement-off projects without a budget show no row | pass | filtered to enforced or budgeted |
| AC7: FIXED override on = override; off = min(override, spend + max(0, max - total)) | pass | FIXED branch applies min() cap only for enforcement-off |
| AC8: enforcement-on rows for non-override members unchanged | pass | enforced projects skipped in _apply_unenforced_limits |
| AC9: stale enforcement-off budgeted totals refreshed live, one /key/info per key | pass | _refresh_user_project_totals over _stale_budgeted_projects |
| AC10: busy per-project or global lock means no fetch, no write | pass | try_lock false -> early return, rollback |
| AC11: insert only on change/reset/first row; else touch spend_date; no row growth | pass | _rows_to_write insert/touch split |
| AC12: settings reads off event loop; failures isolated; HTTP 200 with stored values | pass | asyncio.to_thread; per-project try/except + rollback |
| AC13: /user-project-spending and /project-member-spending output unchanged | pass | effective_limits passed only by budget_usage |
| AC14: nightly step holds global lock exclusively fetch-to-commit; lifetime spend not double-counted | pass | lock held as specified; see CR-005 for a baseline race outside the lock window |
| AC15: enforcement-off FIXED cap cases (binding, non-binding, over budget, zero) | pass | parametrized cap test |
| AC16: external team cases | pass | off via project total; on via allocated_max_budget |
| get_user_project_rows keyword-only flag passed only by get_budget_usage | pass | effective_limits after '*' |
| Enforcement-off rows: reset date comes from the budget that supplies the limit | partial (final round) | FIXED rows keep override reset even when the remaining-project-budget cap binds (CR-002) |
| Enforcement-off rows: missing project total row counts as 0 | pass | totals.get(key, 0.0) |
| Refresh re-checks staleness under the lock | pass | _stale_budgeted_projects after try_lock |
| New provider method collect_project_budget_spend_for_projects; no-op returns [] | pass | protocol, registry, adapter |
| daily_spend/cumulative_spend use the collector's delta helpers | pass | _compute_spend_delta delegates to collector helpers |
| Member spend refresh covers the enforcement-off projects now selected | pass | allocations loaded for selected set |
| UI keeps the server order | pass | no sorting added |
| Non-goal: no new DB columns, tables, migrations, Redis or in-process freshness markers | pass | none added |
| Non-goal: no refactor of _load_project_key_spend_from_litellm | pass | global loader untouched |
| Non-goal: no live refresh for enforcement-on or non-member projects | pass | keyed on subject's unenforced budget ids |
| Non-goal: no remaining-budget cap on enforcement-on rows | pass | cap only in _apply_unenforced_limits |
| Non-goal: no Overridden badge, Shared hint, doughnut-mode or other-view UI change | pass | UI diff limited to project_name renderer |
| Non-goal: no changes to repos/codemie-code | pass | none |
| spend_collector_service.py adds a lock only | pass | lock acquisition and fetch moved inside locked session |

Standards: commit-format pass · code-quality partial (final round) · security pass
