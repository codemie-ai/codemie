# Technical Research

**Task**: analytics metrics tools-usage script-bridge workspace skills (EPMCDME-15566)
**Generated**: 2026-10-09
**Research path**: filesystem

---

## 1. Original Context

Implement Jira EPMCDME-15566 "Script Runs and Their Tool Calls in Analytics". The full ticket text (context, acceptance criteria, decisions) is in docs/superpowers/tasks/2026-10-09-epmcdme-15566-script-token-savings-analytics/ticket.md — read it first. Repo: /Users/yanaasadchaya/Projects/epam/codemie/codemie, current branch builds on EPMCDME-15558 (skill scripts in workspace; see recent commits and AgentWorkspaceService.execute_workspace_script). Find: the existing tools-usage metrics (codemie_tools_usage_*, codemie_skill_tool_invoked), where metrics are emitted/callbacks, the script-execution tool (execute_workspace_script), the codemie_runtime_sdk / tool-call bridge/dispatcher channel that scripts use to call platform tools (and whether a call event/listener seam already exists), run-context object (assistant/model/user/project), workspace seed ledger (skill attribution), workflow tool-step execution path. ALSO research for a user-requested extra: what data is available to estimate tokens saved by calling tools via a script vs directly from the model (tool result sizes that never enter model context, token counting utilities, existing token metrics, CLI analytics storage), so we can brainstorm that analytics.

(feature_area: analytics metrics tools-usage script-bridge workspace skills)

---

## 2. Codebase Findings

### Existing Implementations

Metrics (constants and emission)
- `src/codemie/service/monitoring/metrics_constants.py:15-17` — `TOOLS_USAGE_TOTAL_METRIC` (`codemie_tools_usage_total`), `TOOLS_USAGE_TOKENS_METRIC`, `TOOLS_USAGE_ERRORS_METRIC`; `:45-51` skill metrics (`SKILL_TOOL_INVOKED_METRIC`, `SKILL_TOOL_COMPANION_FILE_INVOKED_METRIC`, ...). `MetricsAttributes` class holds attribute keys.
- `src/codemie/service/analytics/metric_names.py:53-55,88,156-162,195` — `MetricName` enum mirror used by analytics readers (tools usage names, `SKILL_TOOL_INVOKED`; lists at 156-162 and 195 include the three tools metrics). A new metric (`codemie_script_tool_calls_refused_total`, `codemie_skill_script_run`) has no entry in either file today.
- `src/codemie/service/monitoring/agent_monitoring_service.py` — `AgentMonitoringService.send_tool_metrics(tool_name, success, output_tokens_used=0, tool_metadata=None, additional_attributes=None)`. Attributes: TOOL_NAME, ASSISTANT_NAME (`metadata[AGENT_NAME]`), LLM_MODEL, USER_NAME, TOOL_TYPE (default `ToolType.INTERNAL`), PROJECT (`get_current_project(fallback=metadata.get(PROJECT))`); `additional_attributes` is merged last. Success emits `..._total` and `..._tokens` (count = output tokens); failure emits only `..._errors_total`. This is a ready seam for `invoked_by="script"` and `error_code`, but it always emits a tokens metric on success (ticket decision 3 says none for script calls) so a script path needs either a flag or its own method.
- `src/codemie/service/monitoring/skill_monitoring_service.py` — `SkillMonitoringService` (skill_id, skill_name, project, user attributes; `send_skill_tool_invoked...` style methods). Home for a `codemie_skill_script_run` method.
- `src/codemie/agents/callbacks/monitoring_callback.py` — `MonitoringCallback.on_tool_start` stores `{"metadata": metadata, "name", "base_tool_name"}` in `tools_run_map`; `on_tool_end` calls `send_tool_metrics(output_tokens_used=calculate_tokens(output), success=True, additional_attributes={"base_tool_name": ...})`; `on_tool_error` passes `error_class` and tokens of `str(error)`. Direct calls carry `base_tool_name` which the tools-usage view groups on.
- Tool metadata (assistant name, model, user, project) is stamped on each tool at `src/codemie/agents/assistant_agent.py:363-379` (`_configure_tools`: REQUEST_ID, USER_ID, USER_NAME, LLM_MODEL, AGENT_NAME, PROJECT, plus request metadata) and `src/codemie/agents/langgraph_agent.py:1155-1169`. The script tool therefore has `.metadata` at run time, but `ScriptRunContext` does not carry it.
- Reader: `src/codemie/service/analytics/handlers/tools_handler.py` — `get_tools_usage` groups by `attributes.base_tool_name.keyword`; sub-aggregations filter by metric name for TOTAL (count, distinct `attributes.user_id`, distinct `attributes.assistant_name`), TOKENS (sum of `attributes.count`), ERRORS. Other readers reference the same metrics: `analytics_service.py`, `handlers/engagement_handler.py`, `handlers/assistant_handler.py`, `handlers/cli/handler.py`, `rest_api/routers/analytics.py`, and `src/codemie/service/leaderboard/collector.py:880-905` (sums `codemie_tools_usage_total` + `codemie_skill_tool_invoked` as activity terms) — new labelled records in the same metric name will flow into all of these, including the leaderboard.

