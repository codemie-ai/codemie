# Technical Research

**Task**: assistant-factory subagent-executors concurrency build-context
**Generated**: 2026-11-13
**Research path**: filesystem

---

## 1. Original Context

Rebase EPMCDME-15952 (concurrent subagent executors) onto main and reimplement the concurrency layer on main's new SubagentHandle/SubagentBuildContext path (EPMCDME-16463/EPMCDME-16411), dropping the find_or_create_conversation commit.

---

## 2. Codebase Findings

### Existing Implementations

**Purpose: build each sub-assistant (custom and built-in) as a compiled executor, attached to a parent**
- `src/codemie/service/tools/assistant_factory.py:93-119` (main) `AssistantFactory.build()` — builds one sub-assistant via `AssistantService.build_agent_handle(..., is_subagent=True, build_context=self.build_context)` and returns a `SubagentHandle`. Model-whitelist pre-check removed on main (now `ProjectModelPolicy`, EPMCDME-16411).
- `src/codemie/service/tools/assistant_factory.py:107-182` (main) `create_assistant_executors()` — fetches custom sub-assistants (`Assistant.get_by_ids`), pins versions, skips remotely-deleted ones, then runs a **plain sequential `for assistant in assistants:` loop** calling `factory.build()` once per assistant. No thread pool exists here on main.
- `src/codemie/service/assistant/assistant_engine_builder.py:137-184` (main) `LangGraphAssistantBuilder._build_builtin_subagent_executor()` — clones the parent assistant (`parent.model_copy(deep=False)`), overrides `system_prompt`/`llm_model_type`/etc., then calls `AssistantFactory(...).build()`.
- `src/codemie/service/assistant/assistant_engine_builder.py:186-247` (main) `create_subagent_executors()` — calls `create_assistant_executors()` for custom children, then **a plain sequential `for builtin in effective_builtins:` loop** calling `_build_builtin_subagent_executor()` once per builtin. Custom and builtin children are two separate sequential phases, not one pool.
- Consumers of these two functions: `src/codemie/service/assistant_service.py:331-349` (`_create_subagent_executors` static wrapper, called from `_configure_langgraph_agent` → `LangGraphAssistantBuilder.configure_agent_kwargs`), and recursively itself — each built sub-assistant also goes through `build_agent_handle` → `configure_agent_kwargs` → `create_subagent_executors` with a child `SubagentBuildContext`, so nesting already exists serially.
- **None found** of a thread pool, `ThreadPoolExecutor`, or any parallel-build mechanism anywhere in `src/codemie/service/tools/assistant_factory.py` or `assistant_engine_builder.py` on `main` (`git grep "ThreadPoolExecutor\|run_builds\|custom_build_jobs" main -- src/` returns nothing). The entire concurrency layer this task must reimplement does not exist on `main` in any form.

**Purpose: carry build policy (pause/resume ability) down a tree of sub-assistant builds**
- `src/codemie/service/assistant/subagent_build.py:57-76` (main) `SubagentBuildContext` (frozen dataclass, field `can_resume_pause: bool`), with `.root(channel, save_history)` and `.child()`. This is new since the branch diverged and did not exist when EPMCDME-15952 was written.
- `src/codemie/service/assistant/subagent_build.py:79-91` (main) `SubagentHandle` (frozen dataclass: `agent`, `executor`, `tree_requires_confirmation`, `name`, `description`) — what every build now returns instead of a raw `CompiledStateGraph`.
- Consumers found by searching `SubagentBuildContext`/`SubagentHandle`: `agent_build_policy.py`, `assistant_engine_builder.py`, `assistant_factory.py`, `assistant_service.py`, `rest_api/handlers/tool_call_resume.py`. All of these must keep receiving/returning these exact types; none of them exist on the pre-rebase branch.

**Purpose: resolve the sub-assistant's effective tool-call policy and whether its tree pauses for confirmation**
- `src/codemie/service/assistant/agent_build_policy.py:35-44` `resolve_build_policy()` — resolves `ResolvedToolPolicy` once per build and builds the root `SubagentBuildContext` if the caller passed none.
- `src/codemie/service/assistant/assistant_engine_builder.py:299-347` `configure_agent_kwargs()` — takes `resolved_policy: ResolvedToolPolicy` and `build_context: SubagentBuildContext` as caller-supplied parameters (not computed inline), and returns `tree_requires_confirmation = resolved_policy.requires_confirmation or any(handle.tree_requires_confirmation for handle in handles)`. This bubbling-up of confirmation state is new infrastructure the concurrent build must preserve per handle.

