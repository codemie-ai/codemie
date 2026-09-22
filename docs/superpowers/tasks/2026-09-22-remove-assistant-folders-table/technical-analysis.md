# Technical Research

**Task**: assistant_folders conversation_service postgres migration
**Generated**: 2026-09-22T00:00:00Z
**Research path**: filesystem

> **Read this as a snapshot of the branch before the work, not as current fact.** It is kept
> unedited except where it gave forward-looking instructions that the delivered solution
> contradicts; those carry an inline **[superseded]** note. The two that matter: the table was
> removed by deleting its migration rather than by a `DROP TABLE`, and revision `5bba1414c0ca` no
> longer exists. Current state and rationale live in `spec.md`.

---

## 1. Original Context

Remove the unnecessary assistant_folders Postgres table added on branch EPMCDME-13270 (repo codemie, backend) and replace it with a derived/stateless implementation, per reviewer feedback on the MR. Key context: Migration src/external/alembic/versions/v3w4x5y6a7b8_add_assistant_folders.py creates table assistant_folders. Model is src/codemie/rest_api/models/assistant_folder.py (AssistantFolder). Used by ConversationService.get_assistant_folders / delete_assistant_folder in src/codemie/service/conversation_service.py, wired into GET/DELETE /assistant-folders... in src/codemie/rest_api/routers/conversation.py. Conversation.initial_assistant_id already exists to derive groupings (GROUP BY user_id, initial_assistant_id). For delete, reuse the custom-folders pattern: DELETE /conversations/folder/{folder:path} with remove_conversations: bool query param. If persisted dismissed-folder state is needed, add it as JSONB to user_preferences table. Do NOT touch import_source. Write and update tests in tests/codemie/service/test_conversation_service.py. Add a migration that drops assistant_folders table.

---

## 2. Codebase Findings

### Existing Implementations

- `src/external/alembic/versions/v3w4x5y6a7b8_add_assistant_folders.py` — Creates `assistant_folders` table (`id`, `date`, `update_date`, `user_id`, `assistant_id`), unique constraint `uq_assistant_folders_user_assistant (user_id, assistant_id)`, indexes on both columns, and a backfill INSERT from `conversations` grouped by `(user_id, initial_assistant_id)`. Revision ID `v3w4x5y6a7b8`; down-revision `u2v3w4x5y6a7`.
- `src/external/alembic/versions/5bba1414c0ca_merge_assistant_folders_and_chargeback_.py` — Merge migration that joins heads `f1g2h3i4j5k6` and `v3w4x5y6a7b8` into revision `5bba1414c0ca`. This is the current head that any new migration must reference. **[superseded]** This file was deleted along with the table's own migration; it existed only to reconcile a head that `v3w4x5y6a7b8` created. The branch head is now `u2v3w4x5y6a7`, chained off main's `d8e4f1a2b6c3`.
- `src/codemie/rest_api/models/assistant_folder.py` — `AssistantFolder` SQLModel table class with `user_id` and `assistant_id` columns; classmethods `ensure_registered`, `get_by_user`, `delete_registration`, `delete_by_user`. Also defines Pydantic models `AssistantFolderListItem` (`assistant_id`, `name`, `icon_url`) and `AssistantFolderDeleteResponse` (`deleted_conversation_ids`, `folder_deleted`).
- `src/codemie/service/conversation_service.py` — `ConversationService.get_assistant_folders` reads `AssistantFolder.get_by_user(user.id)` and resolves assistant names via `Assistant.get_by_ids`. `ConversationService.delete_assistant_folder` runs a `SELECT ... FOR UPDATE` on `AssistantFolder` for the registration, then deletes conversations where `user_id`, `initial_assistant_id`, no custom folder, no import_source, not pinned, not workflow. Calls `AssistantFolder.ensure_registered` in three places: `upsert_chat_history` (line 322), `upsert_conversation_with_history` (line 378), and `create_conversation` (line 782).
- `src/codemie/rest_api/routers/conversation.py` — `GET /v1/assistant-folders` (line 321) and `DELETE /v1/assistant-folders/{assistant_id}` (line 329) with `action: AssistantFolderDeleteAction` query param (`delete_chats_only` or `delete_folder_and_chats`). Also `DELETE /conversations` (line 305) calls `AssistantFolder.delete_by_user(user.id)`.
- `src/codemie/core/models.py` — `AssistantFolderDeleteAction(StrEnum)` with values `delete_chats_only` and `delete_folder_and_chats` (line 809).
- `src/codemie/rest_api/models/conversation.py` — `Conversation` model has `initial_assistant_id: Optional[str]` (line 261), `is_workflow_conversation`, `import_source`, `folder`, `pinned` — these are the filter fields used in `delete_assistant_folder`.
- `src/codemie/rest_api/models/conversation_folder.py` — `ConversationFolder` SQLModel for `conversation_folders` table; pattern to follow for the delete endpoint (`delete_conversation_folder` in service with `remove_conversations: bool`, `DELETE /conversations/folder/{folder:path}` in router).
- `src/codemie/rest_api/models/user_preferences.py` — `UserPreferences` SQLModel (`user_preferences` table, PK `user_id`) with `pinned_assistants` (JSONB) and `favorites` (JSONB). Candidate for persisting dismissed-folder state if needed.

