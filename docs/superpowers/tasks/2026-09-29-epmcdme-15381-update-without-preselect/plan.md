# EPMCDME-15381 Conversation saves without full-row pre-read — Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** `Conversation.update()` sends one targeted `UPDATE conversations … WHERE id` of explicitly listed columns with no preceding `SELECT`.

**Architecture:** New `Conversation.update(*, columns, …)` override (Core `update()` on an engine connection, `rowcount` check, `set_committed_value` baseline reset, history-compat rule, mismatch detector). All 21 call sites pass `columns=`. Base `update()` is untouched.

**Tech Stack:** SQLModel/SQLAlchemy 2, PostgreSQL (SQLite in tests), pytest.

**Spec:** `docs/superpowers/tasks/2026-09-29-epmcdme-15381-update-without-preselect/spec.md`. Per-site column table and test harness: `docs/01-update-without-preselect.md` §3 and §4. Codebase notes: `technical-analysis.md` in the same folder.

Commit per task using the repository's existing convention (ticket EPMCDME-15381).

## Global Constraints

- `columns` is a required keyword-only argument. There is no fallback to `session.get` + `merge`.
- Each site keeps its current `touch_timestamp` value. Only sites 8 and 9 use `touch_timestamp=False`.
- Deleted row raises `StaleDataError(f"Record {self.id} has been deleted")`, with the same message as today.
- The only SELECT `update()` may issue is `Conversation.exists` (id-only), and only when the written set is empty.
- The written columns go through the existing type decorators (Core `update`, never raw `text()`).
- Line numbers in the design doc may have drifted. Locate each site by its function name.

## Review Focus

- Site 9 with an empty `fields_set` on a deleted row still raises `StaleDataError` (Task 5).
- In-place `history` edits (abort, sites 3/10/12) persist even though change tracking does not see them (Tasks 3, 6, 9).
- Calling `update()` twice on the same instance writes only the second call's columns (Task 3, test c).
- A workflow conversation renamed by name only still writes back its materialized history, and an ordinary chat does not write history (Task 2, tests a and b).
- `None` on JSONB (`clear`, site 17) stores JSON null, as it does today (Task 7).

Note: from Task 2 until Task 9, sites that have not been migrated yet raise `TypeError` (missing `columns`). The tests of each site go green in the task that migrates that site.

---

### Task 1: Re-verify unconfirmed call sites and the bulk move-to-folder bypass

**Files:** read-only. `src/codemie/rest_api/routers/conversation.py`, `src/codemie/service/conversation_service.py`, `src/codemie/service/workflow_service.py`, `src/codemie/rest_api/routers/feedback.py`

Test-first: no — this task only reads code. It confirms facts that Tasks 3–9 depend on.

- [ ] Grep `\.update\(` on `Conversation` instances in `src/`. Confirm that there are exactly 21 sites. Confirm the loader and mutation for the 8 sites research did not see: abort (`routers/conversation.py` ~341), `upsert_conversation_with_history` (~531), import_source (~557), `remove_feedback` (~901), `delete_conversation_folder` (~1150), `update_conversation_folder` (~1181), `clear_conversation_history` (~1294), `workflow_service.py` (~312), `routers/feedback.py` (~141). Also confirm that the column list for each site matches design doc §3.
- [ ] Find the bulk "move to folder" endpoint. Confirm that it uses raw SQL and does not call `Conversation.update()`.
- [ ] If there is a 22nd site, or a site mutates a column the doc does not list, stop and report it. Do not guess a column list.

### Task 2: `Conversation.update()` override, SQLite harness

**Files:**
- Modify: `src/codemie/rest_api/models/conversation.py` (add the override next to `get_by_id` ~500–550)
- Create: `tests/codemie/conftest.py` (shared harness, used by tests under `service/` and `rest_api/`)
- Test: `tests/codemie/rest_api/models/test_conversation_update.py`

**Interfaces:**
- Produces: `Conversation.update(self, *, columns: Sequence[str], refresh: bool = False, validate: bool = True, touch_timestamp: bool = True) -> PostResponse`. The signature intentionally differs from the base method, so add a targeted `# type: ignore[override]` with a comment. Do not add `where_extra`.
- Produces fixtures: `conversation_sqlite_engine` (SQLite engine with `Conversation.__table__` created; patches `Conversation.get_engine` / `PostgresClient.get_engine`) and `conversation_update_sql` (a list of per-call statement lists, recorded only while `Conversation.update` runs).

Test-first: yes — the new model tests fail because `update()` still runs `session.get`. They cover: 1 UPDATE and 0 SELECT; deleted row raises `StaleDataError`; empty set on a deleted row raises through an id-only SELECT; no identity raises; `touch_timestamp=False` leaves `update_date` unchanged; change, save, revert on one instance persists; a JSONB `None` round-trips; history-compat tests (a) and (b); the detector calls `logger.warning` for a changed column that is not listed and still writes the listed columns.

