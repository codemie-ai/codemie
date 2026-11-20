# Rebase and Reimplement Concurrent Subagent Executors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebase EPMCDME-15952 onto `main` and reimplement its subagent-build concurrency layer on `main`'s `SubagentHandle`/`SubagentBuildContext` contracts, since every touched function changed signature/return type under EPMCDME-16463/16411.

**Architecture:** One shared, bounded `ThreadPoolExecutor`-backed `run_builds` pool in `tools/assistant_factory.py` runs both custom and builtin subagent build jobs as one job list; each job returns a `SubagentHandle` (not a raw executor); `configure_agent_kwargs` aggregates `tree_requires_confirmation` by OR-ing over the completed handle list after the pool finishes, never during.

**Tech Stack:** Python, `concurrent.futures.ThreadPoolExecutor`, `contextvars`, pytest/`unittest.mock`.

**Requirements source:** inline (see Acceptance criteria below) — no spec.md for this task.

**Research:** `docs/superpowers/tasks/2026-11-13-rebase-reimplement-subagent-executors/technical-analysis.md`

**Tooling note:** Prefer `mcp__serena__*` symbol-level find/edit tools over freeform text search/edit — the touched files (`assistant_factory.py`, `assistant_engine_builder.py`) are large with many similarly-named helpers.

**Commits:** one commit per task, using this repo's existing convention (`EPMCDME-15952: <summary>`), as seen in the branch's recent commit history. No separate "run quality gates" / review / verification task — the calling flow runs those itself.

## Global Constraints

- Every rebuilt function must keep `main`'s signature and return type (`SubagentHandle`, `SubagentBuildContext`, `ResolvedToolPolicy`) — no reverting to the old `CompiledStateGraph`-returning contracts.
- Custom and builtin subagent builds run through **one** shared pool / one worker budget, not two.
- `tree_requires_confirmation` aggregation happens only after `run_builds` returns, over the order-preserved handle list — never under a lock or mid-build.
- `ConversationService.find_or_create_conversation` (public) is not reintroduced anywhere.
- Builtin clones deep-copy `toolkits`, `mcp_servers`, `context` from the parent — mandatory once builds are concurrent.

## Acceptance criteria

- [ ] Branch is rebased onto `main`; `AssistantFactory.build`, `create_assistant_executors`, `_build_builtin_subagent_executor`, `create_subagent_executors`, `configure_agent_kwargs` all match `main`'s current signatures/return types, with none of the old `run_builds`/`custom_build_jobs`/`collect_custom_executors` machinery or the `find_or_create_conversation` call site carried over unreconciled.
- [ ] Custom and builtin subagent builds run concurrently through one shared, bounded thread pool (`SUBAGENT_BUILD_MAX_WORKERS`, default 4), preserving submission order in the returned `list[SubagentHandle]`.
- [ ] A custom sub-assistant's `MCPToolLoadException` still aborts the remaining unstarted jobs in the phase and is re-raised with assistant context attached, exactly as the sequential path does today.
- [ ] Builtin clones no longer share `toolkits`/`mcp_servers`/`context` object identity with the parent or with sibling clones.
- [ ] `tree_requires_confirmation` is `True` whenever `resolved_policy.requires_confirmation` or any completed child handle reports `True`, computed after all builds finish, with no call to `ConversationService.find_or_create_conversation` anywhere in the path.
- [ ] `SUBAGENT_BUILD_MAX_WORKERS` exists in `configs/config.py` (`Field(default=4, gt=0)`).

---

### Task 1: Rebase branch onto `main`

**Files:**
- Modify (conflict resolution): `src/codemie/service/tools/assistant_factory.py`, `src/codemie/service/assistant/assistant_engine_builder.py`, `src/codemie/configs/config.py`

**Interfaces:**
- Produces: a worktree where every function named in Global Constraints matches `main`'s current signature (`SubagentHandle`-returning, `SubagentBuildContext`/`ResolvedToolPolicy`-threading) with the old concurrency machinery and the `find_or_create_conversation` call removed, not merged.

