# Spec — EPMCDME-15381: Conversation saves without a full-row pre-read

**Baseline:** branch `EPMCDME-15256_sql-update-double-select-workflow-stuck`. **Size:** L (21/36).
**Design source:** `docs/01-update-without-preselect.md` (approach "B"), which is the authoritative source for the per-site column lists and the test harness. This spec does not repeat that material.

## Problem

`BaseModelWithSQLSupport.update()` (`src/codemie/rest_api/models/base.py:518-532`) calls `session.get` on the whole row, including the large `history` JSONB, and then `session.merge` before every write. `Conversation` inherits this. Nearly every chat action saves a conversation, so this read happens all the time and gets more expensive as history grows.

## Goal

Every `Conversation.update()` call sends exactly one targeted `UPDATE conversations … WHERE id = :id` and does not `SELECT` the row first. There is one exception. When nothing is to be written (`touch_timestamp=False` and no columns, for example a PUT with no fields), the call sends 0 `UPDATE` and 1 id-only `SELECT` so a missing row is still detected. `save()` for new rows (INSERT) does not change. User-visible behaviour stays the same.

## Design

1. **`Conversation.update()` override** in `src/codemie/rest_api/models/conversation.py`, next to `get_by_id`. It takes a required keyword-only `columns` argument. It also accepts `refresh`, `validate` and `touch_timestamp`, and ignores `refresh` as today. It returns `PostResponse`. The signature intentionally does not match the base method. Use a targeted type-checker ignore with a comment. The base `update()` does not change.
   ```python
   def update(self, *, columns: Sequence[str], refresh: bool = False,
              validate: bool = True, touch_timestamp: bool = True) -> PostResponse
   ```
2. **Written set:** the columns listed in `columns`, plus `update_date` when `touch_timestamp` is true. `history` is also added when attribute history shows it was re-assigned since load. This history-compat rule keeps today's write-back of materialized workflow history, which ticket 04 will remove. The code comment should reference ticket 04.
3. **Write:** one SQLAlchemy Core `update(Conversation)` with every written column set to its current in-memory value, whether or not change tracking saw it change. The write runs on an engine connection or with `synchronize_session=False`, and it goes through the existing type decorators. `validate_fields()` runs first, as it does today.
4. **Errors:** calling update on an instance with no DB identity raises a clear error. `rowcount == 0` raises `StaleDataError(f"Record {id} has been deleted")`. If the written set is empty, the method skips the UPDATE and still raises for a missing row, using `Conversation.exists` (`conversation.py:546-550`), which reads only the id. This is the only case where `update()` issues a SELECT.
5. **Baseline reset:** after a successful write, call `set_committed_value` on each written column using the existing object. This makes a second `update()` on the same instance diff correctly.
6. **Mismatch detector:** if a column shows as changed in attribute history but is not written, the method logs a warning.
7. **Call-site migration:** all 21 sites listed in design doc §3 pass their column lists in this one change, and each keeps its current `touch_timestamp` value. Site 9 (`update_conversation`) appends each column inside the `if` branch that mutates it. Sites that mutate in place (abort, `save_interrupt_context`, history appends) keep their current code.

## Acceptance criteria

- AC1a: A PUT rename, pin/unpin or folder move (site 9) and a folder rename (site 8, `update_conversation_folder`) persist their values and leave `update_date` unchanged, so sidebar order does not change.
- AC1b: Deleting a folder without deleting its chats (site 7, `delete_conversation_folder`) clears `folder` and still bumps `update_date`, as it does today.
- AC1c: Every other site that uses `touch_timestamp=True` still bumps `update_date`. For example, after a chat turn the conversation still moves to the top of the sidebar.
- AC2: After a chat turn, the stored history is identical to what the old path stored.
- AC3a: Adding and then removing thumbs feedback on a message (sites 5/6) persists correctly in `history`.
- AC3b: Conversation-level final user feedback (site 20, `routers/feedback.py`) persists `final_user_mark`.
- AC3c: Admin operator feedback (site 21, `routers/admin.py`) persists `final_operator_mark`.
- AC4: Aborting a generation persists the INTERRUPTED status of the last message.
- AC5: With tool-call confirmation, `pending_checkpoint` and `pending_tool_call` (including `history_index`) persist and can be cleared.
- AC6: A name-only update of a workflow conversation still writes back the materialized history, and an ordinary chat's history is not written.
- AC7: `update()` on a deleted row raises `StaleDataError` with the same message, including when the written set is empty.
- AC8: For each of the 21 sites, the `update()` call sends exactly 1 `UPDATE` and 0 `SELECT`. The exception is an empty written set, which sends 0 `UPDATE` and 1 id-only `SELECT` (`SELECT id … WHERE id = :id`), never a full-row read. This is checked per site on a real SQLite `conversations` table (JSONB compile hook in a shared `conftest.py`).
- Behaviour tests (a)–(g) and the detector test from design doc §4 pass. Existing tests that assert the old `update` signature or `merge` are updated. `make ruff` and the test suite pass.

## Non-goals

- No schema changes or Alembic migrations.
- Workflow materialized-history write-back is kept as is (ticket 04 removes it).
- `conversation_name` is not narrowed at `upsert_chat_history`; it is still always written (ticket 08).
- No `where_extra` / conditional-update hook (tickets 08/15).
- `BaseModelWithSQLSupport.update()`, `save()` and the other models do not change.
- The bulk "move to folder" endpoint (raw SQL, does not call `update()`) does not change.
- No change to which sites bump `update_date`.
- No UI or API contract changes, and no refactor of in-place mutation code (for example, the pre-existing `is not` int check at site 10).
- No `sql_tags` wrapper. Branch 15344 adds it if it merges later.

## Deliberate difference

Columns a site does not list are no longer overwritten with stale in-memory values (for example `finished_at` or `pending_*` during concurrent saves). This should be called out in the PR.
