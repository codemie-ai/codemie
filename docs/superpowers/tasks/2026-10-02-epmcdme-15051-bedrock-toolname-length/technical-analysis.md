# Technical Research

**Task**: conversation-replay tool-metadata supervisor-handoff
**Generated**: 2026-10-02
**Research path**: filesystem

---

## 1. Original Context

fix this ticket https://jiraeu.epam.com/browse/EPMCDME-15051. My research with another agent: Bug: generation fails with Bedrock 400 on toolUse.name length after a sub-assistant handoff

What the user sees: A user chats with an assistant that has (1) at least one sub-assistant whose name is long (~50+ chars, e.g. "Customer Onboarding Knowledge Base And Documentation Retrieval Specialist Agent"), and (2) an MCP server set to "Use all available tools" (no explicit tool list). The first message that triggers a handoff works. Every later message in that conversation fails with:
BedrockException - Value at 'messages.N.member.content.M.member.toolUse.name' failed to satisfy constraint: Member must have length less than or equal to 64
The conversation stays broken because the bad entry is in saved history.

Conditions (all required): Bedrock model (rejects tool-use names >64 in history); Conversation replay v2 enabled (AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED=True, default) which saves tool metadata and replays past tool calls as real tool-call messages; sub-assistant name long enough that transfer_to_<name> exceeds 64; streaming chat (streaming callback saves the display name; non-streaming invoke path saves the real tool name); assistant has an enabled MCP server with an empty tools list (turns off the filter that would otherwise replay the handoff as plain text); at least one earlier turn where the handoff happened.

Root cause:
1. Handoff tool name is truncated correctly for the LLM: transfer_to_<normalized name> cut to 64 (src/codemie/agents/langgraph_agent.py:~397); original name mapping stored for UI display (~:408).
2. The callback receives the display name, not the tool name. Supervisor handoff callbacks get the original sub-assistant name (langgraph_event_adapter ~:1551), passed through resolve_tool_display_name (agent_streaming_callback.py:318-322).
3. That display name is saved as replay tool name with no sanitization. _build_tool_metadata (src/codemie/agents/callbacks/callback_utils...) lowercases and replaces spaces; no 64-char cap and no [a-zA-Z0-9_-] filtering. metadata.tool_name is 80 chars.
4. Next turn replays it as native tool call. HistoryProjectionService._extract_tool_records takes metadata.tool_name as-is; native tool messages emit it as AIMessage.tool_calls[].name (~:422-433); Bedrock rejects.
5. MCP server matters: past tool call replayed natively only if its name is in current tool list; handoff names aren't in that list, so fallback to plain-text. But _get_available_replay_tool_names returns None when any enabled MCP server has empty tools list (src/codemie/rest_api/...:354-388). None means replay everything natively (history_projection_service.py:360-361).
The fallback path (thoughts without metadata) uses normalize_tool_name which caps at 64 (history_projection_service.py:523-535).

Fix goals: ensure saved metadata.tool_name for handoffs is the real (≤64, valid-charset) tool name, AND defensively sanitize/cap tool names at replay time so already-corrupted histories recover. Identify existing tests for these modules.

---

## 2. Codebase Findings

The user's root-cause analysis checks out against the source. Corrections and additions are marked **[new]**.

### Existing Implementations

