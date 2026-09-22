# Remove assistant_folders Table — Implementation Plan

> Rewritten after implementation to record the plan as executed. The original plan built the task
> around a `DROP TABLE` migration chained off `5bba1414c0ca`; neither that migration nor that
> revision exists in the delivered branch. Steps below are checked because they are done.

**Goal:** Remove the `assistant_folders` table from branch EPMCDME-13270 so it appears nowhere in
the diff against main, and serve the `/v1/assistant-folders` endpoints from a derived query on
`Conversation.initial_assistant_id`.

**Architecture:** The `AssistantFolder` SQLModel table class is deleted; the two Pydantic response
models in the same file are retained. `ConversationService.get_assistant_folders` and
`delete_assistant_folder` query `Conversation` directly. The migration that created the table is
deleted rather than compensated by a drop.

**Tech Stack:** Python, SQLModel, SQLAlchemy, FastAPI, Alembic, pytest.

**Spec:** `docs/superpowers/tasks/2026-09-22-remove-assistant-folders-table/spec.md`

## Global Constraints

- The branch must resolve to exactly one alembic head after every task.
- Do not change `import_source` handling.
- Do not change the URL of either `/v1/assistant-folders` endpoint.
- Do not touch `ConversationFolder`, `UserPreferences` or `WorkflowExecution` beyond existing
  cascade calls.
- Commit per task using the repository's existing convention.

---

### Task 1: Rewrite the service methods as derived queries

**Files:**
- Modify: `src/codemie/service/conversation_service.py`
- Test: `tests/codemie/service/test_conversation_service.py`

**Test-first: yes.**

- [x] Rewrite `test_get_assistant_folders_returns_empty_list_when_no_registrations` and
  `test_get_assistant_folders_returns_mapped_items` to patch `get_session().exec()` instead of
  `AssistantFolder` classmethods. Confirm FAIL.
- [x] Replace `test_delete_assistant_folder_preserves_or_removes_registration` with two cases:
  `remove_conversations=False` (no session call, empty response) and `True` (delete query runs,
  cascades fire). Confirm FAIL.
- [x] Rewrite `get_assistant_folders`: `select(Conversation.initial_assistant_id).distinct()` with
  `import_source IS NULL` and `is_workflow_conversation` NULL-or-false, then `Assistant.get_by_ids`
  to resolve names and icons.
- [x] Rewrite `delete_assistant_folder` to `(user, assistant_id, remove_conversations: bool)`.
  Filter `Conversation` on all six NULL-safe expressions — legacy rows carry NULLs, which is why
  the original backfill used `COALESCE`, so strict `is_(False)` checks would silently skip them:
  1. `user_id == user.id`
  2. `initial_assistant_id == assistant_id`
  3. `or_(folder.is_(None), folder == '')`
  4. `import_source.is_(None)`
  5. `pinned.is_not(True)`
  6. `or_(is_workflow_conversation.is_(None), is_workflow_conversation.is_(False))`
  Keep the cascade deletes in the same transaction; drop the `SELECT FOR UPDATE`.
- [x] Run the tests — PASS.

---

### Task 2: Remove the ORM class and its call sites

**Files:**
- Modify: `src/codemie/rest_api/models/assistant_folder.py`
- Modify: `src/codemie/service/conversation_service.py`

**Test-first: yes.**

- [x] Rewrite `test_conversation_service_create` to drop the `ensure_registered` patch and
  assertion. Confirm FAIL.
- [x] Remove all three `ensure_registered` call sites (`upsert_chat_history`,
  `upsert_conversation_with_history`, `create_conversation`).
- [x] Delete the `AssistantFolder` SQLModel class and its four classmethods. Keep
  `AssistantFolderListItem` and `AssistantFolderDeleteResponse` in place.
- [x] Remove the `AssistantFolder` import from `conversation_service.py`.
- [x] Run the tests — PASS.

---

### Task 3: Router and core models cleanup

**Files:**
- Modify: `src/codemie/rest_api/routers/conversation.py`
- Modify: `src/codemie/core/models.py`

- [x] Replace `action: AssistantFolderDeleteAction = Query(...)` with
  `remove_conversations: bool = False` on `DELETE /v1/assistant-folders/{assistant_id}`.
- [x] Remove the `AssistantFolder.delete_by_user` call from `delete_conversation_by_user`.
- [x] Remove the `AssistantFolderDeleteAction` import and delete the enum from `core/models.py`.
- [x] Run `make ruff` — clean.

> This task introduced the contract mismatch with codemie-ui recorded under **Known open issue** in
> the spec. The UI was written against the `action` enum and still sends it.

---

### Task 4: Take the table out of the migration history

Replaces the original "add a drop migration" task. `assistant_folders` had never been applied to any
database, so the branch can simply stop proposing it.

**Files:**
- Delete: six migration files (see spec, Migration layer)
- Modify: `src/external/alembic/versions/u2v3w4x5y6a7_add_import_source_to_conversations.py`

- [x] Delete `v3w4x5y6a7b8_add_assistant_folders` and the short-lived
  `w2x3y4z5a6b7_drop_assistant_folders`.
- [x] Delete the four merge migrations that existed only to reconcile heads the table's migration
  created: `5bba1414c0ca`, `b77bc0702dea`, `b873650c15c9`, `8bc166e53346`.
- [x] Re-point `u2v3w4x5y6a7.down_revision` from `9b9b4c585e54` to `d8e4f1a2b6c3`, main's head.
- [x] Verify by walking every revision file: 184 revisions, one head (`u2v3w4x5y6a7`), no dangling
  parent. 184 = main's 183 plus this branch's one.

---

### Task 5: Confirm endpoint consumers before deleting anything further

Added during implementation. Both `/v1/assistant-folders` endpoints were deleted on the reasoning
that the sidebar could group client-side, then restored when codemie-ui proved otherwise.

- [x] Check codemie-ui on its `EPMCDME-13270` branch for callers.
- [x] Found `GET v1/assistant-folders` (`store/chats.ts:667`), `DELETE v1/assistant-folders/{id}`
  (`store/chats.ts:118`), and `assistantFolders` feeding both `unifiedChatSidebarViewModel.ts` and
  `focusedChatSidebarViewModel.ts`. Restore both endpoints.
- [x] Confirm the one genuinely uncalled addition: the `assistant_id` filter on
  `GET /conversations`. `getChats` calls `api.get('v1/conversations')` with no parameters and
  nothing else parameterizes it; none of the three supporting symbols exist on main. Removed.

---

## Negative-constraints pass

| Constraint | Honoring task | Violation check |
|---|---|---|
| No dismissed-folder JSONB on `user_preferences` | — | No task adds a column or migration for it |
| No `import_source` changes | T1, T2 | `import_source.is_(None)` filter preserved verbatim |
| No URL changes to either endpoint | T3 | Only the query parameter changed; paths constant |
| No changes to `DELETE /conversations/folder/{folder:path}` | — | No task touches that handler |
| No changes to `UserPreferences`, `ConversationFolder`, `WorkflowExecution` | T1 | Cascades invoked, not modified |
| `remove_conversations=false` is a no-op | T1 | Early return with empty response |
| Pydantic response models stay in `assistant_folder.py` | T2 | ORM class deleted; Pydantic classes untouched |
| Exactly one alembic head | T4 | Verified across all 184 revision files |
