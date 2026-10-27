# Technical Research

**Task**: langgraph supervisor agents streaming truncation token-limit pre_model_hook
**Generated**: 2026-10-22
**Research path**: filesystem

---

## 1. Original Context

Symptom: In a supervisor assistant, a tool-call response from the supervisor agent node (or a sub-assistant) gets cut at the output-token limit. CodeMie logs 'TRUNCATED RESPONSE DETECTED' and 'Agent error [agent_token_limit]', but the subgraph keeps running. It calls the same tool again with empty args, the tool fails with a pydantic 'Field required' error, and the model retries. This continues until the subgraph's own recursion limit (seen: 47 extra LLM calls, ~149 graph steps, 17.5 minutes, $6.10 for one turn; trace ended with GeneratorExit, no GraphRecursionError). Reproduced locally 2/2 runs on main (2026-10-21) with claude-sonnet-5-5 via DIAL/LiteLLM, litellm_params.max_tokens=4096, a supervisor assistant with one sub-assistant and a tool taking a large argument (write_workspace_file), asked to write 1500 CSV rows in one call. Root cause: the truncation check (_safe_check_for_truncation) runs only in the stream consumer (langgraph_event_adapter.py, langgraph_agent.py._stream_graph). create_supervisor (langgraph_supervisor 0.0.31) compiles the supervisor and each sub-assistant as nodes in one graph; with stream_mode=["updates","messages"]/subgraphs=True, LangGraph runs node tasks in a background executor thread (pregel/main.py, _runner.py), so the agent/tools loop keeps running inside the graph regardless of whether the consumer keeps reading the stream. When the parent generator closes, BackgroundExecutor.__exit__ waits for running tasks and swallows their errors when already exiting with an exception, so the loop only stops at the subgraph's recursion limit. Single-agent mode is unaffected because each agent/tools step there is a separate parent task. Proposed fix: (1) run the truncation check inside the graph itself, e.g. via a pre_model_hook on the supervisor's model node and on each sub-agent's model node, so TokenLimitExceededException is raised in the worker thread and fails that node; (2) add an abort flag the stream consumer sets on error/disconnect/hedging-cancel, checked by the pre_model_hook before each LLM call, to also cover client disconnects; (3) add a test with a real compiled supervisor graph asserting a truncated supervisor/sub-agent tool call causes no further LLM calls. Related: EPMCDME-14855 (consumer-side-only fix for the 'stuck in progress' UI symptom, same trigger, touches the same files, status Ready for Review) and EPMCDME-15638 (raising recursion limit 150->1000, would make this loop ~7x longer/costlier if left unfixed first).

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/agents/langgraph_agent.py` — `LangGraphAgent`, the class in question.
  - `_safe_check_for_truncation` (line ~1304) wraps `_check_for_truncated_response` (line ~1366), which inspects `AIMessage.response_metadata` for `finish_reason in ("length","max_tokens")` or `stop_reason/stopReason == "max_tokens"` and raises `TokenLimitExceededException` (`src/codemie/core/exceptions.py:304`).
  - `_build_supervisor_agent` (line ~297) calls `langgraph_supervisor.create_supervisor(...)`, already passing `pre_model_hook=_compose_pre_model_hooks(_strip_handoff_back_messages_pre_model_hook, _strip_subagent_task_messages_pre_model_hook)`.
  - `_build_single_agent` (line ~345) calls `create_smart_react_agent(...)`, already passing `pre_model_hook=_compose_pre_model_hooks(self.history_compaction_pre_model_hook, _subagent_task_pre_model_hook, _image_artifact_pre_model_hook)`.
  - `_bake_interrupt_into_supervisor_node` (line ~327) reaches into `builder.nodes["supervisor"].runnable.builder` to recompile the supervisor's own react-loop node with `interrupt_before=["tools"]` — documented precedent that the supervisor node is a genuine, mutable compiled subgraph before `.compile()`, unlike sub-assistant nodes which `langgraph_supervisor` wraps opaquely (matches the ticket's root-cause note).
  - `_stream_graph` (line ~893) is the sole stream consumer: calls `self.agent_executor.stream(..., stream_mode=["updates","messages"], subgraphs=bool(self.subagents))` and drives `_safe_check_for_truncation` only from the "updates" branch.
- `src/codemie/agents/langgraph_event_adapter.py` — `LangGraphEventAdapter`. `parse_update_type` and `handle_supervisor_tool_calls` call `self.agent._safe_check_for_truncation(last_message)` after a tool-calling turn; `_finalize_truncated_text` additionally re-checks truncated text-only responses whose `invalid_tool_calls` hide cut-mid-JSON args. A module-level `get_truncation_indicator(response_metadata)` duplicates the same finish/stop-reason logic that `LangGraphAgent._get_truncation_indicator` also implements — two parallel copies of the detection rule exist today.
- `src/codemie/agents/supervisor/pre_model_hooks.py` — `_compose_pre_model_hooks(*hooks)` (already the composition point used by both builders above) and `_image_artifact_pre_model_hook`.
- `src/codemie/agents/supervisor/history.py` — `_strip_handoff_back_messages_pre_model_hook`, `_strip_subagent_task_messages_pre_model_hook`, `_subagent_task_pre_model_hook`: existing pre_model_hook implementations for the supervisor/sub-agent nodes, the pattern a new truncation hook would follow.
- `src/codemie/agents/supervisor/bootstrap.py` / `compat.py` — `ensure_langgraph_supervisor_compatibility()` monkey-patches `langgraph_supervisor.supervisor._supports_disable_parallel_tool_calls` and `LangGraphToolNode._combine_tool_outputs`; shows the existing pattern used when `langgraph_supervisor`/`langgraph` internals must be adjusted, for reference if the fix needs more than a `pre_model_hook` kwarg.
- `src/codemie/agents/smart_react_agent.py` — `create_smart_react_agent`; accepts and forwards `pre_model_hook` through several nested builder functions to `create_react_agent`.
- `src/codemie/core/thread.py` — `ThreadedGenerator`: `is_closed()`, `cancellation_reason`, `close(error=None, *, reason=None)`. Already the mechanism `_should_stop_streaming` uses to detect client disconnect and hedging-cancel from the stream consumer side; the proposed "abort flag" in fix item (2) maps naturally onto this existing object, but it is owned by the agent instance on the calling thread, not something a `pre_model_hook` closure automatically has unless explicitly captured.
- `src/codemie/configs/config.py:780` — `AI_AGENT_RECURSION_LIMIT: int = 150`, flows through `LangGraphAgent.__init__(recursion_limit=...)` → `self.recursion_limit` → `_get_run_config()` → `run_config["recursion_limit"]`. Confirms the "150" and the referenced EPMCDME-15638 (150→1000) both target this single value.

### Architecture and Layers Affected

- **Agent runtime layer**: `src/codemie/agents/langgraph_agent.py` (`LangGraphAgent`, truncation detection, stream loop) and `src/codemie/agents/langgraph_event_adapter.py` (stream-chunk dispatch) are the two files the ticket names directly.
- **Supervisor sub-layer**: `src/codemie/agents/supervisor/` (`bootstrap.py`, `compat.py`, `coordinator.py`, `history.py`, `pre_model_hooks.py`, `constants.py`) — holds the existing `pre_model_hook` implementations and the `langgraph_supervisor` compatibility shims; a new truncation-aware `pre_model_hook` belongs here by convention.
- **Third-party graph execution layer**: `langgraph_supervisor==0.0.31` (`create_supervisor`, confirmed installed) and `langgraph==1.1.6` (`langgraph.prebuilt.create_react_agent`, `pregel`). Installed package confirms `create_supervisor` forwards `pre_model_hook` straight into the supervisor's own `create_react_agent(...)` call (`langgraph_supervisor/supervisor.py:431-438`), i.e. the exact extension point fix (1) proposes already exists as a first-class constructor kwarg on both node types — no monkey-patch required for the hook itself.
- **Exceptions/errors layer**: `src/codemie/core/exceptions.py` (`TokenLimitExceededException`), `src/codemie/core/error_constants.py` (`ErrorCode.AGENT_TOKEN_LIMIT = "agent_token_limit"`), `src/codemie/core/errors.py` (`AgentErrorDetails` example payload) — unchanged by this fix but is what the ticket's "Agent error [agent_token_limit]" log line and user-facing error come from.

### Integration Points

- `codemie.enterprise.litellm.proxy_router.handle_agent_exception` converts `TokenLimitExceededException` into the user-facing `agent_token_limit` error (consumed by `_send_error_to_thread`/`_get_user_error_message`); raising the same exception type from inside a `pre_model_hook` instead of from the stream consumer should flow through this same conversion unchanged, since LangGraph re-raises node exceptions out of `.stream()`.
- `langgraph.prebuilt.create_react_agent` is the underlying builder for both the supervisor node (via `create_supervisor`) and each sub-agent node (via `create_smart_react_agent`); `pre_model_hook` runs as a node before the model call in both, in the same worker-thread context the ticket says the agent/tools loop executes in — the mechanism fix (1) needs is already present without needing to patch `pregel/main.py` or `_runner.py`.
- No GraphRecursionError handling exists anywhere in `src/` or `tests/` (`grep` returned no hits) — consistent with the reported trace ending in `GeneratorExit` rather than a `GraphRecursionError` bubbling to a handler.

### Patterns and Conventions

- `pre_model_hook` composition is already standardized via `_compose_pre_model_hooks(*hooks)`, which merges each hook's returned dict and specially threads `llm_input_messages` through the working state for subsequent hooks. A new truncation-check hook would be added to the existing `_compose_pre_model_hooks(...)` calls in `_build_supervisor_agent` and `_build_single_agent` rather than introduced as a separate wiring path.
- Existing hooks (`_strip_handoff_back_messages_pre_model_hook`, `_subagent_task_pre_model_hook`, `_image_artifact_pre_model_hook`, `ConversationHistoryCompactionService.build_langgraph_pre_model_hook`) are free functions taking `state: dict[str, Any]` and returning a partial-state dict (or `None`); a hook that only needs to *raise* rather than mutate state would be a minimal variant of this same shape.
- Ticket-named EPMCDME-14855 (consumer-side-only fix, same files, status Ready for Review) is not yet reflected in the current source: no `EPMCDME-14855` marker appears anywhere in `src/`. EPMCDME-14859 markers are present and unrelated (parallel-handoff/history ordering fix, same files but different bug).

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/agents/langchain-agent-patterns.md` and `.ai-run/guides/workflows/langgraph-workflows.md` exist but cover generic `AIToolsAgent`/LangChain patterns and workflow nodes, not supervisor/`pre_model_hook` specifics — no guide documents the supervisor truncation flow or `pre_model_hook` composition convention described above.