**Write path (saving metadata):**
- `src/codemie/agents/langgraph_agent.py:378-418` `_create_handoff_tools`: tool name = `f"transfer_to_{format_assistant_name(agent_name)}"[:64]` (`ASSISTANT_NAME_MAX_LENGTH = 64`, line 125). Maps truncated suffix to the original name in `_sub_assistant_name_mapping`.
- `langgraph_agent.py:1500-1506` `format_assistant_name`: replaces `[\s<|\\/>]` with `_`, strips `\W`, lowercases, then `truncate_sub_assistant_handoff_tool_name` (1608-1644), which already hash-truncates the name to `64 - len("transfer_to_")`. **[new]** The `[:64]` slice in `_create_handoff_tools` is therefore a no-op safety net.
- `src/codemie/agents/supervisor/coordinator.py:176-190` `queue_supervisor_handoffs` builds `display_name` via `resolve_display_name`. Lines 237-247 call `emit_handoff(f"{handoff_tool_prefix}_{context.raw_author}", run_id, task, author=..., display_name=display_name)`, so the real tool name (`destination`) **is available** here.
- `langgraph_agent.py:1551-1555` `_build_subassistant_display_name`: `get_original_sub_assistant_name(agent_name).replace('_',' ').title()`, plus `f" #{index}"` when the same sub-assistant is handed off more than once per turn. **[new]** The `#` survives into metadata, so the saved name is invalid even when it is short.
- `langgraph_agent.py:1428-1442` `_on_supervisor_handoff` → `src/codemie/agents/langgraph_event_adapter.py:100-113` `on_supervisor_handoff`: `serialized = {"name": display_name or destination}`, which **drops `destination`** before it reaches the callbacks.
- `src/codemie/agents/callbacks/agent_streaming_callback.py:306-326` `on_tool_start`: `resolve_tool_display_name(serialized['name'])` → `storage.create_thought(tool_name=tool_display_name)`.
- `agent_streaming_callback.py:~55-86` thought storage `create_thought`: strips the `AGENT` prefix, `tool_name.replace('_',' ').title()`, then `_build_tool_metadata(tool_name, input_text)`. **[new]** Every streaming tool (not only handoffs) has its metadata name derived from a title-cased display string.
- `src/codemie/agents/callbacks/agent_invoke_callback.py:170-196`: `display_name` is used for `author_name`, but `_build_tool_metadata(tool_name, …)` receives the raw tool name, as the user described.
- `src/codemie/agents/callbacks/utils/name_resolver.py:60-87` `resolve_tool_display_name`: maps names prefixed `transfer_to_` to the original name, then `.replace('_',' ').title()`.
- `src/codemie/agents/callbacks/callback_utils.py:54-70` `_build_tool_metadata`: `normalized_name = tool_name.replace(' ', '_').lower()`. It has no charset filter and no length cap. Returns `{}` when replay v2 is disabled.

**Read path (replay):**
- `src/codemie/service/conversation/history_projection_service.py:158-195` `_extract_tool_records`: `tool_name = str(metadata.get("tool_name") or cls._normalize_tool_name(thought.author_name))`, so a metadata name is used without normalization.
- `history_projection_service.py:349-370` `_should_render_native_tool_replay`: when `available_tool_names is None`, returns True. Otherwise it compares *normalized* names.
- `history_projection_service.py:~410-433` `_render_native_tool_messages`: emits `AIMessage(tool_calls=[{"name": record.tool_name, ...}])` and `ToolMessage(additional_kwargs={"name": record.tool_name})`.
- `history_projection_service.py:522-536` `_normalize_tool_name`: lowercase, `[^a-z0-9_\-]`→`_`, collapses `_+`, strips `_`, caps at `MAX_TOOL_NAME_LENGTH` (`src/codemie/core/constants.py:77`, `= 64`), falls back to `"unknown_tool"`.
- `src/codemie/rest_api/handlers/assistant_handlers.py:353-388` `_collect_mcp_server_tool_names` / `_get_available_replay_tool_names`: return `None` if any enabled MCP server has an empty `tools` list. Called at line 250.
- `assistant_handlers.py:~390-412` `_resolve_history_projection_mode`: **[new]** Bedrock *Agents/Orchestrator* assistants (`BedrockOrchestratorService.is_bedrock_assistant`) use `PLAIN_CHAT_MODE`, and react LLMs use `TEXT_LEDGER_MODE`. LiteLLM-hosted Bedrock models fall through to `NATIVE_TOOLS_MODE`, which is the failing path.

### Architecture and Layers Affected
- Agent runtime (supervisor): `langgraph_agent.py`, `supervisor/coordinator.py`, `langgraph_event_adapter.py`
- Callbacks: `agent_streaming_callback.py`, `agent_invoke_callback.py`, `callbacks/callback_utils.py`, `callbacks/utils/name_resolver.py`
- Service (conversation replay): `service/conversation/history_projection_service.py`
- API handler (replay availability): `rest_api/handlers/assistant_handlers.py`

