# AI Attribution (CLI analytics) access for every role (EPMCDME-17657) Implementation Plan

> **The earlier auditor-only version of this plan is superseded.** It is kept below as the record of the first iteration; the current contract is the "Current contract" section, the guide matrix (`.ai-run/guides/integration/cli-analytics-events.md`, Visibility) and the MR description.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

> **Scope update (2026-11-20, product decision, ticket updated).** The plan was first written for an auditor-only change and the ticket then grew. Statements below that say "auditor only" or "403" describe the first iteration and are superseded by the "Current contract" section.
>
> - `_admin_gate` is removed: every role reaches every CLI analytics route and the viewer scope decides what it reads (a regular user no longer gets 403).
> - `ViewerScope.own_shared` lets a viewer with no admin project read their own sessions, shared and private; `projects` and `users` selectors only narrow the scope.
> - `sees_private(user)` is true for admin, maintainer and auditor (it was auditor only). A project admin and a regular user stay owner-only for private sessions.
> - The role matrix in the guide and in the MR now covers Super Admin, Maintainer, Project Admin, Auditor and Regular User; the RBAC suite covers all five.
> - The dashboard cache key carries `sees_private` and `own_shared`.
> - UI visibility of the tab for every role is still a separate frontend change (ticket AC4).

**Goal (first iteration, extended by the "Current contract" section):** An auditor reads all CLI analytics, read-only: the 11 gated routes, `/sessions`, `/sessions/{trace_id}`, including other users' private Personal sessions and project-unattributed sessions.

**Architecture:** Two layers. Router (`src/codemie/rest_api/routers/cli_analytics.py`): `_admin_gate` admits the auditor; `viewer_access` makes the auditor unrestricted; the `ViewerScope` carries a new `sees_private` flag set only for auditors. Reader (`src/codemie/repository/cli_analytics/scope.py`): the truth table lets `sees_private` read class P rows in `own_only` and `detail` modes. In the first iteration super admin, project admin and regular user behavior was unchanged; the current contract above changes that.

**Tech Stack:** FastAPI, pytest, TestClient with the `make(user)` fixture.

**Spec:** none (requirements inline). Commit per task using the repository's existing convention.

**PO decision (explicit):** the auditor reads all CLI analytics data, including other users' private Personal sessions and unattributed sessions ("the auditor reads everything"). This is the role's rule, not an exception; it supersedes the ticket's "within authorized scope" wording. Private Personal stayed owner-only for super admin, project admin and regular user in the first iteration; admin and maintainer now read it too (see "Current contract"). Project admin and regular user stay owner-only.

## Current contract (supersedes the auditor-only text below)

| Data class | Super Admin / Maintainer | Project Admin | Auditor | Regular User |
|---|---|---|---|---|
| Dashboard routes (the 11 routes) | 200, all projects | 200, own admin projects plus own sessions | 200, all projects | 200, own sessions only |
| `/sessions`, `/sessions/{trace_id}` | all | own admin projects plus own sessions | all | own sessions |
| Own private Personal sessions | yes | yes | yes | yes |
| Others' private Personal sessions | yes | no (404) | yes | no (404) |
| Project-unattributed sessions | yes | no | yes | no |
| Write access | none | none | none | none |

- There is no 403 gate on the dashboard routes: the viewer scope decides what each role reads. A regular user reaches the dashboard and sees only their own data.
- Shared aggregates and the default `/sessions` list exclude private Personal rows for every role; others' private sessions are read on `/sessions?personal_only=true` and session detail only.
- `projects` and `users` selectors only narrow the scope; they never widen it.

## Acceptance criteria

- Auditor (no admin rights) gets 200 on all 11 gated GET routes, with unrestricted scope; no write route exists.
- Auditor reads other users' private Personal sessions via `/sessions?personal_only=true` and `/sessions/{trace_id}`, and project-unattributed sessions (`project_unattributed=true`).
- Super admin: unchanged (everything shared, unattributed allowed, Personal owner-only).
- Project admin: unchanged (own admin projects only, no unattributed, Personal owner-only).
- Regular user (current contract): 200 on the 11 routes with their own sessions only; `/sessions` and detail return only their own sessions. The first iteration of this plan had a 403 here; it is superseded.
- The role matrix is documented in the guide; the old "auditor has no extra right / strictly owner-only even for auditor" rule is replaced.

## Assumptions

