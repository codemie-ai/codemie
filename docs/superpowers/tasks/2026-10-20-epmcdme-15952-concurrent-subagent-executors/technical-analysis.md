# Technical Research

**Task**: assistant subagent executors concurrency langgraph (EPMCDME-15952)
**Generated**: 2026-10-20
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15952 — Prepare subagent executors concurrently to reduce pre-stream latency.
Summary: Prepare configured custom and built-in subagent executors concurrently, where safe, to reduce assistant startup delay and exposure to pre-stream idle timeouts.
Description: LangGraph assistants build subagent executors sequentially before constructing the parent agent. On the streaming chat path, parent construction completes before the handler returns StreamingResponse. Each child build loads request files, may prepare skill/workspace files, assembles toolkit and MCP tools; repeated reads of identical request files. Reporter says ~30s per subagent (unmeasured). Overlap independent setup work using a team-selected concurrency model and limits. Preserve executor identity, handoff mapping, version pinning, failure handling, safe use of shared data and workspace services. Out of scope: parallel subagent task execution (runtime already supports via Send); request-file reuse is an optimization to assess, not required caching redesign.
Affected areas: src/codemie/service/assistant/assistant_engine_builder.py:181-250,314-326; src/codemie/service/tools/assistant_factory.py:100-113,151-195; src/codemie/service/assistant_service.py:545-548,579-610,637-652; src/codemie/core/utils.py:540-563,640-673; src/codemie/service/file_service/file_service.py:20-35; src/codemie/rest_api/handlers/assistant_handlers.py:584-668; src/codemie/agents/langgraph_agent.py:516-557; tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py:478-486.
Acceptance criteria: (1) independent custom+built-in executor prep overlaps; delayed-builder test shows overlap before parent construction. (2) concurrency bounded; limit/scope/default documented; test active builds never exceed it. (3) executor identities, handoff-name mappings, pinned versions correct when builds finish out of order. (4) regression tests preserve failure behavior: regular child failures logged/skipped, MCPToolLoadException propagates with assistant context, built-in failures isolated. (5) shared request data, file objects, callbacks, workspace ops reviewed for concurrent safety; tests verify file availability and no shared-state corruption. (6) per-subagent and total setup timings captured; controlled comparison shows reduced wall time vs serial. (7) tests cover custom-only, built-in-only, mixed, zero, single subagent configs; runtime handoff behavior unchanged.
Be explicit about: sync vs async nature of the build path, thread-safety of shared state (request files, contextvars, DB sessions, workspace/skill file services, callbacks), existing config settings pattern for limits, existing test files/fixtures to extend.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/service/assistant/assistant_engine_builder.py` (`LangGraphAssistantBuilder`)
  - `create_subagent_executors` (L181-250): merges skill builtins (`_merge_skill_builtin_subagents`), calls `create_assistant_executors(...)` for custom ids (fully serial, completes first), then seeds `existing_names` via a SECOND `Assistant.get_by_ids` (best-effort, exceptions swallowed), then a serial `for builtin in effective_builtins` loop. Builtin agent name is allocated by `_make_unique_builtin_agent_name(base_name, existing_names)` which mutates the shared `existing_names` set BEFORE the build in the loop (order-dependent; must stay serial/pre-allocated if builds go parallel). Failures: `MCPToolLoadException` -> `attach_assistant_context(assistant_name=agent_name)` + log, NOT re-raised; any `Exception` -> log, skipped (builtin failures are fully isolated).
  - `_build_builtin_subagent_executor` (L~150-178): `parent.model_copy(deep=False)` (shallow! shares mutable lists/dicts with parent and siblings), sets id=None, name, assistant_ids=[], enabled_builtin_subagents=[], system_prompt, runtime privates `_runtime_builtin_subagent`, `_runtime_disable_skill_builtin_subagents`; then `AssistantFactory(...).build()`.
  - `configure_agent_kwargs` (L279-326): sole call site of `create_subagent_executors` (injected callable); then `get_subagent_descriptions(assistant, user)` (third `get_by_ids`) -> `agent_kwargs["subagents"]` / `["subagent_descriptions"]`. Descriptions are keyed by name, executors are a positional list; handoff mapping relies on list contents/names, so ordering/identity must be deterministic.
- `src/codemie/service/tools/assistant_factory.py`
  - `AssistantFactory.build()` (L64-117): project model-whitelist check (`Application.get_by_id`, `ModelAvailabilityService`), then `AssistantService.build_agent(..., request=self.request.model_copy() (shallow), tool_callbacks=[AgentStreamingCallback(thread_generator)], is_subagent=True)`; `agent.set_thread_context({}, UniqueThoughtParentIds.LATEST.value)`; returns `agent.agent_executor`. Re-raises all exceptions after logging.
  - `create_assistant_executors` (L120-195): `Assistant.get_by_ids`; on fetch failure returns `[]`; serial loop: skip remotely-deleted (Bedrock cleanup, DB side effect), apply pinned version `request.sub_assistants_versions[assistant.id]` via `AssistantVersionService.apply_version_to_assistant`, build; `MCPToolLoadException` -> attach context (name+id), log, RE-RAISE (aborts everything); other exceptions logged and skipped. Order of executors == order returned by `get_by_ids`.
- `src/codemie/service/assistant_service.py` `build_agent` (L~500-660), executed once per child AND parent:
  - `deepcopy(request)` (L541) so each build owns its request copy; `set_llm_context(assistant, None, user)` (L538, WRITES contextvar `litellm_context`); `build_unique_file_objects(request.file_names, conversation_id, history_index)` (L545) -> each build re-reads all request files (and conversation history files) from the file repo; `Application.get_by_id`; `SkillContributionsResolver.resolve_for_assistant` (DB); `prepare_skill_workspace(assistant, request, user)` (L587, creates agent workspace + seeds skill files, per-conversation); `ToolkitService.get_tools(...)` (toolkits + MCP tool loading, the dominant cost); `enforce_oauth_connected`; `_prepare_system_prompt`; agent class construction; for LangGraphAgent `_configure_langgraph_agent` -> `configure_agent_kwargs` (recursion point for subagents; child assistants have `assistant_ids=[]`/no builtins so recursion yields no grandchildren).
- `src/codemie/core/utils.py` L540-563 `_process_file_names_to_objects` and L640-673 `build_unique_file_objects`: pure per-call dict building; calls `FileService.get_file_object` -> `FileRepositoryFactory().get_current_repository().read_file(...)` (`src/codemie/service/file_service/file_service.py` L20-35, stateless classmethods). `_collect_files_from_conversation` also does `Conversation.find_by_id`. No caching; file errors swallowed (ValueError logged, other errors yield FileObject without content). Each call returns fresh `FileObject` instances, so children don't share file objects today.
- `src/codemie/service/skill_workspace_preparation.py` `prepare_skill_workspace` (L53-77): gated by skill_ids + conversation_id + script tool selected; `AgentWorkspaceService().create_workspace(conversation_id)` then `SkillWorkspaceSeeder(...).ensure_seeded(workspace.id, skills)`; never raises (returns `SETUP_FAILED_NOTICE`). Idempotent-looking "ensure" semantic, but concurrent create_workspace/ensure_seeded for the SAME conversation from N children is a check-then-act race candidate (not verified: repository internals not read).
- `src/codemie/rest_api/handlers/assistant_handlers.py` `process_request` (L~542) is a plain `def` (not async); `_handle_stream` (L584-668) calls `AssistantService.build_agent(...)` synchronously (L645) and only then returns `StreamingResponse`. So FastAPI runs the whole build in a threadpool worker thread; the build path is fully SYNC (no awaits) with blocking I/O (DB, HTTP, MCP).
- `src/codemie/agents/langgraph_agent.py` L516-557: runtime parallel handoff (`_build_parallel_sends` -> `Send`) – out of scope, must remain unchanged. `_create_custom_handoff_tool(agent_name, name, description)` creates handoff tools from subagent names.

### Architecture and Layers Affected
Service layer only (no API/router or schema change): `assistant_engine_builder` (orchestration), `assistant_factory` (child factory), `AssistantService.build_agent` (per-agent pipeline). Handler `_handle_stream` is the latency observer (entry), not necessarily modified. Config layer for limit setting.

### Integration Points
- `Assistant.get_by_ids` (DB), `Application.get_by_id` (DB), `BedrockAgentService.validate_remote_entity_exists_and_cleanup`, `AssistantVersionService.apply_version_to_assistant`, `ToolkitService.get_tools` (toolkits, MCP clients), file repository (`FileRepositoryFactory`), `AgentWorkspaceService`/`SkillWorkspaceSeeder`, `SettingsService` (via `set_llm_context`), `ThreadedGenerator`/`MessageQueue` shared by all children (`thread_generator`, and each child gets its own `AgentStreamingCallback(self.thread_generator)`).

### Patterns and Conventions
- Callable injection into `configure_agent_kwargs` (tests pass `create_subagent_executors=lambda **_: []`).
- Lazy imports inside functions to avoid cycles.
- Shared-thread propagation pattern: `copy_logging_context()` / `restore_logging_context()` in `src/codemie/configs/logger.py` L193-225 (explicitly "for propagation into forked tasks/threads"); `get_otel_context_for_thread()` in `src/codemie/core/otel_tracing.py` ~L181 for OTel into ThreadPoolExecutor threads. Existing `ThreadPoolExecutor` usage precedent: `src/codemie/datasource/base_datasource_processor.py` L801 (`max_workers=STORAGE_CONFIG.indexing_threads_count`) and `as_completed`. Existing `asyncio.to_thread` precedent in `enterprise/litellm/budget_provider_adapter.py` (async code only).

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists. Relevant: `agents/langchain-agent-patterns.md`, `agents/agent-tools.md`, `architecture/service-layer-patterns.md`, `development/configuration-patterns.md`, `development/performance-patterns.md` (async/batching), `development/logging-patterns.md`, `testing/testing-patterns.md`, `testing/testing-service-patterns.md`, `workflows/langgraph-workflows.md`, `development/error-handling.md`. (Listed from AGENTS.md; not all re-read for this analysis.)

### Architectural Decisions
Inline comment in `langgraph_agent.py` (~L211, L760): `stream()` runs in a bare `threading.Thread` with no inherited contextvars; hence the code explicitly captures/restores context. Comment in `clients/postgres.py` L260-264: the ambient `_current_session` ContextVar (`transactional_session()`) is "request-scoped and async-safe".

### Derived Conventions
Settings via pydantic `Config` fields in `src/codemie/configs/config.py`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/tools/test_assistant_factory.py` (class `TestAssistantFactory`): factory build, `create_assistant_executors` basic/parent/version/deleted-skip/factory-exception/get_by_ids-exception/empty; patches `AssistantFactory` and `Assistant` with `Mock`/`@patch`. Primary file to extend for custom-executor behavior, version pinning, MCPToolLoadException propagation.
- `tests/codemie/service/assistant/test_configure_agent_kwargs_confirmation.py`: `configure_agent_kwargs` with lambda stubs for `create_subagent_executors`.
- `tests/codemie/service/assistant/test_langgraph_assistant.py`: LangGraph assistant builder tests (likely home for `create_subagent_executors` builtin tests; not read in detail).
- `tests/codemie/service/test_assistant_service_methods.py` L557, L594: patch `AssistantService._create_subagent_executors`.
- `tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py`: handoff tools, parallel handoff state (L441-499 region, `test_parent_context_keeps_...`, `test_strip_handoff_back_messages_pre_model_hook_...`) – the runtime-unchanged regression suite.
- `tests/codemie/agents/tool_confirmation/test_script_tool_confirmation.py` references the executors functions.