### Integration Points
- Chain: coordinator → `LangGraphAgent._on_supervisor_handoff` → `_callback_bridge.on_supervisor_handoff` → `callback.on_tool_start(serialized, …)` for each `supervisor_callbacks`.
- Persisted `Thought.metadata` (`replay_type`, `tool_name`, `tool_args_text`, `tool_args`, `status`, `preserve_full_output`, `llm_tier`) is read back by `HistoryProjectionService` on the next request.
- `_update_tool_replay_metadata` (`callback_utils.py:106+`) reads `metadata["tool_name"]` on tool end (SKILL limit check).
- `on_tool_start` receives the `metadata` kwarg (`OUTPUT_FORMAT`) only. No real-tool-name channel exists today.

### Patterns and Conventions
- Tool-name constants live in `codemie.core.constants` (`SUPERVISOR_HANDOFF_TOOL_PREFIX = "transfer_to"`, `MAX_TOOL_NAME_LENGTH = 64`).
- Callbacks are decoupled from the agent through the `NameResolver` protocol (`NoOpNameResolver` default).
- Replay v2 is gated through `_is_conversation_replay_v2_enabled()` (`DynamicConfigService` with a config default).
- Service methods are `@classmethod` helpers on `HistoryProjectionService`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists. Relevant guides are `agents/langchain-agent-patterns.md`, `architecture/service-layer-patterns.md`, `testing/testing-patterns.md` and `testing/testing-service-patterns.md`. No guide covers conversation replay or the tool-metadata naming contract. (Only `integration/request-hedging.md` mentions `tool_name`.)

### Architectural Decisions
- Inline comments in `langgraph_agent.py:394-407`: the normalized (untruncated) agent name must match the subagent's graph name. Only the tool name is truncated, and the mapping exists "for UI display".
- `name_resolver.py` docstring: display names are meant for the UI only.