Script tool and run
- `src/codemie_tools/data_management/workspace/execute_workspace_script_tool.py` — `ExecuteWorkspaceScriptTool(CodeMieTool)`; fields `conversation_id`, `user`, `workspace_service`, `workspace_id`, `script_registry: ScriptToolRegistry | None`, `tool_calling: ToolCallingSettings | None`. `execute()` calls `workspace_service.execute_workspace_script(workspace_id, script_path, user, export_files, run_context=self._run_context(), tool_calling=...)` and returns JSON of the response (output, workspace_files, warnings).
- `src/codemie_tools/data_management/workspace/toolkit.py:87-93,160` — builds the tool with `ScriptToolRegistry(self.user, workflow_project=self._workflow_project())`; `_workflow_project` returns the project only when the assistant is a `VirtualAssistant` with `is_tool_step`.
- `src/codemie/service/agent_workspace_service.py:582-622` — `execute_workspace_script(...)`: gets workspace and input files, `resolve_tool_calling_settings()` if none given, builds `new_job_bridge_options(settings, build_tool_call_handlers(run_context or ScriptRunContext.without_context(user), max_payload_bytes=...))` (NO `on_settled` passed), runs `WorkspaceScriptRunner.execute_script(script_path, export_files)`, syncs files, returns `ExecuteWorkspaceScriptResponse`. This is where the `codemie_skill_script_run` metric (decision 6) would be emitted at end of run; it currently has no skill/ledger logic and no metrics.
- The REST route `src/codemie/rest_api/routers/agent_workspace.py` also calls it (direct run, no run context -> `no_context`).

