# Lazy Budget Explicit Project Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A project member who has only logged in (never made an LLM request) gets a provider-side LiteLLM allocation at member-add time, and if one is still missing, rebalance/override self-heal it via the explicit project path instead of failing.

**Architecture:** Extract the provider-sync + metadata-persist tail of `ensure_project_member_runtime_ready` into a reusable async helper in `project_member_runtime_sync.py`. Call it eagerly from `ProjectAssignmentService._sync_project_budget_member_added` (via the existing sync/async bridge pattern) at member-add time, and call it defensively from `ProjectBudgetService._resync_member_allocation` / `_sync_fixed_allocation` whenever an allocation has no `provider_member_ref` yet, before/instead of the plain provider update already there.

**Tech Stack:** Python, SQLModel/SQLAlchemy (sync + async sessions), pytest/pytest-asyncio/pytest-mock.

**Requirements:** EPMCDME ticket "Improve lazy budget assignment with an explicit project path for inactive project members" (inline, no spec.md — see Acceptance criteria below).

**Technical analysis:** `docs/superpowers/tasks/2026-10-26-lazy-budget-explicit-project-path/technical-analysis.md`

## Global Constraints

- Commit per task using the repository's existing convention.
- Do not touch `src/codemie/enterprise/litellm/llm_factory.py` or `src/codemie/enterprise/litellm/proxy_router.py`.
- Do not change `ensure_project_member_runtime_ready`'s fail-closed `RuntimeError` behavior or `provision_global_user`.
- Preserve `_resync_member_allocation`'s existing fail-open `try/except Exception` contract (lines 1152-1160 of `project_budget_service.py`) — only add a pre-check/provision step ahead of the provider call already inside that try block.
- No duplicate or cross-project provider calls: a never-synced member going through rebalance/override must be provisioned exactly once per flow invocation.

## Acceptance criteria

- [ ] A newly added member who has only logged in can be included in project budget assignment without making an LLM request.
- [ ] Rebalance succeeds when the project contains such a member and uses the intended project path.
- [ ] Setting an override for such a member succeeds and applies to the intended project and budget category.
- [ ] A missing LiteLLM end-user record is handled through provisioning/sync rather than causing rebalance/override to fail.
- [ ] Regression tests cover a member with no prior usage and a member with prior usage; neither receives a duplicate or cross-project allocation.

## Negative-constraints pass

- "Do NOT touch `llm_factory.py` / `proxy_router.py`" — honored: no task modifies either file.
- "do NOT attempt to reconcile the broader fail-open/fail-closed inconsistency" — honored: Task 2 explicitly preserves `_resync_member_allocation`'s existing `except Exception` contract; `ensure_project_member_runtime_ready` is only read from, not modified in its error-raising behavior.
- "neither receives a duplicate or cross-project allocation" — honored: Task 3's tests assert provider call count == 1 for the never-used-member case, and the shared helper (Task 1) always passes the allocation's own `project_name`/`budget_category`/`user_id`, never a different project's.

---

### Task 1: Extract a shared provisioning helper and call it eagerly on member add

**Files:**
- Modify: `src/codemie/enterprise/litellm/project_member_runtime_sync.py:89-264` (extract) and append new function
- Modify: `src/codemie/service/project/project_assignment_service.py:208-265`
- Test: `tests/enterprise/litellm/test_project_member_runtime_sync.py`
- Test: `tests/codemie/service/project/test_project_assignment_service.py`

**Test-first:** yes — `test_member_added_eagerly_provisions_provider_allocation` must fail before the eager call is added (see Step 5/6).

**Interfaces:**
- Produces: `async def provision_member_allocation(session, *, allocation: ProjectMemberBudgetAssignment, budget: Budget, effective_max_budget: float | None = None) -> BudgetProviderMemberState` in `project_member_runtime_sync.py`. Calls `get_active_provider().sync_member_allocation(allocation=allocation, budget=budget, effective_max_budget=effective_max_budget)`, persists `provider_metadata`/`sync_status`/`budget_reset_at` via `project_member_budget_assignment_repository.update_provider_metadata`, invalidates `_resolution_cache[(allocation.project_name, allocation.budget_category, allocation.user_id)]`, and returns the provider's `member_state`. This is the existing tail of `ensure_project_member_runtime_ready` (current lines 196-264) moved into its own function; `ensure_project_member_runtime_ready` calls it instead of inlining the logic (no behavior change to that function — still raises on provider exception the same way it does today).
- Produces: `def provision_member_allocation_sync(project_name: str, budget_category: str, user_id: str) -> None` in the same module — loads the allocation (`project_member_budget_assignment_repository.get_active_by_project_category_user`) and its budget inside an `async with get_async_session()` block, calls `provision_member_allocation`, and bridges to sync callers using the exact same main-loop-dispatch-else-worker-thread pattern as `resync_project_member_allocations_sync` (lines 333-369): `get_main_event_loop()` + `asyncio.run_coroutine_threadsafe(...).result(timeout=30)` when a loop is running and isn't the caller's own, else a daemon `threading.Thread` running `asyncio.run(coro)`.
- Consumed by Task 2.

