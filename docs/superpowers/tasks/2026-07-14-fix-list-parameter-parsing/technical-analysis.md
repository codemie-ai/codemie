# Technical Research

**Task**: agents langgraph tool-calls event-adapter serialization
**Generated**: 2026-07-14T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-13152: Tool calls with list parameters intermittently fail because list input is parsed as string. The bug manifests when tool_call["args"] is stringified with str() instead of json.dumps() in langgraph_event_adapter.py lines 159 and 226. This creates Python repr format with single quotes instead of JSON with double quotes. Works for simple payloads (ast.literal_eval handles them) but fails for MCP/plugin tools with complex payloads containing nested quotes - parse_to_dict() in utils.py mangles the data and returns None, causing validation errors. Fix: replace str(unpack_json_strings(...)) with json.dumps(unpack_json_strings(...)). Root cause analysis and reproduction already completed - see realistic_bug_reproduction.py and COMPLETE_BUG_TRACE.md for full context.

---

## 2. Codebase Findings

### Existing Implementations

**Primary bug sites (three, not two as stated in ticket):**

- `src/codemie/agents/langgraph_event_adapter.py` line 160 — `parse_update_type()`: processes `"agent"` chunk events; iterates `message.tool_calls`; applies `tool_args = str(unpack_json_strings(tool_call["args"]))` — Python repr output, not JSON
- `src/codemie/agents/langgraph_event_adapter.py` line 227 — `handle_supervisor_tool_calls()`: processes supervisor's regular (non-handoff) tool calls; identical `str(unpack_json_strings(...))` pattern
- `src/codemie/agents/langgraph_agent.py` line 1406 — `_get_tool_call_args()` static method: `return tool_name, str(unpacked_args)` — same repr bug, third occurrence not mentioned in ticket; callers pass this through `_on_tool_start` and ultimately into `set_current_thought(input_text=...)` in callbacks

**Supporting utilities:**

- `src/codemie/core/utils.py` lines 145-187 — `unpack_json_strings()`: recursively parses string-encoded JSON sub-values in a dict/list; returns a native Python dict/list — correct behavior, the wrapping `str()` is the fault
- `src/codemie/agents/utils.py` lines 179-196 — `parse_to_dict()` (agent-layer variant): tries `json.loads` first, then a single-quote-to-double-quote substitution, returns `None` on failure; this is the stricter parser that causes `TypeError`/validation errors when it returns `None`
- `src/codemie_tools/base/utils.py` lines 68-109 — `parse_to_dict()` (tool-layer variant): tries `json.loads` → `ast.literal_eval` → two regex fallbacks; returns `{}` on total failure — more forgiving, which is why simple repr payloads often survive

**Event and callback chain:**

- `src/codemie/agents/langgraph_agent.py` — `LangGraphAgent`: owns the event adapter; routes `_on_tool_start(tool_name, tool_args, run_id)` which feeds serialized args downstream
- `src/codemie/agents/callbacks/agent_streaming_callback.py` — `on_tool_start` receives `input_str`, stores it as `input_text` on a `Thought` object; displayed in streaming UI
- `src/codemie/agents/callbacks/agent_invoke_callback.py` — `on_tool_start` calls `set_current_thought(input_text=input_str, ...)`; the repr string enters the thought metadata here
- `src/codemie/agents/supervisor/coordinator.py` — calls `unpack_json_strings()` but does NOT apply `str()` — correctly uses the native dict result

### Architecture and Layers Affected

- **LangGraph event/stream layer** (`langgraph_event_adapter.py`): primary fix target — two sites in `parse_update_type` and `handle_supervisor_tool_calls`; consumes raw LangGraph streaming chunks and converts them to agent events
- **Agent orchestration layer** (`langgraph_agent.py`): secondary fix target — `_get_tool_call_args` static method at line 1406; owns agent lifecycle and routes events to callbacks
- **Callback/UI layer** (`agent_streaming_callback.py`, `agent_invoke_callback.py`): receives the already-serialized `input_str`; no changes needed here, but must be retested after upstream fix to confirm downstream display is correct
- **Utility/core layer** (`src/codemie/core/utils.py`): no changes needed; `unpack_json_strings` is correct