### Architectural Decisions

- No ADR file found for this area. The closest recorded rationale is inline: the `_bake_interrupt_into_supervisor_node` docstring explaining why the supervisor node (but not sub-agent nodes) is mutable before `.compile()`, and the `EPMCDME-14859` comments in `langgraph_agent.py`/`supervisor/history.py` explaining message-ordering fixes in the same subsystem.

### Derived Conventions

- Inline comments throughout `langgraph_agent.py` and `supervisor/history.py` cite the originating Jira ticket (`EPMCDME-NNNNN:`) next to each non-obvious fix — any change here should follow the same citation convention for traceability.
- New cross-cutting agent-graph behavior is added as a `pre_model_hook` composed via `_compose_pre_model_hooks`, not as a subclass or a change to `create_supervisor`/`create_smart_react_agent` call sites beyond adding one more hook to the existing tuple.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/agents/test_langgraph_truncation.py` — unit-level only. Builds a bare `LangGraphAgent` via `object.__new__` (no real graph) and asserts `_check_for_truncated_response`/`_safe_check_for_truncation` raise `TokenLimitExceededException` for OpenAI/Claude/Bedrock finish/stop-reason formats, plus `extended_error` message redaction (no tool names leaked) and sub-assistant name truncation (unrelated helper also in this file).
- `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py` — supervisor construction/streaming behavior, but its `supervisor_agent` fixture `patch`es `codemie.agents.langgraph_agent.create_supervisor` itself (returns a `MagicMock` whose `.compile()` is also a `MagicMock`). No test in this file runs a real compiled `langgraph_supervisor` graph, so none can observe node-level recursion, background-executor behavior, or a `pre_model_hook` actually firing inside a worker thread.
- `tests/codemie/agents/test_langgraph_event_adapter.py` — adapter-level chunk parsing tests (not reviewed line-by-line here, but by naming scope it exercises `parse_update_type`/`parse_supervisor_update_type`, i.e. the same stream-consumer code path the ticket says is insufficient on its own).

### Testing Framework and Patterns

- pytest with `unittest.mock.Mock`/`MagicMock` and `pytest.fixture`; agents are typically built via `object.__new__(LangGraphAgent)` plus manually-set attributes (truncation tests) or via real `__init__` with `create_supervisor`/LLM dependencies patched out (multi-assistant tests). No fixture in this area builds and runs a genuine compiled `langgraph` graph end-to-end.

### Coverage Gaps

- No test exercises a real compiled supervisor graph (`create_supervisor(...).compile()` unmocked) under a truncated tool-call response — exactly the scenario the ticket's proposed fix item (3) calls for, and exactly what current fixtures mock away.
- No test exists for `pre_model_hook`-raised exceptions propagating out of `create_react_agent`/`create_supervisor` nodes, nor for counting "no further LLM calls" after such a raise.
- No test covers the disconnect/hedging-cancel-triggered abort path proposed in fix item (2); `_should_stop_streaming`/`ThreadedGenerator.is_closed()` are covered only from the stream-consumer side today (implied by existing `supervisor_agent` fixture tests), not from inside a graph node.

---

## 5. Configuration and Environment

### Environment Variables

- None specific to this feature area found; `litellm_params.max_tokens` (the 4096 output-token cap in the ticket's repro) is LiteLLM/DIAL proxy configuration (`litellm_config.yaml` at the repo root), not a CodeMie env var.

### Configuration Files

- `src/codemie/configs/config.py:780` — `AI_AGENT_RECURSION_LIMIT: int = 150`, the single config value behind both this ticket's "recursion limit" and the related EPMCDME-15638 (raise to 1000).
- `litellm_config.yaml` (repo root) — where per-model `max_tokens`/`max_output_tokens` such as the reproduction's 4096 would be configured for DIAL/LiteLLM-routed models.

### Feature Flags and Deployment Concerns

- None found specific to truncation handling or `pre_model_hook` composition. `require_tool_confirmation` (interrupt-before-tools) is a related but orthogonal per-request toggle already baked into the supervisor node via `_bake_interrupt_into_supervisor_node`.

---

## 6. Risk Indicators

- Truncation-indicator detection is duplicated in two places (`LangGraphAgent._get_truncation_indicator` and the module-level `get_truncation_indicator` in `langgraph_event_adapter.py`); a pre_model_hook-based check should reuse one of these rather than adding a third copy.
- Speculative: a `pre_model_hook` can only see `state["messages"]`/`state["llm_input_messages"]` going into the *next* model call — it runs before the LLM call, not after, so detecting that the *previous* call was truncated requires the hook to inspect the last message already in state (the truncated `AIMessage` LangGraph just appended) rather than a response it receives directly; this reshapes how `_check_for_truncated_response` is invoked compared to today's post-call, stream-consumer usage.
- Speculative: because `_compose_pre_model_hooks` merges hook return values into a single update dict, a hook that needs to *raise* rather than return state must be ordered/composed carefully so an exception from the truncation hook doesn't get swallowed by `_compose_pre_model_hooks`'s per-hook iteration (it currently has no try/except, so a raise there should propagate — but this interaction is unverified against a real compiled graph, per the coverage gap in Section 4).
- Speculative: implementing the abort-flag in fix item (2) needs the `pre_model_hook` closure to reach `ThreadedGenerator.is_closed()`/`cancellation_reason` from inside the node's worker thread; `_compose_pre_model_hooks` hooks today are built once per `LangGraphAgent.init_agent()` call and already close over `self`, so the mechanics exist, but no current hook reads disconnect/cancellation state — this is new, not an extension of an existing pattern.
- `ensure_langgraph_supervisor_compatibility()` already monkey-patches two `langgraph_supervisor`/`langgraph` internals; any upgrade of `langgraph_supervisor` (pinned 0.0.31) or `langgraph` (1.1.6) is a risk surface this fix should not casually touch, but should be aware of if the pre_model_hook approach turns out not to fully stop the worker-thread loop and a deeper patch (akin to `_bake_interrupt_into_supervisor_node`) becomes necessary.
- No test currently compiles a real `langgraph_supervisor` graph; the one existing supervisor test file mocks `create_supervisor` entirely, so the fix's own verification (fix item 3) has no scaffold to build on and will likely need new fixtures.
- Related ticket EPMCDME-14855 is described as "Ready for Review" and touching the same files; landing both in parallel risks a merge/rebase collision in `langgraph_agent.py` and `langgraph_event_adapter.py`, and EPMCDME-15638 (recursion limit 150→1000) would 7x the blast radius of this bug if sequenced before this fix lands.

---

## 7. Summary for Complexity Assessment

The change touches the agent runtime layer (`src/codemie/agents/langgraph_agent.py`, `src/codemie/agents/langgraph_event_adapter.py`) and the supervisor sub-layer (`src/codemie/agents/supervisor/pre_model_hooks.py`, `history.py`), plus a third-party integration boundary (`langgraph_supervisor==0.0.31`, `langgraph==1.1.6`) whose node-execution model (background-executor worker threads under `stream_mode=["updates","messages"], subgraphs=True`) is the actual trigger. The good news for scope: the extension point the fix needs — `pre_model_hook` on both the supervisor's own react node (via `create_supervisor(pre_model_hook=...)`) and each sub-agent's node (via `create_smart_react_agent(pre_model_hook=...)`) — already exists and is already used for unrelated purposes (history stripping, image artifacts, history compaction) through a shared `_compose_pre_model_hooks` composer; no new wiring mechanism is required, only a new hook function plus one more entry in each existing composition call.

Technical novelty is moderate: the detection logic (`_check_for_truncated_response`) is proven and already unit-tested for all three provider response-metadata shapes (OpenAI, Claude native, Bedrock camelCase); what's new is invoking an equivalent check from inside a node that runs before the model call rather than after it in a stream consumer, and (for fix item 2) threading a disconnect/cancel signal into that same hook. Both are straightforward within the existing pattern but unverified in this codebase — no current hook raises an exception, and no current hook reads `ThreadedGenerator` state.

Test coverage posture is the main risk: truncation detection itself is well covered at the unit level, but the one supervisor test file that could exercise a compiled graph patches `create_supervisor` out entirely, so the ticket's own acceptance criterion (a real compiled graph, truncated call, no further LLM calls) has no existing fixture to extend and will need new test scaffolding. Key risk factors: two duplicated truncation-indicator implementations to reconcile, an uncertain interaction between an exception raised from inside `_compose_pre_model_hooks` and its dict-merging loop, and two related in-flight tickets (EPMCDME-14855 touching the same files, EPMCDME-15638 changing the recursion-limit blast radius) that affect sequencing but are outside this analysis's scope to resolve.

---

## 8. External References

None named by the task. The task_context describes the bug and proposed fix inline (including internal file/path references such as `langgraph_event_adapter.py`, `pregel/main.py`, `_runner.py`) but does not point to an external document, guide, or source-of-truth path to read before researching; those internal paths were investigated directly as part of Section 2 above. The two related Jira keys (EPMCDME-14855, EPMCDME-15638) are tracker tickets, not filesystem/URL sources, and are not reflected yet in the current source tree (no `EPMCDME-14855` or `EPMCDME-15638` marker found anywhere under `src/`).
