# Feedback Routes Authorization (EPMCDME-17683) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Owner-scope the read routes and ownership-check the write routes of `/v1/feedback`.

**Architecture:** Ownership of a `FeedbackEntry` is derived by joining `feedback.conversation_id` (uuid) to `conversations.id` (varchar) and filtering on `Conversation.user_id`. Write routes load the conversation without raising and require `Ability(user).can(Action.WRITE, conversation)`. Request/response models and the OpenAPI baseline do not change.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy (Postgres), pytest with mocks.

**Spec:** none (flow without spec); requirements are in the Acceptance criteria below and in `technical-analysis.md` next to this file.

**Conventions:** Commit per task using the repository's existing convention (`EPMCDME-17683: <Description>`). Never stage `.ai-run/sdlc-factory/doctor.json`. No code comments, no feature docs. Load before coding: `.ai-run/guides/api/rest-api-patterns.md`, `development/security-patterns.md`, `data/repository-patterns.md`, `standards/code-quality.md`, `testing/testing-patterns.md`, `testing/testing-api-patterns.md` (Router Contract Guards). Quality gates (`make ruff` etc., `.ai-run/guides/quality-gates.md`) belong to the calling flow, not to this plan.

## Acceptance criteria

- A non-admin `GET /v1/feedback` returns only entries whose conversation is owned by the caller; `GET /v1/feedback/{id}` of another user's entry returns 404.
- An admin `GET /v1/feedback` returns every entry, including entries whose conversation no longer exists.
- `POST` or `DELETE /v1/feedback` on a conversation the caller cannot write returns 403; history is unchanged and no `FeedbackEntry` is created or deleted.
- An admin `DELETE /v1/feedback` on another user's conversation succeeds as on main (200, mark cleared, entry removed) provided `feedbackId` belongs to that conversation; an admin `POST` on another user's conversation is still 403, and auditors get no `DELETE` bypass.
- `DELETE` with the caller's own conversation id but another user's `feedbackId` is rejected and the entry is kept.
- `POST`/`DELETE` on the caller's own conversation behave as today; request/response shapes and the OpenAPI snapshot are unchanged.
- A missing conversation returns 404 on `POST` and `DELETE`; a missing entry id returns 404 on `GET /v1/feedback/{id}`.
- Regression tests cover all of the above and pass.

## Global Constraints and Assumptions

- Admin predicate is `User.is_admin` (already true for maintainers). Auditors do NOT get the admin view.
- Forbidden uses `raise_forbidden("write")` (403, `routers/utils.py:68`), not `raise_access_denied` (401).
- `DELETE` only: `User.is_admin` skips the `Ability` write check (`_require_writable_conversation(..., admin_bypass=True)`), keeping main's superadmin cleanup behaviour. The conversation must still exist (404) and the entry/conversation link check still applies to admins. `POST` never bypasses.
- `Conversation.get_by_id` raises `KeyError`, so the current `if not conversation` branches are dead. Use `Conversation.find_by_id` (`models/conversation.py:665`, returns None) and return the existing 404 (`CONVERSATION_NOT_FOUND_*`).
- In `create()` the access check runs BEFORE `feedback.save()`. In `delete()` the entry/conversation link check runs BEFORE any mutation.
- Join is `cast(FeedbackEntry.conversationId, String) == Conversation.id`; never cast `conversations.id` to uuid. Keep `get_all` ordering (`update_date desc`) and the 10,000-row cap.
- Do not change `ConversationService.add_feedback/remove_feedback`, models `FeedbackRequest`/`FeedbackDeleteRequest`/`FeedbackEntry` columns, or add a migration. `/v1/feedback` stays in `STAY_PATHS`.
- `models/conversation.py:41` imports `MarkEnum` from `models/feedback.py`, so `models/feedback.py` must import `Conversation` lazily inside the new classmethods.
- Tests are mock-based like the neighbours (no real Postgres). Pin `config.ENV="development"` and `config.ENABLE_USER_MANAGEMENT=True` (pytest.ini `ENV=local` makes every user admin).

## Review Focus

- Entry whose conversation was deleted: hidden from owners and non-admins, visible to admin (Task 1, Task 2).
- Auditor/maintainer-without-admin flag is not given the admin view (Task 2).
- Attacker passes own conversation id plus victim's `feedbackId` on DELETE (Task 3).
- Admin bypass is limited to `DELETE` and to `is_admin`: `POST` and auditors stay 403 (Task 3).
- Rejected POST leaves no orphan `FeedbackEntry` row (Task 3).
- Missing conversation id returns 404, not an unhandled `KeyError` (Task 3).

---

### Task 1: Owner-scoped FeedbackEntry queries

