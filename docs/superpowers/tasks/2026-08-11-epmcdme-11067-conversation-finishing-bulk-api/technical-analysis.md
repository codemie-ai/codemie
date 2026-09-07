# Technical Research

**Task**: conversation finish message bulk admin
**Generated**: 2026-08-11
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-11067 — Platform-enforced Conversation Finishing with Bulk API Support.

Business Need: Reduce chatbot hallucination risk by prohibiting users from continuing conversations older than N hours, as a platform capability usable from customer UI applications.

Executive Summary: Implement platform-enforced conversation finalization in CodeMie: user and admin APIs to finish conversations (individually and in bulk), model and DB enhancements, server-side enforcement, efficient query support for external schedulers, robust error/permission handling, and test coverage.

Preconditions: Conversation data model and REST APIs exist in CodeMie. Admin authentication and authorization available for admin endpoints.

Acceptance Criteria:
1. Conversation model includes and exposes `is_finished` (default false) and `finished_at` (nullable).
2. Single and bulk endpoints to finish conversations (user and admin) exist and are idempotent.
3. Bulk admin endpoint: `POST /admin/conversations/finish-bulk` with per-ID success/error reporting.
4. All message-append/modify APIs and related mutations are blocked post-finish, HTTP 409 error on violation (deletion excluded from immutability enforcement).
5. All REST APIs that return conversation info enriched with `isFinished` and `finishedAt` parameters.
6. Efficient, paginated API for finding finished/unfinished conversations; no full-table scans.
7. All APIs are permissioned and return clear, observable errors on invalid/unauthorized use.
8. Automated test coverage for individual, bulk, and enforcement scenarios.
9. Scheduler-initiated closure (via these endpoints) is possible, reliable, and efficient at scale.

Implementation Plan (from ticket):
1. **Data Model Changes**: Add Boolean `is_finished` (default false) and nullable `finished_at` timestamp to conversation model. Expose both in all relevant API responses and paginated list summaries. Add DB migration incl. partial index on `(date where is_finished=false)` for scalable queries.
2. **API Changes**: User endpoint `POST /conversations/{id}/finish` (idempotent, owner-only). Admin endpoints: `POST /admin/conversations/{id}/finish` (idempotent, no ownership required) and `POST /admin/conversations/finish-bulk` (multiple conversations in one request, body `{"conversation_ids": [...]}`, response includes per-ID `is_finished`/`already_finished`/`finished_at`/error). Existing conversation-listing endpoints return `isFinished` and `finishedAt` fields.
3. **Server-Side Enforcement**: All append/message-modification operations check `is_finished` and reject with HTTP 409 if set. Applies to all mutation actions except deletion. Minor race conditions between append/finish accepted per ADR intent — document in code/ADR.
4. **Auth/Permissions/Observability**: User endpoints require ownership; admin endpoints require system/admin credentials. Clear, descriptive error responses with appropriate codes. `finished_at` enables audit.
5. **Efficiency**: Partial/composite DB indexes for finished/unfinished queries and bulk ops. Paginate all admin list endpoints.
6. **Automated Testing**: Cover single/bulk finishing, enforcement, scheduler queries, access control, error cases.

Example scheduler usage: `GET /admin/conversations?isFinished=false&startedAt=...&page=0&per_page=100` to list unfinished conversations, then `POST /admin/conversations/finish-bulk` with `conversation_ids` to close them in bulk.

Scope note: no separate `/admin/conversations/stale` endpoint — filtering is done via `isFinished` query param on the existing list endpoint (clarified/resolved in ticket comments before approval).

