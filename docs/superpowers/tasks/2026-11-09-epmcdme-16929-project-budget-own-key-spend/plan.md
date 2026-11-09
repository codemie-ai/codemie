# EPMCDME-16929: Spend billed to the global/own LiteLLM key must not count as project budget spend

Commit nothing and push nothing (run owner's instruction); leave changes in the working tree. No explanatory code comments, no `docs/<feature>.md` page. Production change is read-side only, in `src/codemie/repository/spend_logs_repository.py`.

## Acceptance criteria

1. Given a global LiteLLM integration key is configured, when a user chats with an assistant under their personal project, then the spend is attributed to the global key and the personal project's displayed spend remains unaffected (matches LiteLLM: $0 for the project).
2. Given a global LiteLLM integration key is configured, when a user chats with an assistant under a project with a budget, then the spend is attributed to the global key and the project's displayed spend remains unaffected (matches LiteLLM: $0 for the project).
3. Given spend has been incurred against the global key, when a user or admin reviews spend in the platform frontend, then the numbers shown for personal projects and projects with budget match what LiteLLM reports for those entities.
4. Given a project with a budget has a hard limit configured, when usage occurs under the global LiteLLM integration key, then that usage does not consume or trigger the project's hard limit.
5. Given the fix is applied, when existing non-global-key attribution flows are exercised (personal LiteLLM key, project budget without global key), then their spend attribution behavior remains unchanged.

| AC | Proved by |
|---|---|
| 1 | Task 2 (`PersonalHolder` over own-key rows, pinned) |
| 2 | Task 1 (predicate shape), Task 2 (`ProjectHolder`/`ProjectMemberHolder` figures), Task 3 (projection) |
| 3 | Tasks 1-3; every project budget surface reads these holders, personal surfaces read `PersonalHolder` |
| 4 | Task 2 (the figure that feeds percent/limit math excludes the row); enforcement itself needs no change (verified, below) |
| 5 | Task 2 (project-funded, stopped-budget and personal-customer rows unchanged); Task 1 (`_personal_predicate`, `_project_scoped_match*` untouched). See the approved deviation below. |

## Decisions (user-approved)

- **AC5 deviation.** Own-credentials rows of a personal (non-global) LiteLLM user key also stop counting toward project budget figures, exactly like the global key. This is a deliberate deviation from the literal AC5 wording: global and non-global own keys take the same bypass (EPMCDME-13264) and write the same `end_user='N/A'` row, so the read side cannot tell them apart, and LiteLLM charges the project $0 for both. Every other non-global flow (project-funded, stopped/inactive project budget rows charged to the person's own customer, personal-customer budgets) stays unchanged.
- **Usage analytics out of scope (AC3).** `projects-spending`, summaries and the other usage dashboards are untouched, as is `PersonTotalHolder`. They read report filters/`SpendGroup.TOTAL`, documented as "everything spent in the project" (`project_handler.py:332-339`), not holder predicates.

## Verified in `codemie-enterprise` (2.3.47, the backend pin; read-only)

- **Uploader writes `'N/A'`.** `spend_uploader/transform.py:10-11,97-102`: `END_USER_PLACEHOLDER = "N/A"`, `stored_end_user = end_user if end_user else "N/A"`, so `''` and `None` both become `'N/A'`; the ledger never holds NULL/`''` in `end_user`, and real customer ids are stored verbatim. `transform.py:82-87` calls `decide()` on the raw `end_user` before the substitution.
- **Own-key rows are `explicit` + `project_scoped`.** `attribution.py:119-135` `decide()`: valid UUID `user_id` + category in {platform, cli, premium_models} + non-empty project gives `attr_source='explicit'`, `attr_form='project_scoped'`, `attr_project=<project>` and never consults `end_user`. So a filter on `attr_form`/`attr_project` alone cannot exclude them; `end_user` is the discriminator. The backend sends the metadata from `proxy_router.py:365-410`.
- **Row cases** (`end_user`): (a) own/global key -> `N/A`; (b) project-member customer -> `codemie:project:<p>:category:<c>:user:<id>` verbatim; (c) personal customer -> email, `<email>_codemie_cli`, `<email>_codemie_premium_models`; (d) stopped/inactive project charged to the person's own customer -> same as (c), and if `codemie_project` is still sent the row is `project_scoped` with a personal `end_user`. `NOT IN ('N/A','')` keeps (b), (c), (d) and drops (a), which is what AC5 needs for (d). `END_USER_PLACEHOLDER = 'N/A'` is now the contract this filter relies on; changing it would break the backend filter.
- **Hard limits are LiteLLM-side (AC4).** `litellm/service.py` is an admin-API client: limits are `max_budget` on `BudgetTable` (`:117-122`, `create_budget :204-231`), project customers are `codemie:project:<p>:category:<c>:user:<id>` (`:443-445`, `_upsert_project_customer_budget :983-1022`), `get_customer_spending` (`:1251-1284`) reads LiteLLM `customer.spend`, not the ledger. The only SQL on `litellm_spend_logs` there is the uploader sink (write path) and `report.py:21-36` (counts). Nothing in enterprise mirrors `predicate_for_holder`. A no-customer call cannot move any LiteLLM customer counter, so global-key usage cannot consume or trigger a project hard limit. In this backend `check_user_budget` (`dependencies.py:440`) also reads LiteLLM customer spend, emits metrics only and fails open.

## Verified in the backend

- **Own-key path sends no customer and no body `user`.** The customer id is set only through the `x-litellm-customer-id` header, in non-bypass branches (`llm_factory.py:588,616,1108`; `proxy_router.py:350,1044,1085`). The bypass branches (`llm_factory.py:749-766`, `proxy_router.py:938-950`) set none. The backend never injects a body `user` (no `inject_user_at_brace` or equivalent in `src/codemie/enterprise/litellm`; `_stream_body_bytes` forwards the body unchanged; the chat path sets no `user`/`extra_body`/`model_kwargs`).
- **Personal project (a).** `PersonalHolder` renders it (Profile `<email>` DEFAULT row via `budget_usage_service.py:374`, `user_management_service.py:120`, `spending.py:99`, `budget_handler.py:357`) and `_personal_predicate` already drops `N/A` rows (`spend_logs_repository.py:351-357`, EPMCDME-15204). Personal projects never reach project holders (`member_spend_service.py:307-327` excludes them; their calls carry no `attr_project`: `proxy_router.py:418-432`). No predicate change for them; Task 2 pins it.
- **Hard-limit surfaces (b).** Wrong CodeMie-side figures were display math fed by `ProjectHolder`/`ProjectMemberHolder`: project spend/percent (`project_spending_service.py:312-346`, `crud.py:147-180`), `_project_totals` and the unenforced effective cap (`member_spend_service.py:216-272`), member spend (`:358`, `:440`). Task 1 fixes them, enforcement on or off; no separate task.
- **(c) Predicate choice.** A `LIKE 'codemie:project:%'` term would also drop case (d) rows and reverse the `_personal_predicate` docstring note, so the plan excludes only no-customer rows.
- **Every ledger reader of a project holder.** `predicate_for_holder` has exactly two callers: `sum_for_holder` (`spend_logs_repository.py:713`) and `sum_for_holders` (`:772`), both covered by Task 1. `enterprise/litellm/spend_ledger.py` (`:171`, `:283`, `:368`, `:497`), `service/spend/ledger.py` and `cutover.py` only dispatch to those two or label results; `_project_portion` (`spend_ledger.py:770-785`) is an annotation over project-less rows below the identity floor, not a project-holder figure. No reader builds its own project-holder filter.
- **Legacy/Elasticsearch path needs no change.** Below the coverage floor `legacy_ledger.consumed_for_holders` (`:288-296`) and `default_ledger.consumed_for_holder` (`:422-428`) answer `PersonalHolder`, `ProjectHolder`, `ProjectMemberHolder` and `ProjectGroupHolder` with `SpendResult.no_figure`, because the metric store records no budget category. There is no ES project-holder figure to exclude own-key rows from; only `PersonTotalHolder` goes to ES, and it is out of scope. No task added.

## Still unverified

- Production data: that real own-key rows in the live ledger carry `end_user='N/A'` (supported by the uploader source, the earlier local live repro and the backend row-shape reading above).
- What LiteLLM itself records in `LiteLLM_SpendLogs.end_user` when no customer header is sent. The uploader assumes `''`/NULL. On the proxy path a client-supplied body `user` is forwarded unchanged and LiteLLM could use it; such a row would carry a non-`N/A` `end_user` and would stay in the project figure like case (d). The backend itself never injects one.

## Task 1: Project holders exclude rows that charged no customer

**Files:** modify `src/codemie/repository/spend_logs_repository.py:263-279` (and its docstring :252-259); test `tests/codemie/repository/test_spend_logs_repository.py`, class `TestTheProjectHolders` (:161).

Add one new helper and `and_` it into the `ProjectHolder`, `ProjectMemberHolder` and `ProjectGroupHolder` cases. Do not touch `_project_scoped_match`, `_project_scoped_match_any` or `_personal_predicate`. Adjust the `predicate_for_holder` docstring in one or two sentences so the "two pots" paragraph says a project pot excludes rows that charged no customer.

```python
def _charged_a_customer() -> ColumnElement[bool]:
    return Row.end_user.notin_(_NO_CUSTOMER)
```

Test-first: yes - for each of the three project holders, `sql(predicate_for_holder(...))` contains `end_user NOT IN ('N/A', '')` and still contains its project, category (and user id) conjuncts; it contains no `LIKE`; `sql(_project_scoped_match("a"))` and `sql(_project_scoped_match_any(["a"]))` contain no `end_user`; the `PersonalHolder` rendering is unchanged. All fail before the helper exists.

## Task 2: Row-level figures for own-key, project-funded and stopped-budget rows

**Files:** modify `tests/codemie/repository/test_spend_logs_holder_access_boundary.py` (`_project_row` :87, new test class).

`_project_row` builds rows with NULL `end_user`, which the new term excludes, so give it an `end_user` parameter defaulting to the project-member customer string (`codemie:project:<project>:category:platform:user:<user_id>`); the existing `PROJECT_A` expectations (50.01 / 0.01) then hold unchanged. Add a class on the `every_match_path`-style SQLite fixture with one user and rows in one period: project-funded member row 1.00, own-key row in project A (`explicit`, `project_scoped`, `end_user='N/A'`) 2.00, same with `end_user=''` 4.00, stopped-budget row (`project_scoped`, `end_user=<own customer>`) 8.00, personal-project own-key row (`explicit`, `principal`, no project, `N/A`) 16.00, personal-project own-customer row 32.00.

Test-first: yes - `ProjectHolder(A)` and `ProjectMemberHolder(A, user)` read 9.00, not 15.00 (fails before Task 1; covers AC2/AC4/AC5); `PersonalHolder(user, aliases=None)` reads 40.00 (the 16.00 and the project 2.00/4.00 rows stay out; pins AC1/AC5 and passes already); `sum_for_holders` equals `sum_for_holder` for all three.

## Task 3: Project holders are still answered from the hourly projection

**Files:** modify `tests/codemie/repository/test_spend_projection.py`, beside `test_a_personal_holder_is_answered_from_the_projection` (:227).

Test-first: yes - `projected_statement` over `ReadShape(where=predicate_for_holder(ProjectHolder(...)))` selects `FROM litellm_spend_hourly` and contains `litellm_spend_hourly.end_user != ALL (ARRAY['N/A', ''])` (fails before Task 1: no `end_user` term). Repeat for `ProjectMemberHolder`.

## Negative-constraint pass

- Usage analytics out of scope: no task touches `project_handler`, report filters or `PersonTotalHolder`; `_project_scoped_match*` stay as-is (shared with `access_predicate` and report filters).
- AC5 non-global flows unchanged, except the approved own-key deviation: Task 1 leaves `_personal_predicate` untouched and adds no project-prefix term; Task 2 asserts stopped-budget and personal-customer rows keep their figures.
- No comments/docs/commits: none planned; only the existing docstring is corrected.
- Tests required: all three tasks are test-first.