### Integration Points

**Internal module dependencies (relevant to fix):**

- `langgraph_event_adapter` → `core.utils.unpack_json_strings` (imported directly)
- `langgraph_agent` → `core.utils.unpack_json_strings` (imported independently; also has the third bug site)
- `langgraph_agent` → `langgraph_event_adapter` (instantiates and holds `_event_adapter`)
- `codemie/agents/utils.parse_to_dict` ← `codemie/agents/utils.parse_tool_input` (used during tool input schema validation; this is where `None` return causes downstream `TypeError`)
- `codemie_tools/base/utils.parse_to_dict` ← `codemie_tools/open_api/tools.py`, `codemie_tools/access_management/keycloak/tools.py` (separate call sites, less risky variant)

**External dependencies:**

- `langgraph = "1.1.6"` (pinned) — provides streaming chunks that populate `tool_call["args"]` as native Python dicts
- `langchain-core` — `AIMessage`, `ToolMessage`, `BaseTool`, `BaseCallbackHandler` types throughout the adapter and callbacks
- `pydantic` — `model_validate` called in `parse_tool_input`; validation errors are the user-visible symptom

### Patterns and Conventions

- `unpack_json_strings()` is the correct entry point for normalizing tool args — all other callers (coordinator.py, tools_models.py) use its return value as a native dict without wrapping in `str()`
- The broken pattern `str(unpack_json_strings(...))` is isolated to the three sites above; it was not introduced as a project convention
- `json.dumps` is used elsewhere in `core/utils.py` (`format_json_content`) but is not imported in `langgraph_event_adapter.py` — the fix requires adding the import
- Callback layer passes `input_str` as an opaque string — no assumptions about its format are made at callback definition, but downstream consumers (replay, monitoring) parse it back to dict, where the repr format breaks them

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/agents/langchain-agent-patterns.md` — directly governs this domain; mandates "Normalize tool and model outputs" and "Serialize dicts/models to strings or JSON"; the use of `str()` instead of `json.dumps()` is an explicit violation of this guide
- `.ai-run/guides/agents/agent-tools.md` — requires tool outputs to be "structured, serializable outputs"; the bug violates the "Return structured, serializable outputs" convention; also specifies that schema/adapter logic lives near `src/codemie/agents/tools/`
- `.ai-run/guides/architecture/layered-architecture.md` — establishes layering and shared-core conventions; confirms the event adapter belongs in its current layer
- `.ai-run/guides/workflows/langgraph-workflows.md` — confirms event adapter is correctly positioned; workflow executor nodes must not bypass existing validation paths

### Architectural Decisions

- No formal ADRs found in the repository
- Implicit decision encoded in all non-bugged call sites: `unpack_json_strings()` return values are used as native dicts, not stringified — the `str()` wrapping at the three bug sites is an anomaly, not an intentional design choice
- `TaskResult.from_agent_response` at `src/codemie/agents/assistant_agent.py:99` is the established output normalization point (not relevant to the input serialization bug)

### Derived Conventions

- Tool call arguments flowing into callbacks must be JSON strings (double-quoted, parseable by `json.loads`)
- The `codemie/agents/utils.parse_to_dict` variant is the stricter consumer — it returns `None` on failure, unlike the tool-layer variant that returns `{}`; any fix must produce output that `json.loads` can parse without fallbacks
- `import json` is already used throughout the codebase; adding it to `langgraph_event_adapter.py` follows established convention
- Bug reproduction files (`realistic_bug_reproduction.py`, `COMPLETE_BUG_TRACE.md`) referenced in the ticket do not exist in the repository — they may have been created in a local environment and not committed

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/core/test_core_utils.py` — covers `unpack_json_strings` with string/dict/list/nested inputs, unicode, escaped quotes; does NOT test the combined `str(unpack_json_strings(...))` serialization path
- `tests/codemie_tools/base/test_base_utils.py` — table-driven tests for `parse_to_dict` (valid JSON, Python single-quote dicts, invalid strings, empty, None); does NOT cover failure mode when embedded quotes cause `None`/`{}` return
- `tests/codemie/service/assistant/test_langgraph_assistant.py` line 171 — tests `LangGraphAgent.__parse_update_type`; **asserts `"{'arg': 1}"` (Python repr, single quotes) as expected output** — this assertion encodes the broken behavior and must be updated to `'{"arg": 1}'`
- `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py` lines 647 and 669 — tests supervisor tool-call routing; **asserts `"{'query': 'test'}"` (single-quote repr)** — two assertions that must change to double-quoted JSON
- `tests/codemie/agents/callbacks/test_agent_streaming_callback.py` — covers `on_tool_start`/`on_tool_end` lifecycle; passes raw string literals, no serialization format assertions
- `tests/codemie/agents/callbacks/test_monitoring_callback.py` — covers monitoring callback `on_tool_start`; no args-serialization assertions
- `tests/codemie/agents/callbacks/test_agent_invoke_callback.py` — covers invoke callback `on_tool_start`; passes string literals directly, no serialization assertions
- `tests/codemie/agents/test_agents_utils.py` — covers `parse_tool_input` and other agent utilities; does not touch event adapter