Bridge / dispatcher / call event seam (already partly exists)
- `src/codemie_tools/data_management/code_executor/tool_call_protocol.py` — `CallOutcome` (frozen dataclass: `call_id, op, tool, code, duration_seconds, response_bytes, delivered, withdrawn`; docstring says it is "the one record that the trace, the metric and the audit of later stories consume"), `DispatchResult(body, outcome)`, `RequestDispatcher.handle(request, cancelled)` which builds the `CallOutcome` for every request (success: `code=None`; failures: `code` = the refusal/error code; `tool` = payload `name` string even when unresolved), `ToolCallRefused(code, message)`, `CallDeadline`.
- `.../tool_call_channel.py` — `ToolCallChannel` (poll thread + worker pool). `SettledCallback = Callable[[CallOutcome], None]` (line 94); constructor takes `on_settled`; `_settle()` invokes it guarded (a listener exception is logged, never breaks the channel). `_write_finished` settles with `delivered=True`, or `delivered=False, withdrawn=True` when the script stopped waiting; `_settle_unfinished` settles calls unfinished at run end as `CallOutcome(call_id, None, None, None, 0.0, 0, delivered=False, ...)` — op/tool are lost for these (relevant to "abandoned" status: the tool name is not on that outcome).
- `.../job_bridge.py` — `JobBridgeOptions(exchange_dir, settings, handlers, on_settled)`, `new_job_bridge_options(settings, handlers=None, on_settled=None)`, `JobToolCallBridge.start` wires `on_settled=self._options.on_settled` into the channel. So the single event (`CallOutcome`) and a listener hook (`on_settled`) EXIST but nothing in `src/codemie` supplies a listener. A single-listener Callable, not a multi-listener registry; ticket wants one listener per output (metric, trace step, audit), so a fan-out layer is still to be built.
- `src/codemie/service/script_tool_calls/handler.py` — `build_tool_call_handlers(context, gate=None, max_payload_bytes)` returns `{TOOL_CALL_OP: handler}`; `_handle_tool_call`: parse payload -> `authorize_tool_call` -> `ScriptCallable` check -> `_validated_kwargs` -> `gate.hold()` + `suppress_call_logging()` -> `tool.run_for_script(kwargs, max_result_bytes)` -> `_envelope`. Failure mapping: `tool_failed` (any tool exception), `payload_too_large`, `bad_arguments`, `tool_unavailable`. The handler has the resolved `tool` (so tool_type/name are known) but this information is not placed in the `CallOutcome` (only the raw payload `name`).
- `.../authorizer.py` — `authorize_tool_call`: script tool blocked by name (`tool_blocked`), `scope.resolve(name)`; none/excluded -> `tool_unavailable`; `NoScope.resolve` raises `no_context`. `.../exclusions.py` excluded set.
- `.../context.py` — `ScriptRunContext(user, scope, scope_kind)` frozen dataclass; NO assistant name, model, or project fields (ticket sub-task 1 adds them). `ScriptScopeKind {NONE, ASSISTANT, WORKFLOW}`. `ToolListScope(tools, assistant_id)`; `ProjectScope(user, project, resolver)` (workflow tool step; exposes `.project`); `ScriptToolRegistry(user, workflow_project)` with `fill(tools, assistant)` (keeps only `assistant.id`) and `context()`.
- `.../binding.py` — `bind_script_registries(tools, assistant)` fills each script tool's registry once the tool list is final (the point where assistant and, via metadata, model are known).
- `.../workflow_resolution.py` — `resolve_workflow_tool` builds a tool by name via a temporary virtual assistant (`VirtualAssistantService.create_from_tool_invocation`) for the workflow `ProjectScope`.
- `.../concurrency.py` — `ToolRunGate` / `process_gate()` (process-wide cap `WORKSPACE_SCRIPT_TOOL_CALLS_MAX_CONCURRENT`, hold time `WORKSPACE_SCRIPT_TOOL_CALL_MAX_HOLD_SECONDS`).
- SDK: `src/codemie_tools/data_management/code_executor/runtime_sdk/codemie_runtime_sdk.py` (codes `CODE_TOOL_FAILED`, `CODE_BAD_ARGUMENTS`, `CODE_TOOL_UNAVAILABLE`, `CODE_TOOL_BLOCKED`, `CODE_NO_CONTEXT`, `CODE_PAYLOAD_TOO_LARGE`, `CODE_TIMEOUT`, `MAX_PAYLOAD_BYTES`, `TOOL_CALL_OP`); `call_tool`, `call_tools` (batch up to 32). Reference for users: `docs/workspace-script-sdk.md`.
- Limits: `tool_calling_limits.py` — `ToolCallingSettings(max_payload_bytes, max_parallel_calls=5, ...)`, `RunBudget`, `MAX_RUN_TIMEOUT_SECONDS=480`; gate by customer-config feature `features:workspaceScriptBridge` (`src/codemie/service/workspace_script_bridge.py`).

