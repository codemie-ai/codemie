# EPMCDME-17100 Marketplace decisions by an actor with no `users` row: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `MarketplaceQueueService.decide()` writes into its three users-FK columns only an id that has a `users` row, or NULL. Who acted stays on record through `decided_by_email`.

**Architecture:** There is one named rule, `_recorded_user_id`, on `MarketplaceQueueService`. `decide()` calls it once before the CAS. `_snapshot_actor` uses the same rule. No router, flag or schema change.

**Tech Stack:** Python, SQLModel/SQLAlchemy, pytest. Real-PostgreSQL fixture `mm_pg_engine` (`tests/codemie/service/marketplace_management/conftest.py:148-187`, `MM_TEST_PG_URL`).

**Spec:** `docs/superpowers/tasks/2026-11-23-epmcdme-17100/spec.md`

**Commits:** One commit per task on branch `EPMCDME-17100`, following the repository convention (`EPMCDME-17100: <summary>`, `.ai-run/guides/standards/git-workflow.md`). Never stage `.ai-run/sdlc-factory/doctor.json` or `docs/superpowers/reviews/*`.

## Global Constraints

- Implement the rule as a primary-key `session.get(UserDB, id)`. Do not use `UserRepository.get_existing_user_ids`.
- `SYSTEM_ACTOR_ID` gets no special case: an id is kept exactly when its `users` row exists.
- The idempotency key (`queue_service.py:702`) and the metric actor (`:875`) keep the raw `decided_by_id`.
- Fall back to the `actor` string for the email only when the resolved id is None and no email was passed.
- Do not touch: routers, migrations, identity providers, the 400 error body, gate-off publish, `llm_assessment_service.py`, the prompt-view event or `audit_service.py`. The last three are a spec follow-up.

## Review Focus

- An actor that has a `users` row but whose caller passed no email: the id is kept and `decided_by_email` stays None, as today. Task 2 test.
- `decided_by_id=None` (admin `getattr(user, "id", None)`): no lookup runs, the id stays None and the actor string fallback applies. Tasks 1 and 2 tests.
- A replay returns before the lookup and makes no extra query. Task 2 places the call after `_find_replay`.

---

### Task 1: Recorded-user-id rule, used by `_snapshot_actor`

**Files:**
- Modify: `src/codemie/service/marketplace_management/queue_service.py:1240-1260`
- Test: `tests/codemie/service/marketplace_management/test_record_activity_event.py`

**Interfaces:**
- Produces: `MarketplaceQueueService._recorded_user_id(session, user_id: str | None) -> str | None` (staticmethod). It returns `user_id` when `user_id` is truthy and `session.get(UserDB, user_id)` is not None. Otherwise it returns None.

Test-first: yes — when the `users` row is missing, `_snapshot_actor` drops `actor_id` even when an email is known. Today it keeps `"u-1"`.

- [ ] **Step 1: Update and add tests**
  - Rewrite `test_unresolvable_email_falls_back_to_an_email_shaped_actor_string` (`:84-87`) to assert `(event.actor_id, event.actor_email) == (None, "alice@example.com")`.
  - Add `test_missing_users_row_drops_actor_id_even_with_passed_email`: `_session(user=None)` with `actor_email="p@x.io"` gives `(None, "p@x.io")`.
  - Split `test_system_or_missing_actor_is_not_looked_up` (`:97-103`):
    - `None` is still not looked up.
    - `SYSTEM_ACTOR_ID` with no row is looked up once and ends with `actor_id is None`.
  - Add `_recorded_user_id` tests:
    - A row gives the id.
    - No row gives None.
    - `None` gives None, with `session.get` not called.
- [ ] **Step 2: Run to verify they fail.** Command: `poetry run pytest tests/codemie/service/marketplace_management/test_record_activity_event.py -v`. Expected: the new and rewritten tests FAIL.
- [ ] **Step 3: Implement**
  - Add `_recorded_user_id`.
  - In `_snapshot_actor`, set `actor_id = _recorded_user_id(session, actor_id)` first. Remove the `!= SYSTEM_ACTOR_ID` skip. Read the email and display name from `session.get(UserDB, actor_id)` only when the id was kept; that read comes from the identity map.
  - The email fallback to an email-shaped `actor` stays.
  - Rewrite the docstring to state the rule. Keep the `SYSTEM_ACTOR_ID` import only if it is still used.
- [ ] **Step 4: Re-run Step 2's command.** Expected: PASS.

### Task 2: `decide()` resolves the id once and keeps who acted

**Files:**
- Modify: `src/codemie/service/marketplace_management/queue_service.py:724-821`
- Create: `tests/codemie/service/marketplace_management/test_decide_recorded_user_id.py`
- Create: `tests/codemie/service/marketplace_management/test_withdraw_without_users_row_pg.py`
- Modify: `tests/codemie/rest_api/routers/test_assistant_marketplace.py` (after `:700`)

