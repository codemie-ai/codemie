# EPMCDME-16276: Users management project membership must use the Projects management logic

## Intent

When a user is removed from a project they must stop spending against that project. Today, removing a user through Users management (`DELETE /v1/admin/users/{user_id}/projects/{project}`, `UserAccessService.revoke_project_access`, `src/codemie/service/user/user_access_service.py` ~L198) deletes the `user_projects` row and nothing else. Their `project_member_budget_assignments` (PMBA) row and their LiteLLM per-member customer `codemie:project:<p>:category:cli:user:<uid>` stay active. Runtime budget resolution reads only PMBAs and never `user_projects`, so CLI spend keeps going to the project. This was verified locally with `codemie-claude`.

Removing a user through Projects management (`ProjectAssignmentService.remove_user_from_project`, `src/codemie/service/project/project_assignment_service.py` L749) works correctly. Users-management add (`grant_project_access`) also skips the budget allocation that `assign_user_to_project` (L358) creates. Users-management role update (`update_user_project_access`) records no `USER_PROJECT_ROLE_UPDATED` activity event, which `update_user_project_role` (L442) does.

Git history shows how this happened. Budgets (EPMCDME-11836), the earlier fix for the same symptom (EPMCDME-12022), activity events (EPMCDME-13275) and terminal-spend capture (EPMCDME-13619) were all added only to `ProjectAssignmentService`. Users management was never brought along.

The fix stops the duplication. Users management delegates its project membership operations to `ProjectAssignmentService`, the same way the Projects-management routers in `src/codemie/rest_api/routers/projects/membership.py` call it. With a single implementation, the two paths cannot drift apart again.

## Design (decided)

1. **Delegation.** `UserAccessService.grant_project_access`, `update_user_project_access` and `revoke_project_access` become thin wrappers. Each one:
   1. opens `get_session()`;
   2. loads the project with `application_repository.get_by_name` and returns **404 "Project not found"** when it does not exist;
   3. calls `project_assignment_service.assign_user_to_project` / `update_user_project_role` / `remove_user_from_project` with `session`, `project`, `user_id`, `project_name`, `is_project_admin` (where relevant), `actor` and an `action` string `"<METHOD> /v1/admin/users/{user_id}/projects/{project_name}"`, used for logging;
   4. calls `session.commit()`;
   5. calls `UserAccessService._invalidate_auth_cache(user_id)`;
   6. logs the existing `project_access_*` lines and returns the existing `{"message": ...}` response.

   `ProjectAssignmentService` is imported lazily inside the methods, the way the routers do it, to avoid an import cycle.
2. **Auto-create removed.** Granting access to a project that does not exist now returns 404. `application_repository.get_or_create` is no longer called from this path.
3. **Validation, guards and errors come from `ProjectAssignmentService`.** That covers:
   - the personal-project guard (403 for admins and the owner, 404 for others);
   - the creator guard on remove (400);
   - the UUID check (400; skipped in the LOCAL env);
   - user not found (404);
   - already assigned (409);
   - not assigned (404).

   Status codes match what Users management returned before. The message texts and the `details`/`help` fields become the Projects-management ones.
4. **Side effects come from `ProjectAssignmentService`:**
   - on add: PMBA creation through `_sync_project_budget_member_added`, plus a `USER_PROJECT_ASSIGNED` event;
   - on remove: PMBA soft-delete, terminal-spend capture, LiteLLM `delete_member_allocation` and `_resolution_cache` eviction through `_sync_project_budget_member_removed`, plus a `USER_PROJECT_REMOVED` event;
   - on role update: a `USER_PROJECT_ROLE_UPDATED` event.
5. **Dead code.** UAS helpers left without callers are deleted, for example `_reject_if_personal_project` and `_log_project_authorization_failure`. A helper stays only if it still has a caller. Error constants still used elsewhere stay.
6. `ProjectAssignmentService` itself, the routers and the Projects-management behaviour are not changed.

The builder may adjust names and helper placement inside `UserAccessService`.

## Acceptance criteria

- AC1: Users-management DELETE soft-deletes every active PMBA for (project, user), calls `delete_member_allocation` on the active provider, evicts the `_resolution_cache` entries and records `USER_PROJECT_REMOVED` with `actor_id` = the admin. It does all of this by delegating to `remove_user_from_project`.
- AC2: Users-management DELETE of the project creator returns 400 and changes nothing.
- AC3: Users-management POST creates PMBAs for every active project budget assignment and records `USER_PROJECT_ASSIGNED`, by delegating to `assign_user_to_project`.
- AC4: Users-management POST on a project that does not exist returns 404 and creates neither a project nor a membership.
- AC5: Users-management PUT records `USER_PROJECT_ROLE_UPDATED`, by delegating to `update_user_project_role`.
- AC6: For all three operations:
  - the error status codes stay the same: 404 user not found, 403/404 personal project, 409 already assigned, 404 not a member;
  - the auth cache is invalidated after a successful commit;
  - the success response body is unchanged.
- AC7: Membership and budget mutations commit in one transaction. If the delegated call raises, nothing is committed and the auth cache is not invalidated.
- AC8: `ProjectAssignmentService` and the Projects-management routers are unchanged, and their tests still pass.
- AC9: The UAS unit tests are updated to cover AC1–AC7: delegation arguments, 404 on a missing project, commit and invalidate ordering, and no commit when the call raises.

## Non-goals

- No defensive membership check in the LLM proxy or in budget resolution. This is a follow-up.
- No cleanup of PMBAs or LiteLLM customers orphaned by past Users-management removals. This is a follow-up.
- No cross-worker invalidation of the per-process 60 s `_resolution_cache`.
- No auth-cache invalidation added to the Projects-management path.
- No change to request or response models, or to the set-default and clear-default endpoints.
- No frontend changes.

## Risks left standing

- Orphaned PMBAs in existing data keep charging the project until someone cleans them up.
- Other workers may resolve PROJECT scope for up to 60 s after a removal.
- Clients of Users management see these changes:
  - 404 instead of auto-create when the project is missing;
  - 400 when removing a creator;
  - 400 for a non-UUID user ID outside the LOCAL env;
  - different error message texts.