Discrepancies flagged by ticket fetch (not resolved by ticket data alone, note only):
- Epic Link field says `EPMCDME-13292`, description footer says `Epic: EPMCDME-217`, and a later comment recommends re-linking to `EPMCDME-13285` (AI Safety & Guardrails) as the better-fit parent. Does not affect implementation scope.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/models/conversation.py:225` — `Conversation(BaseModelWithSQLSupport, Owned, table=True)` — target model; **no `is_finished`/`finished_at` fields yet** (greenfield addition). Has `history: List[GeneratedMessage]`, `user_id`, `folder`, `pinned`, `is_workflow_conversation`, etc.
- `src/codemie/rest_api/models/conversation.py:414` — `Conversation.get_by_id` — override that materializes workflow execution refs into history; a finish-state check could layer here or in the service.
- `src/codemie/rest_api/models/conversation.py:160` — `UpsertHistoryRequest` / `UpsertHistoryResponse` — payload for `PUT /conversations/{conversation_id}/history`, the primary history-mutation endpoint and the main AC4 enforcement point.
- `src/codemie/rest_api/routers/conversation.py` — hosts `upsert_conversation_history`, `get_conversation_by_id`, conversation CRUD/search/export routes. `Conversation` has 59 total callers spanning this file plus `handlers/assistant_handlers.py`, `routers/admin.py`, `routers/feedback.py`, `routers/share.py`, and 13 more files — full enumeration not completed in this research pass; re-explore before implementing enforcement.
- `src/codemie/rest_api/routers/admin.py` — existing admin router; natural home for `POST /admin/conversations/{id}/finish` and `POST /admin/conversations/finish-bulk`. Full contents not sourced in this pass — read directly before adding routes.
- `src/codemie/service/conversation_service.py` — conversation service layer, referenced as a caller but not sourced in this pass.
- `src/codemie/service/conversation/history_materializer.py`, `history_projection_service.py`, `history_compaction_service.py` — conversation history read/write pipeline; likely additional mutation touchpoints for AC4 enforcement besides the router.
- `src/codemie/core/ability.py:162` — `Ability.can(action, instance)` permission-check entry point; `Ability.admin(*args)` at line 207 returns `user.is_admin_or_maintainer` — pattern to reuse for admin-only finish endpoints.
- `src/codemie/rest_api/security/authentication.py:158` — `admin_access_only` FastAPI dependency (raises 403 `ExtendedHTTPException` if not admin/maintainer) — reusable for admin finish endpoints.
- `src/codemie/rest_api/routers/projects.py:1145` — `bulk_assign_users_to_project` — existing bulk-endpoint pattern: request model with `min_length`/`max_length` list, per-item `BulkAssignmentResultItem`, response with `total`/`results` — good template for `finish-bulk` per-ID reporting (AC3).
- `src/codemie/rest_api/routers/guardrail.py:253` — `bulk_assign_guardrail` — another existing bulk pattern with `BulkAssignmentResult(success, failed, errors)`.
- `src/codemie/core/exceptions.py` — `ExtendedHTTPException`, the standard typed-exception class used across routers for structured error responses (403/404/400); HTTP 409 usage should follow this same class.

**Important disambiguation**: the only `finish()` symbol currently in the codegraph belongs to unrelated `WorkflowExecutionService.finish` (`workflow_execution_service.py:198`). There is no existing "finish"/"lock" concept on `Conversation` — this is genuinely new behavior, not a refactor.

### Architecture and Layers Affected
REST router (`rest_api/routers/*`) → service layer (`service/*`) → SQLModel entity (`rest_api/models/*`, `BaseModelWithSQLSupport`) → Postgres via `clients/postgres.get_session`. Permission checks via `core/ability.Ability` and `rest_api/security/authentication` FastAPI dependencies sit between router and service.

Layers touched by this task:
- **Model**: `Conversation` — add `is_finished`, `finished_at` fields + migration.
- **Router**: `rest_api/routers/conversation.py` (user finish endpoint, enrich existing list/get responses) and `rest_api/routers/admin.py` (admin finish + bulk finish endpoints).
- **Service**: `service/conversation_service.py` and the history pipeline (`history_materializer.py`, `history_projection_service.py`, `history_compaction_service.py`) for enforcement.
- **Permissions**: `core/ability.py`, `rest_api/security/authentication.py`.
- **Config**: `configs/config.py` if any new env var (e.g. auto-finish threshold) is introduced.

### Integration Points
- Assistant/workflow chat flows (`service/assistant_service.py`, `workflows/workflow.py`) also mutate conversation history and are candidate enforcement points for AC4 — not yet enumerated exhaustively.
- External scheduler integration is via existing paginated list endpoint filtered by `isFinished`, not a new endpoint — no new external integration surface beyond the two new finish endpoints.

### Patterns and Conventions
- Bulk endpoints return per-item result objects (`BulkAssignmentResultItem`, `BulkAssignmentResult`) with counts + per-ID status/error, not a flat list — matches AC3's per-ID success/error reporting requirement.
- Admin-only endpoints use `Depends(admin_access_only)` (or `Ability.admin`), raising `ExtendedHTTPException(code=403, ...)`.
- Owner-only checks use `Ability.can(Action.WRITE, instance)` → `owned_by` permission, or `instance.is_owned_by(user)` directly on `Owned`-derived models (`Conversation` already extends `Owned`).
- Config uses Pydantic `BaseSettings` (`Config` class in `configs/config.py`) with `@model_validator(mode="after")` for cross-field validation.
- Idempotent bulk ops follow the "sync" pattern (create/update/no-op), as seen in `sync_guardrail_bulk_assignments`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router conventions (P0 for API category).
- `.ai-run/guides/api/endpoint-conventions.md` — route/response conventions (P1 for API category).
- `.ai-run/guides/data/database-patterns.md` — SQLModel/session patterns (P0 for Database category — needed for `is_finished`/`finished_at` migration).
- `.ai-run/guides/data/database-optimization.md` — pagination/batching, partial-index guidance (directly relevant to AC6/AC9 efficiency requirement).
- `.ai-run/guides/development/security-patterns.md` — auth/permission/secrets patterns (P0 for Security category).
- `.ai-run/guides/development/error-handling.md` — typed exceptions/handlers (for required HTTP 409 responses).
- `.ai-run/guides/architecture/service-layer-patterns.md` — service orchestration conventions.
- `.ai-run/guides/testing/testing-api-patterns.md`, `.ai-run/guides/testing/testing-service-patterns.md` — test patterns (only relevant once tests are explicitly requested).

### Architectural Decisions
No existing ADR on conversation finishing found. The ticket itself specifies the intended decision: minor race conditions between append and finish are accepted (not solved with distributed locking) — should be documented in code and/or an ADR note per the ticket's own Implementation Plan section 3.

### Derived Conventions
Bulk-operation and admin-permission conventions above are derived from `projects.py`/`guardrail.py`, not from a written guide — no guide document specifically covers "bulk endpoint response shape," so the existing code patterns are the convention of record.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_conversation.py`, `test_conversation_pagination.py` — existing conversation router test suites; target files for new finish-endpoint tests.
- `tests/codemie/core/test_ability_conversation.py` — permission tests for `Conversation`; extend for owner/admin finish checks.
- `tests/codemie/rest_api/models/test_conversation_model.py` — model-level tests; extend for `is_finished`/`finished_at` defaults.
- `tests/codemie/service/test_conversation_service_pagination.py`, `test_history_materializer.py` — service-layer test patterns to mirror.
- `tests/codemie/rest_api/routers/test_user_management_router_crud.py` — reference for admin-only route test conventions.

### Testing Framework and Patterns
pytest with `pytest.mark.asyncio` for async service tests; `unittest.mock.Mock`/`AsyncMock`/`patch` for mocking.

### Coverage Gaps
No existing tests for a "finish"/"lock" concept on `Conversation` (none exists yet). No covering tests found for the unrelated `finish`/`finish_state` symbols in `workflow_execution_service` either (noted as a pre-existing gap in that area, not in scope here).

---

## 5. Configuration and Environment

### Environment Variables
None found specific to conversation finishing today. A new env var may be needed if an auto-finish threshold (N hours) is implemented server-side rather than purely via scheduler — ticket's Business Need mentions "older than N hours" but the Implementation Plan pushes that decision to the external scheduler, not a server-side timer. Confirm scope during spec/brainstorming.

### Configuration Files
- `src/codemie/configs/config.py:44` — `Config(BaseSettings)`, central settings class; add any new env var here.
- `.env` / `.env.local` — loaded via `load_dotenv` in `config.py` (lines 940-941).

### Feature Flags and Deployment Concerns
No feature flag found gating conversation lifecycle behavior. None appears required by the AC — enforcement is unconditional per the ticket.

---

## 6. Risk Indicators

- `Conversation` model has no `is_finished`/`finished_at` fields today — this is a new migration + model change, not a refactor of existing logic.
- Multiple mutation entry points exist for conversation history (`upsert_conversation_history` router endpoint, `history_materializer`, `history_compaction_service`, `history_projection_service`, plus assistant/workflow chat flows in `service/assistant_service.py`, `workflows/workflow.py`) — AC4 requires enforcement to cover all of them, and only ~6 of the 17 files with `Conversation` callers were sourced in this pass. Full mutation-surface enumeration must happen before/during planning.
- `src/codemie/rest_api/routers/conversation.py` and `src/codemie/service/conversation_service.py` full contents were not returned verbatim in this research pass (listed only as callers) — read directly before implementing.
- `src/codemie/rest_api/routers/admin.py` full contents not sourced — verify existing admin conversation routes before adding new ones, to avoid path collisions and to match existing admin router conventions.
- DB migration with a partial index (AC6/AC9: `date where is_finished=false`) needs to match the project's existing migration tooling/conventions — confirm via `.ai-run/guides/data/database-patterns.md` before writing it.
- Ticket-level ambiguity: whether "older than N hours" auto-finish is server-enforced (a background job/timer) or purely client/scheduler-driven via the exposed APIs. The Implementation Plan and scenarios describe only the latter (external scheduler calls the APIs) — treat server-side auto-finish as out of scope unless clarified otherwise in brainstorming.

---

## 7. Summary for Complexity Assessment

This task touches four architectural layers: the `Conversation` SQLModel entity (new fields + migration + partial index), two routers (`rest_api/routers/conversation.py` for the user-facing finish endpoint and enrichment of existing read endpoints, `rest_api/routers/admin.py` for the two new admin endpoints), the service/history-mutation layer (multiple files: `conversation_service.py`, `history_materializer.py`, `history_projection_service.py`, `history_compaction_service.py`, plus assistant/workflow chat flows), and the permission layer (`core/ability.py`, `rest_api/security/authentication.py`) for owner-vs-admin authorization.

Technical novelty is moderate: no prior "finish"/lock concept exists on `Conversation`, but strong, directly-reusable patterns exist in the codebase for both bulk endpoints (`bulk_assign_users_to_project`, `bulk_assign_guardrail`) and admin/owner permission checks, so the implementation is closer to "apply an established pattern to a new domain" than "invent a new pattern." The main complexity driver is breadth, not depth: AC4 requires blocking mutation across potentially 5+ distinct mutation entry points, and the full enumeration of those entry points was not completed in this research pass (59 total `Conversation` callers across 17 files, only ~6 sourced).

Test coverage posture: solid existing test suites for conversation router, model, and permissions to extend, plus service-layer test patterns to mirror — no coverage gap requiring new test infrastructure. Key risks are (1) incomplete mutation-surface enumeration until `conversation.py` router, `conversation_service.py`, and `admin.py` are read in full, (2) a scope ambiguity around whether "older than N hours" implies a server-side timer versus purely scheduler-driven closure via the new APIs (ticket leans toward the latter), and (3) getting the partial-index migration right per project DB conventions.