**Purpose: create (or find) the Conversation row for a chat turn**
- `src/codemie/service/conversation_service.py:308-335` (main) `_find_or_create_conversation()` (private, classmethod) — only called from `upsert_chat_history` (`conversation_service.py:513`), which runs once per turn on the chat-history save path, not once per sub-assistant build.
- **None found**: main's `configure_agent_kwargs()` (shown above) contains no call to any `find_or_create_conversation`/`ConversationService` method at all — compare to the pre-rebase branch version of the same function (`git show HEAD:src/codemie/service/assistant/assistant_engine_builder.py:326-329`), which calls `ConversationService.find_or_create_conversation(request, assistant, user)` once per sub-assistant build when `require_tool_confirmation` is true. That public method (and the race it created when several sub-assistant builds called it concurrently for the same `conversation_id`) does not exist on `main`.

### Decisions Already in the Code

- **No deep-copy of mutable fields on builtin clones (main).** `assistant_engine_builder.py:161-171` (main) does `parent.model_copy(deep=False)` and overrides only `id`, `name`, `assistant_ids`, `enabled_builtin_subagents`, `system_prompt`, `llm_model_type`. It does **not** deep-copy `toolkits`, `mcp_servers`, or `context` — these are shared references with the parent and with sibling clones. This is safe only because builds run sequentially today.
- **Pre-rebase branch's isolation fix is absent from main.** The pre-rebase branch's commit `e2c8d93fda` added `child_assistant.toolkits = deepcopy(parent.toolkits)` / `mcp_servers` / `context` precisely because `build_agent`/`ToolkitService` mutate these in place and concurrent builds would otherwise race on the same objects. Main has none of this; reintroducing a thread pool without reintroducing this isolation reinstates a data race the branch had already fixed once.
- **No `SUBAGENT_BUILD_MAX_WORKERS` config field on main.** `git grep SUBAGENT_BUILD_MAX_WORKERS main` finds nothing; it exists only on the pre-rebase branch (`src/codemie/configs/config.py:158`, `Field(default=4, gt=0)`, modeled on the existing `FILE_DATASOURCE_UPLOAD_MAX_WORKERS: int = Field(default=3, gt=0)` convention at the line above it).
- **No ambient DB session on the build path (recorded finding, still true).** The original spec recorded: "No request-shared Session was found on the build path: `get_session` opens a new Session per call and `transactional_session` is not used on the chat path", and the branch cleared `_current_session` (`codemie/clients/postgres.py`) defensively in pooled calls anyway. Nothing on main changes this.
- **Convention: builtin name allocation is serial and order-independent of completion.** `assistant_engine_builder.py:230` (pre-rebase) / the equivalent loop on main allocates unique builtin names (`_make_unique_builtin_agent_name`) before/independent of the build call itself — any reintroduced concurrency must keep name allocation serial, matching this established rule.
- **Contextvars propagation convention.** The dropped pool used `contextvars.copy_context().run(...)` per submitted job (see `run_builds`, pre-rebase `assistant_factory.py:76-78`) specifically so `litellm_context`, `disable_prompt_cache`, logging vars and OTel context reach worker threads — this is the project's established pattern for running blocking work off the request thread (see also `FILE_DATASOURCE_UPLOAD_MAX_WORKERS`'s own pool, not read in full here).

### Task Statements Checked Against the Code

