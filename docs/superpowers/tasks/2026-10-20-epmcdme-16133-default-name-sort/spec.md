# EPMCDME-16133: A to Z by name as the default order of the admin users list

## Intent

EPMCDME-16070 (MR !4564) added `sort_by=name` with a `btrim`-based key, and the Members tab opted in to it explicitly. This story makes A to Z by name the endpoint default, so every consumer gets the same order without asking for it. It also drops the trimming, which was agreed on the MR !4564 thread (option 2).

Background: newest first was never an explicit requirement. It has been the backend default since the initial import (EPMCDME-9519), and EPMCDME-10807 and EPMCDME-16070 kept it.

Decision: the BA confirmed in writing that A to Z is the intended default for all five consumers of `GET /v1/admin/users` (listed below). The visible change from newest first to A to Z is therefore an accepted risk.

## Affected consumers

None of these send sort params, and all of them inherit the new default:
1. The Users Management page (`UsersManagementPage.tsx`). Needs no code change.
2. The project Members tab (`ProjectMembersManager.tsx`). Its explicit sort is removed (see Frontend).
3. The budget-modal email autocomplete: codemie-ui `userStore.searchUsers` (`src/store/user.ts` ~L266), used by `UserEmailAutocomplete` in `UnifiedProjectBudgetModal.tsx` with `per_page=10`. Needs no code change. Its top-10 suggestions become A to Z.
4. SDK `AdminService.list_users` (`codemie_sdk/services/admin.py` L118-124). Needs no code change.
5. SDK `UserService.list_admin_users` (`codemie_sdk/services/user.py` ~L45). Needs no code change.

## Behavior

**Name key**: `lower(coalesce(nullif(name, ''), username))`, with no `btrim` and no `_NAME_WHITESPACE`. A NULL or empty `name` falls back to `username`. A whitespace-only name is not treated as empty. This is an accepted edge case.

**`GET /v1/admin/users` ordering**
- Without `sort_by`: name key ASC, `email` ASC, `id` ASC. This replaces `date DESC, id ASC` (`src/codemie/repository/user_repository.py` L188-211). `sort_order` is still ignored when `sort_by` is absent, as it is today.
- With `sort_by=name`: unchanged. The name key goes in `sort_order` direction, then `email` ASC, `id` ASC.
- The validation for `sort_by` and `sort_order` (422 on invalid values) and the keyword-only call shape do not change.

**Docs**: wherever the text says "newest first" or mentions whitespace-only fallback, it must say instead that the default is A to Z by name (falling back to username), with ties broken by email then id. That covers the repository docstring and comment (L49-54, L201-202), the service docstrings (`user_management_service.py` L345-346, L535-536) and the router `Query` descriptions (`user_management_router.py` L135-138). The OpenAPI baseline is regenerated with `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`, and its diff must be limited to those descriptions.

**Frontend** (codemie-ui worktree, branch `EPMCDME-16133_members-default-sort`)
- `ProjectMembersManager.tsx`: remove `MEMBER_SORT` (L152-153) and its three spreads (L377, L383, L397).
- `src/store/user.ts`: remove `sortBy`/`sortOrder` from the `getUsers` params type (L87-88) and from the implementation (L420, L433-436). `getUsers` never sends `sort_by` or `sort_order`.
- The Users Management page, the budget-modal email autocomplete and both SDK methods need no code change. They become A to Z through the new backend default.

## Acceptance criteria

1. AC1: The default order of `GET /v1/admin/users` is case-insensitive A to Z on the name key (NULL or empty name falls back to username), with ties broken by email ASC and then id ASC. The key contains no trimming.
2. AC2: Explicit `sort_by=name` with `sort_order=asc|desc` behaves as before, apart from the dropped trimming. Invalid values still return 422.
3. AC3: The Members tab sends no `sort_by` or `sort_order` and still lists members A to Z. `MEMBER_SORT` and the `getUsers` sort options are gone.
4. AC4: The following need no code change and inherit the new A to Z default: the Users Management page, the budget-modal email autocomplete (`userStore.searchUsers`), SDK `AdminService.list_users` and SDK `UserService.list_admin_users`.
5. AC5: Backend tests are updated. In `tests/codemie/repository/test_user_repository_sort.py`: the name expression has no `btrim`, the default-order tests and the positional-call test assert the name/email/id order, the whitespace-strip test is replaced by one asserting that `btrim` is absent, and the stale whitespace comment on `_order_by_clause` is removed. Frontend tests are updated: `ProjectMembersOrdering.test.tsx` asserts that no sort options are passed, and the `getUsers — sort params` block in `user.test.ts` is removed or inverted so that no sort params are sent.
6. AC6: The OpenAPI baseline is regenerated, and its diff is limited to the `sort_by`/`sort_order` descriptions.

## Non-goals

- No trimming or Unicode whitespace normalization of names. Whitespace-only names stay as they are.
- No new sort fields, and no change to the `sort_by`/`sort_order` contract. `sort_order` keeps having no effect without `sort_by`.
- No DB indexes, migrations or collation changes.
- No SDK code change, and no UI sort controls.
- `fetch_projects_map` and `count_users` ordering stay as they are.
- No fix for the pre-existing environmental test failures (`test_load_user_for_auth_found`, OpenAPI snapshot drift unrelated to this change).

## Left to the builder

The exact wording of the docstrings and descriptions. Whether the default path reuses `_SORT_COLUMN_MAP["name"]` or a shared helper.
