# Technical Research

**Task**: conversation persistence repository update
**Generated**: 2026-09-29T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

"Review https://jiraeu.epam.com/browse/EPMCDME-15381 and implement. Use docs/01-update-without-preselect.md for additional technical details. Create a new branch from this branch for now (this feature would depend on the current branch)."

Caller-supplied context: Jira ticket body saved at `/private/tmp/claude-501/-Users-bohdan-maliar-Projects-codemie-dev-codemie/d9229fef-5344-4bcb-8a20-d35760e0292f/scratchpad/ticket.md` (summary: "Remove Redundant Full-Row Read Before Conversation Saves"); technical details in `docs/01-update-without-preselect.md`; parent branch `EPMCDME-15256_sql-update-double-select-workflow-stuck` is prior art. Both files are summarised in Section 8.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/models/base.py:518-532` — `BaseModelWithSQLSupport.update(self, refresh=False, validate=True, touch_timestamp=True)`. Current body, as it stands on the 15256 baseline: sets `update_date` if `touch_timestamp`, runs `validate_fields()` (raises `ValueError`), then in one `Session`: `existing = session.get(type(self), self.id)` (the full-row SELECT the ticket targets), `raise StaleDataError(f"Record {self.id} has been deleted")` if None, `session.merge(self)`, `session.commit()`, returns `PostResponse(id=self.id)`. `refresh` is accepted and ignored. `StaleDataError` is imported from `sqlalchemy.orm.exc` (`base.py:32`).
- `src/codemie/rest_api/models/base.py:500-516` — `save()` (add + commit + refresh). Used for new rows (for example `conversation_service.py:467-468` when `should_create_conversation`).
- `src/codemie/rest_api/models/base.py:242-293` — `PydanticType` (`impl = JSONB`, `process_bind_param` dumps a Pydantic model and strips null bytes; `None` passes through as `None`). `PydanticListType` starts at `:295`; `MutableList.associate_with(PydanticListType)` at `:367`.
- `src/codemie/rest_api/models/conversation.py:247-304` — `Conversation(BaseModelWithSQLSupport, Owned, table=True)`, table `conversations`. Relevant columns: `history` (`Column(PydanticListType(GeneratedMessage))`, `:257-259`), `assistant_ids` (`MutableList.as_mutable(JSONB)`, `:262`), `assistant_data`, `initial_assistant_id`, `final_user_mark` (`PydanticType(UserMark)`), `final_operator_mark` (`PydanticType(FinalOperatorFeedback)`), `project`, `folder`, `pinned`, `conversation_name`, `llm_model`, `enable_image_generation`, `image_generation_model`, `finished_at`, `import_source` (`:300`), `pending_checkpoint` / `pending_tool_call` (plain `JSONB`, `:302-303`), `tool_call_policy` (`String`, `:304`).
- `src/codemie/rest_api/models/conversation.py:500-523` — `Conversation.get_by_id` override: `super().get_by_id` then `conversation.history = materialize_workflow_conversation(...).history` (attribute re-assignment after load).
- `src/codemie/rest_api/models/conversation.py:525-544` — `Conversation.find_by_id` override, same materialization.
- `src/codemie/rest_api/models/conversation.py:546-550` — `Conversation.exists(id_)`: `select(cls.id).where(cls.id == id_)`, id-only query.
- No `update()` override exists on `Conversation` in this branch (codegraph dispatch list for `update` shows 49 implementers of `BaseModelWithSQLSupport`; `Conversation` inherits the base one).
- `src/codemie/rest_api/models/assistant.py:1410` — a precedent for a subclass overriding `update` with its own signature (virtual IDE assistant, no-op).

Call sites of `conversation.update()` confirmed by codegraph in this research (current line numbers):

| Site | File:line | Load path | Mutation before `update()` |
|---|---|---|---|
| add_feedback | `src/codemie/service/conversation_service.py:870` | `Conversation.get_by_id` (materialized) | `history` re-assigned to a deep copy with `user_mark` set |
| update_conversation (PUT rename/pin/folder/model) | `conversation_service.py:1262` | router `get_by_id` (`routers/conversation.py:738`) | per-branch: `conversation_name` (truthy `request.name`), `llm_model`/`enable_image_generation`/`image_generation_model`/`tool_call_policy` (`in fields_set`), `pinned`/`folder` (`is not None`), `assistant_ids` (re-assigned list when `active_assistant_id` in list); `update(touch_timestamp=False)` |
| remove_conversation_history_index | `conversation_service.py:1279` | router `get_by_id` (`routers/conversation.py:493`) | `history` re-assigned (filtered), then in-place `history_index` decrement; `update_conversation_assistants()` |
| update_conversation_ai_message | `conversation_service.py:1320` | router `get_by_id` (`routers/conversation.py:537`) | in-place `conversation.history.append(...)` twice |
| ChatNamingService.rename_conversation | `src/codemie/service/chat_naming_service.py:60` | `Conversation.find_by_id` (materialized) | `conversation_name`; exceptions swallowed + logged |
| save_checkpoint | `src/codemie/service/conversation_checkpoint_service.py:37` | `Conversation.find_by_conversation_id` | `pending_checkpoint` |
| save_pending_tool_call | `conversation_checkpoint_service.py:42` | same | `pending_tool_call = tool_call.model_dump()` |
| save_interrupt_context | `conversation_checkpoint_service.py:74` | same | in-place dict mutation of `pending_tool_call`, then re-assign same object |
| clear | `conversation_checkpoint_service.py:80` | same | `pending_checkpoint = None`, `pending_tool_call = None` |
| append_user_message_on_resume | `src/codemie/service/workflow_service.py:572` | `Conversation.get_by_id` | `history` re-assigned to new list |
| admin final operator feedback | `src/codemie/rest_api/routers/admin.py:117` | (load above `:109`, not shown) | `final_operator_mark` |
| upsert_chat_history | `conversation_service.py:413-468` (update call below `:468`, not shown) | `_find_or_create_conversation` -> `Conversation.find_by_id` (`:212`) | `update_chat_history(...)`, `update_conversation_assistants(...)`, possibly `conversation_name` (`:244`) |

Sites listed in `docs/01-update-without-preselect.md` but not surfaced in this research's codegraph output (line numbers are from the doc, not re-verified here): abort route `routers/conversation.py:341`; `upsert_conversation_with_history` `:531` and import_source `:557`; `remove_feedback` `:901`; `delete_conversation_folder` `:1150`; `update_conversation_folder` `:1181`; `clear_conversation_history` `:1294`; `workflow_service.py:312`; `routers/feedback.py:141`.

### Architecture and Layers Affected

- **Model / persistence layer**: `BaseModelWithSQLSupport` (`base.py`) and `Conversation` (`conversation.py`) — the `update()` contract lives here.
- **Service layer**: `ConversationService` (`conversation_service.py`), `ConversationCheckpointService`, `ChatNamingService`, `WorkflowService`.
- **API/router layer**: `routers/conversation.py` (PUT, history delete/update, abort), `routers/feedback.py`, `routers/admin.py`; `rest_api/handlers/assistant_handlers.py` drives `upsert_chat_history` (18 callers) and tool-call resume.
- **Tool-confirmation / agents layer**: `agents/tool_confirmation/conversation_checkpoint_saver.py` (calls `save_checkpoint`), `tool_call_confirmation_mixin.py` (calls `save_pending_tool_call`).
- **Configuration**: `src/codemie/configs/config.py` (`Config`, pydantic settings).

### Integration Points

- PostgreSQL via `PostgresClient.get_engine()` (`base.py:374-375`), SQLModel `Session`.
- `codemie.service.conversation.history_materializer.materialize_workflow_conversation` — invoked on every `Conversation.get_by_id`/`find_by_id`; resolves workflow execution references into history content.
- `ConversationMetrics.update()` is called next to `conversation.update()` in `add_feedback` (`:871`) — it is a separate model on the base `update()`.
- `WorkflowExecution.update(refresh=True)` in `append_user_message_on_resume` (`workflow_service.py:579`) — separate model on the base `update()`.
- `triggers/actors/conversation.py:60-89` — internal HTTP client calling `PUT /v1/conversations/{id}`; unaffected at code level but exercises site `update_conversation`.

### Patterns and Conventions

- Active-record style: models carry `save()`/`update()`/`delete()`; services mutate the instance, then call `update()`.
- Targeted UPDATE prior art without read-back:
  - `src/codemie/repository/workflow_config_repository.py:28-62` — `engine.begin()` + `conn.execute(text("UPDATE workflows ..."))`, `rowcount == 0` -> `NotFoundException`.
  - `conversation_service.py:28-29` already imports `from sqlalchemy import update as sa_update` and `bindparam` (a Core `update` is used elsewhere in the same module).
- Change-tracking caveat observed in code: `add_feedback` comments "Force SQLAlchemy to detect the change by reassigning the entire list" (`:843-850`) — the codebase already relies on re-assignment for `history` change detection.
- `update_conversation` documents the `touch_timestamp=False` rule inline (`:1258-1261`): rename/pin/folder moves must not bump `update_date` (sidebar recency sort).

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/data/database-patterns.md` — prefer SQLModel/SQLAlchemy expressions over raw SQL string interpolation; schema changes via Alembic in `src/external/alembic/versions/`.
- `.ai-run/guides/development/configuration-patterns.md` — add config values centrally in `src/codemie/configs/`; do not read env vars directly in feature code; documents `CHAT_CONTEXTUAL_NAMING_ENABLED` (relevant to `ChatNamingService` site).
- Other relevant guides present but not opened: `.ai-run/guides/data/repository-patterns.md`, `.ai-run/guides/testing/testing-patterns.md`, `.ai-run/guides/testing/testing-service-patterns.md`, `.ai-run/guides/quality-gates.md`, `.ai-run/guides/development/error-handling.md`.
- `docs/01-update-without-preselect.md` — the design document for this ticket (see Section 8).