- [ ] Write the harness. Model it on `tests/codemie/rest_api/models/test_base_model_update_sqlite.py`. Take the JSONB hook from `tests/codemie/core/workflow_models/test_workflow_config.py:45`:

```python
@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"

@pytest.fixture
def conversation_update_sql(conversation_sqlite_engine, monkeypatch):
    calls: list[list[str]] = []
    active: list[list[str]] = []

    def _listener(conn, cursor, statement, *a):
        if active:
            active[-1].append(statement)

    event.listen(conversation_sqlite_engine, "before_cursor_execute", _listener)
    original = Conversation.update

    def _wrapped(self, *args, **kwargs):
        active.append([])
        try:
            return original(self, *args, **kwargs)
        finally:
            calls.append(active.pop())

    monkeypatch.setattr(Conversation, "update", _wrapped)
    yield calls
    event.remove(conversation_sqlite_engine, "before_cursor_execute", _listener)
```

  Seed rows with `save()` or a raw insert. For instances built directly, call `make_transient_to_detached(conv)`.
- [ ] Write the model tests listed above. For (a), load a conversation through `get_by_id` with `materialize_workflow_conversation` patched to return different history. Run them and confirm they fail.
- [ ] Implement the override in the order given in spec §Design 1–6: `touch_timestamp` adds `update_date`; the history-compat rule adds `history` when `inspect(self).attrs["history"].history.has_changes()` (add a comment that ticket 04 removes this); `validate_fields()`; the `has_identity` guard; the mismatch detector logs a warning for changed columns that are not written; if the written set is empty, raise when `not Conversation.exists(self.id)`; otherwise `with self.get_engine().begin() as conn: rowcount = conn.execute(sa_update(Conversation).where(Conversation.id == self.id).values({c: getattr(self, c) for c in written})).rowcount`; raise when `rowcount == 0`; call `set_committed_value(self, c, getattr(self, c))` for each written column; return `PostResponse(id=self.id)`.
- [ ] Run `pytest tests/codemie/rest_api/models/test_conversation_update.py` and confirm it passes.

### Task 3: conversation_service upsert paths (sites 2, 3, 4)

**Files:** Modify `src/codemie/service/conversation_service.py`: `upsert_chat_history` (~470), `upsert_conversation_with_history` (~531), import_source (~557). Test: `tests/codemie/service/test_conversation_update_sites.py` (create), `tests/codemie/service/test_upsert_conversation_with_history.py`, `tests/codemie/service/test_conversation_service.py`.

Test-first: yes — per-site tests on the SQLite harness assert exactly 1 UPDATE and 0 SELECT per `update()` call, that the listed columns persisted, and that unlisted columns are unchanged. Behaviour test (c): sites 3 then 4 on the same instance, and the second UPDATE contains only `import_source` and `update_date`. Behaviour test (g): an in-place append at site 3 persists.

- [ ] Write the tests. Run the real site functions and mock only unrelated collaborators. Confirm they fail with `TypeError`.
- [ ] Pass `columns=["conversation_name", "history", "project", "assistant_ids", "initial_assistant_id"]` at site 2 (always, and do not narrow it), `["history", "assistant_ids"]` at site 3, and `["import_source"]` at site 4. Update the existing mock assertions in the two test files that assert the old call shape. Run the tests and confirm they pass.

### Task 4: Feedback and folder sites (5, 6, 7, 8)

**Files:** Modify `conversation_service.py`: `add_feedback` (~870), `remove_feedback` (~901), `delete_conversation_folder` (~1150), `update_conversation_folder` (~1181). Test: `tests/codemie/service/test_conversation_update_sites.py`, `test_conversation_service.py` (old `assert_called_once_with(refresh=True, touch_timestamp=False)` ~:193).

Test-first: yes — per-site tests assert 1 UPDATE and 0 SELECT. Adding then removing feedback persists `user_mark` in `history` (AC3a). Site 7 clears `folder` and bumps `update_date` (AC1b). Site 8 persists `folder` and leaves `update_date` unchanged (AC1a).

- [ ] Write the tests and confirm they fail. Pass `columns=["history"]` at sites 5 and 6, and `["folder"]` at sites 7 and 8. Keep the existing `refresh=` and `touch_timestamp=` arguments. Update the old assertions. Run the tests and confirm they pass.

### Task 5: `update_conversation` per-branch columns (site 9)

**Files:** Modify `conversation_service.py:1235-1262`. Test: `test_conversation_update_sites.py`, `test_conversation_service.py` (old `assert_called_once_with(touch_timestamp=False)` ~:146).

