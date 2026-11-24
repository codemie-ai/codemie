# Technical Research

**Task**: marketplace unpublish withdraw assistant decided_by
**Generated**: 2026-11-23
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-17100
Unpublishing assistant from Marketplace fails with ForeignKeyViolation on marketplace_decided_by when MARKETPLACE_MANAGEMENT_ENABLED is False

Description
Users must be able to withdraw/unpublish their assistant from the Marketplace at any time. This bug blocks that action entirely when the Marketplace Management feature flag is disabled, preventing assistant owners from removing their assistant from Marketplace visibility.

Preconditions
MARKETPLACE_MANAGEMENT_ENABLED is set to False
An assistant exists and has been published to Marketplace

Steps to Reproduce
Set MARKETPLACE_MANAGEMENT_ENABLED to False
Create any assistant with any configuration
Publish it to Marketplace
Navigate to the assistant and choose to remove it from Marketplace

Expected Result
The assistant is successfully unpublished/withdrawn from Marketplace. marketplace_status is set to WITHDRAWN, and marketplace_decided_by is populated with a valid existing user id (or left null) without any database error.

Actual Result
The request fails with HTTP 400 - "Failed to unpublish from marketplace assistant". Backend logs show psycopg2.errors.ForeignKeyViolation on constraint assistants_marketplace_decided_by_fkey: the value assigned to marketplace_decided_by (b86ab81f-d249-4003-90e9-53215777f103) does not exist in the users table, so the UPDATE codemie.assistants statement fails.

ForeignKeyViolation: insert or update on table "assistants" violates foreign key constraint "assistants_marketplace_decided_by_fkey"
DETAIL: Key (marketplace_decided_by)=(b86ab81f-d249-4003-90e9-53215777f103) is not present in table "users".

Affected Areas
Backend Marketplace unpublish/withdraw endpoint
Resolution of the acting user id (marketplace_decided_by) when MARKETPLACE_MANAGEMENT_ENABLED is False
assistants table FK constraint assistants_marketplace_decided_by_fkey

Acceptance Criteria
- When MARKETPLACE_MANAGEMENT_ENABLED is False, unpublishing/withdrawing an assistant from Marketplace succeeds without triggering a ForeignKeyViolation.
- marketplace_decided_by is populated only with a valid, existing user id, or left null when no valid acting-user id can be resolved.
- Unpublish behavior when MARKETPLACE_MANAGEMENT_ENABLED is True remains unchanged.
- A regression test covers the unpublish flow with MARKETPLACE_MANAGEMENT_ENABLED=False.

Error as shown in UI (pasted by user):
Status: 400, Error: Failed to unpublish from marketplace assistant, Details: An error occurred while trying to unpublish from marketplace the assistant: (psycopg2.errors.ForeignKeyViolation) insert or update on table "assistants" violates foreign key constraint "assistants_marketplace_decided_by_fkey"
DETAIL:  Key (marketplace_decided_by)=(b86ab81f-d249-4003-90e9-53215777f103) is not present in table "users".
[SQL: UPDATE codemie.assistants SET marketplace_status = %(to_status)s, ... marketplace_decided_by = %(decided_by_id)s, ... WHERE id = %(entity_id)s AND marketplace_version = %(expected_version)s AND (...) RETURNING ...]
[parameters: {'to_status': 'WITHDRAWN', 'to_is_global': False, 'to_is_verified': False, 'decided_by_id': 'b86ab81f-d249-4003-90e9-53215777f103', 'reason': None, 'stamp_submitted': False, 'submitted_at': None, 'entity_id': 'be21bc1d-99b9-43b2-b6db-5d365d3d0cfc', 'expected_version': 0, 'allowed_from': ['PENDING_REVIEW', 'APPROVED'], 'allowed_from_if_global': ['DRAFT']}]
Screenshot context: assistant "SimpleChat Assistant" by "Admin Example", project envoy-test, environment codemie.dz (a deployed env, likely with an auth mode where the acting user id may not exist in the users table, e.g. a dev/static admin user).