### Derived Conventions
- Display name (`author_name`) and replay name (`metadata.tool_name`) are intended to be separate. The invoke callback follows this; the streaming storage and the supervisor-handoff path conflate them.
- Normalization at replay time is centralized in `_normalize_tool_name`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/conversation/test_history_projection_service.py` (13 tests): native vs downgraded replay, available-tool filtering, dedup by call_id, `_normalize_tool_name` charset and max length (lines 451-557). It has no test where `metadata.tool_name` is overlong or invalid.
- `tests/codemie/agents/callbacks/utils/test_name_resolver.py`: `resolve_tool_display_name` handoff and non-handoff cases.
- `tests/codemie/agents/callbacks/test_agent_streaming_callback.py`: `on_tool_start/end/error`, `create_thought` with author storages, routing stamping.
- `tests/codemie/agents/callbacks/test_agent_invoke_callback.py`
- `tests/codemie/agents/test_langgraph_event_adapter.py` (6 KB)
- `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py` (41 KB): handoff tool creation and truncation.
- `tests/codemie/agents/test_langgraph_tool_confirmation.py`: the only test that references the supervisor coordinator.
- `tests/codemie/rest_api/handlers/test_assistant_handlers.py` and `test_assistant_handlers_streaming.py`

### Testing Framework and Patterns
pytest, with `unittest.mock`-style mocks (`mock_streamed_result`, `mock_logging` fixtures) and real `ThoughtQueue` fixtures. Projection tests build `Conversation`/`GeneratedMessage`/`Thought` objects through a `_build_conversation_with_tool_turn` helper and assert on `AIMessage`/`ToolMessage`.

### Coverage Gaps
- `_build_tool_metadata` has no direct test: nothing in tests references `callback_utils`.
- `_get_available_replay_tool_names` / `_collect_mcp_server_tool_names` have no test, including the empty-MCP-tools → `None` case.
- No test covers `on_supervisor_handoff` → saved metadata name, or `_extract_tool_records` with an invalid `metadata.tool_name`.

---

## 5. Configuration and Environment

### Environment Variables
- `AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED` (`src/codemie/configs/config.py:776`, default `True`; dynamic key in `service/constants.py:49`).
- `AI_AGENT_HISTORY_REPLAY_*` limits (summary/full result/log length) in config.

### Configuration Files
`src/codemie/configs/config.py`; dynamic overrides through `DynamicConfigService`.

### Feature Flags and Deployment Concerns
Replay v2 is a runtime flag; turning it off disables metadata saving entirely. No migration exists for stored thoughts: corrupted histories live in persisted conversation documents.

---

## 6. Risk Indicators

- Speculative: if the fix only corrects the write path, existing conversations stay broken. A replay-time `_normalize_tool_name` on `metadata.tool_name` in `_extract_tool_records` (and possibly in `_is_replayable_tool`, which already normalizes) is needed for recovery.
- Speculative: a truncated or sanitized legacy name such as `customer_onboarding_..._specialist_agent` will not equal the real hashed `transfer_to_…` tool name. Native replay of a tool call that is not bound for the current agent may still confuse the model or the provider. Treating handoff records (prefix `transfer_to_` or the old display-derived names) as non-native/text replay may be safer.
- Speculative: changing `create_thought` to stop title-casing would affect all streaming tools' `metadata.tool_name`. Today it round-trips for snake_case names, but a name with `_`-adjacent capitals or digits may differ (for example, `.title()` on `abc_2d` gives `Abc 2D` and then `abc_2d`, which is fine; but `mcp__x` gives `mcp  x` and then `mcp__x`). Scope the change to handoffs or pass the real name separately.
- The `" #N"` suffix in `_build_subassistant_display_name` makes saved names invalid (`#`) even for short sub-assistant names, whenever the same sub-assistant is handed off more than once in a turn.
- `on_supervisor_handoff` discards `destination`, the real tool name. Fixing this changes the `on_tool_start` contract (via `serialized` or `metadata` kwargs) for all `supervisor_callbacks`, including the invoke callback.
- `_get_available_replay_tool_names` returning `None` because of a single empty MCP server disables all filtering. That is a broad blast radius beyond handoffs, and it is untested.
- The working tree includes a stale copy under `src/codemie-repos/john_doe@example.com/...`. Do not edit it by mistake.

---

## 7. Summary for Complexity Assessment

The bug spans two layers: the callback write path (the supervisor handoff emits a UI display name, which `_build_tool_metadata` persists after only lowercasing and replacing spaces) and the replay read path (`HistoryProjectionService._extract_tool_records` trusts `metadata.tool_name` verbatim and emits it in native `tool_calls`). The real handoff tool name (`destination`, already ≤64 and charset-safe via `format_assistant_name`) is available in `coordinator.py`/`langgraph_event_adapter.on_supervisor_handoff`, but it is dropped in favour of `display_name`. The expected change surface is small: `callback_utils.py`, `langgraph_event_adapter.py` and/or `agent_streaming_callback.py`, and `history_projection_service.py`. That is about 3-4 source files plus tests. The pattern is not new: `_normalize_tool_name` and `MAX_TOOL_NAME_LENGTH` already exist.

Test posture is mixed. Projection and name-resolver tests are solid and easy to extend. `_build_tool_metadata`, `_get_available_replay_tool_names` and the handoff-to-metadata flow have no tests. The main risks are recovering already-persisted bad histories (a replay-time fix is required), how legacy handoff records should be replayed (natively with a sanitized name, or downgraded to text), and avoiding regressions in the shared streaming `create_thought` naming path for non-handoff tools. Two further conditions widen the trigger beyond the reported one: the `#N` display suffix and LiteLLM Bedrock models using `NATIVE_TOOLS_MODE`.

---

## 8. External References

- `https://jiraeu.epam.com/browse/EPMCDME-15051`: internal Jira URL, **not fetched** (no network or auth from this agent). The task context's embedded bug analysis was used as the requirement source, and every cited code location was verified against the source (see Section 2).
