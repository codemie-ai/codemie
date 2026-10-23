# EPMCDME-16133 Default A-to-Z Users Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET /v1/admin/users` defaults to case-insensitive A to Z by name (no trimming), and the codemie-ui Members tab stops sending sort params.

**Architecture:** Backend: `UserRepository.query_users` default ORDER BY becomes the existing name key + `email` + `id`; the `btrim` is dropped from the key. Docs/OpenAPI descriptions follow. Frontend: revert codemie-ui commit `71288dcc0a` (MR !2148) and add one guard test.

**Tech Stack:** Python / SQLModel / pytest (codemie); React / vitest (codemie-ui).

**Spec:** `docs/superpowers/tasks/2026-10-20-epmcdme-16133-default-name-sort/spec.md` (technical analysis alongside).

**Repos:** Tasks 1-2 run in `/Users/Anton_Atrashkou/codemie-dev/codemie/.claude/worktrees/EPMCDME-16133`. Tasks 3-4 run in `/Users/Anton_Atrashkou/codemie-dev/codemie-ui/.claude/worktrees/EPMCDME-16133` (branch `EPMCDME-16133_members-default-sort`).

Commit per task using the repository's existing convention, prefixed with `EPMCDME-16133`.

## Global Constraints

- Name key: `lower(coalesce(nullif(name, ''), username))`, with no `btrim` and no `_NAME_WHITESPACE`.
- Default order: name key ASC, `email` ASC, `id` ASC. `sort_order` is still ignored when `sort_by` is absent.
- The `sort_by: Literal["name"] | None` / `sort_order: Literal["asc","desc"]` contract, the 422 validation and the keyword-only call shape do not change.
- The OpenAPI baseline diff is limited to the `sort_by`/`sort_order` descriptions.

## Review Focus

- Whitespace-only `name` now sorts by its own whitespace and does not fall back to username. This is accepted. T1 pins the key with no `btrim`.
- NULL or empty `name` still falls back to username. The `nullif`/`coalesce` in T1's `NAME_EXPR` covers it.
- `sort_order=desc` without `sort_by` must not flip the default. T1 keeps `test_default_order_ignores_sort_order`.
- `count_users` stays unordered. T1 keeps `test_count_users_unordered`.
- The Members tab's budget-filter path (`fetchAllMatchingUsers`, several calls) must also send no sort options. T4 covers both paths.

---

### Task 1: Default name order and no-trim key in `query_users`

**Files:**
- Modify: `src/codemie/repository/user_repository.py:49-54` (remove `_NAME_WHITESPACE`; map entry becomes `func.lower(func.coalesce(func.nullif(UserDB.name, ""), UserDB.username))`; rewrite the comment so it no longer mentions whitespace)
- Modify: `src/codemie/repository/user_repository.py:188-211` (the default branch orders by `_SORT_COLUMN_MAP["name"].asc(), UserDB.email.asc(), UserDB.id.asc()`; the docstring states the A to Z default and that `sort_order` is ignored without `sort_by`)
- Test: `tests/codemie/repository/test_user_repository_sort.py`

**Interfaces:** Signature of `query_users` unchanged.

- [ ] **Step 1: Update tests (failing first).** Module docstring: add EPMCDME-16133. Set `NAME_EXPR = "lower(coalesce(nullif(users.name, ''), users.username))"` and drop its whitespace comment. Remove the btrim comment in `_order_by_clause` (L47). Let `DEFAULT = f"{NAME_EXPR} asc, users.email asc, users.id asc"`. `test_default_order` and `test_default_order_ignores_sort_order` assert `== DEFAULT`. `test_positional_call_shape_still_supported` asserts `f"order by {DEFAULT}" in sql`. Replace `TestNameSortKeyWhitespace` with:

```python
class TestNameSortKeyNoTrim:
    def test_name_key_has_no_trim(self):
        sql = _compile_sql(user_repository._SORT_COLUMN_MAP["name"])
        assert sql == NAME_EXPR
        assert "trim" not in sql
```

- [ ] **Step 2:** Run `poetry run pytest tests/codemie/repository/test_user_repository_sort.py -v`. Expected: the name, default and no-trim tests FAIL.
- [ ] **Step 3:** Apply the two `user_repository.py` changes above.
- [ ] **Step 4:** Re-run the same command. Expected: all PASS.

Test-first: yes — `test_default_order`, `test_default_order_ignores_sort_order`, `test_positional_call_shape_still_supported` and `test_name_key_has_no_trim` fail against the current `date desc` default and `btrim` key.

### Task 2: Service/router descriptions and OpenAPI baseline

**Files:**
- Modify: `src/codemie/service/user/user_management_service.py:345-346, 535-536` (docstrings)
- Modify: `src/codemie/rest_api/routers/user_management_router.py:135-138` (`Query` descriptions)
- Modify: `tests/codemie/rest_api/router_guards/baselines/openapi.json` (regenerated)

Test-first: no — this task only changes text. Replace "default is newest first" with "default is A to Z by name (falls back to username), ties broken by email then id". Keep `sort_order` described as "ignored without sort_by". Then run `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines` and check that `git diff` of `openapi.json` touches only the `sort_by`/`sort_order` descriptions (around L37902-37927). Revert any unrelated hunks before committing.

### Task 3: Revert codemie-ui MR !2148 (codemie-ui worktree)

Test-first: no — this is a revert. First run `git show --stat 71288dcc0a`. Expect `ProjectMembersManager.tsx`, `src/store/user.ts`, `ProjectMembersOrdering.test.tsx` and `src/store/__tests__/user.test.ts`. If other files appear, stop and report. Then run `git revert --no-edit 71288dcc0a`, resolve any conflicts in favor of the removal, and amend the message to the repo convention prefixed `EPMCDME-16133` (for example `EPMCDME-16133: Revert "EPMCDME-16070: Sort project members A to Z by default"`). Afterwards, `grep -rn "MEMBER_SORT\|sortBy\|sort_by" src/store/user.ts src/pages/settings/administration/projectsManagement/ProjectMembersManager.tsx` returns nothing. `npx vitest run src/store/__tests__/user.test.ts` passes.

### Task 4: Guard test — Members tab sends no sort options (codemie-ui worktree)

**Files:**
- Create: `src/pages/settings/administration/projectsManagement/__tests__/ProjectMembersNoSortParams.test.tsx`

Test-first: yes — `getUsers` calls from the Members tab carry no `sortBy`/`sortOrder`. The test passes on the reverted code and must fail if `MEMBER_SORT` is spread back in.

- [ ] **Step 1:** Write the test. Reuse the setup of the deleted `ProjectMembersOrdering.test.tsx` (recover it with `git show 71288dcc0a:<path>`): the toaster mock, `mockProject`, `buildUser`, `useVueRouter` mocked with `mockRouterState`, and `userStore.getUsers = vi.fn(...)` returning one page. Add one `it.each` over router queries `{}` and `{ overrides: 'platform' }`. Each case renders `<ProjectMembersManager project={mockProject} />`, waits for `getUsers` to be called, then for each call asserts `expect(args).not.toHaveProperty('sortBy')` and `expect(args).not.toHaveProperty('sortOrder')`. Module comment: EPMCDME-16133.
- [ ] **Step 2:** Run `npx vitest run src/pages/settings/administration/projectsManagement/__tests__/ProjectMembersNoSortParams.test.tsx`. Expected: PASS.
- [ ] **Step 3 (mutation check, not committed):** Temporarily add `sortBy: 'name'` to the `fetchUsers` `getUsers` call in `ProjectMembersManager.tsx`, re-run, and confirm FAIL. Then undo the change.
