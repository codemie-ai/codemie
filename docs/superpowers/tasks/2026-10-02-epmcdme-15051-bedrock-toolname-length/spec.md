# EPMCDME-15051: Bedrock rejects overlong replayed handoff tool names

## Problem

With conversation replay v2 on, a supervisor handoff to a sub-assistant persists the sub-assistant's
UI display name as `Thought.metadata.tool_name`. That name has no 64-char cap and no charset filter, and
it can carry a ` #N` suffix. On the next turn `HistoryProjectionService` replays it verbatim as
`AIMessage.tool_calls[].name`, and Bedrock returns a 400 ("toolUse.name ... length less than or equal
to 64"). Because the bad entry sits in saved history, every later turn in that conversation fails.

The downgrade-to-text filter would normally hide this. It is disabled when any enabled MCP server has an
empty tools list, because `_get_available_replay_tool_names` then returns `None`
(`src/codemie/rest_api/handlers/assistant_handlers.py:353-388`).

## Approach

Two independent fixes. Either one alone stops the 400. Both together fix new conversations at the
source and let existing conversations recover.

### 1. Persist the real handoff tool name (write path)

`LangGraphEventAdapter.on_supervisor_handoff` (`src/codemie/agents/langgraph_event_adapter.py:100-114`)
already receives `destination`, the real `transfer_to_<name>` tool name, which is at most 64 characters.
Today it discards it. Instead it passes `destination` to the supervisor callbacks as an
additional `serialized` key, and `serialized["name"]` stays the display name:

```python
serialized = {"name": display_name or destination, "tool_name": destination}
```

- `AgentStreamingCallback.on_tool_start` (`agent_streaming_callback.py:306-326`) takes
  `serialized.get("tool_name")` when present and hands it to `create_thought` through a new optional
  argument. `create_thought` (`:56-86`) builds the metadata from that name when it is given, and otherwise
  from the title-cased display name it uses today. `author_name` does not change.
- `AgentInvokeCallback` (`agent_invoke_callback.py:168-196`) also uses `serialized.get("tool_name")`, when
  present, for `_build_tool_metadata`, and keeps the display name as `author_name`.
- Callbacks that ignore the extra key behave exactly as they do today.

### 2. Sanitize tool names at replay time (read path)

In `HistoryProjectionService._extract_tool_records` (`history_projection_service.py:158-195`), every
`metadata.tool_name` goes through the existing `_normalize_tool_name` (`:522-536`, lowercases, restricts to
`[a-z0-9_-]`, caps at `MAX_TOOL_NAME_LENGTH` = 64, falls back to `unknown_tool`). The fallback branch
already normalizes `author_name`. Native `tool_calls[].name` and `ToolMessage.additional_kwargs["name"]`
therefore always carry a valid name, including those built from conversations persisted before this fix.
Because `_should_render_native_tool_replay` already compares normalized names, the native-vs-text decision
for valid names does not change.

## Acceptance criteria

- AC1: After a streaming supervisor handoff with replay v2 enabled, the persisted handoff thought has
  `metadata.tool_name` equal to the coordinator's `destination` tool name (≤ 64 chars).
- AC2: The same holds for the invoke (non-streaming) supervisor callback path.
- AC3: The handoff thought's `author_name` (UI display, including any ` #N` suffix) is unchanged.
- AC4: Non-handoff tool thoughts persist the same `metadata.tool_name` as before, for both callbacks.
- AC5: `_extract_tool_records` returns a tool name of at most 64 characters, matching `[a-z0-9_-]+`, for any
  stored `metadata.tool_name`, including an 80+ char display-derived name and one containing `#`.
- AC6: Replaying a stored conversation whose handoff thought has an overlong or invalid `metadata.tool_name`
  in `NATIVE_TOOLS_MODE` with `available_tool_names=None` produces `AIMessage.tool_calls[].name` and
  `ToolMessage` names that are at most 64 characters and charset-valid.
- AC7: Existing tests in `test_history_projection_service.py`, `test_agent_streaming_callback.py`,
  `test_agent_invoke_callback.py` and `test_langgraph_event_adapter.py` still pass. New tests cover AC1,
  AC2, AC3, AC5 and AC6.

## Non-goals

- Changing `_get_available_replay_tool_names` / `_collect_mcp_server_tool_names`, including their `None`
  result for empty MCP tool lists.
- Changing how handoff tool names are generated (`_create_handoff_tools`, `format_assistant_name`,
  `truncate_sub_assistant_handoff_tool_name`).
- Changing the UI display name, the ` #N` suffix logic, or `resolve_tool_display_name`.
- Rewriting the title-case naming in `create_thought` for non-handoff tools.
- Downgrading handoff records to text replay, or mapping legacy sanitized names back to the real hashed
  tool name.
- Migrating or rewriting persisted conversation documents.
- Adding a charset or length filter inside `_build_tool_metadata`.
- Editing anything under `src/codemie-repos/`.

## Risks

- A legacy name that has been sanitized at replay (for example a truncated `customer_onboarding_...`) is
  valid for Bedrock but does not match any current tool. It replays as a past native tool call that the
  model cannot call again. This is accepted per the ticket's fix goals.
