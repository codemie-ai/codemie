# Technical Research

**Task**: workflow thought persistence callback
**Generated**: 2026-09-09T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14850 — Fix workflow thought-persistence regression introduced by commit 91a7f49fa (EPMCDME-14083: Add Switchyard model routing (proxy + agent paths)). Investigation summary (already completed by prior debugging session):

What broke it: Commit 91a7f49fa made two coupled changes that together destroy workflow thought persistence.

Change 1 — AgentStreamingCallback.on_llm_end now retains the thought (src/codemie/agents/callbacks/agent_streaming_callback.py:247). Before (2.48.0): always deleted after finalizing via storage.delete_thought(run_id). After (HEAD): only deletes when a routed model was resolved: `if model_resolved or isinstance(response, str): storage.delete_thought(run_id)`. In a normal (non-Switchyard-routed) call, response_tier is None so model_resolved = False, and the response is an AIMessage (not str), so the finalized thought is kept in in-memory storage instead of removed.

Change 2 — LangGraphEventAdapter now finalizes the same thought twice (src/codemie/agents/langgraph_event_adapter.py:178 and :199). The stop-turn LLM thought is now finalized from both stream modes for the same message.id: parse_message_type changed from elif to a separate `if is_finish_reason_stop` branch that fires _on_llm_end(run_id=message.id); parse_update_type gained a new `elif is_finish_reason_stop` branch that also fires _on_llm_end(run_id=message.id). Before, the 'updates' stream had no stop branch at all, and on_llm_end always deleted, so a second finalize was impossible.

The failure: Because the thought is retained between the two finalize calls, the workflow ThoughtQueue receives the same thought id with in_progress=False twice. ThoughtConsumer.consume() (thought_consumer.py:69-79, unchanged, no try/except) does `thought = WorkflowExecutionStateThought(id=thought_data.id, ...); thought.save(refresh=True)` (base.py:512 → session.add(self); session.commit() — plain INSERT). WorkflowExecutionStateThought is SQL-backed, save() is insert-only, so the second insert of the same primary key raises IntegrityError. With no exception handling, this kills the consumer thread, so no thoughts get persisted for the execution.

Impact: All workflow tests that rely on triggered_tools via persisted thoughts fail — no persisted thoughts means get_root(...) returns nothing, state.thoughts == [], triggered tools == []. Only workflow tests are affected (they persist thoughts through ThoughtQueue → ThoughtConsumer); Assistant/SSE tests stream through ThreadedGenerator and never touch the consumer.

Candidate fix layers identified by investigation (to be evaluated/decided at spec stage):
- Consumer (thought_consumer.py): guard the save()/dedupe already-persisted in_progress=False ids — robust regardless of duplicate emissions.
- Callback (on_llm_end): don't retain-then-refinalize the same id.
- Event adapter: avoid firing _on_llm_end for the same message.id from both stream modes.

