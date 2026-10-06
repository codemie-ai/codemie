# EPMCDME-12505 — Assistant survives deletion of an attached datasource

**Status:** Approved 2026-09-28 (option A, owner decision; product reply to Jira comment 15948973 still pending — revisit if the team objects).

## Problem

`AssistantService.build_agent` calls `check_context` (assistant_service.py L541/L943). If any datasource in `assistant.context` no longer exists, it raises `MissingContextException`; `_ask_assistant` / `_ask_virtual_assistant` wrap it with `_create_assistant_error`, which hard-codes HTTP 500. Deleting a datasource never touches the assistants that reference it (no FK, JSONB `context`), so the assistant stays broken until someone edits and re-saves it (`_filter_invalid_datasources` on update silently drops the stale reference).

The behaviour exists since the initial commit (`afc61fa12`); EPMCDME-12384 is correlation, not cause.

## Goal

An assistant whose attached datasource was deleted keeps working with the datasources that still exist. No 500 from any entry point that builds an agent.

## Decision (option A — tolerant init)

At agent build time, drop context entries whose `(repo_name, context_type)` no longer exists in the assistant's project, log a warning, and continue. The stored assistant is **not** modified.

Why not persist the cleanup: chat requests come from users who may not own the assistant (marketplace, shared projects), and versions snapshot `context` separately; an in-memory filter fixes every path (master record, `request.version`, rollback, sub-assistants) without write side effects. Owners still clean the record by editing and saving, as today.

Why no user-facing notice: the AC say "clear message **and/or** automatically detached". The assistant health check already reports `context_error` for a missing datasource, which is the owner-facing signal. A chat notice would be a new UI contract — out of scope.

## Design

1. **One existence check.** Move the body of `_filter_invalid_datasources` (routers/assistant.py L2855) into a single helper on the service layer, `AssistantService.drop_missing_context(assistant) -> list[Context]`: targeted query by project + names, keeps entries whose `(name, context_type)` exists, assigns a **new list** to `assistant.context`, returns the dropped entries. The router's `_filter_invalid_datasources` becomes a call to it (create/update behaviour unchanged).
2. **Init uses it.** In `build_agent`, replace `cls.check_context(assistant)` with `drop_missing_context`; when something was dropped, `logger.warning` with assistant id, project and dropped names. Because it runs before `ToolkitService.get_tools`, the CODE-datasource paths that crash on a missing index (`_get_code_fields` ToolException, `toolkit_settings_service` L206 `None.index_type`) are never reached with a deleted datasource.
3. **Remove the dead fail-fast path.** Delete `check_context`, `Assistant.get_deleted_context`, `MissingContextException` and the two `except MissingContextException` blocks in routers/assistant.py (L2344, L2450) — nothing else raises it.
4. Bedrock assistants keep their early return (they never ran the check).

Coverage by construction, since all of them go through `build_agent`: streaming, sync, background, resume, hedged, A2A, health check, sub-assistants (`AssistantFactory.build`), and chat with `request.version` (overlay is applied before `build_agent`).

## Out of scope

- Cleanup on datasource delete (option B) and version-snapshot cleanup.
- Tool-invoke API `tool_execution_service._get_context_tools` (separate endpoint, first context only) — note in the MR as a follow-up.
- Health check's own name-only existence check (assistant_health_check_service.py L146).
- Any UI change.

## Error handling

- Query failure inside `drop_missing_context` propagates as today (a DB error is a real 500).
- All contexts deleted → assistant runs with no datasource tools; not an error.

## Testing (TDD)

- **Regression first:** `build_agent` for an assistant with one existing KB context and one deleted CODE context → currently raises; after the fix builds, and `ToolkitService.get_tools` receives only the existing context.
- `drop_missing_context` unit tests: none/empty context; all present; KB / CODE / PROVIDER missing; same name but different type counts as missing; returns dropped entries; does not call `assistant.update()`.
- Router: `_filter_invalid_datasources` on create/update still filters (first tests for it).
- Replace the three `test_assistant_service_check_context*.py` files; update `test_assistant_service_headers.py` patches.
- EPMCDME-12384 tests stay green: `test_search_kb.py`, `test_code_tools_health.py`, `test_index_health_fields.py`, `test_tool_execution_search.py`.
- Run with `LC_ALL=en_US.UTF-8`.

## Acceptance mapping

| AC | Covered by |
|---|---|
| No 500 after deleting an attached datasource | Design 2–3, regression test |
| Missing references handled gracefully at init | Design 2 |
| Auto-remove invalid references and/or clear message | In-memory drop at init; owner-facing `context_error` in health check; edit+save persists the cleanup |
| Chat remains operational | Assistant builds with remaining datasources |
| Regression coverage vs EPMCDME-12384 | Regression test + 12384 suites green |