### Architecture and Layers Affected

- **Migration layer** (`src/external/alembic/versions/`): requires a new migration that drops `assistant_folders` table; must set `down_revision = "5bba1414c0ca"` (current head after the merge migration). **[superseded]** No drop migration ships. The table had never been applied to a database, so its `CREATE` and the four merge migrations around it were deleted instead.
- **Model layer** (`src/codemie/rest_api/models/assistant_folder.py`): `AssistantFolder` SQLModel table class must be replaced by a stateless implementation (plain Pydantic or pure query); Pydantic response models `AssistantFolderListItem` and `AssistantFolderDeleteResponse` can be retained.
- **Service layer** (`src/codemie/service/conversation_service.py`): `get_assistant_folders` must derive folders via `GROUP BY user_id, initial_assistant_id` on `Conversation`; `delete_assistant_folder` must reuse the custom-folder delete pattern (filter conversations by `initial_assistant_id` instead of a registration row); all three `ensure_registered` call sites must be removed.
- **Router layer** (`src/codemie/rest_api/routers/conversation.py`): `DELETE /v1/assistant-folders/{assistant_id}` may be replaced by `DELETE /conversations/folder/{folder:path}` pattern or the endpoint signature changes; `AssistantFolder.delete_by_user` call in `delete_conversation_by_user` must be removed or replaced.
- **Core models** (`src/codemie/core/models.py`): `AssistantFolderDeleteAction` enum removal or replacement depending on the new delete endpoint design.

### Integration Points

- `Conversation.initial_assistant_id` (existing column) is the derivation key for `get_assistant_folders` and `delete_assistant_folder`; confirmed present in both the model and all SQL queries.
- `Assistant.get_by_ids` is called by `get_assistant_folders` to resolve names/icons from registration IDs — this call remains needed but the input changes from stored registrations to derived `initial_assistant_id` values.
- `WorkflowExecution.delete_by_conversation_ids` is called inside `delete_assistant_folder`'s transaction — must be preserved.
- `SharedConversation.delete_by_conversation` and `ConversationMetrics` deletes also inside the transaction — must be preserved.
- `UserPreferences` table is available for persisting dismissed-folder state (JSONB column addition); `a9f3c1d2e4b5_create_user_preferences_table.py` is its origin migration.
- `5bba1414c0ca` is the current alembic head; any new migration must chain off it. **[superseded]** That revision was deleted; the head is `u2v3w4x5y6a7`.

### Patterns and Conventions

