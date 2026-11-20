# EPMCDME-15952 Concurrent subagent executor preparation - Spec

Size: L (21). Style: minimal. Stdlib `ThreadPoolExecutor`, one config field.

## Problem
`LangGraphAssistantBuilder.create_subagent_executors` (`src/codemie/service/assistant/assistant_engine_builder.py:181-250`) builds custom children (via `create_assistant_executors`, `src/codemie/service/tools/assistant_factory.py:120-195`) and then built-in children, all serially, before the parent is constructed. On the streaming path this happens before `StreamingResponse` is returned (`assistant_handlers.py:584-668`), so setup time is the sum of all child builds. The build path is synchronous blocking I/O running in a FastAPI sync-handler thread, so threads (not asyncio) are the fit.

## Design
1. **One pool per call.** `create_subagent_executors` creates one `ThreadPoolExecutor(max_workers=N)` and runs all custom and built-in child builds on it, so they overlap with each other. N is bounded per request. There is no cross-request limit.
2. **Config.** Add one field in `src/codemie/configs/config.py`: `SUBAGENT_BUILD_MAX_WORKERS: int = Field(default=4, gt=0)`, following `FILE_DATASOURCE_UPLOAD_MAX_WORKERS`. Value 1 gives serial behavior. Document at the field (and in `.ai-run/guides/development/configuration-patterns.md` if env vars are listed there): scope is per request, default 4, and the resource note that MCP tool loads may spawn subprocesses, so a per-call limit multiplied by many concurrent chats can pile up; operators should size it accordingly.
3. **Serial prelude, concurrent builds, ordered assembly.**
   - Serially and before fan-out: fetch the custom assistants, skip remotely deleted ones, apply the pinned version from `request.sub_assistants_versions`, and allocate unique built-in names with `_make_unique_builtin_agent_name`. Name allocation must not depend on completion order.
   - Concurrently: only the expensive `AssistantFactory.build()` calls.
   - Results are re-assembled in submission order: custom children in `get_by_ids` order, then built-ins in `effective_builtins` order. Never use `as_completed` order. Executor identity, handoff names and pinned versions are then identical to the serial result.
4. **Failure semantics: same final outcome.**
   - Regular custom child failure: logged and skipped.
   - Custom `MCPToolLoadException`: context attached (`attach_assistant_context` with name and id), then re-raised after in-flight builds settle. Results are scanned in list order, so the first custom failure in list order wins and the final exception is the same as in the serial code. Pending builds that have not started are cancelled.
   - Built-in failure (including `MCPToolLoadException`): attach context, log, skip, never raised.
   - Timing difference to note in the PR: built-ins may already be running (or finished) when a custom MCP failure aborts the call, whereas serially no built-in started. The wasted work is harmless; the exception is the same.
5. **Context and DB sessions.** Each task runs via `contextvars.copy_context().run(...)`, so `litellm_context`, `disable_prompt_cache`, logging variables and OTel context reach the worker threads. No request-shared Session was found on the build path: `get_session` opens a new Session per call and `transactional_session` is not used on the chat path. As cheap insurance, each worker also clears the ambient `_current_session` (clients/postgres.py) before building, so a copied context could never share one non-thread-safe Session. A test builds concurrently while an ambient session is set and asserts that workers do not see it.
6. **Shared mutable state.**
   - Built-in children: keep the deliberate shallow `parent.model_copy(deep=False)` (its comment says it avoids a full serialize/validate pass). In `_build_builtin_subagent_executor`, additionally copy only the fields that `build_agent` / `ToolkitService` mutate in place: `toolkits`, `mcp_servers`, `context`, plus any others found when reading `assistant_service.py:540-660` (for example `_apply_marketplace_tool_mappings` targets and `llm_model_type`, `_runtime_skill_contributions`). The implementer lists the final set from that read; do not deep-copy the whole assistant.
   - `request`: `build_agent` already deep-copies it. Keep that.
   - File objects: each child keeps reading its own request files and gets fresh `FileObject`s, so nothing is shared. Sharing a read-once dict across children is not implemented. This is the "assess only" item; record the assessment and the opportunity in the PR notes.
   - `thread_generator` / `AgentStreamingCallback`: read `core/thread.py` and confirm that put and queue are thread-safe. Add a lock only if they are not.
   - Skill workspace (`prepare_skill_workspace`, runs per child for the same conversation): verify first, then decide. `create_workspace` is already race-safe (unique `(conversation_id, user_id)` plus IntegrityError re-fetch). The open question is whether concurrent `SkillWorkspaceSeeder.ensure_seeded` calls write the same paths: two built-ins share the parent's `skill_ids`, so they target the same skill folder, but a collision is unproven. Read the seeder and repository. If children write disjoint paths or seeding is idempotent and atomic, add no lock. If they collide, use a lock keyed per conversation/workspace path (never a global lock), or seed once before fan-out. Record the finding in the PR; add a test only for whichever branch applies.
7. **Timing.** Log one structured line per child (`subagent`, `kind=custom|builtin`, `duration_ms`) and one for the whole phase (`total_ms`, `count`, `max_workers`). Use `time.perf_counter()` and the existing logger, with no new metrics system.
8. **Zero or one child.** If there are 0 or 1 builds, skip the pool and run inline. The result is identical and there is no thread overhead.

## Observable behavior change
Today the last serially built child's `litellm_context` is left in the request thread, so the parent's later code sees it. With copied contexts, children cannot overwrite the parent's context. This is intended: the parent keeps its own context. Verify that nothing depends on the old behavior.

## Acceptance criteria
1. Custom and built-in builds overlap. A delayed-builder test shows that builds start concurrently, before parent construction.
2. At most `SUBAGENT_BUILD_MAX_WORKERS` builds are active at once, tested with an active-counter. The limit, scope (per call) and default (4) are documented.
3. Out-of-order completion, forced by inverted delays, still gives correct executor order, names, handoff mapping and pinned versions.
4. Failure regression tests: a regular custom failure is logged and skipped. A custom `MCPToolLoadException` propagates with assistant context. Built-in failures are isolated, including MCP failures. Built-in name allocation is stable.
5. Concurrent-safety tests verify that request files are available in every child, that siblings and the parent are not mutated by one another, and that workers do not see an ambient DB session. The safety review of callbacks, workspace seeding and the thread generator is recorded in the PR.
6. Per-child and total timings are logged. A controlled test with fixed sleeps shows concurrent wall time is clearly less than serial wall time.
7. Tests cover custom-only, built-in-only, mixed, zero and single-child configs. Runtime handoff tests (`tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py`) are unchanged and pass.

## Tests to extend
`tests/codemie/service/tools/test_assistant_factory.py`, `tests/codemie/service/assistant/test_langgraph_assistant.py`, and the config test if one exists.

## Non-goals
- Parallel subagent task execution at runtime (the `Send` handoff in `langgraph_agent.py:516-557` is untouched).
- A request-file caching or reuse redesign (assessed only).
- Removing the duplicate `get_by_ids` lookups.
- A shared cross-request pool, asyncio conversion, or a new dependency.
- Changes to handler, API, schema or the handoff runtime.
- New metrics, tracing or dashboards beyond log lines.

## Risks
- The default of 4 is unmeasured against DB pool, MCP server and file store limits. The reporter's ~30s per subagent is also unmeasured. MCP loads may spawn subprocesses, so many concurrent chats at 4 workers each can pile up.
- Fatal-error timing differs slightly from serial (see Design 4); note in the PR.
- Workspace seeding collision is unproven either way until the seeder is read.
- A custom child with `skill_ids` can still contribute built-ins. Verify that no nested pool results (children return `[]` early).