### Testing Framework and Patterns

- pytest `^8.3.1` (compiled evidence of 8.3.3 and 8.4.2)
- Plugins: `pytest-asyncio ^0.23.7`, `pytest-cov ^5.0.0`, `pytest-env ^1.1.3`, `pytest-mock ^3.14.0`, `pytest-httpx ^0.35.0`
- `@pytest.mark.parametrize` for table-driven cases (primary pattern in utils tests)
- Class-based groupings (`class TestLangGraphAgent:`)
- `MagicMock(spec=...)` for typed mocking of LangChain/LangGraph objects
- `patch()` as context manager and `@patch` decorator for config overrides
- Name-mangled private method calls (e.g., `agent._LangGraphAgent__parse_update_type(value)`) to test internals — this is the pattern to follow for new event adapter tests
- No `conftest.py` exists under `tests/codemie/agents/` — no shared agent-level fixtures

### Coverage Gaps

1. **No test file for `langgraph_event_adapter.py`** — `LangGraphEventAdapter.parse_update_type` and `handle_supervisor_tool_calls` have zero direct unit test coverage; this is the primary risk area
2. **No test for list parameters in tool args** — no test exercises `tool_call["args"]` containing a list value (e.g., `{"files": ["a.py", "b.py"]}`), which is the exact failing case
3. **No test for the serialization-to-parse round-trip** — no test covers: args dict → serialized string → `parse_to_dict` → recovered dict; this is the full path where the bug manifests
4. **No test for MCP/plugin tools with nested quotes** — `parse_to_dict` failure on payloads with embedded double quotes is completely untested
5. **Three existing assertions encode the buggy behavior** — they will fail immediately after the fix lands; they must be updated as part of the same PR

---

## 5. Configuration and Environment

### Environment Variables

- `LOG_LEVEL` — set to `DEBUG` in `.env` and Helm values; debug log lines in `langgraph_event_adapter.py` at lines 162 and 229 print the serialized args string, making the repr vs JSON distinction observable in logs
- `CODE_EXECUTOR_ENABLED` — enables code execution tools (e.g., `GetFilesInput` in `tools_models.py`) that use list-typed inputs; this is the tool category most affected by the bug
- `LLM_PROXY_MODE` / `LLM_PROXY_ENABLED` / `LITE_LLM_URL` — route LLM calls; determine which model backend produces `tool_call` dicts; no direct effect on serialization

### Configuration Files

- `pyproject.toml` — pins `langgraph = "1.1.6"`, `langchain-anthropic = "1.4.0"`, `langchain-aws = "1.4.3"`; no serialization-related settings
- `config/llms/` (4 YAML files) — LLM provider routing; governs model selection, not arg serialization
- `.env` — local dev overrides; no agent serialization switches
- `tests/.env.test` — test-only env vars; no tool call configuration

### Feature Flags and Deployment Concerns