### Testing Framework and Patterns
pytest (`pytest.ini`), `unittest.mock` (`Mock`, `patch`), class-based tests with fixtures `mock_assistant`, `mock_user`, `mock_request`. Per AGENTS.md tests only written/run when explicitly requested.

### Coverage Gaps
- No tests for concurrency, timing, ordering under out-of-order completion, bounded parallelism, or contextvar propagation.
- No evident tests for builtin subagent failure isolation / unique-name allocation (to be confirmed in `test_langgraph_assistant.py`).
- No timing/latency instrumentation exists for per-subagent or total setup (grep for such found none in these files).

---

## 5. Configuration and Environment

### Environment Variables
None exist for subagent build concurrency. Nearest precedents (all fields in `src/codemie/configs/config.py`): `WORKFLOW_MAX_CONCURRENCY: int = 5` / `WORKFLOW_DEFAULT_CONCURRENCY: int = 2` (L184-185), `FILE_DATASOURCE_UPLOAD_MAX_WORKERS: int = Field(default=3, gt=0)` (L153; best validated-int precedent), `MAX_CONCURRENT_DATASOURCE_INDEXING: int = 5` (L191), `SUBWORKFLOW_POOL_MAX_SIZE: int = 5` (L611), `CRON_SCHEDULER_MAX_WORKERS: int = 20` (L371).