- Shared aggregates (the 11 routes, and the default `/sessions` list) keep excluding Personal rows for every role, auditor included (mode `exclude`). Simplest consistent rule: the SQL builders and the cost-class definitions rely on it. The auditor reaches others' private sessions through the Personal list (`personal_only=true`) and detail only.
- In `personal_only` mode `_resolve` still ignores `users`/`projects` selectors (`:482-484`); the auditor's Personal list is therefore unfiltered by user.

## Global Constraints

- Do not touch UI; no new routes. Follow `.ai-run/guides/` (error-handling, testing-patterns). The dashboard 403 gate (`_admin_gate`) was removed in the extended scope, so there is no 403 shape to keep (first iteration: only its `details` text mentioned the auditor).

## Review Focus

- Project admin and super admin must NOT gain others' Personal (negative tests, Tasks 1 and 3).
- Auditor+project-admin: still `sees_private` and unrestricted (Task 2).
- A fail-closed reader: a scope with `sees_private` not a bool, or an unknown mode, allows nothing (Task 1).
- Other readers' use of `own_only` (grep `own_only` under `src/codemie/repository/cli_analytics`) must honor the flag, not only `_condition` (Task 1).

---

### Task 1: Reader scope — private-visibility flag

**Files:**
- Modify: `src/codemie/repository/cli_analytics/scope.py:15-31` (docstring), `:64-95` (`ViewerScope`, `_valid`, `allowed`), `:116-146` (`_condition`)
- Modify any other reader code that special-cases `mode == "own_only"` or `detail` (grep first)
- Test: the existing scope truth-table test file under `tests/codemie/repository/cli_analytics/` (find with `Grep "def test.*allowed|sql_list_condition"`)

**Interfaces:**
- Produces: `ViewerScope(viewer_email, unrestricted, admin_projects, mode, sees_private: bool = False)`; frozen dataclass, default keeps all existing constructions valid.
- Truth table: `own_only` = P and (owned or `sees_private`); `detail` = (P and (owned or `sees_private`)) or ((N or M) and authNP); `exclude` unchanged.

- [ ] **Step 1: Write failing tests.** `allowed()` cases: `sees_private=True` in `own_only` and `detail` allows a P row owned by someone else; `exclude` still denies P; `sees_private=False` (default) unchanged; non-bool `sees_private` fails closed. SQL twin: `sql_list_condition` with `mode="own_only", sees_private=True` is the bare `sql_private("x")` condition with no `scope_viewer` requirement; `detail` ORs it with `non_personal`; with `sees_private=False` output is byte-identical to today.
- [ ] **Step 2:** Run the scope test file; expect FAIL (unknown field).
- [ ] **Step 3: Implement.** Add the field and `_valid` check; update the module docstring table; in `allowed` set `owned = row_class == "P" and (scope.sees_private or <existing match>)`; in `_condition` use `owned = is_p` when `sees_private`. Apply the same flag to any other `own_only`/`detail` SQL found by the grep.
- [ ] **Step 4:** Run `poetry run pytest tests/codemie/repository/cli_analytics -q`; expect PASS.

**Test-first: yes — `allowed(ViewerScope(..., sees_private=True), "P", "other@x", ...)` fails today (the field does not exist; foreign P denied).**

### Task 2: Router policy — admit and widen the auditor

**Files:**
- Modify: `src/codemie/rest_api/routers/cli_analytics.py:145-167` (`viewer_access`, `_admin_gate`), `:450` (`resolve_sessions`), `:505-510` (the `ViewerScope(...)` built in `_resolve`), and the `/sessions/{trace_id}` scope at ~`:1090`
- Modify test: `tests/codemie/rest_api/routers/test_cli_analytics_owner_sessions.py:227-298`

**Interfaces:**
- Consumes: Task 1 `ViewerScope.sees_private`.
- Produces: `viewer_access(user) -> tuple[bool, frozenset[str]]` (signature unchanged): unrestricted = `bool(user.is_admin or user.is_auditor)`. New helper `sees_private(user) -> bool` returning `bool(user.is_auditor)` (auditor only, not super admin), passed as `sees_private=` to every `ViewerScope` the router builds (list, personal_only and detail).