- [ ] **Step 1: Write failing tests for the extracted helper and the new sync wrapper**
  Add `test_provision_member_allocation_persists_metadata_and_invalidates_cache` and `test_provision_member_allocation_sync_uses_main_loop_when_running` / `..._uses_thread_when_no_loop` to `tests/enterprise/litellm/test_project_member_runtime_sync.py`, mirroring the existing `test_sync_wrapper_uses_asyncio_run_when_no_running_event_loop` style (mock `get_active_provider`, a fake `BudgetProviderMemberState`, assert `update_provider_metadata` called once and `_resolution_cache` popped).

- [ ] **Step 2: Run the new tests, confirm they fail**
  Run: `poetry run pytest tests/enterprise/litellm/test_project_member_runtime_sync.py -k provision_member_allocation -v`
  Expected: FAIL — `provision_member_allocation` / `provision_member_allocation_sync` not defined.

- [ ] **Step 3: Implement `provision_member_allocation` and `provision_member_allocation_sync`**
  Extract the body at lines 196-264 of `ensure_project_member_runtime_ready` into `provision_member_allocation`, have `ensure_project_member_runtime_ready` call it with the already-resolved `allocation`, `budget`, `effective_max_budget`. Add `provision_member_allocation_sync` as described above.

- [ ] **Step 4: Run the new tests, confirm they pass; re-run the full existing file to confirm no regression**
  Run: `poetry run pytest tests/enterprise/litellm/test_project_member_runtime_sync.py -v`
  Expected: all PASS, including the pre-existing `ensure_project_member_runtime_ready` suite unchanged.

- [ ] **Step 5: Write failing test for eager provisioning on member add**
  Add `test_member_added_eagerly_provisions_provider_allocation` to `tests/codemie/service/project/test_project_assignment_service.py`: mock `provision_member_allocation_sync` (patched at `codemie.service.project.project_assignment_service.provision_member_allocation_sync`), call `_sync_project_budget_member_added`, assert it was called once per created allocation with the new allocation's `(project_name, budget_category, user_id)`.
  Run: `poetry run pytest tests/codemie/service/project/test_project_assignment_service.py -k eagerly_provisions -v` — expect FAIL (no such call made yet).

- [ ] **Step 6: Call the new sync wrapper from `_sync_project_budget_member_added`**
  After `session.add(allocation)` (current line 260) inside the per-assignment loop in `project_assignment_service.py:208-265`, call `project_member_runtime_sync.provision_member_allocation_sync(project_name=project_name, budget_category=assignment.budget_category, user_id=user_id)`, wrapped in `try/except Exception` that logs a warning and continues (matches this method's existing fail-open posture — the DB row is still created and will self-heal via Task 2's rebalance/override fallback if this call fails). Import `project_member_runtime_sync` at module level (import cycle check: `project_assignment_service.py` does not currently import from `enterprise.litellm`; verify no circular import before committing — if one exists, import inside the method body instead, matching the existing local `from codemie.service.settings.settings import SettingsService`-style inline import used elsewhere in this codebase).

- [ ] **Step 7: Run the test from Step 5, confirm it passes; run the full file**
  Run: `poetry run pytest tests/codemie/service/project/test_project_assignment_service.py -v`
  Expected: all PASS.

- [ ] **Step 8: Commit**

---

### Task 2: Defensive detect-and-provision fallback in rebalance/override

**Files:**
- Modify: `src/codemie/service/budget/project_budget_service.py:1080-1160` (`_sync_fixed_allocation`, `_resync_member_allocation`)
- Test: `tests/codemie/service/budget/test_project_budget_service.py`

**Test-first:** yes — `test_resync_member_allocation_provisions_when_no_provider_member_ref` and its FIXED-mode counterpart must fail against the current code before the guard is added (see Step 1/2).

**Interfaces:**
- Consumes: `provision_member_allocation` from Task 1 (`codemie.enterprise.litellm.project_member_runtime_sync`).
- Produces: no new public symbols; behavior change only.