- No feature flags or runtime switches exist for the serialization path
- The fix is a pure code change — no environment variable or configuration change is required alongside it
- No deployment manifest changes are needed; the bug and fix are entirely within application code
- After the fix, the debug log lines at 162 and 229 will begin emitting valid JSON strings instead of Python repr — this is an improvement for log observability and not a breaking change

---

## 6. Risk Indicators

- **Three bug sites, not two**: The ticket mentions lines 159 and 226 in `langgraph_event_adapter.py`, but `langgraph_agent.py` line 1406 (`_get_tool_call_args` static method, `return tool_name, str(unpacked_args)`) contains the same pattern and must be fixed in the same PR — missing this site leaves the bug partially unfixed
- **No unit test file for `langgraph_event_adapter.py`**: The two primary fix sites have zero direct test coverage; correctness of the fix can only be verified through the existing integration-style tests, which currently assert the broken output format
- **Three existing test assertions encode the buggy repr format**: `test_langgraph_assistant.py` line 171 and `test_langgraph_multi_assistant_supervisor.py` lines 647 and 669 assert single-quote Python repr strings; these will fail after the fix and must be updated — a PR that fixes the source but not the tests will fail CI
- **Two distinct `parse_to_dict` implementations with different failure modes**: `codemie/agents/utils.parse_to_dict` returns `None` on failure (causes `TypeError`), while `codemie_tools/base/utils.parse_to_dict` returns `{}` (silent data loss); the fix in `langgraph_event_adapter.py` addresses the root cause, but the inconsistency between these two implementations is a latent risk
- **No test for list-typed args in the serialization path**: The exact failing case (`{"files": ["a.py", "b.py"]}`) is not covered by any existing test; new test(s) should be added to prevent regression
- **Bug reproduction artifacts not committed**: `realistic_bug_reproduction.py` and `COMPLETE_BUG_TRACE.md` referenced in the ticket do not exist in the repository; the fix cannot be verified by running those files
- **`json.dumps` not imported in `langgraph_event_adapter.py`**: The fix requires adding `import json` to that file; this is trivial but must not be overlooked
- **`unpack_json_strings` may return a list at the top level**: If `tool_call["args"]` is a list (not a dict), `json.dumps(unpack_json_strings(...))` is still correct, but callers expecting a dict string may need to be verified; `parse_to_dict` in `agents/utils.py` calls `json.loads` first which handles lists, but the downstream `dict(None)` error only triggers on `None` return

---

## 7. Summary for Complexity Assessment

This task is a targeted serialization bug fix touching two primary files (`src/codemie/agents/langgraph_event_adapter.py` and `src/codemie/agents/langgraph_agent.py`) and three test files (`tests/codemie/service/assistant/test_langgraph_assistant.py`, `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py`, and ideally a new test file for the event adapter). The actual code changes at each fix site are one-liners — replacing `str(unpack_json_strings(...))` with `json.dumps(unpack_json_strings(...))` plus one `import json` addition. The architectural layers affected are the LangGraph event/stream layer (primary) and the agent orchestration layer (secondary); the callback and UI layers receive the fixed output passively without requiring changes.

The fix follows a well-established pattern — `json.dumps` is already used throughout the codebase, and the project guides explicitly mandate JSON serialization for tool args. There is no technical novelty; this is a clear substitution of the wrong serialization function for the correct one. The root cause is fully understood and isolated to three call sites where `unpack_json_strings()` return values are wrapped in `str()` instead of `json.dumps()`. The third site (`langgraph_agent.py` line 1406) is not mentioned in the ticket and is the main complexity risk: if it is missed, the bug remains present in the non-supervisor code path.

Test coverage posture is mixed to weak. The affected event adapter (`langgraph_event_adapter.py`) has no unit tests at all, which means the fix cannot be directly unit-tested without writing new tests. More critically, three existing test assertions actively assert the broken Python repr output format and will cause CI failures if not updated alongside the fix. The complexity-assessor should weight this as low-to-medium complexity (the code change itself is trivial, the test work is non-trivial), with the main execution risk being the undiscovered third bug site and the mandatory test updates that accompany the fix.
