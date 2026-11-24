# EPMCDME-17100: Marketplace decisions by a user with no `users` row must not violate FKs

## Problem

An owner who removes an assistant from the Marketplace gets HTTP 400, "Failed to unpublish from marketplace
assistant". The cause is a ForeignKeyViolation on `assistants_marketplace_decided_by_fkey`.

The ticket ties this to `MARKETPLACE_MANAGEMENT_ENABLED=False`. The code does not: the unpublish route
(`rest_api/routers/assistant/marketplace.py:665-733`) and `MarketplaceQueueService.withdraw`/`decide()`
(`service/marketplace_management/queue_service.py:536-897`) never read that flag. The failure comes from the
acting `user.id` having no row in `users`. That is the case in IDP-claims mode (`ENABLE_USER_MANAGEMENT=False`,
the default, `legacy_jwt.py:27-69`) and for stand-in users (`security/user.py:242-251`), under either flag value
(technical-analysis.md §2, "Task Statements Checked").

`decide()` writes that raw id into three nullable FK columns to `users.id`:
- `assistants.marketplace_decided_by`: the CAS parameter, `queue_service.py:738`.
- `mm_review_decision.decided_by_id`: `queue_service.py:798`.
- `activity_events.actor_id`: `queue_service.py:820`.

Six call sites pass raw `user.id` into `decide()` (analysis §2, "Consumers"). Two of them are the re-submit
after an edit (`management.py:385-392`) and after a rollback (`assistant_version_service.py:357-363`), and both
run even when the approval gate is off.

## Approach

Add one named rule to `MarketplaceQueueService`, the **recorded-user-id rule**. It is a primary-key lookup of
the `users` row (`session.get(UserDB, id)`, as at `queue_service.py:1252`). It returns the id when the row
exists and None otherwise. `SYSTEM_ACTOR_ID` gets no special case: an id is kept exactly when its row exists.

- `decide()` calls the rule once per call, before the CAS. All three FK writes use the result.
- `_snapshot_actor` applies the same rule: it drops `actor_id` whenever the `users` row is missing, including
  when an email is known. Today it keeps that id (`queue_service.py:1251-1259`). After the change, the class
  holds one rule, not two that disagree.

Who acted stays on record:
- `mm_review_decision.decided_by_email` (`models.py:521`, no FK) is written as the caller passed it
  (`queue_service.py:799`).
- When the id resolves to NULL and the caller passed no email, `decide()` uses the `actor` string as the email
  for both the ledger row and the activity row. Six call sites pass `getattr(user, "email", None)` with no
  fallback (`marketplace_management.py:1396,1486,1519,1554,1581`, `assistant_version_service.py:362`). Their
  `actor` is the email, the username or the id. `ActivityEventCreate.actor_email` is a plain string
  (`activity_models.py:214`).
- `_snapshot_actor` passes the email through when `actor_id` is None (`queue_service.py:1251-1260`).

The idempotency key (`queue_service.py:702`) and the metric actor (`:875`) keep the raw authenticated id. They
are not FKs.

No router change, no flag branch, no migration: all three columns are already nullable (`assistant.py:740-743`,
`models.py:517-519`, `activity_models.py:151-158`).

## Acceptance criteria

1. With `MARKETPLACE_MANAGEMENT_ENABLED=False`, unpublish by a user with no `users` row succeeds, sets
   `marketplace_status=WITHDRAWN` and raises no FK violation.
2. `assistants.marketplace_decided_by`, `mm_review_decision.decided_by_id` and `activity_events.actor_id` each
   hold an existing `users.id` or NULL. They are NULL exactly when the acting id has no `users` row.
3. When the id is NULL, the ledger row still records the actor's email in `decided_by_email`, or the `actor`
   string when the caller passed no email.
4. A user who has a `users` row has their id recorded in all three columns exactly as today, under both
   `MARKETPLACE_MANAGEMENT_ENABLED` values.
5. All six `decide()` call sites get the same rule with no change of their own: withdraw, submit/re_submit,
   force_publish, re_submit after edit or rollback, and the admin approve/reject/withdraw/force actions.
