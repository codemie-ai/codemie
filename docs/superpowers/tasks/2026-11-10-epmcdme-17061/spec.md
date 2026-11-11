# EPMCDME-17061: first member add on a provisioning-budgeted empty project fails with a 500

## Problem

A budget group set on a project that has no active members has no `<budget_id>:shared` child row. Group creation skips it in that case (`project_budget_service.py:2171`, `:2627`). Provisioning projects start with no members (`project_service.py:225`). When the first member is added, `_sync_project_budget_member_added` (`project_assignment_service.py:133-189`) adds an allocation whose `shared_budget_id`/`effective_budget_id` point at that missing row (`:178-179`), which violates `fk_pmba_shared_budget_id` and `fk_pmba_effective_budget_id`. `SQLActivityEventRepository.insert` (`activity_repository.py:142-159`) opens `begin_nested`, which flushes the caller's pending allocation outside the savepoint. The catch-all then swallows the error and the next commit raises PendingRollbackError, which ends in the generic 500. Every provisioning resend repeats commit 1 (admins) before commit 2, so a resend never converges (technical-analysis §2).

## Behaviour

1. **The missing shared row is created at member add.** For each active budget assignment that gets a new allocation, `_sync_project_budget_member_added` looks up `build_shared_project_budget_id(budget_id)`. When the row is missing, it creates it in the caller's sync session and transaction, and writes it before the allocation that references it.
   - Contents: one pure function in `budget_models.py` (beside `member_effective_max_budget`, `:44-52`) returns the whole shared row: the fields of `project_budget_service.py:409-426` with `budget_origin_type="shared_default"`, name, `created_by` and amounts. The new creator and `_upsert_child_budget`'s shared rows build from it; override rows are unchanged.
   - `created_by`: the actor, else the main budget's `created_by` (IdP bootstrap passes `actor_id=None`, `authentication_service.py:280`; precedent `project_budget_service.py:1241`).
   - Amounts: soft from the equal member share, else the main budget's; max through `member_effective_max_budget`. A first member gets the main budget's amounts. The five existing amount expressions call it with unchanged results (`project_budget_service.py:1021-1024`, `:2177-2180`, `:2633-2634`, `:1690-1693`; `budget_service.py:1649-1654`).
   - It writes the DB row only. Provider sync stays deferred (allocation `PENDING`), as today.
   - An existing shared row is left untouched.
   - This covers every member-add caller: single assign, bulk assign, CSV import, provisioning `assign_admins`, IdP bootstrap and reactivate. On projects already broken it repairs the shared row and members added from now on, not existing members (see Non-goals).
2. **Activity-event insert stops hiding the caller's failures.** In `insert` and `async_insert`, a failure while flushing the caller's pending writes propagates as the original exception, so the session can be rolled back and is never committed in a PendingRollbackError state. A failure of the event row itself is still logged and swallowed (best-effort contract, `test_activity_repository.py:109`). `insert_strict` is unchanged.
3. **Admin assign routes return a specific error.** The single (`membership.py:41`), bulk (`:116`) and CSV import (`:179`) routes map a `SQLAlchemyError` raised by the service call or by `session.commit()` to an `ExtendedHTTPException` with status 500 and a fixed message: "The member could not be assigned; nothing was saved." The message carries no DB text, and the real cause is logged (`error-handling.md` Sanitized Failures). The mapping lives in the routes, not in `ProjectAssignmentService`, because provisioning `assign_admins` shares that service.
4. **Provisioning is unchanged on the wire.** A commit-1 failure still answers the generic 500 and logs its cause (`provisioning-api.md:72,80`).

## Acceptance criteria