- [ ] **Step 1:** Run `git rebase main`. On conflicts in the files above, resolve by taking `main`'s version of every touched function wholesale (do not hand-merge old `run_builds`/`_pooled_call`/`_timed_call`/`custom_build_jobs`/`collect_custom_executors`/`is_fatal_custom_failure` bodies into the new signatures — drop them; Tasks 3–6 rebuild the equivalent behavior against the new contracts). Drop the `find_or_create_conversation` IntegrityError-tolerance commit's hunk entirely if it reappears as a conflict.
- [ ] **Step 2:** Run `pytest tests/codemie/service/tools/test_assistant_factory.py tests/codemie/service/assistant/test_langgraph_assistant.py -v`. Expected: collection succeeds (files import cleanly against `main`'s new types); failures referencing removed old-branch symbols are expected and resolved by Tasks 3–7, not here.
- [ ] **Step 3:** Commit the rebase resolution.

Test-first: no — mechanical conflict resolution onto `main`'s existing contracts; no new behavior is introduced in this task, so there is no new failing test to write first.

---

### Task 2: Reinstate builtin-clone isolation fix

**Files:**
- Modify: `src/codemie/service/assistant/assistant_engine_builder.py:161-171` (main line numbers) — the `child_assistant = parent.model_copy(deep=False)` block in `_build_builtin_subagent_executor`.
- Test: `tests/codemie/service/assistant/test_langgraph_assistant.py`

**Interfaces:**
- Consumes: `Assistant.toolkits: list`, `.mcp_servers: list`, `.context` (existing fields).
- Produces: no signature change — same clone object, now with independent `toolkits`/`mcp_servers`/`context` list/object identity from the parent.

- [ ] **Step 1 (test-first):** Add a test building two builtin clones from the same parent `Assistant` (mock `BuiltinSubagentsRegistry.get_system_prompt` and the `AssistantFactory.build` call), then assert `clone_a.toolkits is not parent.toolkits` and `clone_a.toolkits is not clone_b.toolkits` (same for `mcp_servers`, `context`).
- [ ] **Step 2:** Run it — expect `AssertionError` (currently shared references).
- [ ] **Step 3:** In `_build_builtin_subagent_executor`, after the existing `model_copy(deep=False)` overrides, add: `child_assistant.toolkits = deepcopy(parent.toolkits)`, `child_assistant.mcp_servers = deepcopy(parent.mcp_servers)`, `child_assistant.context = deepcopy(parent.context)` (import `deepcopy` from `copy` at module level).
- [ ] **Step 4:** Run the test — expect PASS.

Test-first: yes — object-identity assertion on two sibling builtin clones' `toolkits`/`mcp_servers`/`context`.

---

### Task 3: Rebuild the shared bounded build pool

**Files:**
- Modify: `src/codemie/service/tools/assistant_factory.py` (module level, near where the old `run_builds` lived) — re-add `SUBAGENT_BUILD_MAX_WORKERS` to `src/codemie/configs/config.py` (`Field(default=4, gt=0)`, next to `FILE_DATASOURCE_UPLOAD_MAX_WORKERS`).
- Test: `tests/codemie/service/tools/test_assistant_factory.py`

**Interfaces:**
- Produces: `run_builds(jobs: list[tuple[str, str, Callable[[], SubagentHandle]]], is_fatal: Callable[[str, BaseException], bool]) -> list[tuple[SubagentHandle | None, BaseException | None]]` — same bounded/ordered/fatal-abort contract as before, but each job now returns a `SubagentHandle` instead of a `CompiledStateGraph`. Internals (`_timed_call`, `_pooled_call` clearing `_current_session`, `contextvars.copy_context().run(...)` per submission) are unchanged in shape from the pre-rebase version.

- [ ] **Step 1 (test-first):** Add a test submitting `SUBAGENT_BUILD_MAX_WORKERS + 2` jobs, each a `fn` that increments/decrements a shared active-counter and sleeps briefly; assert the observed peak concurrent count never exceeds `SUBAGENT_BUILD_MAX_WORKERS`, and that `outcomes` preserves submission order regardless of completion order.
- [ ] **Step 2:** Run it — expect FAIL (`run_builds` does not exist yet post-rebase).
- [ ] **Step 3:** Re-add `run_builds`/`_timed_call`/`_pooled_call` as they were pre-rebase (contextvars-propagating, bounded `ThreadPoolExecutor`, order-preserving, fatal-abort-on-first-match), unchanged except the jobs' `fn` return type is now whatever the caller supplies (callers in Tasks 4–5 supply `SubagentHandle`-returning callables).
- [ ] **Step 4:** Run the test — expect PASS.

Test-first: yes — peak-concurrency bound and submission-order preservation across out-of-order completions.

---

### Task 4: Run custom subagent builds through the pool, returning `SubagentHandle`

**Files:**
- Modify: `src/codemie/service/tools/assistant_factory.py:93-119` (`AssistantFactory.build`), `:107-182` (`create_assistant_executors`) (main line numbers).
- Test: `tests/codemie/service/tools/test_assistant_factory.py`

**Interfaces:**
- Consumes: `run_builds` from Task 3; `AssistantService.build_agent_handle(..., is_subagent=True, build_context=self.build_context) -> SubagentHandle` (existing on `main`).
- Produces: `create_assistant_executors(...) -> list[SubagentHandle]`, same argument list `main` already has (including `build_context` threading), in submission order.

- [ ] **Step 1 (test-first):** Add a test with two custom sub-assistants where one raises `MCPToolLoadException` and the other succeeds; assert the exception is re-raised with `attach_assistant_context(...)` called, and that no `SubagentHandle` is returned for either when the fatal one is first in the list (remaining unstarted job cancelled).
- [ ] **Step 2:** Run it — expect FAIL.
- [ ] **Step 3:** Rebuild `custom_build_jobs`/`collect_custom_executors` (or inline equivalents) so each job's `fn` is `AssistantFactory(...).build` (returns `SubagentHandle`), submitted through `run_builds` with `is_fatal_custom_failure`; walk outcomes in order, re-raising `MCPToolLoadException` with context attached and logging+skipping other errors, same as the pre-rebase behavior, but collecting `SubagentHandle` instead of a raw executor.
- [ ] **Step 4:** Run the test — expect PASS.

Test-first: yes — MCP load failure aborts the phase and is re-raised with assistant context attached.

---

### Task 5: Unify custom + builtin builds onto one pool call

**Files:**
- Modify: `src/codemie/service/assistant/assistant_engine_builder.py:186-247` (`create_subagent_executors`, main line numbers).
- Test: `tests/codemie/service/assistant/test_langgraph_assistant.py`

**Interfaces:**
- Consumes: `run_builds` (Task 3), `_build_builtin_subagent_executor` (Task 2, now isolation-safe), `create_assistant_executors`'s job-building half (Task 4).
- Produces: `create_subagent_executors(...) -> list[SubagentHandle]`, custom handles first then builtin handles, both built through the **same** `run_builds` call.

- [ ] **Step 1 (test-first):** Add a test with 1 custom + 1 builtin subagent where both builds' `fn`s are mocked to record wall-clock start times; assert both start before either finishes (i.e. they overlap — ran on one shared pool, not two sequential phases).
- [ ] **Step 2:** Run it — expect FAIL (current code runs two separate sequential loops).
- [ ] **Step 3:** In `create_subagent_executors`, keep builtin name allocation serial (unchanged, before submission), but append the builtin jobs to the **same** job list as the custom jobs (built via Task 4's job-building helper) and call `run_builds` **once** over the combined list; split the returned outcomes back into custom/builtin segments by original slice length to reassemble per-kind error handling, then concatenate into one ordered `list[SubagentHandle]`.
- [ ] **Step 4:** Run the test — expect PASS.

Test-first: yes — a custom and a builtin build overlap in wall-clock time (one pool, not two sequential loops).

---

### Task 6: Aggregate `tree_requires_confirmation` post-completion

**Files:**
- Modify: `src/codemie/service/assistant/assistant_engine_builder.py:299-347` (`configure_agent_kwargs`, main line numbers).
- Test: `tests/codemie/service/assistant/test_langgraph_assistant.py`

**Interfaces:**
- Consumes: `create_subagent_executors(...) -> list[SubagentHandle]` (Task 5); `resolved_policy: ResolvedToolPolicy` (existing on `main`).
- Produces: `configure_agent_kwargs(...)` returns `tree_requires_confirmation: bool` unchanged in meaning: `resolved_policy.requires_confirmation or any(handle.tree_requires_confirmation for handle in handles)`.

- [ ] **Step 1 (test-first):** Add a test with `resolved_policy.requires_confirmation=False` and three concurrently-built mock `SubagentHandle`s where only the last one has `tree_requires_confirmation=True`; assert the function's returned `tree_requires_confirmation` is `True`, and assert `ConversationService.find_or_create_conversation` is never called (patch it and assert `assert_not_called()`).
- [ ] **Step 2:** Run it — expect FAIL if the aggregation or the handle-list wiring from Task 5 isn't yet threaded through this function.
- [ ] **Step 3:** Confirm/wire `configure_agent_kwargs` to compute `tree_requires_confirmation` by OR-ing over the handle list returned by Task 5's `create_subagent_executors`, strictly after that call returns (no lock, no partial aggregation during the pool run). Confirm no call to `ConversationService.find_or_create_conversation` exists anywhere in this function (it must not reappear from the rebase).
- [ ] **Step 4:** Run the test — expect PASS.

Test-first: yes — `tree_requires_confirmation` OR-aggregates over handles after completion, and no `find_or_create_conversation` call exists.

---

## Negative constraints checked

- No reintroduction of `ConversationService.find_or_create_conversation` (public) — honored by Task 1 (drop on rebase) and verified by Task 6's `assert_not_called()`.
- No two separate pools/loops for custom vs builtin — honored by Task 5 (single `run_builds` call over the combined job list), verified by its overlap test.
- No lock/mid-build aggregation of `tree_requires_confirmation` — honored by Task 6 (computed only after `run_builds`/`create_subagent_executors` returns).
- No verbatim reuse of the old branch's test file — Tasks 2–6 write new tests against `SubagentHandle` fields (`.executor`, `.tree_requires_confirmation`, `.agent`, `.name`, `.description`), not against raw executors.
- No whole-suite quality-gate, browser-verification, code-review, or commit-the-artifacts task — none emitted; the calling flow owns those stages.