**Interfaces:**
- Consumes: `_recorded_user_id` from Task 1.

Test-first: yes — a database-free `decide()` test with `_recorded_user_id` patched to return None fails, because the CAS `decided_by_id`, the ledger `decided_by_id` and the activity `actor_id` still carry the raw id.

- [ ] **Step 1: Write the unit test** (`test_decide_recorded_user_id.py`)
  - Patch `queue_service.get_session` to yield a MagicMock session. Its `exec(...).mappings().first()` returns the current row, then the CAS row with `to_version`, `name` and `project`.
  - Patch `_find_replay` to return None, and patch `_record_activity_event` and `_side_effects`.
  - Call `withdraw(assistant_id=..., actor=..., decided_by_id="u-raw", decided_by_email=...)` and run these cases:
    - **Lookup returns None, email `"e@x.io"`:** the CAS params `decided_by_id` are None, the `MarketplaceReviewDecision` passed to `session.add` has `decided_by_id` None, and `_record_activity_event` gets `actor_id` None. The ledger keeps `decided_by_email == "e@x.io"`.
    - **Lookup returns None, email None, `actor="bob"`:** the ledger `decided_by_email` and the activity `actor_email` are both `"bob"`.
    - **Lookup returns `"u-raw"`, email None:** `"u-raw"` is in all three writes and `decided_by_email` is None.
    - **All cases:** `_derive_key` receives `decided_by_id="u-raw"`.
- [ ] **Step 2: Write the PostgreSQL regression test** (`test_withdraw_without_users_row_pg.py`)
  - Use `mm_pg_engine`, plus a local `db` fixture that patches `PostgresClient.get_engine` and cleans up the same way as `test_queue_ordering.py:143-160`.
  - Seed an `APPROVED` assistant and call `withdraw`. Assert the cases from spec AC6:
    - **No `users` row, with email:** status `WITHDRAWN`, `marketplace_decided_by` NULL, and a ledger row with a NULL id that keeps the email.
    - **Seeded `UserDB` row:** the id is kept in both columns.
    - **No row, no email:** the ledger `decided_by_email` equals the `actor` string.
  - The module docstring says it skips without PostgreSQL and must be run locally before the MR (`.ai-run/guides/testing/local-verification.md`).
- [ ] **Step 3: Run to verify they fail.** Command: `poetry run pytest tests/codemie/service/marketplace_management/test_decide_recorded_user_id.py tests/codemie/service/marketplace_management/test_withdraw_without_users_row_pg.py -v`. Expected: the unit cases FAIL. The PG cases FAIL with `ForeignKeyViolation`, or SKIP when no database is available.
- [ ] **Step 4: Implement in `decide()`**
  - After the replay return and before `pre_cas_hook` (`:724`), add `recorded_id = self._recorded_user_id(session, decided_by_id)`.
  - If `recorded_id is None and not decided_by_email`, set `decided_by_email = actor`.
  - Use `recorded_id` at `:738`, `:798` and `:820`. Leave `:702` and `:875` on `decided_by_id`.
  - In the comment at `:811`, change `activity_repository.py:140` to `activity_repository.py:242`.
- [ ] **Step 5: Add the router regression test** `test_unpublish_assistant_with_marketplace_management_disabled`. It mirrors `test_unpublish_assistant_from_marketplace` (`:669-700`) with `monkeypatch.setattr(config, "MARKETPLACE_MANAGEMENT_ENABLED", False)`, and asserts 200 and one call to `withdraw`.
- [ ] **Step 6: Re-run Step 3's command plus `test_assistant_marketplace.py -k unpublish`, `test_record_activity_event.py` and `test_queue_ordering.py`.** Expected: PASS. The PG tests must pass, not skip, before the MR.

## Negative-constraint check

| Spec constraint | Where it is honoured |
|---|---|
| No router change or flag branch | Task 2 adds only a router *test* |
| No schema or migration change | Neither task touches `alembic/` |
| No identity-provider change | Neither task touches identity providers |
| No 400-body change | Neither task touches the 400 body |
| No gate-off publish change | Neither task touches gate-off publish |
| Idempotency key and metrics stay on the raw id | Task 2 Step 4, asserted in Step 1 |
| Sibling writers stay on the raw id (follow-up) | Global Constraints exclude them |
| No `get_existing_user_ids` | Global Constraints |
| No `SYSTEM_ACTOR_ID` special case | Task 1 |
| Fallback only when the id resolves to NULL | Task 2 Step 4, and Step 1 case 3 |

No task violates any of these.