(SQL body abbreviated in this copy; the full statement is `_CAS_SQL`, `queue_service.py:318`.)

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A: an owner removes their assistant from the Marketplace.**
- `unpublish_assistant_from_marketplace` — `src/codemie/rest_api/routers/assistant/marketplace.py:665-733`. `POST /v1/assistants/{id}/marketplace/unpublish`. Write-access check, then `MarketplaceQueueService().withdraw(..., decided_by_id=user.id, decided_by_email=user.email or user.username)` (`:697-702`), then background `_remove_marketplace_assistant`. Any non-conflict exception → `_raise_assistant_operation_error` (`_shared.py:157`) → HTTP 400 "Failed to unpublish from marketplace assistant" (matches the reported error). **No flag check on this route**: it always goes through the WITHDRAW CAS.
- `MarketplaceQueueService.withdraw` → `decide()` — `src/codemie/service/marketplace_management/queue_service.py:536-561`, `654-897`. `decide` passes `decided_by_id` unchanged into three FK'd writes in one transaction:
  1. `_CAS_SQL` `marketplace_decided_by = :decided_by_id` (`:318-344`, param `:738`) — the reported failure.
  2. `MarketplaceReviewDecision(decided_by_id=...)` (`:785-804`) — `mm_review_decision.decided_by_id` FK `users.id` (`models.py:517-519`).
  3. `_record_activity_event(actor_id=decided_by_id)` (`:814-834`) — `activity_events.actor_id` FK `users.id` (`activity_models.py:151-158`).
  Also uses `decided_by_id or actor` for the idempotency key (`:702`) and the metric actor (`:875`).
- Admin bulk unpublish — `src/codemie/rest_api/routers/marketplace_management.py:1385-1397` calls `_queue_service.withdraw(decided_by_id=getattr(user,"id",None))`. Router mounted only when `MARKETPLACE_MANAGEMENT_ENABLED` (`config.py:1003-1006`).

**Purpose B: record who decided, only as an id that exists in `users` (or null).**
- `MarketplaceQueueService._snapshot_actor` — `queue_service.py:1241-1260`. For activity rows only: looks up `UserDB` by id; drops `actor_id` only when no email can be resolved. It does **not** drop `actor_id` when the users row is missing but an email is known.
- `UserRepository.get_existing_user_ids` — `src/codemie/repository/user_repository.py:453`; bulk id-existence check, used by `project_assignment_service.py:497,640`.
- `user_by_id_loader.UserLookup.has_row` — `src/codemie/service/user/user_by_id_loader.py:35-104`; distinguishes "users row exists" from IDP-snapshot users. Consumers: `security/idp/local.py:71-131`, `triggers/bindings/utils.py:58-62`.
- No helper found that maps an authenticated `User.id` to "FK-safe users.id or None" for write paths (searched `existing_user_id`, `user_exists`, `resolve_*user_id`, `has_row`).

**Other touched code**
- `Assistant.marketplace_decided_by` — `src/codemie/rest_api/models/assistant.py:740-743`, `String(36) FK users.id ON DELETE SET NULL, nullable`; migration `src/external/alembic/versions/f3e2d1c0b9a8_marketplace_management_squashed.py:97-105`.

### Decisions Already in the Code
- Unpublish is a moderation decision, not a field assignment, regardless of flags (docstring `marketplace.py:674-686`). Contrast: gate-off **publish** is a plain field assignment that writes no `decided_by` and no ledger row (`_publish_with_gate_disabled`, `marketplace.py:490-519`, "CR-013").
- Decision identity comes only from `Depends(authenticate)`, never from client input (`test_marketplace_management_contract.py:1180-1195`).
- `decided_by_id` is already optional throughout `decide()` ("a decision taken by a background job has no user", `queue_service.py:1278-1283`); all three FK columns are nullable.
- Identity mode: `ENABLE_USER_MANAGEMENT=False` (default, `config.py:317`) uses `LegacyJwtUserProvider` — "Reconstructs User from JWT claims ... Zero database queries" (`user_providers/legacy_jwt.py:27-69`); "IDP-claims mode ... keeps no users rows" (`idp_user_snapshot_service.py:17`, `user_management.py:99`). `stand_in_user` (`security/user.py:242-251`) likewise builds a `User` with no row. In that mode `user.id` is a JWT subject with no `users` row.
- `ACTIVITY_EVENTS_ENABLED` defaults False (`config.py:459`); when True, `activity_event_repository.insert` calls `session.flush()` **before** its savepoint (`activity_repository.py:241`), so a bad `mm_review_decision.decided_by_id` raises there; when False it raises at `session.commit()` → `_resolve_integrity_error` (`queue_service.py:1099-1146`) re-raises any constraint other than the two uniqueness ones.