Test-first: yes — one parametrized test per branch (name, llm_model, enable_image_generation, image_generation_model, pinned, folder, tool_call_policy, assistant_ids). Each asserts that only that column and no `update_date` is written. An empty request on a deleted row raises `StaleDataError` with 0 UPDATE and 1 id-only SELECT. Also: a workflow conversation renamed by name only writes back its materialized history (AC6).

- [ ] Write the tests and confirm they fail. Initialize `columns: list[str] = []`, append the column name inside each existing `if` branch next to its assignment (do not restate the conditions), and call `update(columns=columns, touch_timestamp=False)`. Update the old assertions. Run the tests and confirm they pass.

### Task 6: History-edit sites (10, 11, 12)

**Files:** Modify `conversation_service.py`: `remove_conversation_history_index` (~1279), `clear_conversation_history` (~1294), `update_conversation_ai_message` (~1320). Test: `test_conversation_update_sites.py`.

Test-first: yes — per-site tests. Site 10 persists the in-place `history_index` decrement. Site 12 persists in-place `history.append`s (behaviour test g). Site 11 persists the cleared history.

- [ ] Write the tests and confirm they fail. Pass `columns=["history", "assistant_ids", "initial_assistant_id"]` at sites 10 and 11, and `["history"]` at site 12. Leave the in-place mutation code and the `is not` int check unchanged. Run the tests and confirm they pass.

### Task 7: Checkpoint service (sites 14–17)

**Files:** Modify `src/codemie/service/conversation_checkpoint_service.py:37,42,74,80`. Test: `test_conversation_update_sites.py`, `tests/codemie/service/test_conversation_checkpoint_service.py`.

Test-first: yes — per-site tests. Behaviour test (f): `save_interrupt_context` persists `history_index` and `original_user_message`. `clear` stores JSON null in both columns and does not touch `history`.

- [ ] Write the tests and confirm they fail. Pass `["pending_checkpoint"]` at site 14, `["pending_tool_call"]` at sites 15 and 16 (leave the in-place dict code unchanged), and `["pending_checkpoint", "pending_tool_call"]` at site 17. Update the existing assertions. Run the tests and confirm they pass.

### Task 8: Chat naming and workflow service (sites 13, 18, 19)

**Files:** Modify `src/codemie/service/chat_naming_service.py:60` and `src/codemie/service/workflow_service.py` (~312, :572). Test: `test_conversation_update_sites.py`, `tests/codemie/service/test_chat_naming_service.py`, `tests/codemie/service/test_workflow_service.py`.

Test-first: yes — per-site tests. Site 13 writes only `conversation_name` and `update_date` for an ordinary chat. Sites 18 and 19 persist `history`. Assert on the SQL issued by `Conversation.update` only, not on `WorkflowExecution.update`.

- [ ] Write the tests and confirm they fail. Pass `["conversation_name"]` at site 13 and `["history"]` at sites 18 and 19. Update the existing assertions. Run the tests and confirm they pass.

### Task 9: Router sites (1 abort, 20 feedback, 21 admin)

**Files:** Modify `src/codemie/rest_api/routers/conversation.py` (~341), `routers/feedback.py` (~141), `routers/admin.py:117`. Test: `tests/codemie/rest_api/routers/test_conversation_update_router_sites.py` (create), `tests/codemie/rest_api/routers/test_assistant_refresh_flow.py`.

Test-first: yes — behaviour test (e): the abort route persists INTERRUPTED/`in_progress` on the last message without changing the in-place edit. Assert on the stored row, because the route swallows exceptions. Site 20 persists `final_user_mark` (AC3b). Site 21 persists `final_operator_mark` (AC3c). Each call issues 1 UPDATE and 0 SELECT.

- [ ] Write the tests and confirm they fail. Pass `["history"]` at site 1, `["final_user_mark"]` at site 20, and `["final_operator_mark"]` at site 21. Update `test_abort_endpoint_success` if it asserts the call shape. Run the tests and confirm they pass.

### Task 10: Remaining legacy test fixtures

**Files:** `tests/codemie/rest_api/models/test_conversation_model.py`, `test_conversation_models.py`, and any other test found by `grep -l "Conversation" tests | xargs grep -l "update"` that calls the real `Conversation.update()` or asserts its old signature.

Test-first: no — this task only adapts tests to the new contract. It adds no behaviour.

- [ ] Add `columns=` to direct calls. Add `make_transient_to_detached(conv)` where a directly built `Conversation` hits the identity guard. Leave `test_base_model_update_*.py` unchanged, because the base `update()` does not change. Run the edited files and confirm they pass.
