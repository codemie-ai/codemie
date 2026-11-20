# Concurrent Subagent Executor Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Build custom and built-in subagent executors on one bounded per-call thread pool, preserving order, identity, pinning and failure semantics.

**Architecture:** Serial prelude (fetch, deleted-skip, version pin, builtin name allocation), concurrent `AssistantFactory.build()` calls in `copy_context().run`, results assembled in submission order, then failure handling applied in list order.

**Tech Stack:** stdlib `ThreadPoolExecutor`, `contextvars`, pydantic `Config`, pytest + `unittest.mock`.

**Spec:** `docs/superpowers/tasks/2026-10-20-epmcdme-15952-concurrent-subagent-executors/spec.md`

## Global Constraints

- Commit per task using the repository's existing convention.
- Stdlib only; no new dependency, no asyncio, no cross-request pool, never `as_completed` ordering.
- Config: `SUBAGENT_BUILD_MAX_WORKERS: int = Field(default=4, gt=0)`; 1 = serial; scope per call.
- Failure semantics unchanged: custom regular failure logged+skipped; custom `MCPToolLoadException` context-attached and re-raised (first in list order, after in-flight settle, unstarted cancelled); built-in failures (incl. MCP) attach context, log, skip.
- 0 or 1 build: run inline, no pool.
- Untouched: handler, API, schema, `langgraph_agent.py` `Send` handoff, request-file caching, duplicate `get_by_ids`.
- Locks are never global; copy only what children mutate, not a full deep copy.

## PR-notes obligations (put in the PR description)

- Behavior difference: results are scanned in list order, so a fatal custom MCP failure at index 5 is seen only after job 0 finishes, unstarted jobs are cancelled later than in serial code, and built-ins may already be running when a custom MCP failure aborts. The final raised exception is unchanged.
- Safety review (callbacks, workspace, thread generator, DB sessions) and the file-reuse assessment (per-child fresh `FileObject`s kept; a shared read-once dict is a possible later optimization).
- `litellm_context` no longer leaks from the last child into the parent.

---

### Task 1: Config field and ordered bounded runner

**Files:**
- Modify: `src/codemie/configs/config.py` (next to `FILE_DATASOURCE_UPLOAD_MAX_WORKERS`, ~L153); add the env var to `.ai-run/guides/development/configuration-patterns.md` only if env vars are listed there.
- Modify: `src/codemie/service/tools/assistant_factory.py` (new module-level function)
- Test: `tests/codemie/service/tools/test_assistant_factory.py`

**Interfaces:**
- Produces: `run_builds(jobs: list[tuple[str, str, Callable[[], Any]]], is_fatal: Callable[[BaseException], bool] = lambda e: False) -> list[tuple[Any, BaseException | None]]`. Each job is `(name, kind, fn)`, `kind` in `custom|builtin`. Returns `(result, error)` per job in submission order. On the first `is_fatal` error found scanning in order, cancel unstarted futures and wait for the rest so the caller can raise.

- [ ] Add the config field. Its comment must state: scope is per call (per chat request), not global; default 4; 1 = serial; and that each in-flight child build can load toolkits and start MCP server subprocesses, so many simultaneous chats multiply to `chats x workers` concurrent builds and can exhaust processes, DB pool and MCP server limits. Lower it on constrained deployments.
- [ ] Implement `run_builds`: `len(jobs) <= 1` runs inline; otherwise `ThreadPoolExecutor(max_workers=min(cfg, len(jobs)))`, each job submitted as `contextvars.copy_context().run(fn)` timed with `time.perf_counter()` and logging `subagent=<name> kind=<kind> duration_ms=<n>`. One phase line `total_ms count max_workers`. Collect outcomes via `future.result()` in submission order.
- Test-first: yes — failing tests: inverted sleeps (first job slowest) still return outcomes in submission order; active-counter asserts max active <= patched limit of 2 with 5 jobs; fixed-sleep test asserts concurrent wall time is clearly below the serial sum; a job sees a `ContextVar` set by the caller; 0 and 1 job run on the caller thread; a fatal error cancels unstarted jobs.

### Task 2: Use one pool in custom and built-in paths

**Files:**
- Modify: `src/codemie/service/tools/assistant_factory.py:120-195` (split `create_assistant_executors` into serial prelude plus `run_builds`; keep its public signature and behavior)
- Modify: `src/codemie/service/assistant/assistant_engine_builder.py:181-250` (`create_subagent_executors`)
- Test: `tests/codemie/service/tools/test_assistant_factory.py`, `tests/codemie/service/assistant/test_langgraph_assistant.py`

**Interfaces:**
- Consumes: `run_builds` from Task 1.
- Produces: `prepare_assistants(assistant_ids, user, request, parent_assistant) -> list[Assistant]` in `assistant_factory.py` (fetch, `[]` on fetch failure, remote-deleted skip, version pinning, all serial).