- [ ] **Step 1: Update tests.** Role matrix (`:230-238`): `auditor` row becomes unrestricted `True`, mode `exclude`; `auditor+project-admin` also unrestricted `True`. Replace `test_an_ordinary_auditor_stays_out_of_the_aggregates` with `test_an_ordinary_auditor_reads_the_aggregates_unrestricted` (200, `scope.unrestricted is True`, `deny_all is False`, `scope.sees_private is True`). Rewrite `:261` and `:277` to the unrestricted behavior (auditor-project-admin with `projects=p1,other` keeps `["p1","other"]`). Extend the matrix test: `/sessions?personal_only=true` scope is `own_only` with `sees_private == user.is_auditor`; detail scope `sees_private` likewise; for super admin and project admin it is `False`. Add `test_auditor_project_unattributed_is_allowed_and_project_admin_denied`: auditor `/overview?project_unattributed=true` gives `deny_all is False`; project admin gives `deny_all is True`.
- [ ] **Step 2:** Run the file; expect FAIL.
- [ ] **Step 3: Implement.** `_admin_gate` admits `user.is_auditor`; message "requires administrator, project administrator or auditor privileges." `viewer_access` and `resolve_sessions` (`privileged` adds `is_auditor`); rewrite the `viewer_access` docstring (auditor is unrestricted and reads private Personal by PO decision; super admin is not widened). The unattributed check `deny_all or not unrestricted` (`:493`) needs no change because the auditor is now unrestricted; leave it and cover with the test above.
- [ ] **Step 4:** `poetry run pytest tests/codemie/rest_api/routers -k cli_analytics -q`; fix other tests that asserted auditor 403 (`test_cli_analytics_pricing_visibility.py`, `test_cli_analytics_policy_contracts.py`); expect PASS.

**Test-first: yes — auditor `/overview` returns 403 today; the new test expects 200 with `scope.unrestricted is True` and `scope.sees_private is True`.**

### Task 3: Per-role RBAC regression suite

**Files:**
- Create: `tests/codemie/rest_api/routers/test_cli_analytics_rbac_roles.py` (license header; reuse the `make` fixture pattern from `test_cli_analytics_owner_sessions.py:83`)

- [ ] **Step 1: Write tests (as built, current contract).** Over the dashboard paths x roles (super admin, maintainer, project admin, auditor, regular user): `test_every_role_reaches_every_dashboard_route` (no role gets 403); `test_scope_by_role` (admin, maintainer and auditor unrestricted; project admin limited to `{p1}`; regular user has no admin project; every role has `own_shared`); `test_regular_user_cannot_widen_the_scope_with_selectors` and `test_project_selector_never_widens_the_scope`; `test_project_unattributed_by_role` (`deny_all` for project admin and regular user only); `test_private_visibility_by_role` (`sees_private` true for super admin, maintainer and auditor on the list, `personal_only`, detail and aggregate scopes; false for project admin and regular user); `test_sessions_by_role` (default list `exclude` for every role; `personal_only=true` gives `own_only`); `test_no_write_methods` (POST/PUT/DELETE/PATCH give 405). Also assert `/classification-policy` is open to every role. First iteration of this step asserted a regular-user 403 and `sees_private` for the auditor only; both are superseded.
- [ ] **Step 2:** Run it; expect PASS after Task 2.

**Test-first: yes — the auditor 200 and `sees_private` cases fail before Tasks 1 and 2.**

### Task 4: Guide role matrix and OpenAPI baseline

**Files:**
- Modify: `.ai-run/guides/integration/cli-analytics-events.md` (~`:514`)
- Modify if changed: `tests/codemie/rest_api/router_guards/baselines/openapi.json`

- [ ] **Step 1:** Replace the "strictly owner-only even for a super-admin or auditor" and "auditor has no extra right" text with the new rule. Add a role matrix (Super Admin, Project Admin, Auditor, Regular User) by data class: shared aggregates, shared sessions, own Personal, others' Personal, project-unattributed, write access. Record the Assumptions section and the PO-approved AC3 relaxation. State that UI visibility alignment is a separate frontend change.
- [ ] **Step 2:** `poetry run pytest tests/codemie/rest_api/router_guards -q`. If the OpenAPI baseline fails, refresh it with the repo's documented command and check the diff is only the 403 text.

**Test-first: no — documentation and snapshot refresh, verified by the router_guards test.**

## Negative-constraint pass

- First iteration: super admin not widened to others' Personal (`sees_private(user)` = auditor only). Superseded: `sees_private` is now true for admin, maintainer and auditor.
- Project admin and regular user: no Personal of others, no unattributed (project admin): Task 3. A regular user no longer gets 403 on the routes; they read their own data (current contract).
- Auditor read-only: Task 3 `test_no_write_methods`.
- Shared aggregates still exclude Personal: assumption, `exclude` mode unchanged in Task 1.
- Do not touch UI: no task does.
