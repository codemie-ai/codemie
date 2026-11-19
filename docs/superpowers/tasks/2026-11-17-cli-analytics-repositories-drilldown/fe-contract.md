# FE API Contract - EPMCDME-17349: CLI Analytics Repositories drill-down

**Backend ticket:** EPMCDME-17349
**Backend branch:** `EPMCDME-17349`
**Status:** implemented on branch EPMCDME-17349; pending release measurement
**Applies to:** `GET /v1/analytics/cli-analytics/projects`, `/repositories`, `/branches` (new), `/sessions` (one new flag)

---

## Summary

Each drill-down level loads only its own data. The projects list has totals only when the UI sends `include_repositories=false`. A project modal pages its repositories, a repository modal pages its branches. All paged lists share the envelope `{page, per_page, total_count, has_more}`. `page` is **zero-based** (FE hook state is 1-based: send `page - 1`). Page size for this feature is 10.

All routes keep the response envelope `{"data": {...}, "metadata": {...}}`, admin gate and the shared filter params (time, `users`, `subscription_type`, `project_origin`, ...).

## Unattributed row (all three lists)

Applies to `/projects` only with `include_repositories=false`, to `/repositories` only with `unattributed_last=true` (without it the no-repository group is ranked and counted like any row, as before), and always to `/branches`.

A row whose name is `null` (`project_name`, `repository` or `branch`) means sessions with no project / repository / branch.

- It is **not** counted in `total_count` and not in `has_more`.
- It is returned once, as the **last row of the last page**, outside the 10 named rows (the last page can hold 11 rows). Other pages never contain it.
- It is absent when no such sessions exist, and **absent whenever `search` is set**. A page beyond the last returns no rows.
- When a list has only an Unattributed row, `total_count` is 0, `has_more` false and `rows` holds that one row (page 0).
- **Empty state is keyed on `rows` being empty, not on `total_count` being 0.** With `rows: []` show the empty state and no pagination; with only the Unattributed row show it as a normal list.
- The row is best effort across requests: sessions arriving between page clicks can add or remove it.
- No-project data is visible to super-admins only. For anyone else a no-project request (`is_project_unattributed=true`) on `/repositories` or `/branches` returns `rows: []`, `total_count` 0 with **HTTP 200** (not an error). `/sessions` differs: it returns the viewer's own sessions.

---

## GET `/projects` - new opt-out `include_repositories`

Params: `page` (0-based), `per_page`, `search`, `include_repositories` (bool, default `true`), filters.

**The UI must send `include_repositories=false`.** The two modes:

| | `include_repositories=true` (default, unchanged) | `include_repositories=false` (drill-down) |
|---|---|---|
| Row `repositories` | nested repositories, each with its branches | `null`, nothing nested is built |
| `search` | project name or repository name | project name only |
| Unattributed project (`project_name: null`) | ranked last among the projects and counted in `total_count` | **not** ranked or counted; appended once as the last row of the last page, outside `per_page`; never under a search (Unattributed row rule above) |

- Kept on each row in both modes: `project_name`, `repository_count`, `session_count`, `turns`, `cost_usd`, `files_changed`, `lines_added`, `lines_removed`, `net_lines`, `tool_calls`, `tool_calls_success`, `tool_success_rate`, estimate fields. With `include_repositories=false` use the clicked row as the modal Summary.
- `ProjectRow.repositories` is optional in the FE type (`ProjectRow["repositories"]?: Repository[] | null`).

```json
{"data": {"rows": [{"project_name": "alpha", "repository_count": 3, "session_count": 40, "cost_usd": 12.3, "repositories": null}, {"project_name": null, "repository_count": 1, "session_count": 4, "repositories": null}],
          "pagination": {"page": 0, "per_page": 10, "total_count": 1, "has_more": false}}}
```

## GET `/repositories` - project modal

Request: `projects=<project name>&page=0&per_page=10&unattributed_last=true`, or for the Unattributed project row `is_project_unattributed=true&page=0&per_page=10&unattributed_last=true` (super-admin only; the backend now honours `is_project_unattributed`, it was ignored before). **The UI must send `unattributed_last=true`** on this request; without it the response is the unchanged legacy one (the `repository: null` group ranked and counted inside `per_page`).

- Rows contain only that project's sessions, with that project's own metrics (the project filter is applied before grouping). Send exactly one project: a comma list returns one combined view across them, a shared repository summed.
- Order: session count desc, then cost desc, then name. With `unattributed_last=true` the Unattributed repository row (`repository: null`) follows the Unattributed row rule above.
- Row shape is unchanged (`repository`, `project_name`, metrics, `branches` absent unless `include_branches=true`).
- Without `page`/`per_page` the endpoint still returns everything with branches (unchanged, for other views). Do not use it for the drill-down.

## GET `/branches` - new

Request: `repository=<repository name>` (required, query param; names may contain `/`; ANDed with the shared `repositories` filter), plus `projects=<project name>` or `is_project_unattributed=true` for scope, `page`, `per_page`.

```json
{"data": {"rows": [{"branch": "main", "session_count": 12, "turns": 80, "cost_usd": 3.2, "files_changed": 20,
                    "lines_added": 300, "lines_removed": 40, "net_lines": 260, "tool_calls": 90,
                    "tool_calls_success": 85, "tool_success_rate": 0.94},
                   {"branch": null, "session_count": 2}],
          "pagination": {"page": 0, "per_page": 10, "total_count": 1, "has_more": false}}}
```

Order: session count desc, then cost desc, then name. The `branch: null` row follows the Unattributed row rule. Use the clicked repository row as the modal Summary. The Unattributed repository row (`repository: null`) has no branch list: open the sessions modal for it directly.

## GET `/sessions` - one new optional flag

`is_branch_unattributed=true` returns only sessions with no branch. It ANDs with `projects`, `is_project_unattributed` and `repositories`, so send the same scope as the list row you clicked. It **requires `repositories`** (422 without) and is **invalid with `branch`** (422). Use it for the Unattributed branch row. Omitted/false: no change. Existing flags (`branch`, `is_unattributed`, `is_project_unattributed`) are unchanged.

---

## Recommended FE usage

1. Repositories tab: `/projects?include_repositories=false`, page size 10, search = project name.
2. Project modal: Summary from the clicked project row; `/repositories` with the project scope.
3. Repository modal: Summary from the clicked repository row; `/branches` with the repository and its project scope.
4. Branch modal: unchanged; use `is_branch_unattributed` for the null branch row.
5. Drop `fetchProjectBranches` and `useCliAnalyticsProjectBranches` (they relied on the unpaged forced-all call).

## Backwards compatibility

- `/projects` with no `include_repositories` (the default `true`) is unchanged: nested `repositories`, name-or-repository search, Unattributed project ranked and counted. Existing readers need nothing; only the drill-down UI opts in with `include_repositories=false`, so FE and backend need not be released together for `/projects`.
- `/repositories`, `/sessions`: additive only. `/repositories` without `unattributed_last` (default `false`) behaves as before this branch, apart from the previously ignored `is_project_unattributed` now being honoured (the one intentional change); only the drill-down UI opts in with `unattributed_last=true`. The unpaged mode stays; its only drill-down consumer is the 17301 loader being removed, `OverviewView` uses the paged form.
- OpenAPI: no schema file for FE is published; the guard baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json` is regenerated with this change.