**Files:**
- Modify: `src/codemie/rest_api/models/feedback.py:36-57` (add two classmethods to `FeedbackEntry`; import `Session`, `select`, `cast`)
- Test: `tests/codemie/rest_api/models/test_feedback_entry.py` (create; style of `tests/codemie/rest_api/models/test_base_model.py:44-113`: `@patch("codemie.rest_api.models.feedback.Session")` and `@patch("codemie.clients.postgres.PostgresClient.get_engine")`)

**Interfaces:**
- Produces: `FeedbackEntry.get_all_for_user(cls, user_id: str) -> List["FeedbackEntry"]` and `FeedbackEntry.find_by_id_for_user(cls, id_: str, user_id: str) -> Optional["FeedbackEntry"]`.

**Test-first: yes — `test_get_all_for_user_filters_by_conversation_owner` fails with AttributeError (method missing).**

- [ ] **Step 1: Write failing tests** in the new file; capture the statement via `mock_session.exec.call_args[0][0]` and compile it with `postgresql.dialect()`:
  - `test_get_all_for_user_filters_by_conversation_owner`: SQL contains `JOIN conversations`, `CAST(feedback.conversation_id AS VARCHAR) = conversations.id`, `conversations.user_id = `, `ORDER BY feedback.update_date DESC`, `LIMIT`; compiled params contain the user id and 10000; returns the mocked `.all()` list.
  - `test_get_all_for_user_does_not_cast_conversation_id_to_uuid`: SQL has no `CAST(conversations.id`.
  - `test_find_by_id_for_user_applies_owner_and_id_filters`: SQL has the same join plus `feedback.id = `; returns `.first()` result, and None when the mock returns None.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/rest_api/models/test_feedback_entry.py -v`; expect FAIL (AttributeError).
- [ ] **Step 3: Implement** both classmethods: `with Session(cls.get_engine())`, `select(cls).join(Conversation, cast(cls.conversationId, String) == Conversation.id).where(Conversation.user_id == user_id)`; the list variant adds `.order_by(cls.update_date.desc()).limit(10_000)` and returns `.all()`; the by-id variant adds `.where(cls.id == id_)` and returns `.first()`. `Conversation` is imported inside each method.
- [ ] **Step 4: Run** the same command; expect PASS.

---

### Task 2: Owner-scoped read routes

**Files:**
- Modify: `src/codemie/rest_api/routers/feedback.py:43-56` (`index`, `show`; add `Ability`/`Action`, `raise_forbidden` imports here for Task 3 only if needed there)
- Test: `tests/codemie/rest_api/routers/test_feedback.py` (create; port the fixtures from `/tmp/repro_17683/test_repro_17683.py`)

**Interfaces:**
- Consumes: `FeedbackEntry.get_all_for_user`, `FeedbackEntry.find_by_id_for_user` (Task 1); existing `FeedbackEntry.get_all`, `FeedbackEntry.find_by_id`.
- Produces: shared test fixtures reused by Task 3: autouse pin of `config.ENV`/`ENABLE_USER_MANAGEMENT`; a `world` fixture with real `Conversation(id=cid, conversation_id=cid, user_id=ALICE, history=[...])` objects in a dict, `Conversation.find_by_id` patched to read it, a fake `ConversationService` (`add_feedback`/`remove_feedback` mutating `history[i].user_mark`), `FeedbackEntry` persistence patched onto an in-memory dict (`get_all`, `find_by_id`, `get_all_for_user`, `find_by_id_for_user` filtering by owner via the conversation dict, `save`, `delete`), and `client_as(user_id, is_admin=False)` overriding `authenticate` on a bare `FastAPI()` with `router_mod.router`. Include an orphan entry (conversation missing from the dict).

**Test-first: yes — `test_user_list_returns_only_own_entries` fails because Mallory currently sees Alice's entry.**

- [ ] **Step 1: Write failing tests** (exact status/body assertions):
  - `test_user_list_returns_only_own_entries`: Mallory gets `[]`, Alice gets her one entry, neither sees the orphan.
  - `test_admin_list_returns_every_entry`: admin gets Alice's entry and the orphan.
  - `test_auditor_list_is_scoped_like_a_user`: `User(id=..., is_admin=False, is_auditor=True)` (verify the field name at `security/user.py:152`) gets only own entries.
  - `test_show_foreign_entry_returns_404`: Mallory on Alice's entry id gets exactly 404; `test_show_missing_entry_returns_404`; `test_show_own_entry_returns_200`; `test_show_orphan_entry_admin_200_user_404`.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/rest_api/routers/test_feedback.py -v`; expect the scoping tests FAIL.