This is a bug-fix task: root-cause a regression and land a fix (plus regression coverage) for workflow thought persistence.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/agents/callbacks/agent_streaming_callback.py` — `AgentStreamingCallback` (extends `StreamingStdOutCallbackHandler`). Holds per-author `ThoughtInMemoryStorage` dicts (`self._storages: dict[str | None, ThoughtInMemoryStorage]`), each a `dict[str, Thought]` subclass with `create_thought`/`update_thought`/`delete_thought` keyed by `str(run_id)`.
  - `on_llm_end` (lines 199–248): calls `_extract_response_routing(response)` to get `response_tier`/`response_cost`; computes `model_resolved = response_tier is not None` (line 234); updates the thought with `in_progress=False`; sends it via `_send_thought`; then at line 247 `if model_resolved or isinstance(response, str): storage.delete_thought(run_id)` — the retain branch cited in the ticket.
  - `on_llm_error` and `on_tool_end`/`on_tool_error` unconditionally call `storage.delete_thought(run_id)` after sending — only `on_llm_end` has the conditional retain.
  - `_send_thought` pushes a `StreamedGenerationResult(thought=thought, ...).model_dump_json()` into `self.gen` (a `ThoughtQueue` for workflow executions, a `ThreadedGenerator` for Assistant/SSE).
- `src/codemie/agents/langgraph_event_adapter.py` — `LangGraphEventAdapter.parse_message_type` (168–180) and `parse_update_type` (182–209), the two "agent" (non-supervisor) stream-chunk handlers.
  - `parse_message_type`: for `chunk_type == "messages"`. Line 176-178 comment: *"Use a separate `if` (not `elif`) so that a chunk carrying both content and a stop reason also triggers LLM-end finalization."* Calls `self.agent._on_llm_end(response=message, run_id=message.id)` when `is_finish_reason_stop(message)` and `metadata.get("langgraph_node") == "agent"`.
  - `parse_update_type`: for `chunk_type == "updates"`. Line 185 `is_finish_reason_tool_calls` branch (pre-existing) calls `_on_llm_end` before dispatching tool calls. Line 199 `elif is_finish_reason_stop(last_message):` is the newly-added branch (comment at 200-202: *"Simple chat completion (no tools): finalize the LLM thought. The 'messages' stream may not emit a dedicated stop chunk, so the 'updates' stream is the reliable trigger for this path."*) — also calls `self.agent._on_llm_end(response=last_message, run_id=last_message.id)`.
  - Both `parse_message_type` and `parse_update_type` chunks are dispatched from the same `process_chunk`/`process_chunk_for_agent` (279–284) per LangGraph stream turn, so both fire for the same stop-turn message when both `mode="messages"` and `mode="updates"` chunks are streamed for it.
  - The supervisor-path equivalents (`parse_supervisor_message_type` 211–226, `parse_supervisor_update_type` 228–258) do **not** have an analogous "updates" stop branch — `parse_supervisor_update_type` only finalizes via `handle_supervisor_tool_calls` (tool-call path) or reacts to handoff/tool-message branches, not a bare stop. This asymmetry matches the ticket's framing that the regression is specific to the plain-agent (non-supervisor) `parse_update_type` path.
- `src/codemie/service/workflow_execution/thought_consumer.py` — `ThoughtConsumer.consume()` (43–79): pulls `ThoughtQueueItem`s off `self.message_queue.queue`; `_update_thought_cache` (81–86) accumulates `message` text into `self.cache[thought_data.id]`; when `thought_data.in_progress` is falsy, constructs `WorkflowExecutionStateThought(id=thought_data.id, execution_state_id=..., ...)` and calls `thought.save(refresh=True)` (line 78), then `self.cache.pop(thought_data.id)` (line 79) — unconditionally, with **no try/except** anywhere in `consume()`.
- `src/codemie/rest_api/models/base.py` — `BaseModelWithSQLSupport.save()` (500–516): always does `session.add(self); session.commit(); session.refresh(self)` — a plain insert, not an upsert (`update()` at 518 uses `session.merge` but is a separate method never called by the consumer).
- `src/codemie/core/workflow_models/workflow_execution.py:47` — `WorkflowExecutionStateThought(BaseModelWithSQLSupport, table=True)`, table `workflow_execution_state_thoughts`, primary key `id` (inherited from `BaseModelWithSQLSupport`), fields `execution_state_id`, `parent_id`, `author_name`, `author_type`, `content`, `input_text`. `get_root(state_ids, ...)` (59–79) queries `execution_state_id IN state_ids AND parent_id IS NULL` — the read path the ticket says returns empty when the consumer thread has died.
- `src/codemie/core/thought_queue.py` — `ThoughtQueue.send()` (58–76) wraps each thought into a `ThoughtQueueItem(data=thought, context=ThoughtContext(...))` and puts it on `self.queue`; `ThoughtContext.execution_state_id` gates whether the consumer processes the item at all (thought_consumer.py:57-59).
- `src/codemie/rest_api/routers/utils.py:106` — `run_consumer_in_thread_pool(func, *args)` submits `instance.consume` to `consumer_executor` (a `ThreadPoolExecutor`, defined line 47). The returned `Future` is not awaited/`.result()`'d by the caller in the code paths inspected, so an unhandled exception inside `consume()` (e.g. the `IntegrityError`) silently ends the worker thread without surfacing to the workflow execution flow — consistent with the ticket's description of the consumer thread dying silently.

### Architecture and Layers Affected

- **Callback layer** — `AgentStreamingCallback` (`src/codemie/agents/callbacks/agent_streaming_callback.py`), specifically the in-memory `ThoughtInMemoryStorage` lifecycle around `on_llm_end`.
- **Agent/event-adapter layer** — `LangGraphEventAdapter` (`src/codemie/agents/langgraph_event_adapter.py`), specifically `parse_message_type` and `parse_update_type` chunk dispatch for the non-supervisor agent path.
- **Workflow execution / persistence layer** — `ThoughtConsumer` (`src/codemie/service/workflow_execution/thought_consumer.py`) and `WorkflowExecutionStateThought.save()` (`src/codemie/rest_api/models/base.py` → `src/codemie/core/workflow_models/workflow_execution.py`), which turn `ThoughtQueue` items into SQL rows.
- **Queueing layer** — `ThoughtQueue` (`src/codemie/core/thought_queue.py`), the transport between callback and consumer for workflow executions only; `ThreadedGenerator` is the parallel transport for Assistant/SSE and is unaffected because it has no consumer thread doing SQL inserts.

### Integration Points

- `AgentStreamingCallback.on_llm_end` → `_extract_response_routing` → `codemie.core.routing_info.compose_routing_info` / `default_routing_extractors` (Switchyard/LiteLLM routing metadata extraction) → `RoutingInfo` dataclass/model — this is the routing feature introduced by 91a7f49fa that added the `model_resolved` condition.
- `LangGraphEventAdapter` is driven by `self.agent` (a `LangGraphAgent`-shaped object per `src/codemie/agents/langgraph_agent.py`, not read in full here) via `_on_llm_end`, `is_finish_reason_stop`, `is_finish_reason_tool_calls`, `is_valid_ai_message` — these predicates are defined on the agent, not the adapter.
- `ThoughtQueue.send()` is called from `AgentStreamingCallback._send_thought` (`self.gen.send(...)`), i.e. the callback and the queue are coupled only through `self.gen`, which is chosen by the caller of `AgentStreamingCallback.__init__` (workflow executions pass a `ThoughtQueue`, Assistant/SSE pass a `ThreadedGenerator`).
- `run_consumer_in_thread_pool` → `consumer_executor` (`ThreadPoolExecutor`) in `src/codemie/rest_api/routers/utils.py` — shared thread pool infrastructure also used for producer/assistant execution (`run_producer_in_thread_pool`, `run_assistant_in_thread_pool`), so any consumer-side fix (e.g. wrapping `consume()` in try/except) is scoped to this one pool submission.

### Patterns and Conventions

- `BaseModelWithSQLSupport` exposes both an insert-only `save()` and a separate merge-based `update()` (`src/codemie/rest_api/models/base.py:500` and `:518`); the codebase already has an upsert-capable path (`update()` uses `session.merge`) that `ThoughtConsumer` does not currently use.
- Other callback methods (`on_llm_error`, `on_tool_end`, `on_tool_error`) all call `storage.delete_thought(run_id)` unconditionally right after `_send_thought`; `on_llm_end` is the only method with conditional deletion, introduced specifically for the Switchyard routing-metadata-arrival-order problem described in the code comment at lines 230-233 of `agent_streaming_callback.py`.
- `LangGraphEventAdapter` comments at both new branch sites (176-177, 200-202) explicitly document *why* each `if`/`elif` exists — both comments describe the desired single-finalization intent (catch a stop reason wherever it appears) without accounting for the case where it appears in both streams for the same message.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/workflows/langgraph-workflows.md` — directs workflow-behavior changes to `WorkflowExecutor`/nodes (`src/codemie/workflows/workflow.py`); does not cover the callback/event-adapter/thought-consumer chain this ticket touches, so it offers no direct guidance for this fix's location.
- `.ai-run/guides/development/error-handling.md` — states the project convention: use typed/shared exceptions and sanitized, context-specific error messages rather than swallowing exceptions silently; cites `src/codemie/rest_api/main.py:804` and `src/codemie/rest_api/security/authentication.py:109` as the pattern for catching-with-context. Directly relevant if the fix adds error handling in `ThoughtConsumer.consume()` — the guide's convention is "log with useful type and operation context," not a silent catch-all.
- `.ai-run/guides/testing/testing-patterns.md` — tests live beside the behavior under `tests/codemie/...`, mirroring `src/`; explicitly calls out a "seam test" convention: when a helper encapsulates a decision (e.g. `model_resolved` branching, or a duplicate-finalize guard), each callsite needs its own test observing the outer-boundary effect, not just an isolated unit test of the helper.

