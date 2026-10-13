# Script Runs and Tool Calls in Analytics Implementation Plan

> **Status (2026-10-13), read first.** This plan was written for the spec as approved on 2026-10-09 and was not updated afterwards. What changed: **Task 5 (skill script-run metric) is withdrawn**, the ticket owner moved script origin to a separate story (2026-10-12); the estimator of Task 6 is `tiktoken_v1`, not `bytes4_v1`. Built after the approval and not planned here: spec sections 5 (attribution of direct calls), 7 (sandbox runs and capacity), 8 (tool run latency), the concurrency numbers (`CallOutcome.concurrency`, `peak_parallel`, gate waits) and the Kibana dashboard. The spec is the source of truth; the tasks below are history.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Count script-made tool calls, refusals and estimated token savings in analytics (skill script runs: withdrawn, see the status note).

**Architecture:** Extend the existing `CallOutcome` / `on_settled` seam with `seq` and `request_bytes`. A per-run observer in `service/script_tool_calls/` collects outcomes and handler detail. A fan-out helper feeds a metric listener. `AgentWorkspaceService.execute_workspace_script` and `ExecuteWorkspaceScriptTool` emit run-end records in `finally`.

**Tech Stack:** Python, pytest, OpenTelemetry-backed `BaseMonitoringService`.

Commit per task using the repository's existing convention. Spec: `spec.md`; research: `technical-analysis.md` (same dir). Quality gates and manual verification are run by the calling flow, not here.

**Verified lookups (plan time):**
- Payload fields are `name` and `args` (`script_tool_calls/handler.py:49,114-117`); `request_bytes` = UTF-8 bytes of name + serialized `args`.
- `tokens_size_limit` is set only on assistant-agent tools (`assistant_agent.py:388`, `langgraph_agent.py:1178`) and does not apply to bridge results. Bridge results are bounded by `max_payload_bytes`. Cap each inner-call output contribution at `assistant.tools_tokens_size_limit` when known, else at the `max_payload_bytes` byte equivalent. Confirm in Task 6.
- Ledger `AgentWorkspaceFolderOwner` has no checksum column. The seed `reference.checksum` (`agent_workspace_repository.py:50,189`) lives on the seeded file record. Task 5 checks whether the workspace file checksum can be compared; if not, attribute by folder and document the overwrite risk.
- Bare tool-step model availability is not confirmed. Task 2 verifies it. Use an empty `llm_model` if absent.

## Acceptance criteria
Per `spec.md` (spec path was supplied, so the criteria are not repeated here).

negative-constraints: honored as follows.
- No tokens in `codemie_tools_usage_tokens` for script calls: Task 3 uses a new emit method, never `send_tool_metrics`.
- No script path label on call metrics: Tasks 3 and 6 omit it. No metric carries a script path (the skill script-run metric of Task 5 is withdrawn).
- No platform fields on `CallOutcome`: Task 1 adds only `seq` and `request_bytes`.
- Handler signature unchanged: Task 1 uses the contextvar `current_call_seq()`.
- No schema change, cost figures, new reader or dashboard, and no trace or audit listener: no task touches them.
- Estimates never summed into the tools-usage tokens: Task 6 uses a separate metric name.
- Workflow tool step and REST runs get no `script_tool` records: Task 6.

## Task 1: Call event fields (seq, request_bytes, current_call_seq)
**Files:** Modify `src/codemie_tools/data_management/code_executor/tool_call_protocol.py` (`CallOutcome`, `RequestDispatcher.handle`, `_answer` ~260-275), `tool_call_channel.py` (`_settle_unfinished`, in-flight tracking). Tests: `tests/codemie_tools/data_management/code_executor/test_tool_call_protocol.py`, `test_tool_call_channel.py`, `test_job_bridge.py`.
Test-first: yes — the outcome carries a distinct `seq` per call even when `call_id` repeats; `request_bytes` equals name + args bytes; `request_bytes == 0` for oversize, bad-encoding and unparseable requests (failure branches); an abandoned outcome keeps the in-flight `seq`; a handler reads `current_call_seq()` during `handle`.
- [ ] Add `seq: int` and `request_bytes: int` to `CallOutcome`. Add a dispatcher counter and `_current_call_seq` contextvar with a public `current_call_seq()`, set and reset in the same try/finally as `_current_deadline`/`_current_cancel`. Compute `request_bytes` after `parse_request`, 0 otherwise.
- [ ] Channel: track `seq` per in-flight `call_id` (replace the bare id set) so `_settle_unfinished` builds outcomes with it and `request_bytes=0`.
- [ ] Update positional `CallOutcome(...)` constructions in the three test files.