- Derived/stateless folder list: query `Conversation` table with `GROUP BY user_id, initial_assistant_id` filtering out `import_source IS NOT NULL` and `is_workflow_conversation = TRUE`, analogous to the backfill SELECT already in the migration.
- Custom-folder delete pattern: `ConversationService.delete_conversation_folder(user, folder, remove_conversations)` accepts a `remove_conversations: bool` and either deletes or unfolds the conversations. The equivalent for assistant-folder delete would operate by `initial_assistant_id` instead of `folder`.
- All SQLModel table classes inherit from `BaseModelWithSQLSupport` (has `id`, `date`, `update_date`), use `Session` from `sqlmodel`, and `get_session()` from `codemie.clients.postgres`.
- Migrations use `op.create_table` / `op.drop_table` with explicit column definitions; `op.execute` for backfills.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/data/database-patterns.md` — covers SQLModel and session patterns; relevant for derived query implementation.
- `.ai-run/guides/data/repository-patterns.md` — relevant for how queries should be structured.
- `.ai-run/guides/architecture/layered-architecture.md` — confirms service/repository/router layers.
- `.ai-run/guides/api/rest-api-patterns.md` and `.ai-run/guides/api/endpoint-conventions.md` — relevant for the revised endpoint signatures.

### Architectural Decisions

- The merge migration `5bba1414c0ca` records that `v3w4x5y6a7b8` was introduced alongside another feature (`f1g2h3i4j5k6` chargeback head). The drop migration must succeed `5bba1414c0ca`, not `v3w4x5y6a7b8` directly. **[superseded]** Both revisions were deleted; there is no drop migration to chain.
- The `delete_assistant_folder` implementation already uses `Conversation.import_source.is_(None)` to exclude imported conversations and `Conversation.is_workflow_conversation.is_(False)` to exclude workflows — the same guards must apply in the stateless derivation.
- The `upsert_conversation_with_history` path skips `ensure_registered` when `import_source` is set (`if not import_source: AssistantFolder.ensure_registered(...)`), which is a deliberate exclusion that must be replicated in the derived list filter.

### Derived Conventions

- Response model `AssistantFolderListItem` (Pydantic-only) is already decoupled from the ORM table — it can remain unchanged.
- `AssistantFolderDeleteResponse` is also Pydantic-only — it can remain or be adapted to the new delete response.
- The pattern for "list by user with assistant resolution" is already established in `get_assistant_folders`; only the data-source layer changes from DB table to derived query.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/test_conversation_service.py`:
  - `test_conversation_service_create` (line 490) patches `AssistantFolder.ensure_registered` and asserts it is called with `(user.id, "123")`.
  - `test_conversation_service_create_workflow_does_not_register_assistant_folder` (line 518) asserts `ensure_registered` is NOT called for workflow conversations.
  - `test_delete_assistant_folder_preserves_or_removes_registration` (line 542, parametrized) patches `AssistantFolder.delete_registration` and `get_session`, asserts delete count and response fields.
  - `test_get_assistant_folders_returns_empty_list_when_no_registrations` (line 1325) patches `AssistantFolder.get_by_user` returning `[]`.
  - `test_get_assistant_folders_returns_mapped_items` (line 1334) patches `AssistantFolder.get_by_user` returning mock registrations.

### Testing Framework and Patterns

- pytest with `unittest.mock.patch` and `MagicMock`.
- Pattern: patch the model class methods (`AssistantFolder.ensure_registered`, `AssistantFolder.get_by_user`, `AssistantFolder.delete_registration`), construct service calls, and assert response shape.
- Session mocking: `get_session` patched to return a context-manager mock with `.exec()` side effects.
- Fixtures: `mock_user`, `mock_assistant`, `mock_conversation`, `mock_conversation_metrics`, `mock_request`.

### Coverage Gaps

- No existing test for `get_assistant_folders` when `initial_assistant_id` is used as the derivation source — these tests currently mock the table read, so the derived GROUP BY query path has no test.
- No test for `delete_assistant_folder` with the stateless (no registration row) code path.
- No test covers the call to `AssistantFolder.delete_by_user` inside `delete_conversation_by_user` (router level — but service-layer equivalent is not tested either).
- No test for `upsert_conversation_with_history` covering the `ensure_registered` removal with `import_source` set.

---

## 5. Configuration and Environment

### Environment Variables

No environment variables specific to `assistant_folders` were found. Database connection is governed by `POSTGRES_*` variables used by `codemie.clients.postgres`.

### Configuration Files

- `src/external/alembic/alembic.ini` — Alembic configuration; `env.py` in same directory drives migration execution.
- `pyproject.toml` — declares `sqlmodel`, `alembic` (transitively), and `sqlalchemy` as dependencies.

### Feature Flags and Deployment Concerns