### Task Statements Checked Against the Code
- "fails ... when MARKETPLACE_MANAGEMENT_ENABLED is False" — **contradicted** as the cause: the unpublish route and `withdraw()` contain no reference to that flag (`marketplace.py:665-733`, `queue_service.py:536-897`). The flag only gates the admin router mount and schedulers (`main.py:893,964,1053`; `management.py:125`). The FK fails whenever `user.id` has no `users` row — the code ties that to `ENABLE_USER_MANAGEMENT=False` / stand-in users, not to the marketplace flag. Whether codemie.dz runs with `ENABLE_USER_MANAGEMENT=False` is **unverified** (deployment config not in repo).
- "Unpublish behavior when MARKETPLACE_MANAGEMENT_ENABLED is True remains unchanged" — **holds** as a description of the current code path (same path for both values); note the same path also fails with the flag True for a user without a users row.
- "marketplace_decided_by ... valid existing user id, or left null" — **holds** schema-wise: column nullable, `ON DELETE SET NULL` (`assistant.py:740-743`).
- "a dev/static admin user" — **unverified**; `ADMIN_USER_ID` / IDP-role admin in IDP-claims mode is documented in `docs/marketplace-management-feature-flags.md:95-99`.

### Consumers of Changed Contracts
Searched `decided_by_id=`, `MarketplaceQueueService().`, `marketplace_decided_by`, `_queue_service.` across `src/`. Every caller passing raw `user.id` into `decide()`:
- `marketplace.py:697-702` withdraw (owner unpublish) — reported path.
- `_publish_support.py:124-141` submit / re_submit (publish, only when gate armed, `marketplace.py:404-405`).
- `_publish_support.py:190-197` force_publish (admin skip-queue, only when gate armed).
- `management.py:385-392` re_submit after edit — runs for `PENDING_REVIEW` **even with gate off** (`management.py:548-550`).
- `assistant_version_service.py:357-363` re_submit after rollback — same PENDING_REVIEW rule (`:339-342`).
- `marketplace_management.py:1395,1485,1518,1553,1580` admin approve/reject/withdraw/force — router mounted only with flag True.
Readers of the stored value: `MarketplaceConflictError.to_detail()["marketplace_decided_by"]` (`queue_service.py:286`), admin entity detail `marketplace_management.py:1859,2241`, `test_marketplace_management_router.py:650`. `mm_review_decision.decided_by_id` read in `queue_service.py:1047` and index `ix_mm_review_decision_decided_by_created` (`models.py:474-478`). All already tolerate NULL. Idempotency key derivation (`queue_service.py:702`) falls back to `actor` when `decided_by_id` is None.

### Architecture and Layers Affected
- API router: `rest_api/routers/assistant/marketplace.py` (unpublish), `_publish_support.py`, `management.py`.
- Service: `service/marketplace_management/queue_service.py` (`decide`, `_snapshot_actor`).
- Data: `assistants`, `mm_review_decision`, `activity_events` FKs to `users`.
- Security/identity: `rest_api/security/user_providers/*`, `security/user.py`.

### Integration Points
- PostgreSQL via `get_session()` raw SQL CAS; Elasticsearch de-index in background (`_remove_marketplace_assistant`, `_shared.py:228`) and `_side_effects`.
- `activity_event_repository.insert` (savepoint, swallows its own failures).

### Patterns and Conventions
- One write path: every marketplace transition goes through `MarketplaceQueueService.decide`.
- Routers import queue service lazily inside the handler (tests patch `codemie.service.marketplace_management.queue_service.MarketplaceQueueService`).
- Post-commit side effects wrapped in log-and-continue (CR-022/023).

