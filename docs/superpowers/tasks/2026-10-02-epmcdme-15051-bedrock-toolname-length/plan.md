# EPMCDME-15051 Replayed Handoff Tool Name Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Bedrock 400s on replayed handoff tool names by persisting the real `transfer_to_*` name and normalizing every `metadata.tool_name` at replay time.

**Architecture:** Write path: the event adapter adds `serialized["tool_name"] = destination`; both supervisor callbacks prefer it for `_build_tool_metadata` and keep the display name as `author_name`. Read path: `_extract_tool_records` runs every name through the existing `_normalize_tool_name`.

**Tech Stack:** Python, LangChain callbacks, pytest.

**Spec:** `docs/superpowers/tasks/2026-10-02-epmcdme-15051-bedrock-toolname-length/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- Max tool name length: `MAX_TOOL_NAME_LENGTH` = 64 (`codemie.core.constants`); valid charset `[a-z0-9_-]`.
- Do not touch `_get_available_replay_tool_names`, `_collect_mcp_server_tool_names`, `_create_handoff_tools`, `format_assistant_name`, `truncate_sub_assistant_handoff_tool_name`, `resolve_tool_display_name`, the ` #N` suffix logic, or `_build_tool_metadata`.
- Do not change title-case naming in `create_thought` for non-handoff tools.
- Never edit anything under `src/codemie-repos/`.

## Review Focus

- Handoff display name with ` #2` suffix: `author_name` keeps `#2`, `metadata.tool_name` is the clean `destination` (Task 2, Task 3 tests).
- Callback receives `serialized` without `tool_name` (all normal tools): metadata identical to today (Task 2, Task 3 tests).
- Stored `metadata.tool_name` that is already valid (e.g. `search_docs`): unchanged after normalization, so native-vs-text decision is unchanged (Task 4 test).
- Stored name that normalizes to empty (e.g. `"###"`): becomes `unknown_tool`, no crash (Task 4 test).
- Handoff with empty `input_str`: metadata stays `{}` as today (no new behavior; existing guard in both callbacks).

---

### Task 1: Event adapter forwards the real handoff tool name

**Files:**
- Modify: `src/codemie/agents/langgraph_event_adapter.py:108`
- Test: `tests/codemie/agents/test_langgraph_event_adapter.py`

**Interfaces:**
- Produces: supervisor callbacks' `on_tool_start` receives `serialized == {"name": display_name or destination, "tool_name": destination}`.

**Test-first: yes** — `on_supervisor_handoff("transfer_to_x", run_id, "task", display_name="X #2")` calls each mocked supervisor callback with `serialized` containing `"tool_name": "transfer_to_x"` and `"name": "X #2"`; fails because `tool_name` is absent.

- [ ] Write the failing test in the existing adapter test style; run `pytest tests/codemie/agents/test_langgraph_event_adapter.py -v`, expect FAIL.
- [ ] Change line 108 to `serialized = {"name": display_name or destination, "tool_name": destination}`.
- [ ] Re-run the file; expect PASS.

### Task 2: Streaming callback persists the real tool name

**Files:**
- Modify: `src/codemie/agents/callbacks/agent_streaming_callback.py:56-86` (`create_thought`), `:306-326` (`on_tool_start`)
- Test: `tests/codemie/agents/callbacks/test_agent_streaming_callback.py`

**Interfaces:**
- Consumes: `serialized.get("tool_name")` from Task 1.
- Produces: `create_thought(..., by_run_id: bool = False, replay_tool_name: str | None = None) -> Thought`.

**Test-first: yes** — `on_tool_start({"name": "Customer Onboarding ... Specialist Agent #2", "tool_name": "transfer_to_customer_onboarding_abc123"}, "task", run_id=...)` stores a thought whose `metadata["tool_name"]` equals the `tool_name` value and whose `author_name` is the title-cased display name; a second test with `{"name": "search_docs"}` asserts `metadata["tool_name"] == "search_docs"` (unchanged). The first fails today.

- [ ] Write both tests (replay v2 enabled, matching how existing tests drive `on_tool_start`); run the file, expect the handoff test to FAIL.
- [ ] Add the `replay_tool_name` kwarg to `create_thought`; at line 83 pass `replay_tool_name or tool_name` to `_build_tool_metadata`. `author_name` logic unchanged.
- [ ] In `on_tool_start`, pass `replay_tool_name=serialized.get("tool_name")` to `create_thought`.
- [ ] Re-run the file; expect PASS.

### Task 3: Invoke callback persists the real tool name

**Files:**
- Modify: `src/codemie/agents/callbacks/agent_invoke_callback.py:168-196` (`set_current_thought`), `:280-290` (`on_tool_start`)
- Test: `tests/codemie/agents/callbacks/test_agent_invoke_callback.py`

**Interfaces:**
- Consumes: `serialized.get("tool_name")` from Task 1.
- Produces: `set_current_thought(..., run_id: Any | None = None, replay_tool_name: str | None = None)`.

**Test-first: yes** — `on_tool_start({"name": "Long Agent Name #2", "tool_name": "transfer_to_long_agent_name"}, "task", run_id=...)` yields a current thought with `metadata["tool_name"] == "transfer_to_long_agent_name"` and `author_name == "Long Agent Name #2"` (via the resolver); a no-`tool_name` case keeps today's metadata. The first fails today.

- [ ] Write both tests; run the file, expect the handoff test to FAIL.
- [ ] Add `replay_tool_name` kwarg to `set_current_thought`; at line 195 pass `replay_tool_name or tool_name` to `_build_tool_metadata`. `display_name` resolution unchanged.
- [ ] In `on_tool_start`, pass `replay_tool_name=serialized.get("tool_name")`.
- [ ] Re-run the file; expect PASS.

### Task 4: Normalize stored tool names at replay

**Files:**
- Modify: `src/codemie/service/conversation/history_projection_service.py:166`
- Test: `tests/codemie/service/conversation/test_history_projection_service.py`

**Interfaces:**
- Consumes: existing `HistoryProjectionService._normalize_tool_name(name: str) -> str` (`:522-536`).

**Test-first: yes** — using `_build_conversation_with_tool_turn`, a thought with `metadata.tool_name` of 80+ chars (display-derived, e.g. `customer_onboarding_..._agent_#2`) projected in `NATIVE_TOOLS_MODE` with `available_tool_names=None` yields `AIMessage.tool_calls[0]["name"]` and `ToolMessage.additional_kwargs["name"]` of length ≤64 matching `^[a-z0-9_-]+$`; also direct `_extract_tool_records` cases: a `#`-containing name, `"###"` → `unknown_tool`, and a valid `search_docs` stays `search_docs`. The overlong/`#` cases fail today.

- [ ] Write the tests; run the file, expect FAIL.
- [ ] At line 166, wrap the result: `tool_name = cls._normalize_tool_name(str(metadata.get("tool_name") or thought.author_name))`.
- [ ] Re-run the file; expect PASS.