Workspace seed ledger (skill attribution)
- The ledger is the table `AgentWorkspaceFolderOwner` (`agent_workspace_folder_owners`; columns `workspace_id`, `source_kind` (`SOURCE_KIND_SKILL = "skill"`), `source_id` (skill id), `folder`) in `src/codemie/rest_api/models/agent_workspace.py:54-82`; unique per (workspace, source) and (workspace, folder).
- `src/codemie/repository/agent_workspace_repository.py:150-205` — `seed_skill_references(...)` writes it; `get_folder_owner(workspace_id, source_kind, source_id)` reads by source (lookup by folder is not provided; a script-path -> skill lookup would need a folder-based query or listing owners). No consumer of `get_folder_owner` outside the repository (grep).
- `src/codemie/service/skill_workspace_seeder.py` (`SkillWorkspaceSeeder.ensure_seeded`), `skill_workspace_preparation.py` (`prepare_skill_workspace`), `file_service/workspace_path_rules.py` (`skill_workspace_folder` — folder name includes a skill id fragment per commit 5a369a8dc).

Workflow tool-step path
- `src/codemie/workflows/nodes/tool_node.py:294` — `result = tool.execute(**tool_input)` directly (no LangChain callbacks), confirming ticket text that a bare tool step is not counted by `MonitoringCallback`. The tool step's assistant is a `VirtualAssistant` built with `is_tool_step=True` (`src/codemie/service/assistant/virtual_assistant_service.py:77`, model field `src/codemie/rest_api/models/assistant.py:1394`), which `toolkit.py` turns into `ProjectScope`. Tests: `tests/codemie/service/script_tool_calls/test_workflow_tool_step_scope.py`.

### Architecture and Layers Affected
- Tool/bridge layer (`codemie_tools/data_management/code_executor`): `CallOutcome`, dispatcher, channel, `JobBridgeOptions.on_settled`.
- Service layer (`codemie/service/script_tool_calls`, `agent_workspace_service`): handler, context, registry, listener wiring.
- Monitoring/metrics layer (`codemie/service/monitoring`): `AgentMonitoringService`, `SkillMonitoringService`, `metrics_constants`.
- Analytics read layer (`codemie/service/analytics`): `metric_names.py`, `tools_handler.py`.
- Persistence: ledger table read (no schema change observed to be required to read it).
- Workflow layer: `tool_node.py` / virtual assistant for tool step (verification only).

### Integration Points
- `agent_workspace_service.execute_workspace_script` -> `build_tool_call_handlers` + `new_job_bridge_options` -> `JobToolCallBridge` -> `ToolCallChannel` -> `RequestDispatcher` -> handler -> `tool.run_for_script`.
- Metrics sink: `BaseMonitoringService.send_count_metric` (OpenTelemetry/ES metrics store read by `MetricsElasticRepository`).
- Contextvars: handlers run in a copy of the request context (`job_bridge._run_in_request_context`), so `get_current_project()` used by `send_tool_metrics` is available in the handler thread; the `on_settled` callback runs on the channel's polling thread (via `_settle`) and is NOT wrapped in that context copy (observation from `job_bridge.py`/`tool_call_channel.py`; needs verification before relying on `get_current_project` there).