## Task 2: Run observer, fan-out, context fields, service wiring
**Files:** Create `src/codemie/service/script_tool_calls/observer.py` (`ScriptRunObserver`) and `fanout.py`. Modify `context.py` (`ScriptRunContext` gains `assistant_name`, `llm_model`, `project`, `user_id`), `binding.py` and `toolkit.py` (fill from tool metadata in the request thread, project with the billing fallback), `handler.py` (`build_tool_call_handlers(..., observer)` records resolved name and tool type under `current_call_seq()` after authorization), `src/codemie/service/agent_workspace_service.py:582-622` (optional `observer`; create one if absent; pass `on_settled=fanout`), `execute_workspace_script_tool.py` (create and pass the observer). Tests: `tests/codemie/service/script_tool_calls/test_observer.py`, `test_fanout.py`, `test_context.py`, `test_handler.py`, `test_end_to_end.py`, `test_workflow_tool_step_scope.py`.
Test-first: yes — a settled outcome is counted by the observer first, a raising listener does not hide the next one, a late settle after close is not aggregated, the detail table maps `seq` to the resolved name (`unknown` when unresolved or abandoned), and the context captures the request-thread project and user id.
- [ ] Observer holds the detail table, per-group counts, per-tool request/response byte sums for success-delivered calls, attribution, `source` in `{script, test_run}`, skill lookup result and a closed flag under a lock. `close()` returns one snapshot.
- [ ] Verify whether a bare tool step has a model (`virtual_assistant_service.py:77`, `toolkit.py:_workflow_project`) and record the result in the context test.
- [ ] Run end: the service reads and closes the observer in `finally`.

## Task 3: Usage and refused metrics for script calls
**Files:** Create `src/codemie/service/script_tool_calls/outcome_metrics.py` (`classify()` and the listener). Modify `metrics_constants.py` (`codemie_script_tool_calls_refused_total`, `invoked_by`, `error_code`, `status` labels in one place), `service/analytics/metric_names.py` (`MetricName`), `service/monitoring/agent_monitoring_service.py` (new `send_script_tool_call_metrics`, no tokens). Tests: `tests/codemie/service/script_tool_calls/test_outcome_metrics.py`, `tests/codemie/service/monitoring/test_agent_monitoring_service.py`.
Test-first: yes — one parametrized test over the spec outcome map; a contract test asserts every protocol/SDK code is classified; success writes usage total only (never `codemie_tools_usage_tokens`); a refusal writes only the refused counter; abandoned and withdrawn write usage with `status=abandoned`; attributes include `base_tool_name` and `invoked_by=script`.
- [ ] Emit with explicit attributes captured in the context (the polling thread has no request contextvars). Bare tool step: no assistant, user and project from the project scope. REST `no_context`: refused records only.

## Task 4: Leaderboard script filter
**Files:** Modify `src/codemie/service/leaderboard/collector.py:~901` (exclude `invoked_by=script` from the `tool_name` cardinality only). Test: `tests/codemie/service/leaderboard/` (existing collector test file).
Test-first: yes — the built query excludes `invoked_by=script` for `unique_tools` while the totals terms stay unchanged.

## Task 5: Skill script-run metric and ledger lookup
**Files:** Modify `src/codemie/repository/agent_workspace_repository.py` (`get_folder_owner_by_folder(workspace_id, folder)`), `skill_monitoring_service.py` (`send_skill_script_run`), `metrics_constants.py`, `metric_names.py`, `agent_workspace_service.py` (resolve the owner once before the run; emit `codemie_skill_script_run` in `finally` with `status`, `skill_script` relative to the ledger folder and `bridge_calls`, entry script only). Tests: repository test, `tests/codemie/service/test_agent_workspace_service.py`.
Test-first: yes — a skill-seeded script emits one record with `skill_script`, `status` and `bridge_calls`, also on failure and for a REST direct run (no context); a non-ledger script emits none; `codemie_skill_tool_invoked` is untouched.
- [ ] Apply the checksum decision from the verified lookups: compare only if the seeded file checksum is readable, otherwise attribute by folder.

## Task 6: Estimated savings metric
**Files:** Create `src/codemie/service/script_tool_calls/token_estimate.py` (`estimate_tokens(bytes) = ceil(bytes/4)` and the emit helpers). Modify `metrics_constants.py`, `metric_names.py`, `agent_workspace_service.py` (emit `side=inner_calls` per run and resolved tool from the observer snapshot, input = `request_bytes`, output = `response_bytes`, capped per call), `execute_workspace_script_tool.py` (`finally`: `side=script_tool`, input = tool arguments + script code bytes, output = bytes of the returned string, 0 on raise; only when `scope_kind == ASSISTANT`; labels `bridge_calls`, `bridge_ok`). Tests: `test_token_estimate.py`, `tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py`.
Test-first: yes — inner_calls records are aggregated per run and tool and exclude non-success calls; script_tool records are emitted when the run raises, with `bridge_calls=some` and `bridge_ok` set from the observer; a workflow tool step or REST run emits no script_tool record; all four series use the same estimator; nothing is written to `codemie_tools_usage_tokens`.
- [ ] Confirm the cap source (see verified lookups) and use the label `estimator=tiktoken_v1`.

## Task 7: Documentation
**Files:** Modify `docs/stories/2026-09-28-custom-tools/architecture-spec.md` (D23 per spec amendments), `src/codemie_tools/data_management/code_executor/README.md` (replace "nothing listens yet"), `docs/workspace-script-sdk.md` only if user-visible.
Test-first: no — documentation only; edit the three spots named above.