- [ ] Move the prelude of `create_assistant_executors` into `prepare_assistants`; `create_assistant_executors` becomes prelude plus `run_builds` with the existing per-error handling applied in order (regular failure logged and skipped; `MCPToolLoadException` attach name+id, log, raise).
- [ ] In `create_subagent_executors`, call `prepare_assistants`, allocate all built-in names serially with `_make_unique_builtin_agent_name` (same `existing_names` seeding as now), build one combined job list (custom then built-in) and call `run_builds` once with `is_fatal` true only for custom `MCPToolLoadException`. Walk outcomes in order with the same handling as today (built-ins: attach `assistant_name=agent_name`, log, skip).
- Test-first: yes — failing tests: mixed custom+built-in builders both start before either finishes (delayed-builder, before parent construction); inverted delays still give custom order then built-in order, correct names, and the pinned version applied to the right assistant; custom-only, built-in-only, mixed, zero, single configs; regular custom failure skipped; custom `MCPToolLoadException` propagates with `assistant_name`/`assistant_id` and unstarted jobs do not run; built-in failure and built-in MCP failure skipped without raising; built-in names stable under inverted completion order.

### Task 3: Shared-state and DB-session safety

**Files:**
- Modify: `src/codemie/service/assistant/assistant_engine_builder.py:160` (`_build_builtin_subagent_executor`)
- Modify (only if Step 2 says so): `src/codemie/service/skill_workspace_preparation.py:53-77`
- Modify (only if Step 1 says so): `run_builds` in `assistant_factory.py` to clear `_current_session`
- Test: `tests/codemie/service/assistant/test_langgraph_assistant.py`, `tests/codemie/service/tools/test_assistant_factory.py`, nearest skill-workspace test file

Verify-then-decide steps. Record each result in the PR notes.

- [ ] **Step 1, DB sessions.** Pre-verified: `src/codemie/clients/postgres.py:292-312` `get_session()` opens a fresh `Session` per call unless `_current_session` (ContextVar) is set; `transactional_session()` is used only in `rest_api/routers/project_settings.py:178` and `user_settings.py:243`, not on the chat/build path; `assistant_service.py` and `assistant_handlers.py` hold no request-shared session; repositories open their own `Session(...)`. So no worker shares a Session today. Re-grep `build_agent`, `ToolkitService.get_tools` and integration/MCP lookups for any session passed by argument; if none is found, no clearing is needed. Still make it robust: inside each job, run `_current_session.set(None)` (import from `codemie.clients.postgres`) before `fn`, so a future caller inside `transactional_session` cannot share one Session across threads. Cheap and chosen by default.
- [ ] **Step 2, workspace race.** Pre-verified: `AgentWorkspaceService.create_workspace` (`agent_workspace_service.py:140-166`) is race-safe by the unique `(conversation_id, user_id)` constraint plus `IntegrityError` re-fetch. Open question: `SkillWorkspaceSeeder._seed_one` (`skill_workspace_seeder.py:58+`) writes under `skill_workspace_folder(skill.name, skill.id)` in the same workspace; built-in children share the parent's `skill_ids` (shallow copy), so two built-ins seed identical paths. Read `_seed_one` and the repository write path and decide: if seeding is idempotent or upserts safely, add no lock. If concurrent writes collide, add a lock keyed per `workspace.id` (dict of locks guarded by a small lock), never one global lock; or dedupe by seeding once serially before fan-out.
- [ ] **Step 3, built-in copy.** `model_copy(deep=False)` carries the comment "cheaply (no full serialize/validate pass)", so the reason is speed; the git history could not be read here, so run `git log -S"model_copy(deep=False)" -- src/codemie/service/assistant/assistant_engine_builder.py` to confirm. Do not switch to `deep=True`. Keep the shallow copy and replace only the fields `build_agent` and `ToolkitService` mutate in place (`toolkits`, `mcp_servers`, `context`, plus any others found by reading `assistant_service.py:540-660` for in-place mutation, e.g. `_apply_marketplace_tool_mappings`) with `copy.deepcopy` of just those fields. The runtime privates are set after the copy as today, so nothing to re-set.
- [ ] **Step 4, thread generator.** Read `src/codemie/core/thread.py` and confirm `MessageQueue` put/queue is thread-safe; add a lock only if not.
- Test-first: yes — failing tests: mutating a built-in child's `toolkits`/`mcp_servers`/`context` does not change the parent or a sibling built-in; children built concurrently while a `transactional_session`-style ambient session is set each see `_current_session` as `None` and never receive the ambient session object; with file reads mocked at the file-repository level every concurrent child receives the request files; a workspace test mirrors the Step 2 decision (lock-per-workspace test only if a lock is added, otherwise a test that concurrent `prepare_skill_workspace` calls on one conversation both succeed).