6. Regression tests:
   - A real-PostgreSQL queue test runs `withdraw` with an actor that has no `users` row and asserts
     WITHDRAWN, a NULL `marketplace_decided_by`, and a ledger row with a NULL id and the email kept. It follows
     `test_queue_ordering.py:93-140`.
   - The same test also covers an actor whose `users` row exists, and asserts that id is kept.
   - A third case covers an actor with no `users` row and no email, and asserts the ledger keeps the `actor`
     string.
   - The PostgreSQL test skips itself without a database, so it is run locally before the MR
     (`.ai-run/guides/testing/local-verification.md`).
   - A unit test of `decide()` that needs no database, with the users lookup patched:
     - When the lookup returns None, it asserts that the CAS parameter `decided_by_id`, the
       `MarketplaceReviewDecision.decided_by_id` added to the session and the `actor_id` handed to
       `_record_activity_event` are all None.
     - When the lookup returns a row, it asserts the id is kept in all three.
   - A `_snapshot_actor` unit test asserts that a missing `users` row drops `actor_id` even when an email is
     known. It extends `test_record_activity_event.py:66-98`.
   - A router test of `POST /v1/assistants/{id}/marketplace/unpublish` with `MARKETPLACE_MANAGEMENT_ENABLED`
     patched to False (`test_assistant_marketplace.py:669-722`).

## Non-goals

- No schema or migration change.
- No change to identity providers or to when `users` rows are created (`ENABLE_USER_MANAGEMENT` behaviour).
- No change to the 400 error body, which embeds raw SQL (`_shared.py:164-168`).
- No change to gate-off publish, which writes no `decided_by` (CR-013, `marketplace.py:490-519`).
- No change to the idempotency key or the decision metrics.
- The other marketplace writers of a users-FK actor column stay on the raw id. A follow-up has each of them
  call the same rule:
  - `mm_llm_assessment.requested_by_id` (`llm_assessment_service.py:731`);
  - the prompt-view activity event (`marketplace_management.py:2169-2183`);
  - `mm_audit_run.triggered_by_id` (`audit_service.py:1211,1619`, not yet wired).

## Decisions

- Fix inside `decide()` over only the unpublish endpoint, so all six call sites are covered — user
- Make the rule flag-independent over keying it on `MARKETPLACE_MANAGEMENT_ENABLED`; AC3 means "users with a
  `users` row are unchanged under both values" — user
- When the id has no `users` row, keep the actor's email as the record over dropping the actor entirely.
- Keep the idempotency key and the metric actor on the raw authenticated id over the resolved id, so replay
  keys do not change.
- Cover the FK behaviour with a real-PostgreSQL queue test over only the mocked router test: a mocked queue
  cannot show an FK error.
- Use one named recorded-user-id rule, shared by `decide()` and `_snapshot_actor`, over an inline check in
  `decide()` that sits beside `_snapshot_actor`'s opposite rule.
- Implement the rule as a primary-key `session.get(UserDB, id)` over `UserRepository.get_existing_user_ids`, so
  `_snapshot_actor`'s lookup of the same id comes from the session's identity map.
- Keep the FK on the actor columns and write NULL over dropping the FK or creating `users` rows in IDP-claims
  mode. That alternative needs a migration on five tables and the agreement of the identity and activity-events
  owners.
- Leave the assessment requester, prompt-view event and audit-run trigger to a follow-up over fixing them in
  this bug fix.
- Fall back to the `actor` string for the email only when the id resolves to NULL, over always falling back.
  Users with a `users` row are then recorded exactly as today.
- Add a database-free unit test of `decide()` over relying on the PostgreSQL test alone, which skips itself
  without a database.

## Risks

- The real-PostgreSQL tests skip themselves when `MM_TEST_PG_URL` is unreachable.
- Each decision now makes one extra primary-key lookup on `users`.
- An actor with no `users` row leaves no id in `assistants.marketplace_decided_by` or the ledger id column.
  - Those rows cannot be backfilled by id later.
  - The admin detail and the conflict body show no decider for them.
- Until the follow-up lands, the three sibling writers keep losing rows silently for such actors.