- AC1: When a member is added to a project whose budget assignment has no shared row, the row is created (fields and amounts as in Behaviour 1) and then the allocation, in one transaction, with no FK violation. This holds for every member-add caller, including first-login bootstrap with `actor_id=None`, tested against a session that enforces NOT NULL.
- AC2: Provisioning PUT `{"amounts":{"platform":100}}` on an empty project, then admin single or bulk assign: the call succeeds, and the user is a member with a `PENDING` allocation pointing at `<budget_id>:shared`.
- AC3: When a caller's pending write fails during `insert`/`async_insert`, the original DB exception propagates and no PendingRollbackError follows. Admin assign routes answer 500 with the fixed message. Provisioning answers the generic 500 with the cause logged.
- AC4: When the event row itself fails, the failure is logged and swallowed, and the caller's transaction commits.
- AC5: The same PUT followed by a resend with `project.admin_emails=[<email>]` succeeds and makes the user a project admin. A second identical resend succeeds and leaves the stored rows unchanged.
- AC6: On projects whose shared row already exists (admin-created), member add stores the same rows as before, and existing member-add and budget tests pass unchanged. In `test_activity_repository.py` only the `insert`/`async_insert` flush stubs and call counts may change (`:65-74`, `:109-117`, `:133-144`); AC4 keeps the event-row swallow pinned.

## Non-goals

- Creating the shared row when a budget group or category is set on an empty project (`project_budget_service.py:2171`, `:2627`).
- A data migration or backfill. The fix covers the shared row and members added from now on. Members who joined a broken project before it keep no allocation; the workaround is to remove and re-add them (`project_assignment_service.py:309-316`, `:505-512`).
- Narrowing `_commit_project`'s retry of any `IntegrityError`, or its `provisioning_sync_conflict_retry` label (`provisioning_budget_service.py:372-376`).
- Changing provisioning status codes or response bodies, or the provisioning guide.
- Changing when the provider is synced, or `insert_strict`, or the `ACTIVITY_EVENTS_ENABLED` default.
- Handling two concurrent first adds that race to create the same shared row. The loser fails on the `budgets` key: a first-login member then keeps no budget share for good (`authentication_service.py:284-292`); admins get the fixed 500 and can retry; provisioning converges via the commit-1 retry, so narrowing that retry must keep this violation retryable.

## Decisions

- Create the missing shared row only at member add, over also creating it when a group is set on an empty project. — user
- Admin assign routes answer 500 with a fixed, specific message while provisioning keeps the generic 500, over leaving both generic or making both specific. — user
- Create the row in the member-add sync transaction, over calling the async `_ensure_shared_child_budget` in a separate transaction.
- Give the shared row one owner, a pure function in `budget_models.py` that every creator uses, over sharing only the field set with `_upsert_child_budget` (AR-2 plan 1).
- Take the row's max through `member_effective_max_budget`, over copying the new allocation's max.
- Fall back to the main budget's creator for `created_by`, over requiring an actor.
- Leave an existing shared row untouched on member add, over upserting it.
- Fix both `insert` and `async_insert`, over fixing only the sync `insert`.
- Keep the best-effort contract for failures of the event row itself, over making `insert` strict.
- Include the CSV import route in the admin error mapping, over limiting it to the single and bulk routes.
- Map errors in the admin routes, over mapping them in `ProjectAssignmentService` (which provisioning shares).
- Document remove-and-re-add for members who joined a broken project before the fix, over backfilling them here. — user, answers AR-4
- Keep the concurrent first-add race out of scope with its outcome recorded, over an `ON CONFLICT DO NOTHING` insert.

## Approaches considered

- **Chosen:** repair at member add, plus an `insert` that does not hide the caller's failures. One place creates the row for every caller and every broken project.
- **Always create the row when a group is set, plus a backfill:** this leaves projects missing the row for other reasons unfixed, adds a migration, and does not meet AC1 as worded.
- **Bridge to the async creator from the sync path:** this splits one change across two transactions and crosses the sync/async boundary.

## Verification notes

- There is no live-Postgres harness, and the mock sessions cannot raise FK violations (technical-analysis §4).
- Test the `insert` change against a sqlite-backed session (pattern: `tests/codemie/clients/test_postgres_transactional_session.py`).
- In member-add tests, `session.get` must return `None` for the shared id, because the current mock returns a budget for any id (`test_project_assignment_service.py:823-960`).
- Reproduce the ticket's steps on a local stack with `ACTIVITY_EVENTS_ENABLED` both on and off.