### Configuration Files
`src/codemie/configs/config.py` (pydantic settings, env override by field name); `deploy-templates/` for helm values if documenting a new env var; `.ai-run/guides/development/configuration-patterns.md`.

### Feature Flags and Deployment Concerns
None for this domain. Other relevant boolean toggle precedents: `SUBWORKFLOW_POOL_ENABLED`, `DATASOURCE_CONCURRENCY_LIMIT_ENABLED`. Concurrency multiplies load on DB pool, MCP servers and file store (pool sizes not inspected).

---

## 6. Risk Indicators

- Sync path / thread model: build is sync inside a FastAPI sync-def threadpool thread; the only natural fit is a thread pool (`ThreadPoolExecutor`), not asyncio. Speculative: a per-call pool bounded by a new config field; or a shared module-level pool (cross-request limit) – a team decision (AC2 "scope").
- ContextVars do not cross into new threads: `litellm_context` (set by `set_llm_context` in `llm_service/utils.py` L118), `disable_prompt_cache` (`core/dependecies.py` L167), user context, logging vars (`logging_uuid` etc.), OTel context, DB `_current_session`. Worker threads would see defaults unless wrapped with `contextvars.copy_context().run` (copies all) or `copy_logging_context`/`restore_logging_context`. Speculative: `copy_context().run(...)` per task is the broadest fix.
- `set_llm_context` is called inside every child `build_agent` and writes the contextvar. Today serial children leave the LAST child's context in the request thread; in per-thread copied contexts the parent's context would not be overwritten by children (behavior difference to verify -- parent build calls `set_llm_context` first at the start of ITS build_agent, and children are built mid-way, so the parent's subsequent code sees the last child's litellm context today).
- DB sessions: `get_session()` opens a new `Session` per call unless an ambient `transactional_session` exists via ContextVar; if the handler ever runs inside `transactional_session`, copied context would share one (non-thread-safe) `Session` across threads through `_JoinedSession`. Check for such callers before using `copy_context`.
- Shallow `model_copy(deep=False)` for builtin children and `request.model_copy()` (shallow) in `AssistantFactory.build`: mutable fields (lists, dicts) shared among siblings; `build_agent` mutates `assistant.llm_model_type`, `_runtime_skill_contributions`, sets `request.file_names`; custom children are distinct `Assistant` objects from `get_by_ids`, but builtin children share the parent's sub-objects (toolkits, mcp_servers, context) – `ToolkitService`/`_apply_marketplace_tool_mappings` mutate in place (L577) -> real race risk for builtins vs parent and each other. The parent `request` is deep-copied inside `build_agent`, so `deepcopy(request)` races only if parent request mutated concurrently (it is not).
- Shared `thread_generator` (MessageQueue) used by every child's `AgentStreamingCallback`; need confirmation that its put/queue is thread-safe (not read; `core/thread.py`).
- Workspace/skill seeding: `prepare_skill_workspace` runs per child for the same `conversation_id`; concurrent `create_workspace` + `ensure_seeded` may duplicate or collide. Speculative: serialise per conversation (lock) or hoist once before fan-out. AC5 test "file availability".
- Repeated file reads: N children re-read identical request/history files (each returns fresh `FileObject`); sharing one dict would reintroduce shared mutable state (FileObject mutation unknown). Request-file reuse is "assess only" per ticket.
- Failure semantics are asymmetric and order-dependent: custom `MCPToolLoadException` aborts the whole list (serial: first failure raised; concurrent: need to choose deterministic exception, cancel/not-start pending, and still attach context); builtin failures never raise. Custom runs entirely before builtins today, so a custom MCP failure prevents any builtin build; with overlap, builtin work may already be running (wasted but harmless?) -- decision for spec.
- Naming: `existing_names` seeded from a second `get_by_ids` that can silently fail; builtin name allocation must be done up-front serially to remain deterministic regardless of completion order.
- Ordering: results must be re-assembled in original order (custom in `get_by_ids` order, then builtins in `effective_builtins` order) -- `as_completed` ordering would break identity/handoff mapping (AC3).
- Pool-resource risk: DB engine pool size, MCP server connection limits, file store throughput; default limit choice unmeasured; reporter's ~30s/subagent unmeasured (AC6 needs new timing instrumentation; none exists).
- Nested concurrency: children are built in worker threads and each child's `build_agent` calls `configure_agent_kwargs` -> `create_subagent_executors`, which returns `[]` early for children (no assistant_ids/builtins), so no nested pools; skill-contributed builtins are disabled for builtin children via `_runtime_disable_skill_builtin_subagents` but for CUSTOM children `_merge_skill_builtin_subagents` could still apply (custom child with `skill_ids` + builtin contribution) -> possible nested pool (verify).
- Duplicate DB lookups (3x `get_by_ids` per request) are an adjacent optimisation, not required.