- [ ] **Step 1: Write failing tests for the never-synced-member path**
  Add to `tests/codemie/service/budget/test_project_budget_service.py`:
  - `test_resync_member_allocation_provisions_when_no_provider_member_ref`: build an allocation whose `provider_metadata` has no `provider_member_ref` (or is `{}`), call `_resync_member_allocation(...)`, assert `provider.sync_member_allocation` (the mocked provider) was called exactly once and `update_provider_metadata` reflects the provisioned state — no second/duplicate provider call for the same allocation in one invocation.
  - `test_resync_member_allocation_skips_provisioning_when_provider_member_ref_present`: same setup but `provider_metadata={"raw": {"provider_member_ref": "ref-1"}}` — assert the normal single provider call happens as today, with no extra provisioning call.
  - `test_sync_fixed_allocation_provisions_when_no_provider_member_ref`: same two-case check for the FIXED-mode path through `_sync_fixed_allocation`.
  Run: `poetry run pytest tests/codemie/service/budget/test_project_budget_service.py -k provisions_when_no_provider_member_ref -v`
  Expected: FAIL (current code always goes straight to the existing `provider.sync_member_allocation` call regardless of `provider_member_ref`, so call-count/metadata assertions for the "provision path taken" case do not hold yet).

- [ ] **Step 2: Add the detect-and-provision guard in `_resync_member_allocation` (line 1120) and `_sync_fixed_allocation` (line 1080)**
  In both methods, before the existing `provider.sync_member_allocation(...)` call, read `provider_member_ref` the same way `resync_project_member_allocations` does (`_metadata_value(updated.provider_metadata or {}, "provider_member_ref")` from `project_member_runtime_sync.py:284-286` — import `_metadata_value` or inline the same two-line lookup). If absent, call `provision_member_allocation(session, allocation=updated, budget=budget, effective_max_budget=effective_max_budget)` (Task 1) instead of the existing direct `provider.sync_member_allocation` call for that allocation — this single call both provisions and syncs current amounts, so skip the subsequent plain provider call in that branch to avoid a duplicate. If `provider_member_ref` is present, keep the existing code path unchanged. In `_resync_member_allocation`, this guard sits inside the existing `try/except Exception` (lines 1134-1160) — no change to that except block's behavior.

- [ ] **Step 3: Run the Step 1 tests, confirm they pass; run the full project_budget_service test files**
  Run: `poetry run pytest tests/codemie/service/budget/test_project_budget_service.py tests/codemie/service/budget/test_project_budget_service_lifecycle.py tests/codemie/service/budget/test_project_budget_service_group_creation.py -v`
  Expected: all PASS.

- [ ] **Step 4: Commit**

---

### Task 3: End-to-end regression tests for rebalance and override on a never-used member

**Files:**
- Test: `tests/codemie/service/budget/test_project_budget_service.py`

**Test-first:** yes — the two tests below are written against existing public methods; they confirm (or, if gaps surface, drive a fix to) Task 2's guard at the acceptance-scenario level.

**Interfaces:**
- Consumes: `rebalance_project_budget`, `override_member_allocation` (existing, `project_budget_service.py:1355`, `:1512`); the provisioning behavior from Task 2.

- [ ] **Step 1: Write acceptance-level tests**
  Add:
  - `test_rebalance_provisions_never_used_member_exactly_once`: project budget with two members — one with `provider_metadata` containing `provider_member_ref` (prior usage), one without (never used). Call `rebalance_project_budget`. Assert: call succeeds (no exception, no 4xx raised), the mocked provider's `sync_member_allocation` was called exactly once for the never-used member and exactly once for the existing member (no duplicates), and the never-used member's resulting allocation still has `project_name`/`budget_category` matching the budget under test (no cross-project write).
  - `test_override_provisions_never_used_member_and_succeeds`: same never-used-member fixture, call `override_member_allocation(...)` for that member, assert it succeeds and the provider was called exactly once for the provisioning step (plus whatever single downstream rebalance call `override_member_allocation` already performs today — assert total count matches the pre-existing override+rebalance call shape, not an extra one).
  Run: `poetry run pytest tests/codemie/service/budget/test_project_budget_service.py -k "provisions_never_used_member" -v`
  Expected: these exercise the public-method acceptance scenario directly; if Task 2 is complete they should PASS — this task exists to lock in the ticket's exact repro scenario, not to introduce new implementation.

- [ ] **Step 2: If either test fails, fix the gap in Task 2's guard**
  (Only if Step 1 surfaces a gap not caught by Task 2's unit tests — e.g. `override_member_allocation`'s call into `rebalance_project_budget` double-triggering provisioning.) Adjust the guard in `project_budget_service.py` so provisioning happens once per allocation per outer call, then re-run.

- [ ] **Step 3: Run the full budget test suite for this area**
  Run: `poetry run pytest tests/codemie/service/budget/test_project_budget_service.py tests/enterprise/litellm/test_project_member_runtime_sync.py -v`
  Expected: all PASS.

- [ ] **Step 4: Commit**
