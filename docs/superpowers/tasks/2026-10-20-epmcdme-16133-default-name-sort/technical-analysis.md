# Technical Research

**Task**: admin users sort
**Generated**: 2026-10-20
**Research path**: filesystem

---

## 1. Original Context

Jira EPMCDME-16133 (Story): "Make A to Z by name the default order of the admin users list and simplify the sort key". Relates to EPMCDME-16070 (closed; added sort_by/sort_order with a btrim'd name key in MR !4564).
Scope:
- Backend (this repo): make `lower(coalesce(nullif(name, ''), username)) ASC, email ASC, id ASC` the default order of `GET /v1/admin/users`, without trimming (remove the btrim / _NAME_WHITESPACE handling); explicit `sort_by`/`sort_order` keep working as they do today.
- Frontend (separate repo /Users/Anton_Atrashkou/codemie-dev/codemie-ui/.claude/worktrees/EPMCDME-16133, branch EPMCDME-16133_members-default-sort): the project Members tab stops sending `sort_by`/`sort_order`; remove `MEMBER_SORT` and the `getUsers` sort options.
Acceptance criteria:
1. Default order is A to Z, case-insensitive, NULL or empty name falls back to username, ties break by email then id.
2. The Members tab sends no sort parameters and stays A to Z.
3. The Users Management page and SDK `list_users` are A to Z.
4. Tests and the OpenAPI baseline are updated.
Known touch points: src/codemie/repository/user_repository.py, user_management_service.py, user_management_router.py (docstrings say "newest first"), tests/codemie/repository/test_user_repository_sort.py, tests OpenAPI baseline openapi.json. Note: OpenAPI snapshot test and test_authentication_service::test_load_user_for_auth_found were failing on clean main earlier (environmental). Research the frontend repo worktree too (MEMBER_SORT, getUsers sort options, Users Management page).

---

## 2. Codebase Findings

### Existing Implementations

Backend (`/Users/Anton_Atrashkou/codemie-dev/codemie/.claude/worktrees/EPMCDME-16133`):
- `src/codemie/repository/user_repository.py`
  - L49-54: `_NAME_WHITESPACE = " \t\n\r "` and `_SORT_COLUMN_MAP = {"name": func.lower(func.coalesce(func.nullif(func.btrim(UserDB.name, _NAME_WHITESPACE), ""), UserDB.username))}`, typed `dict[str, ColumnElement[str]]`.
  - L188-211 `query_users(session, search, filters, page, per_page, *, sort_by: Literal["name"] | None = None, sort_order: Literal["asc","desc"] = "asc")`. With `sort_by`: `(primary asc|desc, UserDB.email.asc(), UserDB.id.asc())`. Without: `(UserDB.date.desc(), UserDB.id.asc())`; docstring says "Without it, `sort_order` is ignored and users are ordered newest first."
  - `count_users` (L180-186) has no ORDER BY.
  - `fetch_projects_map` (L213+) orders by `UserDB.date.desc(), UserProject.project_name`. It builds a dict keyed by user id, so its order does not affect the page order.
- `src/codemie/service/user/user_management_service.py`
  - `list_users` (L~325-365) and `list_users_with_flow` (L~515-555) both take keyword-only `sort_by`/`sort_order`, pass them through to `query_users`, and have the docstring line `sort_by: Optional sort field ("name"); default order is newest first` (L345, L535).
- `src/codemie/rest_api/routers/user_management_router.py` L135-138: `sort_by: Literal["name"] | None = Query(None, description="Sort field. 'name' sorts by display name (falls back to username); default is newest first")`, `sort_order = Query("asc", description="Sort direction for sort_by; ignored without it")`, both forwarded to `list_users_with_flow` (L168-169).

Frontend (`/Users/Anton_Atrashkou/codemie-dev/codemie-ui/.claude/worktrees/EPMCDME-16133`, on branch `EPMCDME-16133_members-default-sort`):
- `src/pages/settings/administration/projectsManagement/ProjectMembersManager.tsx` L152-153: `/** Project members are always listed A to Z by name; the backend owns the ordering. */ const MEMBER_SORT = { sortBy: 'name', sortOrder: 'asc' } as const`. It is spread into three `userStore.getUsers` calls: L377 and L383 (`fetchAllMatchingUsers`, first page and parallel pages) and L397 (`fetchUsers`).
- `src/store/user.ts`: in the `UserStoreType.getUsers` params type (L75-89), `sortBy?: 'name'` and `sortOrder?: 'asc' | 'desc'`. In the implementation (L419-436), `sortBy`/`sortOrder` are destructured and `sort_by` is appended, plus `sort_order` only when `sortBy` is set.
- `src/pages/settings/administration/UsersManagementPage.tsx` L129, L191: `getUsers({ page, perPage, filters })`. It sends no sort params, so it gets the backend default (newest first today).
- No other caller passes `sortBy` to `getUsers` (grep across `src`).

SDK (`/Users/Anton_Atrashkou/codemie-dev/codemie-sdk/sdk/codemie-python/src/codemie_sdk/services/admin.py` L118-124): `list_users` sends only `page`, `per_page`, `search`, with no sort params, so it gets the backend default.

### Architecture and Layers Affected
- Repository: `UserRepository.query_users`, `_SORT_COLUMN_MAP`, `_NAME_WHITESPACE`.
- Service: docstrings only (`list_users`, `list_users_with_flow`).
- API router: `Query` descriptions, which feed the OpenAPI schema.
- Frontend store (`userStore.getUsers`) and page component (`ProjectMembersManager`).

### Integration Points
- Router → `UserManagementService.list_users_with_flow` → `list_users` → `user_repository.query_users` / `count_users`.
- UI `userStore.getUsers` → `GET /v1/admin/users`. Callers: Members tab, Users Management page. The SDK `AdminService.list_users` calls the same endpoint.

### Patterns and Conventions
- The class-level `_SORT_COLUMN_MAP` mirrors `application_repository.py` L338 (`_SORT_COLUMN_MAP`, used at L414-415).
- Sort params are keyword-only (`*`) in the repo and service layers, and a test pins that the positional call shape still works.

---

## 3. Documentation Findings

### Guides and Architecture Docs
No `.ai-run/guides/`. The repo has `docs/` (architecture, api, data, quality-gates.md), but none of it is specific to the user-list sort. CHANGELOG has no 16070 entry.

### Architectural Decisions
- The inline comment at `user_repository.py` L49/L51 records the 16070 decision to strip whitespace, including NBSP, before the empty check. This task reverses it.
- The UI comment at `ProjectMembersManager.tsx` L152 already says "the backend owns the ordering".
- `test_openapi_snapshot.py`: "A failing run never rewrites the snapshot". Regenerate with `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines` and review the diff.

### Derived Conventions
Tests carry an EPMCDME ticket id in their module docstring. ORDER BY is asserted by compiling SQL with the postgres dialect and `literal_binds`.

---

## 4. Testing Landscape

### Existing Coverage
Backend (pytest):
- `tests/codemie/repository/test_user_repository_sort.py`
  - `NAME_EXPR` is the btrim expression.
  - `test_name_asc_order` and `test_name_desc_order`.
  - `test_default_order` and `test_default_order_ignores_sort_order`, which assert `users.date desc, users.id asc`.
  - `test_positional_call_shape_still_supported`, which asserts `order by users.date desc, users.id asc`.
  - `TestNameSortKeyWhitespace.test_name_key_strips_all_whitespace_kinds`, which is btrim-specific.
  - `test_count_users_unordered`.
  - The `_order_by_clause` helper splits on `\s+` "without collapsing whitespace" because of the btrim char set.
- `tests/codemie/rest_api/routers/test_user_management_router_sort.py`: params reach the service, `sort_order` defaults to `asc`, and invalid values return 422.
- `tests/codemie/rest_api/routers/test_user_management_router_pagination.py` and `test_user_management_router_auditor.py` call `list_users(..., sort_by=None, sort_order="asc")`.
- `tests/codemie/service/user/test_user_management_service_projects.py` L217-251: forwarding tests and defaults `{"sort_by": None, "sort_order": "asc"}`.
- `tests/codemie/rest_api/router_guards/test_openapi_snapshot.py` against `baselines/openapi.json`. The "newest first" description appears at L37902 and L37916, and the `sort_order` description at L37921 and L37927.

Frontend (vitest; `npm run test:unit`):
- `src/pages/settings/administration/projectsManagement/__tests__/ProjectMembersOrdering.test.tsx`: L87-100 expect `getUsers` to be called with `sortBy: 'name', sortOrder: 'asc'`, and L102-114 check the same on every call when a budget filter is active.
- `src/store/__tests__/user.test.ts` L528-562, `describe('getUsers — sort params')`: sends `sort_by`/`sort_order`, sends none when omitted, and ignores `sortOrder` without `sortBy`.

### Testing Framework and Patterns
- Backend uses pytest with `MagicMock(spec=Session)` and inspects `session.exec.call_args`. There is no DB integration test for ordering.
- Frontend uses vitest. `userStore.getUsers` is replaced with `vi.fn`, and `mockGet` is checked for the query string.

### Coverage Gaps
- No DB-level test verifies real collation or NULL/empty fallback behaviour. Every check is on the SQL string.
- The SDK has no `list_users` tests (grep of `codemie-sdk/sdk/codemie-python/tests` found none).
- No test covers the Users Management page's default order.

---

## 5. Configuration and Environment

### Environment Variables
`config.ENABLE_USER_MANAGEMENT` gates the endpoint (router returns 400 if disabled). No sort-related env vars.

### Configuration Files
None relevant beyond the OpenAPI baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json`.

### Feature Flags and Deployment Concerns
None found. No migrations or indexes involve `users.name`, `username` or `date` ordering.

---

## 6. Risk Indicators

- The OpenAPI snapshot test was already failing on clean main for environmental reasons, per the task. If the baseline is regenerated on a dirty or env-divergent tree, unrelated diffs may get in. Review the diff so that it is limited to the descriptions around L37902-37927.
- Speculative: `test_user_repository_sort.py` needs `NAME_EXPR`, the default-order tests and the positional-call test rewritten, and the whitespace test class removed or inverted. The `_order_by_clause` comment about btrim whitespace becomes stale.
- Speculative: the semantics of explicit `sort_by=name` change, because whitespace-only names will no longer fall back to username. The ticket accepts this, but the router's `Query` description and the comment on L51 ("whitespace-only") must be updated, or they will be misleading.
- Speculative: once the default equals `sort_by=name asc`, `sort_order` is still "ignored without sort_by". Docstrings and the API description need to state the new default consistently in the repo (L201-202), the service (L345-346, L535-536) and the router (L136, L138).
- Behaviour change for consumers: the Users Management page and the SDK switch from newest-first to A to Z with no client change. Anyone who relied on "new users at top" will see a different order.
- No real-DB test exists for the ordering, so collation and case behaviour are asserted only through compiled SQL.
- Separately, `test_authentication_service::test_load_user_for_auth_found` is failing for environmental reasons and is unrelated to this change.

---

## 7. Summary for Complexity Assessment

The change spans two repos and four layers, and every edit is small. In the backend, the repository default `order_by` in `user_repository.py` changes from `date desc, id asc` to the name key with email/id tie-breakers, and the `btrim`/`_NAME_WHITESPACE` handling is dropped from `_SORT_COLUMN_MAP`. The service and router get docstring and `Query` description edits, and the OpenAPI baseline gets regenerated. In the frontend, the `MEMBER_SORT` constant and its three spreads are removed from `ProjectMembersManager.tsx`, and the `sortBy`/`sortOrder` options are removed from the `getUsers` type and implementation in `src/store/user.ts`. The Users Management page and the SDK already send no sort params, so they become A to Z with no code change.

There is nothing technically new here. The sort map, keyword-only params and SQL-string tests from EPMCDME-16070 are reused, with less code than before. Backend coverage is good at the SQL-string level, but there is no real-DB ordering test. Tests will need edits in `test_user_repository_sort.py`, `ProjectMembersOrdering.test.tsx` and the `getUsers — sort params` block of `user.test.ts`. Router and service forwarding tests should keep passing as they are, because the explicit-param contract does not change.

The main risks are procedural. The OpenAPI snapshot is already failing for environmental reasons, so the regenerated baseline must be reviewed carefully. The changed default is visible to the Users Management page and SDK consumers, and whitespace-only names will no longer fall back to username. Overall size is small.

---

## 8. External References

- Frontend worktree `/Users/Anton_Atrashkou/codemie-dev/codemie-ui/.claude/worktrees/EPMCDME-16133`: resolved, on branch `EPMCDME-16133_members-default-sort` (HEAD `6858cb217`). Facts are in Sections 2 and 4: `MEMBER_SORT` at `ProjectMembersManager.tsx` L153, spread at L377/L383/L397. In `src/store/user.ts`, `sortBy?: 'name'` / `sortOrder?: 'asc' | 'desc'` at L87-88, and the implementation at L420 and L433-436:
  `if (sortBy) { queryParams.append('sort_by', sortBy); if (sortOrder) queryParams.append('sort_order', sortOrder) }`.
- SDK `/Users/Anton_Atrashkou/codemie-dev/codemie-sdk/sdk/codemie-python/src/codemie_sdk/services/admin.py`: resolved. `list_users` builds `params = {"page": page, "per_page": per_page}` and adds `search`, with no sort params.