- "main has introduced SubagentHandle/SubagentBuildContext (EPMCDME-16463/EPMCDME-16411)" — **holds**: confirmed in `src/codemie/service/assistant/subagent_build.py` (new file, not present on the branch) and threaded through `assistant_engine_builder.py`, `assistant_factory.py`, `assistant_service.py`, `agent_build_policy.py`, `rest_api/handlers/tool_call_resume.py` on `main`.
- "drop the find_or_create_conversation commit" — **keeps a mechanism of section 1 in place, but that mechanism no longer exists to be kept**: main's `configure_agent_kwargs()` never calls any conversation-creation method during a sub-assistant build (consumers: none found in that call path); the race the dropped commit (`7865de1571`) patched in `ConversationService.find_or_create_conversation` cannot recur via that path because the method and its call site were removed by the EPMCDME-16463 refactor. The method still exists privately as `_find_or_create_conversation`, called once per turn from `upsert_chat_history`, a different, non-per-subagent call site.
- "reimplement the concurrency layer ... on the new path" (implies concurrency must still exist after the reimplementation) — **unverified against any requirement in this repo**: nothing in the codebase states concurrent subagent building is required; it is this task's own goal, not a fact to check. The facts above only establish that main currently builds all subagents (custom and builtin) strictly sequentially, in two separate loops.

### Consumers of Changed Contracts

- `create_assistant_executors` (returns `list[SubagentHandle]` on main, was `list[CompiledStateGraph]` on the pre-rebase branch) — consumed by `LangGraphAssistantBuilder.create_subagent_executors` (`assistant_engine_builder.py`), which on main appends builtin `SubagentHandle`s to the same list and reads `.tree_requires_confirmation` / `.executor` off every item. Any reimplementation must keep returning `SubagentHandle`, in submission order, for both custom and builtin children.
- `configure_agent_kwargs` signature (main: `resolved_policy`, `build_context`, `is_root`, callback returns `tree_requires_confirmation: bool`) — consumed by `AssistantService._configure_langgraph_agent` (`assistant_service.py:518-545`), which passes it straight through from `build_agent_handle`. A concurrent reimplementation that changes this signature breaks this one caller; searched for other callers of `configure_agent_kwargs` and `create_subagent_executors` and found only this one production call site plus tests.
- `AssistantFactory.__init__`/`.build()` (main: takes `build_context: SubagentBuildContext | None`, returns `SubagentHandle`) — consumed by `create_assistant_executors` and `_build_builtin_subagent_executor`, both in-repo; no external callers found (`git grep "AssistantFactory(" main -- src/` limited to these two files and `assistant_service.py` is not a caller).
- `ConversationService.find_or_create_conversation` (public method the dropped commit patched) — **none found**: no production caller remains on main (only the still-present private `_find_or_create_conversation`, a different method, called from `upsert_chat_history`). Dropping the commit removes no live behavior.

### Architecture and Layers Affected

- **Service / build-policy layer**: `assistant_service.py` (`build_agent_handle`, `_configure_langgraph_agent`, `_create_subagent_executors`), `assistant/agent_build_policy.py`, `assistant/subagent_build.py`.
- **Builder layer**: `assistant/assistant_engine_builder.py` (`LangGraphAssistantBuilder.create_subagent_executors`, `_build_builtin_subagent_executor`, `configure_agent_kwargs`).
- **Tool/factory layer**: `tools/assistant_factory.py` (`AssistantFactory`, `create_assistant_executors`) — this is where the dropped branch's `run_builds`/`custom_build_jobs`/`collect_custom_executors`/`ThreadPoolExecutor` lived and where it must be reintroduced or replaced.
- **Config**: `configs/config.py` (missing `SUBAGENT_BUILD_MAX_WORKERS` on main).
- **Conversation layer**: `service/conversation_service.py` — only touched by *not* reintroducing the dropped patch; no reimplementation needed there.

### Integration Points

- MCP tool loading (`MCPToolLoadException`, `service/mcp/models.py`) — a custom child's MCP load failure must still abort the phase and attach assistant context, exactly as the sequential `create_assistant_executors` does today (`except Exception` branch, `assistant_factory.py` main).
- Builtin subagent registry (`service/subagents/builtin_subagents_registry.py`, `builtin_subagents.py`) — supplies system prompts/display names, read serially before any build.
- Skill contributions (`service/skills/skill_contributions.py`) resolve `enabled_builtin_subagents` from skills; feeds `effective_builtins` consumed by the builtin loop.

### Patterns and Conventions