### Patterns and Conventions
- Metric emission via `*MonitoringService` classmethods deriving `BaseMonitoringService`; metric names in `metrics_constants.py` + `MetricName` enum; attribute keys in `MetricsAttributes`.
- Listener errors are swallowed and logged (`_settle`); argument values and result content never in logs/messages (handler docstring).
- Frozen dataclasses for protocol records; `ToolCallRefused(code, message)` for coded refusals; no sensitive content in logs (`suppress_call_logging`).
- Layout/test conventions per `.ai-run/guides/` (see Section 3).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/agents/*` (agent tools, tool overview), `.ai-run/guides/development/logging-patterns.md`, `configuration-patterns.md`, `testing/*`, `quality-gates.md` exist; this analysis did not re-read them (not specific to metrics). `.ai-run/guides/integration/cli-analytics-storage.md` and `cli-analytics-events.md` cover the separate Postgres CLI analytics (token columns).
- `docs/workspace-script-sdk.md` — user-level reference for the SDK and what a script may call.
- `src/codemie_tools/data_management/code_executor/README.md` — operator section "Workspace Script Bridge" (rollout gates, limits).
- Story docs: `docs/stories/2026-09-28-custom-tools/architecture-spec.md` (decision D23, v5), `children/script-analytics/{story.md,technical-analysis.md,complexity-assessment.json}`, `children/script-trace-breakdown/*` (EPMCDME-15561 consumer), `children/script-tool-calls/subtasks/*`. The earlier `script-analytics/technical-analysis.md` (2026-10-02) is consistent with the findings here but predates the discovery of `CallOutcome`/`on_settled`, which already exist in code.

### Architectural Decisions
- D23 (architecture spec v5): one call event emitted by the channel's dispatcher, one listener per output (metric, trace step, audit); this story builds event/emission/listener seam first. Ticket decisions 1-6 (2026-10-06) in `ticket.md`.
- Code comment in `tool_call_protocol.py` (`CallOutcome` docstring) already names metric/trace/audit as the consumers.

### Derived Conventions
- Settled-callback pattern: callbacks must never raise into the channel.
- Tool-level script contract: `ScriptCallable` + `run_for_script` + `script_callable` opt-in flag.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/script_tool_calls/` — `test_handler.py`, `test_authorizer.py`, `test_context.py`, `test_concurrency.py`, `test_end_to_end.py` (real SDK/handler through the channel, incl. result-above-model-token-limit tests at lines ~140-166), `test_error_code_contract.py`, `test_exclusions.py`, `test_workflow_tool_step_scope.py`.
- `tests/codemie_tools/data_management/code_executor/` — `test_tool_call_protocol.py`, `test_tool_call_channel.py`, `test_job_bridge.py`, `test_tool_calling_limits.py`.
- `tests/codemie/agents/callbacks/test_monitoring_callback.py` (parametrized on_tool_end/on_tool_error asserting `send_tool_metrics` kwargs) and `tests/codemie/service/monitoring/test_agent_monitoring_service.py` (asserts TOTAL/TOKENS metric emission).

### Testing Framework and Patterns
- pytest (see `pytest.ini`); fixtures, `tmp_path`, mocked `AgentMonitoringService`; end-to-end tests drive the SDK module against a real channel/handler.

### Coverage Gaps
- No test for `on_settled` wired from `src/codemie` (no production listener exists); no tests for ledger lookup by folder/script path; no analytics reader tests for `invoked_by`/refused counter (not present yet); `execute_workspace_script` metric emission untested (none exists).

---

## 5. Configuration and Environment

### Environment Variables
- `src/codemie/configs/config.py:576-578` — `WORKSPACE_SCRIPT_TOOL_CALLS_MAX_CONCURRENT` (10), `WORKSPACE_SCRIPT_TOOL_CALL_MAX_HOLD_SECONDS` (480.0).

### Configuration Files
- `src/codemie/service/customer_config_declarations.py:225` `WORKSPACE_SCRIPT_BRIDGE` setting declaration; `src/codemie/service/workspace_script_bridge.py` reads `features:workspaceScriptBridge` (`timeoutSeconds`, `maxParallelCalls`).

### Feature Flags and Deployment Concerns
- Tool calling (and hence all script-call events) exists only while the `workspaceScriptBridge` feature is on and the sandbox is in jobs mode; otherwise `bridge=None` and no events. The `execute_workspace_script` skill-run metric is independent of the bridge.
- Metric cardinality: ES metrics are label-based; `skill_script` (path) and `tool_name` labels grow with usage (ticket allows `skill_script`, forbids a script path label on call metrics).

---

## 6. Risk Indicators

- `CallOutcome` lacks assistant/model/user/project and the resolved tool's type; the dispatcher only knows the payload `name`. `_settle_unfinished` outcomes have `op=None, tool=None` (abandoned calls lose the tool name; ticket wants `status=abandoned` counted as a use of the tool). Speculative: the event would need enrichment or the handler/listener a call-id -> metadata map.
- `ScriptRunContext` carries no assistant name/model/project; `ScriptToolRegistry.fill` keeps only `assistant.id`. Model and agent name are on the script tool's `metadata` (set in `assistant_agent._configure_tools`, `langgraph_agent`), which the registry does not see. Project for chat comes from `get_current_project()` fallback; for a tool step from `ProjectScope.project`.
- `on_settled` is a single callback (Optional), and runs on the channel thread outside the per-request contextvars copy; fan-out to metric/trace/audit listeners and contextvar-dependent helpers (`get_current_project`) need care.
- `send_tool_metrics` always emits a tokens metric on success and has no error-code attribute; decision 3 forbids tokens for script calls (Speculative: needs a bypass or a separate method).
- Double counting: `execute_workspace_script` direct call is already counted by `MonitoringCallback`; script-made calls get new records under the same metric name, so every reader of `codemie_tools_usage_total` (tools_handler, engagement/assistant handlers, leaderboard collector `collector.py:880-905`) will change its numbers. This conflicts with "Out of scope: changing how existing numbers are computed" unless labelled records are filtered or accepted. Per-user/assistant cardinality in `tools_handler` also grows.
- Tools that emit their own metric (e.g. datasource usage) are not counted twice only if totals use the call-event metric; need to check whether tools emit metrics inside `run_for_script` (not researched in depth).
- Seed ledger lookup by script path/folder has no repository method (`get_folder_owner` is keyed by source id); path-to-skill attribution must come from the ledger, never from the path text (ticket).
- Batch `call_tools` runs calls concurrently in the worker pool; events can arrive out of order and after run end (withdrawn / unfinished) -> "abandoned" status path.
- Workflow bare tool step: the tool step's `execute_workspace_script` is not counted today (callbacks bypassed); only script-made calls would be (accepted asymmetry). `is_tool_step` determines scope; verify user/project there (ticket sub-task 2).
- New metric names must be registered in both `metrics_constants.py` and `MetricName` (+ lists at `metric_names.py:156-162,195`) for readers.
- CLI analytics storage is a separate Postgres store for plugin events; it is not fed by platform tool metrics.

---

## 7. Summary for Complexity Assessment

The change touches four layers: the bridge protocol (`tool_call_protocol.py`, `tool_call_channel.py`, `job_bridge.py` in `codemie_tools`), the script-call service package (`codemie/service/script_tool_calls`: handler, context, registry), the workspace service (`AgentWorkspaceService.execute_workspace_script`, ledger read), and the monitoring/analytics layer (`AgentMonitoringService`, `SkillMonitoringService`, `metrics_constants`, `metric_names`, `tools_handler`). Much of the seam already exists: `CallOutcome` is built for every request, the channel settles each outcome through an `on_settled` callback, and `new_job_bridge_options` accepts it, but no production code supplies a listener, there is only one callback slot, and the event lacks the attribution fields and resolved tool details the metric needs.

Technical novelty is moderate: no new infrastructure, but concurrency (worker pool threads, contextvars, calls finishing after the run ended), enrichment of the run context with assistant/model/project, a ledger-by-folder lookup that does not exist yet, and the double-counting/impact analysis on readers (tools_handler, leaderboard) of the shared `codemie_tools_usage_total` metric. Test coverage around the bridge and handler is strong (end-to-end tests with real channel); there are no tests or code on the metrics/listener/ledger-attribution side, and monitoring callback/service tests are small and parametric.

Key risks: attribution data missing from the event/context; abandoned calls losing tool names; single-slot `on_settled` versus the multi-listener design (D23); the tokens metric emitted by `send_tool_metrics` against decision 3; changed numbers in existing views sharing the metric name; and workflow tool-step semantics. No DB schema change was identified as required for the metrics themselves.

### Extra: data available for a "tokens saved by scripts" estimate (research only; ticket decision 3 says NO tokens metric for script calls, so anything below is a design question to surface)

What exists
- Token counting: `codemie.core.utils.calculate_tokens(text, llm_model=default)` (tiktoken via `codemie_tools.base.utils.get_encoding`); `ConversationService.calculate_tokens` (fixed gpt-4.1-mini encoding). Direct calls: `MonitoringCallback.on_tool_end` emits `codemie_tools_usage_tokens` = tokens of the tool output that entered model context (`calculate_tokens(output)`); `tools_handler` sums it (`attributes.count`).
- Script path: `run_for_script` returns a `ScriptResult` (JSON-able `result`, optional HTTP status); the encoded response length is already captured as `CallOutcome.response_bytes` (bytes of the full JSON response body incl. envelope), plus `duration_seconds`, `tool`, `code`, `delivered`/`withdrawn`. The result is not otherwise measured; it never enters model context. Result size is bounded by `max_payload_bytes` (`MAX_PAYLOAD_BYTES`), so the saved volume per call is bounded.
- What the model DOES see from a script run: the `execute_workspace_script` tool's JSON output (stdout, workspace file list, warnings) which `MonitoringCallback` already counts as `codemie_tools_usage_tokens` for that tool. So "tokens saved" is approximately (sum of tokens of the results of the script's inner calls) minus (tokens of the script tool's returned output), conceptually, if the counterfactual is "model would have called each tool directly and seen each full result".
- Other token metrics: `MetricsAttributes.INPUT_TOKENS/OUTPUT_TOKENS/CACHE_READ_INPUT_TOKENS` (`metrics_constants.py:87-89`) used by conversation/workflow/datasource/LLM-proxy monitoring services (`conversation_monitoring_service.py`, `workflow_monitoring_service.py`, `llm_proxy_monitoring_service.py`, `datasource_monitoring_service.py`), `datasource_tokens_usage` metric; LiteLLM spend logs tables (`l1t2l3m5s6p7_create_litellm_spend_logs`) store per-request tokens; CLI analytics Postgres (`usage_requests`, `session_usage`, `cost_daily`) stores token counts for CLI sessions only, not platform tool calls.
- No existing code estimates counterfactual tokens or avoided round trips.

Estimable quantities (candidate analytics, to brainstorm)
- Result tokens kept out of context per call: tokens of the serialized result (from `response_bytes` via bytes/4 heuristic, or `calculate_tokens` of the result in the handler where the result is in hand; counting costs CPU on up-to-payload-limit strings). Aggregate by tool, assistant, skill, project.
- Tokens actually returned to the model per script run (already available as the script tool's `codemie_tools_usage_tokens`); net saving = inner result tokens - script output tokens (can be negative when a script prints raw data).
- Avoided model round trips: number of inner calls minus 1 (each direct call would be one model tool-call turn that re-sends the context); an estimate of re-sent prompt tokens needs the conversation context size (not in the call event; available in LLM request metrics per conversation).
- Batch parallelism (`call_tools`) and duration give latency saved (parallel vs sequential) from `duration_seconds`.
- Caveats: counterfactual is an assumption (the model might not have made those calls, or might truncate); tokenizer differs per model (`calculate_tokens` uses default model encoding); `ScriptCallable` results are JSON-normalised and may differ in size from the direct-tool string output; abandoned/withdrawn calls should be excluded; contradicts ticket decision 3 unless named as a separate "estimated tokens kept out of context" metric (different meaning than `codemie_tools_usage_tokens`, must not be summed into it).

---

## 8. External References

None named by the task (the only named path, the ticket file `docs/superpowers/tasks/2026-10-09-epmcdme-15566-script-token-savings-analytics/ticket.md`, is in-repo, was read, and its decisions are reflected above).