### Architectural Decisions

- Recorded in `docs/01-update-without-preselect.md`: approach "B" (explicit `columns=`), no fallback to `session.get` + `merge`, history-compat rule preserving workflow materialized-history write-back (removal deferred to "ticket 04"), `where_extra` hook reserved for "ticket 08".
- Inline decision: `update_conversation` must not touch `update_date` (`conversation_service.py:1258-1261`).

### Derived Conventions

- `StaleDataError(f"Record {self.id} has been deleted")` is the established deleted-row signal from `update()` (introduced on the parent 15256 branch).
- Test env: `tests/conftest.py:41` loads `tests/.env.test` with `override=True` before any codemie import; the session-scoped autouse fixture `mock_database_engine` (`:44-59`) patches `PostgresClient.get_engine` with a `MagicMock`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/models/test_base_model_update_sqlite.py` — real in-memory SQLite engine, private test model `_UpdateSqliteTestModel` using `JSONB().with_variant(JSON(), "sqlite")`; `before_cursor_execute` listener captures SELECTs (`test_update_issues_exactly_one_select` asserts exactly one SELECT); deleted-row `StaleDataError`; mutable JSON round-trip; concurrent-delete race. It exercises the base `update()`, not `Conversation`.
- `tests/codemie/rest_api/models/test_base_model_update_deleted.py` — fully mocked `Session`; asserts `mock_session.merge.assert_called_once_with(model)` and `merge` not called on deleted row.
- `tests/codemie/rest_api/models/test_conversation_model.py`, `tests/codemie/rest_api/models/test_conversation_models.py` — conversation model tests (contents not inspected).
- `tests/codemie/service/test_conversation_service.py` — covers `update_conversation`, `update_conversation_ai_message` (incl. 409 when finished), `upsert_chat_history`; per the doc it asserts old call signatures such as `assert_called_once_with(touch_timestamp=False)`.
- `tests/codemie/service/test_conversation_checkpoint_service.py` — `test_save_checkpoint_writes_to_db`, `test_save_pending_tool_call_writes_to_db`, not-found case.
- `tests/codemie/service/test_chat_naming_service.py` — ChatNamingService.
- `tests/codemie/service/test_upsert_conversation_with_history.py` — upsert with history.
- `tests/codemie/service/test_workflow_service.py` — `append_user_message_on_resume` (5 tests incl. "also_updates_conversation" and 409 when finished).
- `tests/codemie/rest_api/routers/test_assistant_refresh_flow.py` — abort endpoint (`test_abort_endpoint_success`), `save_interrupt_context` via callers.
- `tests/codemie/core/workflow_models/test_workflow_config.py` — per the doc, contains a `@compiles(JSONB, "sqlite")` hook at `:45` (not viewed in this research).

### Testing Framework and Patterns

- pytest with `unittest.mock` (`patch`, `MagicMock`), `pytest.mark.asyncio` for async repositories.
- Two styles coexist: mocked `Session` (`test_base_model_update_deleted.py`) and real SQLite engine with `patch.object(Model, "get_engine", return_value=sqlite_engine)` (`test_base_model_update_sqlite.py`).
- SQL statement capture via `sqlalchemy.event.listen(engine, "before_cursor_execute", ...)`.

### Coverage Gaps

- No codegraph-reported tests for `add_feedback` ("no tests found within 3 caller hops") nor for `routers/admin.py` final operator feedback.
- No existing test creates the real `conversations` table on SQLite; no test observes query count for any `Conversation.update()` call site.
- No test covering the workflow-conversation materialized-history write-back on `update()`.

---

## 5. Configuration and Environment

### Environment Variables

- `tests/.env.test` exists and is loaded by `tests/conftest.py:41` with `override=True`.
- `CHAT_CONTEXTUAL_NAMING_ENABLED` / `CHAT_CONTEXTUAL_NAMING_LLM_MODEL` gate the `ChatNamingService` save path (`chat_naming_service.py:29-33`).
- No existing env var governs `Conversation.update()` behaviour.

### Configuration Files

- `src/codemie/configs/config.py` — central `Config` (pydantic settings), per the configuration guide.
- No config file governs the conversations table persistence path.

### Feature Flags and Deployment Concerns

- None found around conversation persistence. The ticket's Out of Scope excludes schema changes and migrations.

---

## 6. Risk Indicators

- Silent lost writes: after dropping `merge`, any column mutated but not written is lost without error. In-place mutations that change tracking does not see exist at `update_conversation_ai_message` (`history.append`, `conversation_service.py:1317-1318`), `remove_conversation_history_index` (in-place `history_index` decrement, `:1274-1276`), `save_interrupt_context` (in-place dict, `conversation_checkpoint_service.py:71-73`) and, per the doc, the abort route (`routers/conversation.py:337-341`).
- `history` is `PydanticListType` without `MutableList` (`conversation.py:257-259`); only `assistant_ids` is `MutableList` (`:262`). Attribute history cannot detect in-place `history` edits.
- Workflow-conversation parity: `Conversation.get_by_id`/`find_by_id` re-assign `history` after load (`conversation.py:522`, `:543`), so today's `merge` writes materialized history back on any update. Name-only sites (`chat_naming_service.py:60`, `update_conversation`) depend on this implicitly.
- `update_conversation` (`conversation_service.py:1235-1262`) has 8 independently-guarded mutations with mixed guard styles; it is the site where a per-branch mistake is most likely.
- Test churn: `test_base_model_update_deleted.py` asserts `merge` calls; `test_conversation_service.py` asserts `update` call signatures; the session-wide `MagicMock` engine (`tests/conftest.py:44-59`) means many tests never touch real SQL. The doc counts 20 test files mentioning both `Conversation` and `update`.
- No real-table SQLite fixture for `conversations` exists; JSONB on SQLite needs a compile hook (`test_workflow_config.py:45` per doc).
- Deleted-row semantics (AC 7) must keep `StaleDataError` for missing rows, including when nothing is written (`touch_timestamp=False` with no changed fields in `update_conversation`).
- 8 of the doc's 21 call sites were not surfaced in this research's codegraph output; their line numbers are unverified here.
- Speculative: a `Conversation.update()` override with a required keyword-only `columns` argument would be signature-incompatible with `BaseModelWithSQLSupport.update()` and may need a type-checker suppression; the doc proposes this.
- Sequencing: the doc notes branch `EPMCDME-15344_conversation-query-sql-tagging` adds a `sql_tags` wrapper around `Conversation.update`; merge order affects the override body.

---

## 7. Summary for Complexity Assessment

The change centres on one method, `BaseModelWithSQLSupport.update()` (`src/codemie/rest_api/models/base.py:518-532`), which on the current branch does one `session.get` (the full-row SELECT the ticket targets) followed by `session.merge` and `commit`. `Conversation` (`src/codemie/rest_api/models/conversation.py`) inherits it unchanged, and it is called from about 21 sites across the service layer (`conversation_service.py`, `conversation_checkpoint_service.py`, `chat_naming_service.py`, `workflow_service.py`) and the router layer (`routers/conversation.py`, `routers/feedback.py`, `routers/admin.py`). Codegraph confirmed 13 of these sites directly; the other 8 come from the design doc. The layers touched are the model/persistence layer, the service layer, and the router layer. No schema or migration is in scope.

The technical novelty is moderate. The codebase already has targeted UPDATEs without a read-back: `WorkflowConfigRepository` uses `engine.begin()` plus a `rowcount` check, and `conversation_service.py` imports `sqlalchemy.update`. However, `Conversation` depends on implicit `merge` diffing in two ways. First, `history` is a non-mutable `PydanticListType`, so in-place edits at several sites are not tracked. Second, `get_by_id`/`find_by_id` re-assign materialized workflow history after load, which today causes an implicit write-back. Keeping behaviour identical means reproducing both effects on purpose. The design doc (`docs/01-update-without-preselect.md`) is detailed and already fixes the approach, the per-site column lists and the test strategy, so requirements are clear.

Test coverage is mixed. There is a real-SQLite pattern for `update()` (`test_base_model_update_sqlite.py`) and service tests for most sites, but they run on a session-wide `MagicMock` engine and many assert the old `update()` call signatures or `merge` calls, so a meaningful number of test files will change. No fixture builds the real `conversations` table on SQLite, and `add_feedback` and the admin feedback route have no tests close to them. The main risks are silent lost writes from wrong column lists, parity for workflow-conversation history, the 8-branch `update_conversation` site, and churn across roughly 20 test files.

---

## 8. External References

**1. `/private/tmp/claude-501/-Users-bohdan-maliar-Projects-codemie-dev-codemie/d9229fef-5344-4bcb-8a20-d35760e0292f/scratchpad/ticket.md`** — resolved.
- Key EPMCDME-15381, "Remove Redundant Full-Row Read Before Conversation Saves", status In Progress, no linked issues. The Jira AC field is empty; the ACs are in the description.
- AC1 rename/pin/folder move persists after reload, sidebar ordering unchanged. AC2 sending a message keeps the full history exactly as before. AC3 feedback add then remove is reflected after reload. AC4 an aborted generation shows the last message as interrupted. AC5 with tool-call confirmation, the pending call and its history position survive reload and resume. AC6 a renamed workflow conversation's stored content is identical to before. AC7 delete in one tab plus rename in another: no crash, same error behaviour as before. AC8 no conversation save performs a full read of the row before writing, verifiable via DB query monitoring.
- Out of scope: DB schema changes and migrations; removing materialized workflow history write-back (separate work needing product sign-off); narrowing chat-name re-save during concurrent renames; any UI change.
- Ticket's own complexity: Size L, score 21/36, 5 SP, recommended flow sdlc-standard.

**2. `docs/01-update-without-preselect.md`** (repo root relative) — resolved.
- Baseline is the 15256 branch (commit `1ec3d1574` removed the second SELECT, so `update()` does 1 full-row SELECT here and 2 on `main`).
- Approach "B": a `Conversation.update(self, *, columns: Sequence[str], refresh=False, validate=True, touch_timestamp=True, where_extra=None) -> PostResponse` override. `columns` is required and keyword-only, and `refresh` is accepted but ignored. There is no fallback to `session.get` + `merge`, and the base `update()` stays unchanged for other models.
- Steps:
  1. `touch_timestamp` adds `update_date`.
  2. History-compat rule: add `history` when `inspect(self).attrs["history"].history.has_changes()`, to preserve workflow write-back until ticket 04.
  3. `validate_fields()` runs as today.
  4. Raise if `not inspect(self).has_identity`.
  5. Issue one Core `update(Conversation).where(Conversation.id == self.id).values({col: getattr(self, col)})`, run via `engine.begin()` or with `synchronize_session=False`.
  6. `rowcount == 0` raises `StaleDataError(f"Record {self.id} has been deleted")`.
  7. If the written set is empty, run `Conversation.exists(id)` and raise if the row is missing.
  8. After the write, `set_committed_value(self, col, getattr(self, col))` for each written column.
  9. Return `PostResponse(id=self.id)`.
  10. Mismatch detector: log a warning when a column shows as changed but is not written.
- `where_extra` is optional here; if added, it needs its own unit test.
- Per-site column lists (update_date is automatic; ts=F means `touch_timestamp=False`):
  1. abort `routers/conversation.py:341`: `history`
  2. `upsert_chat_history` `:470`: `conversation_name, history, project, assistant_ids, initial_assistant_id`
  3. `upsert_conversation_with_history` `:531`: `history, assistant_ids`
  4. import_source `:557`: `import_source`
  5. `add_feedback` `:870`: `history`
  6. `remove_feedback` `:901`: `history`
  7. `delete_conversation_folder` `:1150`: `folder`
  8. `update_conversation_folder` `:1181`: `folder`, ts=F
  9. `update_conversation` `:1262`: per-branch subset of `conversation_name, llm_model, enable_image_generation, image_generation_model, pinned, folder, tool_call_policy, assistant_ids` (may be empty), ts=F
  10. `remove_conversation_history_index` `:1279`: `history, assistant_ids, initial_assistant_id`
  11. `clear_conversation_history` `:1294`: same three columns
  12. `update_conversation_ai_message` `:1320`: `history`
  13. `chat_naming_service.py:60`: `conversation_name`
  14. `conversation_checkpoint_service.py:37`: `pending_checkpoint`
  15. `:42`: `pending_tool_call`
  16. `:74`: `pending_tool_call`
  17. `:80`: `pending_checkpoint, pending_tool_call`
  18. `workflow_service.py:312`: `history`
  19. `:572`: `history`
  20. `routers/feedback.py:141`: `final_user_mark`
  21. `routers/admin.py:117`: `final_operator_mark`
- In-place mutation sites (1, 3, 10, 12, 16) keep their code unchanged. Listing the column is enough because listed columns are written unconditionally. Do not "fix" the `is not` int comparison at site 10.
- Verified on Postgres 17: JSONB `None` stores JSON `'null'` on both paths, and `UPDATE` on a missing id gives `rowcount == 0`.
- Tests:
  - Build on the real `Conversation` table on SQLite, using the `@compiles(JSONB, "sqlite")` hook (from `test_workflow_config.py:45`) in a shared `conftest.py`.
  - Write one test per site, capturing statements only while `update()` runs, and assert exactly 1 `UPDATE` and 0 `SELECT`.
  - Use `make_transient_to_detached(conv)` for directly built instances.
  - Add behaviour tests (a)-(g): workflow write-back kept, ordinary chat not written, double update on the same instance, change-save-revert, abort persistence, interrupt-context persistence, in-place appends.
  - Test that the mismatch detector warns.
  - Update the old signature assertions in `test_conversation_service.py` (~`:146`, `:193`), `test_conversation_model.py` and `test_base_model*.py`.
- Sequencing: if branch 15344 (`sql_tags`) lands, wrap the override body in `with sql_tags(action="update")`.
- Deliberate improvement to mention in the PR: columns a site does not list are no longer overwritten with stale values.
