# Technical Research

**Task**: feedback authorization conversation ownership
**Generated**: 2026-11-23
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-17683 [G0.25] L43: feedback routes give and change the entries of all users.

Problem: Feedback is the mark a user gives to an answer in a conversation. Each feedback entry holds the request, the response and the history of that conversation. Today GET /v1/feedback and GET /v1/feedback/{document_id} return every feedback entry to every authenticated user, and the two write routes (POST /v1/feedback writes a user mark into the history of a conversation by id; DELETE /v1/feedback removes a mark) do not check who owns the conversation. Any user can read other users' conversation content and change the history of their conversations.

Required change: 1. A user gets only the feedback entries of that user. Admins get every entry. 2. The write routes check that the caller has use access to the conversation.

Acceptance criteria:
1. Given feedback entries of several users, when a user calls GET /v1/feedback, the response contains only that user's entries, and a read by id of another user's entry does not return it.
2. Given the same entries, when an admin calls GET /v1/feedback, the response contains every entry.
3. Given a conversation of a different user on which the caller has no use access, when the caller calls POST or DELETE on /v1/feedback, the request fails and the conversation is not changed.
4. Given a conversation of the caller, when the caller writes or removes a feedback mark, the call works as today.
5. A regression test covers these cases and passes in CI.

Decisions already taken with the user (treat as given): (a) Entry ownership is derived by joining FeedbackEntry.conversationId to Conversation.user_id; no new column, no migration. Entries whose conversation no longer exists are visible to admins only. (b) Write routes require Ability(user).can(Action.WRITE, conversation) (owner only), as in src/codemie/rest_api/routers/conversation/history.py. (c) DELETE must also verify the FeedbackEntry belongs to request.conversation_id (today feedback_id is deleted without any link check, so an attacker could pass their own conversation id with a victim's feedbackId). (d) Foreign entry read by id -> 404; write on a conversation the caller cannot write -> 403.

Code already inspected: src/codemie/rest_api/routers/feedback.py (index, show, create, delete do not use the user for authorization), src/codemie/rest_api/models/feedback.py (FeedbackEntry SQLModel table 'feedback', no user_id column, conversationId UUID column 'conversation_id'), ConversationService.add_feedback/remove_feedback in src/codemie/service/conversation_service.py (~line 1068-1138; user only used for metrics), Ability in src/codemie/core/ability.py (Conversation READ = SHARED_WITH/OWNED_BY, WRITE = OWNED_BY). A throwaway in-process repro exists at /tmp/repro_17683/test_repro_17683.py (4 red, 3 green control tests).

Two things you MUST answer (5 and 6): which identifier FeedbackEntry.conversationId holds and the join column/type difference; who consumes the four routes and whether owner-filtering breaks any consumer, plus how "is admin" is computed, how neighbouring routes filter by owner in SQL, and where the repository query should live.

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: a user sees only their own feedback entries (admin sees all).
- `src/codemie/rest_api/routers/feedback.py:43-56` `index()` returns `FeedbackEntry.get_all()`; `show()` returns `FeedbackEntry.get_by_id(document_id)`. Neither takes `user`. The router carries `Depends(authenticate)` at router level (`feedback.py:36-40`) so every caller is authenticated, but no ownership rule exists. Dependents: only these two routes (grep of `FeedbackEntry`/`/v1/feedback` across `src/` and `tests/`).
- `BaseModelWithSQLSupport.get_all` (`src/codemie/rest_api/models/base.py:413`) pages with `items_per_page=10_000`, ordered `update_date desc`; `get_by_id` (`base.py:392`) raises `KeyError` when absent (no handler for `KeyError` in `rest_api/main.py`; so today `show` of a missing id is an unhandled `KeyError`).
- `FeedbackEntry` (`src/codemie/rest_api/models/feedback.py:36`): table `feedback`, `id` str PK, `conversationId: UUID4` mapped to column `conversation_id` of type `UUID` (nullable=True in migration `src/external/alembic/versions/a3a7c53bf1c1_create_feedback.py:36`), `history` JSONB list of `ChatMessage`. No `user_id`, no index on `conversation_id`.
- Owner-filtered peer already exists for the other feedback route: `final_feedback` (`feedback.py:119-150`) looks up `Conversation.get_by_fields({"user_id": user.id, "conversation_id": ...})` and 404s on miss.
- Admin variant of final feedback: `src/codemie/rest_api/routers/admin.py:151-173` (`admin_access_only`).

Purpose B: only someone with access to a conversation may change its history through feedback.
- `create()` (`feedback.py:59-89`): `Conversation.get_by_id(request.conversation_id)`, then `if not conversation` 404 (dead branch: `Conversation.get_by_id` override at `models/conversation.py:640` raises `KeyError`, it never returns None), saves `FeedbackEntry`, then `ConversationService.add_feedback(request, user=user)`. No ability check.
- `delete()` (`feedback.py:92-116`): same load, then `ConversationService.remove_feedback`, then `FeedbackEntry.delete_feedback(id=request.feedback_id)` (`models/feedback.py:53`; `find_by_id` then `delete`; returns `{"status":"not found"}` silently). No link check between `feedback_id` and `conversation_id`. `KeyError` is caught only around `remove_feedback`, not around the first `get_by_id`.
- `ConversationService.add_feedback` / `remove_feedback` (`service/conversation_service.py:1068-1107`, `1110-1138`): reload conversation and `ConversationMetrics.get_by_conversation_id`, rewrite `history[request.message_index].user_mark` (remove: `operator_mark` if `author == OPERATOR`), `conversation.update()` and `conversation_metrics.update()`. `user` is used only for metrics (`ConversationMonitoringService.send_feedback_metric` / `send_feedback_delete_metric`).
- Reference ability check: `routers/conversation/history.py:65-68`, `86-89`, `109-112`: `conversation = Conversation.get_by_id(id)`; `if not Ability(user).can(Action.WRITE, conversation): raise_access_denied("write")`. `raise_access_denied` returns **401** (`routers/utils.py:58`); `raise_forbidden` returns **403** (`routers/utils.py:68`). Same file imports `CONVERSATION_NOT_FOUND_MESSAGE/HELP` from `routers/feedback.py:31-34` (also imported by `messages.py:37`, `retrieval.py:34`, `share.py:21`).
- `Ability.PERMISSIONS["Conversation"]` (`core/ability.py:148-150`): READ `[SHARED_WITH, OWNED_BY]`, WRITE `[OWNED_BY]`. `Conversation.is_owned_by` = `user_id == user.id`; `is_managed_by`/`is_shared_with` return False (`models/conversation.py:940-947`). `Ability.can` takes no admin shortcut for Conversation (`ability.py:224-244`; `admin` role is not in the Conversation lists).

Other touched code: `src/codemie/rest_api/models/standard.py` (`PostResponse`, `FinalFeedbackRequest`), `src/codemie/rest_api/main.py:1546` (router mount).

### Decisions Already in the Code

- Conversation id convention: `Conversation.id` (PK, string) is set equal to `Conversation.conversation_id` on every creation path: `conversation_service.py:323-325`, `821-823`, `1167-1169`; `conversation_service.py:998` passes `conversation.id` as a conversation id. Both columns are `AutoString` (varchar) (`alembic/versions/13758951666c_create_conversations.py:53,56`); `conversation_id` is indexed, `id` is the PK. Ids are generated as `str(uuid.uuid4())` (`conversation_service.py:1154`, `core/models.py:692`, `assistant_handlers.py:675`, `workflow_service.py:286`) but the upsert-history path accepts "custom ID from client" (`conversation_service.py:812`); no UUID validation found on that path. No alternative recorded; reason "none found".
- Feedback side is typed UUID in both layers: `FeedbackEntry.conversationId: UUID4` (pydantic validates on construction; non-UUID ids raise) and DB column `UUID`. Request side is `str` (`FeedbackRequest.conversation_id`, `FeedbackDeleteRequest.conversation_id`, `models/feedback.py:61,78`).
- Admin convention: `User.is_admin` is a stored/derived bool (`security/user.py:150`); `resolve_is_admin` (`user.py:160-178`) forces True when `ENV=local`, derives from `ADMIN_USER_ID`/`ADMIN_ROLE_NAME` when `ENABLE_USER_MANAGEMENT` is off, uses the DB value when on, and sets `is_admin=True` for any maintainer. `User.is_admin_or_maintainer` (`user.py:195`) -> `permissions.is_admin_or_maintainer` (`rest_api/security/permissions.py:20`: `is_admin or is_maintainer`, non-bool values coerced to False). The route-level gate `admin_access_only` (`security/authentication.py:189`) uses `is_admin_or_maintainer`. Auditors: `is_auditor` (`user.py:152`); `is_admin_maintainer_or_auditor` (`permissions.py:33`) is the "may read admin data" helper, used for read-only admin views (`project_spending_service.py:258,482`). The feedback routes currently use none of them. Given decision text says "Admins", the helper in use for admin elsewhere is `is_admin_or_maintainer`; auditor inclusion is not specified by the task.
- Error-shape convention: `ExtendedHTTPException(code, message, details, help)`; feedback router builds 404 with `CONVERSATION_NOT_FOUND_MESSAGE/HELP`.
- Owner filtering in SQL convention: models/repos use `select(...).where(X.user_id == user.id)`, e.g. `models/conversation.py:1046`, `repository/user_project_repository.py:40`, `service/settings/settings_index_service.py:134,203`, `skill_event_repository.py:128,161`. Sessions: `Session(cls.get_engine())` on models, `get_session(...)` (`codemie.clients.postgres`) in repositories (`conversation_pause_repository.py:35`, `base.py:526`). For repositories in `src/codemie/repository/`, `.ai-run/guides/data/repository-patterns.md` only says "extend the matching repository / services must not access storage directly when a repository exists"; no repository for `feedback` or `conversations` listing exists (`conversation_analytics_repository.py`, `conversation_pause_repository.py`, `conversation_analysis_queue_repository.py` are other tables/purposes). Model-level classmethods with `select` are the existing alternative (`Conversation.search_by_name_and_user` at `models/conversation.py:950`, `IndexInfo` at `models/index.py:1403`).
- Data needed in a form it was not stored for: ownership of a feedback row is stored on `conversations.user_id` (varchar); the feedback row holds only `conversation_id` (UUID). Needed direction: feedback -> conversation owner.

### Answer 5 - which identifier `FeedbackEntry.conversationId` holds

- `create()` calls `Conversation.get_by_id(request.conversation_id)` = `session.get(Conversation, id)` on the primary key (`base.py:392-397`, override `conversation.py:640`), and then stores the same `request.conversation_id` in `FeedbackEntry.conversationId` (`feedback.py:78`). So the value written is by construction the **`Conversation.id`** (PK) value.
- Because `Conversation.id == Conversation.conversation_id` on all creation paths found (see Decisions), the two columns hold the same string for rows created through the service; `final_feedback()` uses the `conversation_id` field only because it filters with `get_by_fields`.
- UI (codemie-ui) posts `conversationId = currentChat.id` (`src/pages/chat/.../MessageFeedbackActions.tsx:90,69` -> `src/store/chats.ts:1011-1032, 1056-1072`); `currentChat.id` is the `Conversation.id` (also compared as `chat?.id === conversationId` at `chats.ts:1075`). codemie-code CLI: no `/v1/feedback` call found (searched the repo; `feedback` hits are session fixtures, docs and hook text).
- Join column: `conversations.id` (PK, indexed by PK) is the one the write path used. Type: `feedback.conversation_id` is `uuid`, `conversations.id` is `varchar`. A SQL join needs a cast on one side; casting `conversations.id` to uuid errors in Postgres for any non-UUID id (custom client ids on the upsert path), while casting `feedback.conversation_id` to text (`cast(FeedbackEntry.conversationId, String) == Conversation.id`) cannot error but compares the canonical lowercase hyphenated form and cannot use an index on `feedback.conversation_id` (none exists anyway). Pydantic `UUID4` also rejects non-v4 UUIDs on `FeedbackEntry` construction, so such conversations cannot have feedback rows today.
- Legacy risk: rows older than the current write path were not inspected (no DB access); the Conversation id-equals-conversation_id invariant is verified only in code.

### Task Statements Checked Against the Code

- "GET /v1/feedback and GET /v1/feedback/{id} return every entry to every authenticated user": holds (`feedback.py:43-56`).
- "write routes do not check who owns the conversation": holds (`feedback.py:59-116`; `add_feedback`/`remove_feedback` use `user` for metrics only).
- "FeedbackEntry has no user_id column; conversationId UUID column 'conversation_id'": holds (`models/feedback.py:36-50`).
- "Ability Conversation READ = SHARED_WITH/OWNED_BY, WRITE = OWNED_BY": holds (`ability.py:148-150`); note `is_shared_with` is always False (`conversation.py:946`), so READ = owner in practice.
- Decision (b) "as in history.py": holds for the `Ability(user).can(Action.WRITE, conversation)` call; but `history.py` denies with `raise_access_denied` = 401, not 403 (`routers/utils.py:58`); decision (d) 403 matches `raise_forbidden` (`utils.py:68`). Contradicted only if "as in history.py" is read to include the status code.
- Decision (c) "feedback_id is deleted without any link check": holds (`feedback.py:115`; `delete_feedback` at `models/feedback.py:53`).
- Decision (a) "entries whose conversation no longer exists visible to admins only": no existing mechanism; conversation deletion leaves feedback rows (grep of `deletion.py` / `FeedbackEntry` shows no cascade; `FeedbackEntry` is referenced only in `routers/feedback.py` and `alembic/env.py`). Not verified against production data.
- "A regression test ... passes in CI": no feedback test file exists (`tests/codemie/**/*feedback*` none). The /tmp repro stubs `Conversation`, `ConversationService` and `FeedbackEntry` persistence (`/tmp/repro_17683/test_repro_17683.py`), and does not exercise SQL.
- Keeps a mechanism in place: none of the statements keeps an existing same-purpose mechanism, except that decision (b) reuses `Ability`-WRITE (owner only) rather than the READ (`SHARED_WITH`) level quoted in "use access".

### Consumers of Changed Contracts

Routes changed: GET `/v1/feedback`, GET `/v1/feedback/{document_id}`, POST, DELETE `/v1/feedback`.
- codemie-ui: POST `v1/feedback` (`src/store/chats.ts:1032`) and DELETE `v1/feedback` (`chats.ts:1072`) only, both from `MessageFeedbackActions.tsx` on the user's own open chat; the UI reads back `responseData.feedback_id || responseData.id` and stores it as `userMark.feedback_id`, later sent as `feedbackId` on DELETE. No GET of `v1/feedback` anywhere in `codemie-ui/src` (grep for `v1/feedback|/feedback`: two hits). No admin page lists feedback.
- codemie-code CLI: no consumer found.
- This repo: no internal caller of the GET routes. `FeedbackEntry` referenced only by `routers/feedback.py` and `src/external/alembic/env.py:98` (metadata import). `FeedbackRequest`/`FeedbackDeleteRequest` used by `conversation_service.py:66`. `MarkEnum` imported by `models/conversation.py:41` and `conversation_monitoring_service.py:22`. Analytics: feedback metrics go through `ConversationMonitoringService` (Elastic/metrics), `ConversationMetrics` rows, and `leaderboard/collector.py:778-794` ("feedback given") which reads `assistant_user_interaction` / `skill_user_interaction`, not the `feedback` table. No reader of the `feedback` table beyond the two GET routes was found. SDK / enterprise code: no `/v1/feedback` reference in `src/` outside the router.
- Guards: `tests/codemie/rest_api/test_core_gating.py:29` lists `/v1/feedback` in `STAY_PATHS` (path stays reachable when core is disabled; prefix match, so also covers `/v1/feedback/{document_id}`); changing handlers does not change the path. OpenAPI snapshot `tests/codemie/rest_api/router_guards/baselines/openapi.json:68784-68964` records: DELETE/GET/POST `/v1/feedback` and GET `/v1/feedback/{document_id}`, with `200` (+`422` where a body/param exists) and `security [user-id, X-Bind-Key]`, plus `FeedbackEntry` schema (line 10843). Adding a `user: User = Depends(authenticate)` parameter to `index`/`show` leaves the snapshot unchanged (same dependency as the router level, security already listed); documented 4xx via `ExtendedHTTPException` are not declared in the baseline for these routes. A changed response model or added `responses=` would change the snapshot (`test_openapi_snapshot.py`, `regenerate_baselines.py`).
- Behaviour change for consumers: the UI POST/DELETE use the owner's own `currentChat.id`, so owner-only WRITE keeps them working (AC4). Shared-conversation viewers (`SharedConversation` / `share.py`) have READ only and cannot reach feedback buttons via a conversation they do not own; whether the UI hides the buttons for shared views was not checked.

### Architecture and Layers Affected

REST router (`routers/feedback.py`), model/SQL layer (`models/feedback.py`, `models/conversation.py`), service (`ConversationService.add_feedback/remove_feedback`), security (`core/ability.py`, `security/permissions.py`). No repository exists for the `feedback` table.

### Integration Points

`Conversation` / `ConversationMetrics` tables (read+write on feedback POST/DELETE), `ConversationMonitoringService` metrics, `Ability`.

### Patterns and Conventions

`BaseModelWithSQLSupport` classmethods with `Session(cls.get_engine())`; `ExtendedHTTPException` for errors; `Ability(user).can(...)` then `raise_forbidden`/`raise_access_denied` from `routers/utils.py`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/data/repository-patterns.md` (repository boundary only; no feedback/conversation repository), `.ai-run/guides/api/rest-api-patterns.md`, `security-patterns.md` listed in AGENTS.md (not read in detail).

### Architectural Decisions
None recorded for feedback ownership.

### Derived Conventions
See Decisions Already in the Code.

---

## 4. Testing Landscape

### Existing Coverage
No test file for the feedback router or `FeedbackEntry`. Related: `tests/codemie/service/test_upsert_conversation_with_history.py`, `tests/codemie/rest_api/routers/test_conversation_metrics_access.py` (access-check style neighbour), `tests/codemie/rest_api/routers/test_conversation*.py`, `tests/codemie/rest_api/test_core_gating.py`, `router_guards/` snapshot tests. Code-graph reports no tests within 3 hops of `add_feedback`, `remove_feedback`, `delete_feedback`, `final_feedback`.

### Testing Framework and Patterns
pytest (Python 3.12/3.13 caches); router tests use FastAPI `TestClient` with `app.dependency_overrides[authenticate]` (as in the repro). Repro builds `User(id=..., is_admin=...)`; note `User.resolve_is_admin` overrides `is_admin` when `ENV=local` or user management is off, so tests must pin `config.ENV` and `ENABLE_USER_MANAGEMENT` as the repro does. Area style per project memory: mock-based, no real-Postgres tests.

### Coverage Gaps
Whole feedback router; SQL join between feedback and conversations; orphaned-entry case.

---

## 5. Configuration and Environment

### Environment Variables
`ENV` (`Environment.LOCAL` forces admin), `ENABLE_USER_MANAGEMENT`, `ADMIN_USER_ID`, `ADMIN_ROLE_NAME` (`security/user.py:160-173`).

### Configuration Files
`config/customer/customer-config.yaml:33` mentions a `feedbackAssistant` (unrelated to the route).

### Feature Flags and Deployment Concerns
`/v1/feedback` stays reachable in non-core mode (`STAY_PATHS`); `final_feedback` is behind `require_core`, the four routes are not. Alembic: `feedback.conversation_id` has no index.

---

## 6. Risk Indicators

- Type mismatch for the join: `feedback.conversation_id uuid` vs `conversations.id varchar` (Section 2, Answer 5); direction of cast decides error-vs-no-match for non-UUID conversation ids.
- `Conversation.get_by_id` raises `KeyError` (`conversation.py:640`); `if not conversation` 404 branches in `create`/`delete` are dead, and no `KeyError` handler exists in `main.py`; a missing conversation today surfaces as an unhandled error.
- Decision (d) 403 vs `history.py` precedent 401 (`raise_access_denied`); `raise_forbidden` exists.
- `Ability.can(WRITE)` has no admin shortcut for Conversation; admins cannot write on others' conversations through it.
- Admin predicate choice not fixed by the task: `is_admin` vs `is_admin_or_maintainer` (maintainer already sets `is_admin=True` at `user.py:175`) vs auditors (`is_admin_maintainer_or_auditor`).
- `create()` saves the `FeedbackEntry` before `add_feedback` (`feedback.py:86-88`), so a check placed after the save would leave a row behind; `add_feedback` rewrites the whole `history` JSONB and updates `ConversationMetrics` (see project memory on write amplification).
- No cascade/cleanup of `feedback` rows on conversation deletion (none found), so orphan entries exist in practice; admin-only visibility from decision (a) depends on it.
- No existing tests; the /tmp repro stubs persistence, so SQL join behaviour is untested.
- OpenAPI baseline snapshot (`openapi.json:68784-68964`) must stay matching unless responses/models are changed.
- Speculative: shared-conversation viewers may still see feedback buttons in the UI and receive 403 after the change (not checked).
- Speculative: `GET /v1/feedback` returns up to 10,000 rows with full `history`; a joined query must keep pagination/ordering of `get_all` to avoid a behaviour change.

---

## 7. Summary for Complexity Assessment

Layers touched: the feedback router (4 handlers), one new owner-scoped query for `FeedbackEntry` joined to `Conversation` (no repository exists for either table; model classmethods with `select` and `Session(get_engine())` are the in-repo style), and the `Ability`/permission helpers that already exist. Existing files in the change surface: `routers/feedback.py`, `models/feedback.py`, tests; the service (`add_feedback`/`remove_feedback`) and OpenAPI baseline are consumers rather than expected edits. The request/response shape of every route is unchanged.

Technical novelty is low: ownership check via `Ability` is an established pattern (`history.py`), and owner-scoped SQL is common. The one non-routine fact is the `uuid` vs `varchar` join between `feedback.conversation_id` and `conversations.id`, which is the id the write path already uses. The only consumer found is codemie-ui POST/DELETE on the user's own chat; no GET consumer, CLI consumer, or analytics reader of the `feedback` table was found, so owner-filtering the GETs breaks no consumer in reach.

Test posture: no feedback tests exist; neighbour router tests use `TestClient` with `dependency_overrides[authenticate]` and mocks. Key risks: the cast direction and non-UUID ids, dead `KeyError` branches producing unhandled errors, 401 vs 403 precedent, admin predicate choice, save-before-check ordering in `create`, and orphaned entries.

---

## 8. External References

None named by the task as a source of truth. Files read for context:
- `/tmp/repro_17683/test_repro_17683.py` (resolved): FastAPI app with `router_mod.router`, `authenticate` overridden by `User(id=..., is_admin=...)`, `config.ENV="development"`, `ENABLE_USER_MANAGEMENT=True`; monkeypatches `router_mod.Conversation`, `router_mod.ConversationService`, and `FeedbackEntry.get_all/get_by_id/find_by_id/save/delete`. Tests: AC1 list/show (RED), AC2 admin sees all (green control), AC3 POST/DELETE on foreign conversation expect status in (403, 404) and no change (RED), AC4 owner POST (green). Note it accepts 403 or 404 for AC3 and `status != 200` for show, so it does not pin decision (d).
- codemie-ui: `/Users/bohdan_maliar/Projects/codemie-dev/codemie-ui/src/store/chats.ts:1011-1090`, `.../MessageFeedbackActions/MessageFeedbackActions.tsx` (resolved).
- codemie-code: `/Users/bohdan_maliar/Projects/codemie-dev/codemie-code` (resolved, no `/v1/feedback` usage).