---

## 7. Summary for Complexity Assessment

Change surface is concentrated in the service layer: `assistant_engine_builder.create_subagent_executors` and `assistant_factory.create_assistant_executors` (serial loops to fan out), plus one new config field and timing instrumentation; handler, runtime (`langgraph_agent` handoffs) and file helpers are expected to be read-only or lightly touched. Roughly 3-4 source files and 2-4 test files. The build path is wholly synchronous blocking I/O running in a FastAPI sync-handler threadpool thread, so a bounded `ThreadPoolExecutor` is the only fit; there is an existing ThreadPoolExecutor/`as_completed` precedent in the datasource processor and context-propagation helpers (`copy_logging_context`, OTel) but no precedent for fanning out agent builds.

Technical novelty is moderate-to-high in correctness terms rather than code volume: ContextVars (litellm context, prompt-cache flag, user, logging, OTel, ambient DB session) are not inherited by threads and `set_llm_context` mutates one inside each child build; builtin children are shallow copies of the parent and `build_agent` mutates assistant objects in place; skill workspace seeding for the same conversation may race; `thread_generator` thread-safety is unverified. Failure semantics (custom MCPToolLoadException propagates, others skipped, builtins fully isolated) and deterministic ordering/naming must be preserved when builds complete out of order.

Test posture: good unit coverage for the serial factory (`test_assistant_factory.py`) and handoff runtime, with mock-heavy fixtures that make delayed-builder, active-count and ordering tests straightforward; no concurrency, timing or contextvar tests exist and no timing instrumentation exists. Main risks: hidden shared mutable state, contextvar leakage/regression in LiteLLM credentials, unbounded or poorly sized concurrency against DB/MCP backends, and unmeasured latency claims. Estimated as a medium change with high review/verification burden.

---

## 8. External References

None named by the task