- No feature flags found for `assistant_folders`.
- The drop migration must be deployed after any running instances stop writing to `assistant_folders`; since the table is being replaced by a derived query with no write path, a standard rolling deploy is safe once the application code no longer references the table. **[superseded]** There is no drop to sequence. No environment ever ran the create, so no deployment ordering applies.
- The `5bba1414c0ca` merge migration is the alembic head; all deployments must be at that revision before the new drop migration is applied. **[superseded]** That revision was never deployed and no longer exists.

---

## 6. Risk Indicators

- **Alembic chain**: The new drop migration must set `down_revision = "5bba1414c0ca"` (the merge head), not `v3w4x5y6a7b8`. Using the wrong parent will create a parallel head and break `alembic upgrade head`. **[superseded]** The risk was real but was resolved by removing the revisions rather than chaining onto them. The delivered check is the same in spirit: walk every revision file and confirm a single head with no dangling parent.
- **Residual call in delete_conversation_by_user**: `AssistantFolder.delete_by_user(user.id)` is invoked at router line 314 inside `delete_conversation_by_user`. If the `AssistantFolder` table class is removed without updating this call, the endpoint will fail at runtime.
- **Three ensure_registered call sites**: Lines 322, 378, and 782 of `conversation_service.py` must all be removed. Missing any one leaves a dead import that raises `AttributeError` at runtime once the model class loses its `ensure_registered` classmethod.
- **SELECT FOR UPDATE on registration**: The `delete_assistant_folder` method opens a session with `select(AssistantFolder).with_for_update()`. This lock strategy is table-specific; the replacement derived delete will need its own concurrency strategy or can rely on row-level locks on `Conversation`.
- **AssistantFolderDeleteAction enum**: Defined in `src/codemie/core/models.py` and imported by the router. If the DELETE endpoint is replaced with the custom-folder pattern (`remove_conversations: bool`), this enum becomes dead code and its import from the router must be cleaned up.
- **Five tests reference AssistantFolder by name**: All five must be updated to match the new stateless implementation. Tests that patch `AssistantFolder.get_by_user` will become tests that patch the derived Conversation query.
- **Speculative: dismissed-folder state**: If the product requires users to hide/dismiss an assistant folder without deleting conversations, a new JSONB column on `user_preferences` is required. No such column exists today; the migration for it must be separate from the drop migration.
- **import_source exclusion logic**: The existing `delete_assistant_folder` already filters `import_source IS NULL`. The derived `get_assistant_folders` list must apply the same filter (consistent with the original backfill SQL in the migration).

---

## 7. Summary for Complexity Assessment

The change touches four layers: the alembic migration tree, the `assistant_folder.py` model/Pydantic module, `ConversationService` in the service layer, and the `/assistant-folders` routes in the router. The migration layer carries the highest structural risk because the existing table was introduced via a merge migration (`5bba1414c0ca`), so the new drop migration must be chained off that merge head rather than the feature migration directly. **[superseded]** The migration layer did carry the highest risk, but the resolution was to delete the create and its merge migrations outright, leaving one migration and one head. The service layer changes are moderate in scope: three `ensure_registered` call sites must be removed, and two methods (`get_assistant_folders`, `delete_assistant_folder`) must be rewritten to derive their data from the `Conversation` table using `GROUP BY user_id, initial_assistant_id`. The delete method also carries a `SELECT ... FOR UPDATE` lock that must be redesigned for the stateless path.

Test coverage of the affected code is present but tightly coupled to the table-backed implementation: five tests in `tests/codemie/service/test_conversation_service.py` mock `AssistantFolder` classmethods by name and will need to be fully rewritten to patch the derived Conversation queries. Additionally, there is a residual `AssistantFolder.delete_by_user` call in the "delete all user conversations" endpoint that is not covered by any current test, raising the risk that it is silently missed during refactoring.

Overall complexity is medium. The `Conversation.initial_assistant_id` column that enables the derived grouping already exists and is indexed; the delete logic pattern is already established in the `delete_conversation_folder` service method. The main effort is surgical removal of the table class and its call sites, rewriting two service methods to query Conversations directly, updating five tests, and authoring the drop migration with the correct revision chain. The optional dismissed-folder state (JSONB on `user_preferences`) is a conditional addition that would add a separate migration and a new service/repository call but is independent of the core refactor.

---

## 8. External References

None named by the task.