- [ ] **Step 3: Implement** `index(user: User = Depends(authenticate))`: `user.is_admin` -> `FeedbackEntry.get_all()` else `get_all_for_user(user.id)`. `show(document_id: str, user: User = Depends(authenticate))`: entry via `find_by_id` (admin) or `find_by_id_for_user(document_id, user.id)`; None -> `ExtendedHTTPException(404)` with a new `FEEDBACK_NOT_FOUND_MESSAGE`/`FEEDBACK_NOT_FOUND_HELP` pair defined beside the `CONVERSATION_NOT_FOUND_*` constants and the same field layout. `response_model` unchanged.
- [ ] **Step 4: Run** the same command; expect PASS.

---

### Task 3: Ownership-checked write routes

**Files:**
- Modify: `src/codemie/rest_api/routers/feedback.py:59-116` (`create`, `delete`; add imports `Ability`, `Action` from `codemie.core.ability` and `raise_forbidden` from `codemie.rest_api.routers.utils`)
- Test: `tests/codemie/rest_api/routers/test_feedback.py` (extend, reusing Task 2 fixtures)

**Interfaces:**
- Consumes: Task 2 fixtures; `Conversation.find_by_id`; `raise_forbidden("write")`; `FEEDBACK_NOT_FOUND_*`.

**Test-first: yes — `test_post_on_foreign_conversation_is_403_and_unchanged` fails because the route returns 200 and mutates history.**

- [ ] **Step 1: Write failing tests**:
  - `test_post_on_foreign_conversation_is_403_and_unchanged`: exactly 403, `history[0].user_mark is None`, entry count unchanged (no `FeedbackEntry` saved).
  - `test_delete_on_foreign_conversation_is_403_and_unchanged`: exactly 403, mark kept, entry kept.
  - `test_delete_own_conversation_with_foreign_feedback_id_is_rejected`: Mallory owns a conversation, sends Alice's `feedbackId`; 404, Alice's entry and mark kept, `remove_feedback` not called.
  - `test_post_and_delete_missing_conversation_return_404`: unknown conversation id, exactly 404, no entry saved.
  - `test_owner_post_then_delete_work_as_today`: owner POST 200 sets the mark and stores one entry; DELETE with the returned id 200, clears the mark, removes the entry; response bodies keep today's shape (`id` for POST, `message` "Specified feedback entry removed" for DELETE).
  - `test_admin_can_delete_feedback_on_foreign_conversation`: admin DELETE with Alice's conversation id and entry id is 200, body `{"message": "Specified feedback entry removed"}`, entry removed, mark cleared.
  - `test_admin_delete_with_mismatched_conversation_and_feedback_is_rejected`: admin sends Mallory's conversation id with Alice's `feedbackId`; 404, entry and mark kept, `remove_feedback` not called.
  - `test_auditor_cannot_delete_feedback_on_foreign_conversation`: `is_auditor` non-admin DELETE on Alice's conversation is 403, entry kept.
  - `test_admin_cannot_write_on_foreign_conversation`: admin POST on Alice's conversation is 403.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/rest_api/routers/test_feedback.py -v`; expect the new tests FAIL.
- [ ] **Step 3: Implement** in both handlers: `conversation = Conversation.find_by_id(request.conversation_id)`; None -> existing 404; `if not Ability(user).can(Action.WRITE, conversation): raise_forbidden("write")`, factored into `_require_writable_conversation(conversation_id, user, *, admin_bypass=False)`; with `admin_bypass=True` and `user.is_admin` it returns right after the existence check. `create` calls it with the default (no bypass), `delete` with `admin_bypass=True`. In `create` do both before building/saving the entry. In `delete`, after the ability check and before `remove_feedback`: `entry = FeedbackEntry.find_by_id(request.feedback_id)`; if `entry` exists and `str(entry.conversationId) != request.conversation_id`, raise the feedback 404. After `remove_feedback`, call `entry.delete()` when `entry` is not None (no second read; a missing entry keeps today's no-op 200).
- [ ] **Step 4: Run** `poetry run pytest tests/codemie/rest_api/routers/test_feedback.py tests/codemie/rest_api/models/test_feedback_entry.py tests/codemie/rest_api/test_core_gating.py tests/codemie/rest_api/router_guards -v`; expect PASS, with the OpenAPI snapshot untouched. Do not run `regenerate_baselines.py`; a snapshot diff means a model or `responses=` leaked into the routes.

---

## Self-review

- Coverage: AC1 -> Tasks 1-2; AC2 -> Task 2; AC3 and AC4 -> Task 3; AC5 -> Task 3 (+ guard tests); AC6 -> all tasks.
- negative-constraints: honored - no new column or migration (Task 1 is query-only); `ConversationService` untouched (Task 3 changes the router only); no 401 (`raise_forbidden`); auditors not admin (Task 2 test, and Task 3 auditor DELETE test); no real-Postgres tests (mocks in Tasks 1-3); no models or OpenAPI change (guard run in Task 3); no `.ai-run/sdlc-factory/doctor.json` staging, no docs, no code comments (Conventions); no commit text beyond the repo convention.