- Static/classmethod factory pattern throughout `LangGraphAssistantBuilder` and `AssistantFactory` — no DI container, plain static dispatch with local imports to avoid circular imports (`from codemie.service.tools.assistant_factory import AssistantFactory` inside methods).
- Frozen `@dataclass` value objects for build-time state (`BuildChannel`, `ResolvedToolPolicy`, `SubagentBuildContext`, `SubagentHandle`) — any new concurrency-carrying type should follow this, not a mutable class.
- Config fields for per-request worker bounds follow `FILE_DATASOURCE_UPLOAD_MAX_WORKERS` (`Field(default=N, gt=0)`, with an explanatory comment on resource exhaustion risk).

---

## 3. Documentation Findings

### Guides and Architecture Docs
No guide under `.ai-run/guides/` or `docs/` mentions subagent build concurrency, `SubagentBuildContext`, or `SubagentHandle` by name (checked `development/performance-patterns.md`, `architecture/service-layer-patterns.md` — no match for `ThreadPoolExecutor`/`contextvars`/`concurrent`). Conventions below are derived from code.

### Architectural Decisions
No ADR found for EPMCDME-16463/16411's `SubagentBuildContext`/`SubagentHandle` split; the rationale is recorded in code docstrings (`subagent_build.py:1-19`, `agent_build_policy.py:15-19`) rather than a separate doc, and in that module's own "(STR-3, STR-4, AR-5)" rule references, which point at a spec not present in this repository.