---

## 3. Documentation Findings

### Guides and Architecture Docs
No `.ai-run/guides/` file mentions marketplace. `docs/marketplace-management-feature-flags.md` covers the flags and the `ENABLE_USER_MANAGEMENT` admin-resolution split (`:95-99`); it says nothing about unpublish or `decided_by`.

### Architectural Decisions
Inline: CR-013 (gate-off publish writes no ledger row), CR-004 (pre-CAS hook), F-35 (activity events in same transaction), EPMCDME-16625 (IDP-claims mode keeps no users rows; stand-in users).

### Derived Conventions
Optional actor ids are passed as `None` for system/background decisions; `_snapshot_actor` is the existing place that sanitises the actor for activity rows.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_assistant_marketplace.py:669-722` — unpublish happy path and 401; `MarketplaceQueueService` fully mocked, so no FK behaviour. `:892` parametrises `MARKETPLACE_MANAGEMENT_ENABLED` for publish.
- `tests/codemie/service/marketplace_management/test_queue_ordering.py:93-140` and `test_marketplace_management_contract.py:1189+` — real PostgreSQL (`MM_TEST_PG_URL`, skip when unreachable), create `users` + FK tables, seed a users row before deciding.
- `test_record_activity_event.py:66-98` — `_snapshot_actor` rules (users row, missing email, system actor).
- `test_withdraw_public_draft.py` — WITHDRAW transition table / CAS text only.
- `test_bulk_unpublish.py` — admin bulk withdraw, queue mocked.

### Testing Framework and Patterns
pytest + pytest-asyncio; httpx `ASGITransport` against `app`; `unittest.mock.patch`; `monkeypatch.setattr(config, ...)` for flags; real-DB fixtures self-skip.

### Coverage Gaps
- No test of `decide()`/`withdraw()` with a `decided_by_id` that has no users row.
- No router test of unpublish with `MARKETPLACE_MANAGEMENT_ENABLED=False`.
- No test of `ENABLE_USER_MANAGEMENT=False` identity on any marketplace write.

---

## 5. Configuration and Environment

### Environment Variables
`MARKETPLACE_MANAGEMENT_ENABLED` (False, `config.py:1006`), `MARKETPLACE_APPROVAL_GATE_ENABLED` (False, `:1020`), `ENABLE_USER_MANAGEMENT` (False, `:317`), `ACTIVITY_EVENTS_ENABLED` (False, `:459`), `MM_TEST_PG_URL` (tests).

### Configuration Files
`src/codemie/configs/config.py`, `src/codemie/configs/marketplace_management.py:303-356` (`is_approval_gate_armed`).

### Feature Flags and Deployment Concerns
codemie.dz flag values not in the repo. No migration surface found for the reported path (column already nullable).

---

## 6. Risk Indicators

- The ticket's stated trigger (`MARKETPLACE_MANAGEMENT_ENABLED=False`) is not referenced on the failing path; the code ties missing users rows to `ENABLE_USER_MANAGEMENT=False` / stand-in users (see Task Statements). A flag-keyed fix would leave the same failure when the marketplace flag is True.
- Speculative: nulling only the CAS parameter would move the failure to the `mm_review_decision.decided_by_id` FK at flush/commit (`queue_service.py:785-804`, `activity_repository.py:241`), since the same id feeds three FK columns.
- `_snapshot_actor` keeps `actor_id` when the users row is missing but an email exists (`queue_service.py:1251-1259`); the activity insert then fails inside its savepoint and the event is silently dropped (logged at error).
- Same raw `user.id` reaches `decide()` from five other call sites (Consumers section); the PENDING_REVIEW re-submit after edit/rollback runs even with the gate off.
- Existing router tests mock the queue service; FK behaviour is only observable with the real-PostgreSQL fixtures, which skip when no DB is reachable.
- Error detail returned to the client embeds the raw SQL and parameters (`_shared.py:164-168`).

---

## 7. Summary for Complexity Assessment

The failing path is small and fully located: `POST /v1/assistants/{id}/marketplace/unpublish` (`marketplace.py:665-733`) calls `MarketplaceQueueService.withdraw` → `decide()` (`queue_service.py:654-897`), which writes the caller's `user.id` into `assistants.marketplace_decided_by`, `mm_review_decision.decided_by_id` and `activity_events.actor_id`, all FK'd to `users.id` and all nullable. The route has no flag branch; the id has no users row whenever authentication is IDP-claims mode (`ENABLE_USER_MANAGEMENT=False`, the default) or a stand-in user, so the `MARKETPLACE_MANAGEMENT_ENABLED` link in the ticket is a correlation, not a code path. Layers touched: API router and the marketplace queue service; no schema change is indicated by the code.

No novel pattern: `decide()` already accepts `decided_by_id=None`, and the code has an id-existence helper (`UserRepository.get_existing_user_ids`) and an actor-sanitising hook (`_snapshot_actor`). Six call sites pass raw `user.id` into `decide()`, so the shape of the change (endpoint-local vs. inside `decide`) determines the surface.

Test posture: router tests for unpublish exist but mock the queue service; real-DB queue tests exist with a users-table fixture and self-skip without PostgreSQL. There is no test of a decision by a user without a users row. Key risks: fixing only the CAS column (second FK on the ledger insert), the flag mismatch between ticket and code, and silent activity-event loss.

---

## 8. External References

None named by the task.

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- Model: `src/codemie/service/marketplace_management/models.py:521` (`MarketplaceReviewDecision`): `decided_by_email: Optional[str]`, `Column(Text, nullable=True)`. There is no FK and no constraint. Its sibling `decided_by_id` (`models.py:517-520`) is `String(36)`, has a FK to `users.id` and uses `ondelete=SET NULL`.
- Migration: `src/external/alembic/versions/f3e2d1c0b9a8_marketplace_management_squashed.py:342`: `sa.Column("decided_by_email", sa.Text(), nullable=True)`.
- The router `src/codemie/rest_api/routers/assistant/marketplace.py:698-702` calls `MarketplaceQueueService().withdraw(actor=user.name or user.username or user.id, decided_by_id=user.id, decided_by_email=user.email or user.username)`.
- In `decide()` (`queue_service.py:659-890`), `decided_by_email` is used in three places:
- `decided_by_email` is not part of the CAS UPDATE. That UPDATE writes only `decided_by_id` to `assistants.marketplace_decided_by` (`queue_service.py:324`).
- It is not part of the idempotency key either. `_derive_key` uses `decided_by_id or actor` (`queue_service.py:702`).
- `activity_event_repository.insert` runs inside a SAVEPOINT and swallows its own failures (comment at `queue_service.py:811-813`).
- `rest_api/routers/assistant/_publish_support.py:128,138,195`, `marketplace.py:701` and `management.py:390` pass `user.email or user.username`.
- `rest_api/routers/marketplace_management.py:1396,1486,1519,1554,1581` and `service/assistant/assistant_version_service.py:362` pass `getattr(user, "email", None)`, with no username fallback.
- `_snapshot_actor` requires an email whenever `actor_id` is set (`ActivityEventCreate` rejects an id without an email, `queue_service.py:1246-1248`).
- "keeping the actor's email": **holds**. Nothing on the `decide()` path derives `decided_by_email` from `decided_by_id`. The value comes from the caller and is written as received (`queue_service.py:799`).
- "write it or NULL to ... activity_events.actor_id": **keeps an existing mechanism in place**. `_snapshot_actor` already does the users lookup for `activity_events`, but it keeps an unresolved id when an email is present. Its consumer is `_record_activity_event` (`queue_service.py:1301`).
- `latest_force_publish` (`queue_service.py:500-534`):
- That reaches the review-detail response field `overridden_by` (`rest_api/routers/marketplace_management.py:1888`, set at `:2259` via `:1983`).
- `latest_submissions` (`queue_service.py:1021-1066`):
- It is used for the pending list at `marketplace_management.py:1232,1245`, which falls back further to `_submitted_by(a)`.