### Architectural Decisions

- No ADR files found in `docs/` for this domain. The only recorded rationale is inline code comments in `agent_streaming_callback.py` (lines 230-233, 244-246) and `langgraph_event_adapter.py` (lines 176-177, 200-202) explaining the Switchyard-routing-driven retain/finalize logic — these comments describe intent but predate the regression and do not address the duplicate-finalize interaction the ticket identifies.

### Derived Conventions

- Callback thought lifecycle convention observed across `on_tool_end`, `on_tool_error`, `on_llm_error`: send the final thought, then always delete from `ThoughtInMemoryStorage`. `on_llm_end`'s conditional retain is the outlier introduced by 91a7f49fa.
- `ThoughtConsumer` has no idempotency or dedupe concept today — `_update_thought_cache` keys purely by `thought_data.id`, and the terminal `save()` call is unconditional once `in_progress` is false.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/workflow_execution/test_thought_consumer.py` — comprehensive unit coverage of `ThoughtConsumer.consume()`/`_update_thought_cache`, all with `WorkflowExecutionStateThought` mocked out (`@patch('codemie.service.workflow_execution.thought_consumer.WorkflowExecutionStateThought')`). Notably `test_error_handling_during_save` (lines 525-540) sets `mock_thought_instance.save.side_effect = Exception("Database error")` and asserts `pytest.raises(Exception, match="Database error")` around `consumer.consume()`, with the comment *"should raise the exception since there's no try/except in the code"* — this test currently encodes the exact absence of error handling the ticket flags as the root failure mode, and would need updating if a consumer-side fix adds a try/except or dedupe guard.
- `tests/codemie/agents/callbacks/test_agent_streaming_callback.py` — has one `on_llm_end`-specific test, `test_on_llm_end_stamps_routed_model_and_cost_on_thought_and_metadata` (line 416), which exercises the Switchyard-routed path (`response_tier` present via `SwitchyardMeta` in `response_metadata`) and asserts on the emitted thought's `routing`/`metadata` fields and `message` content. It does **not** assert on `storage.delete_thought` / whether the thought remains in `ThoughtInMemoryStorage` after `on_llm_end`, and there is no test exercising the non-routed path (`response_tier is None`, `AIMessage` response) that the ticket identifies as the retain-trigger.
- `tests/codemie/agents/test_langgraph_event_adapter.py` — covers `parse_update_type` for tool-call argument serialization (`test_parse_update_type_serializes_list_args_as_json` and siblings, lines 36-156+). No test found for the `elif is_finish_reason_stop` branch (line 199) added by 91a7f49fa, and none exercising `parse_message_type` + `parse_update_type` together for the same `message.id` to check for a double `_on_llm_end` call.

### Testing Framework and Patterns

- pytest (declared in `pyproject.toml`, per `.ai-run/guides/testing/testing-patterns.md:13`).
- `unittest.mock.MagicMock`/`patch` are the dominant mocking tools in the thought-consumer and callback tests; `mock_agent` fixtures are used for `LangGraphEventAdapter` tests to stub `self.agent`'s predicate/handler methods.
- Real (non-mocked) `ThoughtQueue`/`ThreadedGenerator` fixtures (`real_thought_queue`, `real_thread_generator`) also exist in the callback test file for lighter-weight end-to-end-style checks (e.g. `test_agent_streaming_callback_with_thought_queue`, line 36).

### Coverage Gaps

- No test asserts `ThoughtInMemoryStorage` state (retained vs. deleted) after `on_llm_end` for the **non-routed** case (`response_tier is None`, `isinstance(response, AIMessage)`) — exactly the condition the ticket says leaks a thought.
- No test exercises `parse_message_type` and `parse_update_type` firing `_on_llm_end` for the *same* `message.id` in sequence (the double-finalize scenario) at the `LangGraphEventAdapter` level.
- No test in `test_thought_consumer.py` covers `consume()` receiving two `ThoughtQueueItem`s with the same `id` and `in_progress=False` (the duplicate-insert / `IntegrityError` scenario) — the closest existing test (`test_error_handling_during_save`) asserts the opposite: that an exception during `save()` propagates uncaught.
- No integration-level test found (searched `tests/codemie/workflows/`, `tests/integration/`, `tests/api/`) asserting that a full workflow execution persists thoughts end-to-end through `ThoughtQueue` → `ThoughtConsumer` → `WorkflowExecutionStateThought.get_root(...)`; existing workflow tests in `tests/codemie/workflows/` were scanned by filename and none reference `triggered_tools`, `WorkflowExecutionStateThought`, or `get_root` literally, so the regression's actual failure surface (workflow tests that assert on persisted-thought-derived state) could not be located by name in this pass — worth confirming directly with whoever ran the failing suite, since the term "triggered_tools" does not appear anywhere in the repository (`grep -rl "triggered_tools"` returned no matches in `src/` or `tests/`).

---

## 5. Configuration and Environment

### Environment Variables

- `SWITCHYARD_ENABLED` (`src/codemie/configs/config.py:853`) — "Master switch for Switchyard routing; False disables all routers," default `False`. When disabled (the common/default case), `response_tier` will virtually always be `None` in `on_llm_end`, meaning `model_resolved` is `False` for effectively all calls — so the regression's retain-branch is not a rare edge case but the default behavior for any deployment without Switchyard routing turned on.
- `SWITCHYARD_CLASSIFIER_MODEL` (`config.py:850`) — configures the routing classifier model; relevant only to when `response_tier`/`response_cost` get populated, not to the persistence bug itself.

### Configuration Files

- `src/codemie/configs/config.py` — central settings module defining `SWITCHYARD_ENABLED`/`SWITCHYARD_CLASSIFIER_MODEL`; no other config file found that toggles thought persistence or the consumer thread pool behavior.

### Feature Flags and Deployment Concerns

- `SWITCHYARD_ENABLED` acts as the only relevant flag in this area, but it does not gate the regression — the retain condition in `on_llm_end` (`model_resolved or isinstance(response, str)`) evaluates the *same way* whether Switchyard is enabled-but-not-routed-this-call or fully disabled, so the flag does not scope the blast radius the way its name might suggest.
- No Dockerfile, CI/CD, or deploy-template references to `thought_consumer`, `agent_streaming_callback`, or `langgraph_event_adapter` were found; this is an application-code-only regression with no deployment-manifest dimension.

---

## 6. Risk Indicators

- **Speculative**: A consumer-side fix that adds error handling or dedupe to `ThoughtConsumer.consume()` will directly conflict with the existing test `test_error_handling_during_save` (`tests/codemie/service/workflow_execution/test_thought_consumer.py:525-540`), which currently asserts that a `save()` exception propagates uncaught — that test's intent will need to be revisited as part of any consumer-layer change, not just left passing incidentally.
- The retain condition in `on_llm_end` (`agent_streaming_callback.py:247`) is keyed off `model_resolved`, which is `False` whenever `SWITCHYARD_ENABLED=False` (the default) or a call simply wasn't routed — meaning this is not a narrow Switchyard-only regression but affects the default/common code path for any non-routed LLM turn in a workflow execution.
- The duplicate-finalize risk is asymmetric across agent shapes: the non-supervisor path (`parse_message_type` + `parse_update_type`, both operating on `self.agent`) has the new stop branch in both stream-mode handlers, while the supervisor path (`parse_supervisor_message_type` + `parse_supervisor_update_type`) does not have an analogous bare-stop branch in its "updates" handler — a fix localized only to the adapter needs to account for (or explicitly scope out) this asymmetry rather than assuming a single shared code path.
- `run_consumer_in_thread_pool`'s `Future` return value is not observed by the caller in the code inspected — an unhandled exception inside `consume()` fails silently from the workflow execution's perspective (no error surfaces upstream), which is itself a secondary risk independent of this specific regression: any future exception in the consumer thread (not just the duplicate-insert case) will silently stop thought persistence for that execution with no visible signal.
- No integration/e2e test was located that exercises the full `ThoughtQueue → ThoughtConsumer → WorkflowExecutionStateThought.get_root()` path for a live workflow execution; all existing coverage for this chain is unit-level with the DB model mocked out, so a regression test proving the fix (not just the component-level cause) will likely need new test infrastructure or an existing workflow-execution test fixture not yet identified.
- The term "triggered_tools" from the ticket's impact description does not appear literally anywhere in `src/` or `tests/` in this repository — the specific failing test(s) referenced by the prior debugging session could not be located by name and should be confirmed directly before scoping regression-test changes.

---

## 7. Summary for Complexity Assessment

The regression's root cause spans three layers already precisely located by the prior investigation: the callback layer (`AgentStreamingCallback.on_llm_end`'s conditional `delete_thought`, `agent_streaming_callback.py:247`), the event-adapter layer (`LangGraphEventAdapter.parse_message_type`/`parse_update_type`'s two independent stop-reason branches, `langgraph_event_adapter.py:178` and `:199`), and the workflow-persistence layer (`ThoughtConsumer.consume()`'s unconditional insert-only `save()`, `thought_consumer.py:78`, backed by `BaseModelWithSQLSupport.save()` in `base.py:500`, which has no upsert variant in use). All three files and their exact line numbers were confirmed present and unchanged from the ticket's description. This is a well-bounded bug fix rather than new-feature work — the change surface is a handful of files with a small number of candidate fix points, not a new architectural layer.

Test coverage is a genuine gap rather than a solved problem: `test_thought_consumer.py` currently has a test (`test_error_handling_during_save`) that explicitly asserts the *absence* of error handling that a consumer-side fix might need to change; `test_agent_streaming_callback.py` has no assertion on `ThoughtInMemoryStorage` retention/deletion state for the non-routed `on_llm_end` path; and `test_langgraph_event_adapter.py` has no test exercising both stop-reason branches for the same `message.id`. No integration-level test proving end-to-end thought persistence through the full `ThoughtQueue → ThoughtConsumer → get_root()` chain was found, so validating the fix will likely require either new integration test infrastructure or precise identification of the originally-failing workflow test suite (which could not be located by the "triggered_tools" terminology used in the ticket).

Key risk factors: three plausible, non-mutually-exclusive fix layers (callback, adapter, consumer) each with different blast radii and different existing-test conflicts; a default-configuration (not edge-case) trigger condition (`SWITCHYARD_ENABLED=False` is the default, so `model_resolved` is `False` for virtually all calls); an asymmetry between the supervisor and non-supervisor event-adapter paths that a narrowly-scoped adapter fix could miss; and silent failure propagation from the consumer thread pool (`run_consumer_in_thread_pool`'s `Future` is not awaited), which means any fix should also consider whether consumer-thread failures ought to surface at all going forward.

---

## 8. External References

None named by the task. The task_context cites internal commit `91a7f49fa` (EPMCDME-14083, present in this repository's `git log`) and internal file paths/line numbers (`src/codemie/agents/callbacks/agent_streaming_callback.py`, `src/codemie/agents/langgraph_event_adapter.py`, `src/codemie/service/workflow_execution/thought_consumer.py`), all of which are inside this repository and were read directly as part of Section 2 rather than being an external source of truth.