### Derived Conventions
- Build-time cross-cutting state is threaded explicitly through function parameters (`build_context`, `resolved_policy`), not through a context manager or thread-local, except for `contextvars` used specifically to make blocking thread-pool work see the ambient request context (dropped branch's `run_builds`).
- Local, in-function imports are used pervasively to avoid circular imports between `assistant_service.py`, `assistant_engine_builder.py`, and `tools/assistant_factory.py`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/tools/test_assistant_factory.py` (main, 909 lines): `TestAssistantFactory`, `TestAssistantFactoryModelPolicy`, `TestBuildAgentResolvedModel`, `TestCreateAssistantExecutorsFailureLog`. No concurrency-related test class exists on main.
- `tests/codemie/service/assistant/test_langgraph_assistant.py` (main): only `TestLangGraphAgent`. The pre-rebase branch's `TestCreateSubagentExecutorsConcurrency` (line ~800) and `TestBuiltinSubagentIsolation` (line ~940) — covering overlap timing, bounded worker count, out-of-order completion reassembly, failure propagation, and the toolkits/mcp_servers/context isolation fix — do not exist on main and will need re-creation against the new `SubagentHandle`-returning signatures.
- `tests/codemie/service/test_conversation_service_find_or_create.py` (pre-rebase branch only) exercises the dropped commit's `IntegrityError` tolerance; it has no counterpart on main and should not be re-added, since its target method has no live caller on main (see Consumers above).
- `tests/codemie/service/test_skill_workspace_preparation.py` already carries `test_concurrent_prepare_calls_on_one_conversation_both_succeed` from the pre-rebase branch, asserting workspace seeding is race-safe independent of this task's thread pool — worth checking whether this file on `main` still has it (not independently re-verified here; same file name exists on both).

### Testing Framework and Patterns
pytest, `unittest.mock.patch`/`MagicMock`, direct `ThreadPoolExecutor`/active-counter patterns for concurrency assertions (`run_builds` tests), no async test runner needed since the build path is sync.

### Coverage Gaps
Main has zero tests asserting anything about build *ordering*, *overlap*, or *isolation* for subagent builds — the entire concurrency behavior this task reimplements is currently untested on `main` because it currently doesn't exist there.

---

## 5. Configuration and Environment

### Environment Variables
No subagent-build-specific env var exists on main. The pre-rebase branch's `SUBAGENT_BUILD_MAX_WORKERS` is a `pydantic-settings` `Field` on `Config`, so it is settable via env var `SUBAGENT_BUILD_MAX_WORKERS` once added back, following the same mechanism as `FILE_DATASOURCE_UPLOAD_MAX_WORKERS`.

### Configuration Files
`src/codemie/configs/config.py` — single `Config(BaseSettings)` class; all per-feature worker-pool bounds live here as individual `Field`s (no nested config object for this domain).

### Feature Flags and Deployment Concerns
None found specific to subagent build concurrency. `customer_config.is_feature_enabled(...)` gates other, unrelated features in the same files (e.g. `projectModelOverride`, now removed from `AssistantFactory.build()` per EPMCDME-16411) but nothing gates subagent-build parallelism.

---

## 6. Risk Indicators

- Main's builtin-clone isolation gap (`assistant_engine_builder.py:161-171`, no deep-copy of `toolkits`/`mcp_servers`/`context`) is currently harmless only because builds are sequential; reimplementing concurrency here without reinstating the isolation fix from `e2c8d93fda` reintroduces a known, already-fixed-once data race between sibling builtin clones and the parent.
- Both custom and builtin subagent builds are two separate sequential loops on main (`create_assistant_executors` and the `for builtin in effective_builtins` loop in `create_subagent_executors`), not one unified path as the pre-rebase branch had (`run_builds` over combined jobs) — reimplementation must decide how to recombine these two call sites, which did not need recombining before the EPMCDME-16463 refactor split policy resolution out of `AssistantFactory.build()`.
- `configure_agent_kwargs`'s signature and return contract (`resolved_policy`, `build_context`, `is_root` in; `tree_requires_confirmation: bool` out, bubbled from `handle.tree_requires_confirmation` across all children) is new infrastructure with exactly one production caller found; any concurrency reimplementation must preserve this bubbling across concurrently-built handles without a data race on how `tree_requires_confirmation` is aggregated.
- `find_or_create_conversation` (public): confirmed no production caller remains on main, so dropping that commit removes dead-on-main code with no replacement needed — low risk, see Task Statements Checked Against the Code.
- No test on main currently exercises build ordering, overlap, or cross-child isolation for subagents (see Section 4) — any reimplementation is working against an un-pinned-by-tests area of the new code path, raising the chance that a subtle ordering/isolation regression is not caught without re-adding the pre-rebase branch's concurrency test classes against the new `SubagentHandle` contract.
- `SUBAGENT_BUILD_MAX_WORKERS` and the whole `run_builds`/`custom_build_jobs`/`collect_custom_executors` module-level API from the pre-rebase branch (`tools/assistant_factory.py`) no longer exists on main in any form and must be re-created or redesigned against the new `SubagentHandle`-returning, `build_context`-threading signatures rather than merely reapplied via rebase.

---

## 7. Summary for Complexity Assessment

This task touches three layers: the tool/factory layer (`tools/assistant_factory.py`, where the sequential-vs-concurrent build loop lives), the builder layer (`assistant/assistant_engine_builder.py`, which now threads `SubagentBuildContext`/`ResolvedToolPolicy` and aggregates `tree_requires_confirmation` across children), and configuration (`configs/config.py`, missing the worker-count field). The branch's prior implementation (9 commits) added a generic bounded, ordered `ThreadPoolExecutor` runner (`run_builds`), unified custom and builtin build jobs onto one pool, and added shared-state isolation (deep-copying `toolkits`/`mcp_servers`/`context` on builtin clones, clearing an ambient DB session per worker) — all of that machinery is completely absent from `main` today, because `main` independently refactored the same functions around new `SubagentBuildContext`/`SubagentHandle`/`ResolvedToolPolicy` types (EPMCDME-16463 pause/resume, EPMCDME-16411 model policy) that did not exist when the branch was written. This is not a mechanical rebase: every function the concurrency layer touched (`AssistantFactory.build`, `create_assistant_executors`, `_build_builtin_subagent_executor`, `create_subagent_executors`, `configure_agent_kwargs`) has a different signature and return type on main than on the branch.

Technical novelty is moderate-to-high: the reimplementation must thread a bounded thread pool through code that now returns `SubagentHandle` objects (not raw executors) and must aggregate a new `tree_requires_confirmation` boolean across concurrently-completing children without races, while reinstating the shared-mutable-state isolation fix that main currently lacks (a correctness gap that is latent, not introduced by this task, but that concurrency would expose). Test coverage for the target behavior is zero on main (the prior concurrency test classes do not exist there), so the reimplementation is unverified by any existing test and needs new or adapted tests against the new contracts. The `find_or_create_conversation` drop is low-risk and well-supported: its target method has no production caller on main, confirmed by searching all call sites.

---

## 8. External References

None named by the task.
