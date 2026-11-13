# EPMCDME-14996 Batch Project Member Budget Sync Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Make project budget save faster by batching each category's member syncs. Stored results and error behavior stay identical to `main`.
**Architecture:** The service prepares frozen `MemberSyncItem` snapshots, then makes one `provider.sync_member_allocations(items)` call, then persists the outcomes in input order. The LiteLLM adapter runs one strict lookup, one write per payload run and parallel assignments. Every blocking call goes through a dedicated executor capped by one `asyncio.BoundedSemaphore`. The executor is shut down at application shutdown.
**Spec:** `spec.md` (authority: `core-sdlc-standard-prompt.md`). **Research:** `technical-analysis.md`.
Commit per task using the repository's existing convention. Tests: `poetry run pytest <path> -v`.

## Global Constraints
- Develop against the editable codemie-enterprise 2.3.45. Never commit local path dependencies or `compose.enterprise-local.yaml`. The pin bump for `pyproject.toml:58` and `poetry.lock` is **blocked on registry publication by the user** and is not a task in this plan.
- No enterprise package changes. Never call private enterprise helpers. Core never imports `codemie_enterprise` at module top.
- No admission limit, queue timeout, overload response, blanket retry or overall shutdown deadline. Per-call timeouts and retries are unchanged.
- Single-member callers stay untouched: reset (:1366), clear override (:1547), `budget_service.py:1477` and `project_member_runtime_sync.py`.
- Categories stay sequential. No ORDER BY is added. Neither unchanged nor FIXED members are skipped. Keep existing fixed-sum validation in its current position; do not introduce earlier validation. Don't fix the shared-amount mismatch.

## Review Focus
- Interleaved effective IDs (A,B,A). Outcomes map back by index, and runs are consecutive within A's own subsequence (T4).
- A cancelled awaiter mid-batch. Capacity is freed only when the worker ends, and the next batch is not starved (T2).
- Submission fails after the slot was acquired (the executor is already shut down, or `submit` raises). The slot is released at once and the error reaches the caller (T2).
- The strict map omits an ID. Ensure then gets `existing_budget=None` (the create path) (T4).
- Shutdown with calls in flight. `shutdown(wait=True)` runs off the loop and lets submitted calls finish, and the executor is not recreated (T2, T7).

---

### T1: MemberSyncItem, protocol batch method, noop batch
**Files:** Modify `src/codemie/service/budget/provider.py` (near :60, protocol :192/:340), `src/codemie/service/budget/provider_registry.py:222`. Test `tests/codemie/service/budget/test_provider_registry.py`.
**Produces:**
```python
@dataclass(frozen=True)
class MemberSyncItem:
    allocation_id: str; project_budget_id: str; project_name: str
    budget_category: str; user_id: str; allocation_mode: str
    effective_budget_id: str | None; override_budget_id: str | None; shared_budget_id: str | None
    allocated_max_budget: float; allocated_soft_budget: float
    budget_duration: str; budget_reset_at: str | None   # copied unconverted from budget
    effective_max_budget: float | None
MemberSyncOutcome = BudgetProviderMemberState | Exception
async def sync_member_allocations(self, items: Sequence[MemberSyncItem]) -> list[MemberSyncOutcome]
```
Test-first: yes — the noop batch returns `[]` for empty input. For 3 items it returns 3 NOOP states equal to the looped `sync_member_allocation` results. An exception raised by the per-item call becomes that item's outcome, and the items after it still run.
- [ ] Write the failing tests, run them (FAIL), and implement. The noop loops in order with `await self.sync_member_allocation(allocation=item, budget=item, effective_max_budget=item.effective_max_budget)`; the item duck-types both arguments. It catches `Exception` per item. Run the tests (PASS).

### T2: Bounded executor module and worker setting
**Files:** Create `src/codemie/enterprise/litellm/project_member_sync_executor.py`. Modify `src/codemie/configs/config.py:767-773`: `LITELLM_PROJECT_MEMBER_SYNC_WORKERS: int = Field(default=8, ge=1, le=32)`. Tests: create `tests/enterprise/litellm/test_project_member_sync_executor.py`, and extend `tests/codemie/configs/test_config.py`.
**Produces:**
```python
class ProjectMemberSyncExecutor:
    def __init__(self, workers: int | None = None) -> None   # None -> config
    async def run(self, fn: Callable[..., T], /, **kwargs) -> T
    async def shutdown(self) -> None
def get_project_member_sync_executor() -> ProjectMemberSyncExecutor
async def shutdown_project_member_sync_executor() -> None
```
Design:
- **Executor.** A dedicated `ThreadPoolExecutor(max_workers=workers)`, created lazily.
  - Once `shutdown` has run it is never recreated, and `run` raises `RuntimeError`.
  - The adapter's existing per-item and lookup `except Exception` handling absorbs that error like any other call failure. No special-case logic is added for it.
- **Cap.** One standard `asyncio.BoundedSemaphore(workers)`, shared on the application's main event loop. It is created lazily on the first `run`, which also records its owning loop.
- **`run`.**
  1. `await sem.acquire()` before submit.
  2. Submit the call to the executor. The worker runs `fn` inside `contextvars.copy_context().run`, with the OTel context captured by `get_otel_context_for_thread` and attached/detached (`core/otel_tracing.py:180-206`; mirror `hedged_handler.py`). If getting the executor or `submit` raises, `sem.release()` immediately and re-raise.
  3. Otherwise the only release path is `cf.add_done_callback(lambda _: loop.call_soon_threadsafe(sem.release))`, guarded against a closed loop. It fires when the worker call finishes.
  4. `return await asyncio.shield(asyncio.wrap_future(cf))`, so cancelling the awaiter neither cancels the call nor frees its slot.
- **`shutdown`.** Mark the executor closed. If it exists, `await asyncio.to_thread(executor.shutdown, wait=True)`. It is idempotent. There is no admission gate and no coordinator tracking or drain.

Test-first: yes — failing tests, each building its own `ProjectMemberSyncExecutor(workers=2)` so the semaphore binds to that test's loop:
- At most N calls are in flight (5 blocking calls held by `threading.Event`s).
- Normal completion frees the slot.
- A failing submit (patch `submit` to raise) releases the slot immediately and re-raises.
- A cancelled awaiter keeps the slot held until its worker finishes, and the slot is freed after that.
- A contextvar and the OTel context are visible in the worker.
- After `shutdown`, the executor is shut down, `run` raises `RuntimeError`, and no new executor is built.
- The config rejects 0 and 33 and accepts 1, 8 and 32.
- [ ] Write the tests, run them (FAIL), implement, run them (PASS).

### T3: Adapter: shared conversion helper, routing, ordered path, preflight and lookup fallback
**Files:** Modify `src/codemie/enterprise/litellm/budget_provider_adapter.py`: :116 and :1153-1235, plus the new `sync_member_allocations`. Test `tests/enterprise/litellm/test_budget_provider_adapter.py`.
**Consumes:** T1 and T2. **Produces:** `_member_state_from_result(result, *, project_name, project_budget_id, budget_category, allocation_id, user_id) -> BudgetProviderMemberState`.
- Move :1196-1235 verbatim into that helper, keeping the same logs. `sync_member_allocation` keeps `asyncio.to_thread` and calls the helper.
- Widen `_effective_project_member_budget_id`'s type hint to accept `MemberSyncItem`. Its body is unchanged (getattr-based).
- `sync_member_allocations` steps:
  1. Empty input → `[]`.
  2. `_get_service()` is None → a FAILED state per item, logged as at :1176.
  3. Compute the effective IDs and the resolved max (`effective_max_budget` if it is not None, else `allocated_max_budget`).
  4. Preflight: any repeated `(project_name, budget_category, user_id)` → ordered path for all items.
  5. `executor.run(service.get_budget_info_map_strict, budget_ids=unique_ids)`. If it raises, log and take the ordered path for all items. That exception is never an outcome.
  6. Otherwise call T4's `_sync_budget_groups`.
- `_run_ordered(service, executor, items, ids, idxs, outcomes)`: for each index in order, `executor.run(service.sync_project_member_budget_assignment, ...)` with the same kwargs as :1184. Convert the result with the helper; `except Exception` makes the exception that item's outcome. It never calls `sync_member_allocation`.

Test-first: yes — failing tests, using a `SimpleNamespace` service with sync callables and a real `ProjectMemberSyncExecutor(workers=2)`:
- Empty input makes no service call.
- A None service gives a FAILED state per item.
- A duplicate customer means the strict lookup is never called, and the ordered calls happen in input order.
- A strict lookup that raises (httpx and ValueError) gives an ordered call per item with normal mapping. One item raising does not stop later items.
- Results map to None→FAILED, missing `budget_id`→FAILED, and OK with metadata.
- Existing single-member tests still pass.
- [ ] Write the tests, run them (FAIL), implement, run them (PASS).

### T4: Adapter fast path: runs, parallel IDs and per-ID fallbacks
**Files:** Same adapter and test file as T3. **Produces:** `_sync_budget_groups(...)` and `_sync_budget_group(...)`, both private.
- Group indices by effective ID, keeping input order. Run the groups through `asyncio.gather` (parallel across IDs).
- Within a group, split the ID's subsequence into consecutive runs keyed by `(id, resolved_max, soft, duration, reset)`. `current = snapshot.get(id)`. For each run, in order:
  - Reset is None → `_run_ordered` on this run and all later items of the ID; stop.
  - Otherwise `ensure = executor.run(service.ensure_project_member_budget, budget_id, max_budget, soft_budget, budget_duration, budget_reset_at, existing_budget=current)`.
    - **Raises:** the first member's outcome is the exception (no assign). Send `run[1:]` and the later runs to the ordered path; stop.
    - **`write_succeeded`:** `gather` an assignment per member (`assign_project_member_budget(..., budget_id=id, budget=ensure.budget, budget_reset_at=item.budget_reset_at)`), each wrapped so that `except Exception` becomes its own outcome. Then set `current = ensure.budget` and go to the next run.
    - **Failed create** (`ensure.budget is None`): the first member gets `helper(None)`.
    - **Failed update:** assign the first member with the stale `ensure.budget` and convert normally (an exception is its outcome).

    After a failed create or update, the rest of the ID goes to the ordered path; stop. That ID never returns to batching.

Test-first: yes — failing tests:
- A normal 5-member shared category with a remainder (2 runs) gives 1 lookup, 2 ensures (the second with the first's returned budget) and 5 assigns, with no assignment of run 2 starting before every assignment of run 1 ends.
- Interleaved A,B,A IDs map outcomes by index.
- An ID missing from the strict map gives `existing_budget=None`.
- Each fallback trigger (reset None, failed update, failed create, ensure raises) gives the outcome in spec §2.5, with the remaining items of that ID on the ordered path and another ID's items still assigned on the fast path.
- One assignment raising leaves its siblings and later runs processed, with no retries.
- [ ] Write the tests, run them (FAIL), implement, run them (PASS).

### T5: Service create flow → prepare, batch, persist
**Files:** Modify `src/codemie/service/budget/project_budget_service.py:501-574`. Test `tests/codemie/service/budget/test_project_budget_service.py`; update `test_project_budget_service_group_creation.py` doubles. **Produces:** `_member_sync_item(allocation, budget, effective_max_budget) -> MemberSyncItem`. It reads the ORM in the sequential layer: `allocation_id=str(allocation.id)` and the reset copied as-is from `budget`.
- Build the items in today's allocation order, using today's `effective_max_budget` expression.
- Make one `provider.sync_member_allocations(items)` call.
- Persist by zipping outcomes in order:
  - Exception → today's log plus FAILED marker only.
  - State → today's two writes inside `try`. A persistence exception goes to the same log and FAILED marker as today.
- No `update_allocation` or `_ensure_override_child_budget` calls are added. No fixed-sum validation is added to this flow.

Test-first: yes — update `test_sync_created_member_allocations_marks_failures` and `..._uses_full_budget_when_enforce_limit_false` so the doubles expose `sync_member_allocations`, then add failing tests:
- The batch receives items with the exact `effective_max_budget` values.
- A mixed OK / FAILED / NOOP / exception batch gives today's writes per member in order.
- A persistence exception on member 2 gives the FAILED marker, and member 3 is still persisted.
- [ ] Write the tests, run them (FAIL), implement, run them (PASS).

### T6: Service EQUAL/FIXED resync → prepare, batch, persist
**Files:** Modify `project_budget_service.py:1064-1205`. Tests: `test_project_budget_service.py` (:30 test), `test_project_budget_service_lifecycle.py`.
- **Fixed-sum validation is kept as is.** The `_equal_amounts_with_fixed_overrides` call and its `ValidationException` → `ExtendedHTTPException(code=400)` mapping at :1181-1184 stay in their current position: after `get_active_by_budget_id`, before `enforce_limit` and before preparation. No step removes, moves, duplicates, bypasses or wraps them. Preparation consumes the `new_alloc_map` they return.
- Replace `_resync_member_allocation` with `_prepare_resync_member(...) -> tuple[ProjectMemberBudgetAssignment, MemberSyncItem, tuple[float, float]] | None`.
  - It skips when `new_amounts` is None and when `update_allocation` returns None.
  - For FIXED it runs `_ensure_override_child_budget` (the :1073 args) and builds an item with `effective_max_budget=None`.
  - For EQUAL the item gets `None if enforce_limit else eff_max`, and the amounts are `(eff if not None else allocated_max, allocated_soft)`.
  - Its `except` emits the exact :1137 warning and returns None.
- Split `_sync_fixed_allocation` into prepare and `_persist_fixed_member_state(session, budget, alloc_id, updated, member_state)`, which holds the :1083-1102 writes.
- `_resync_member_allocations` then works in three phases:
  1. Prepare the members sequentially, in list order.
  2. Make one batch call.
  3. Persist in order:
     - Exception → the :1137 warning.
     - FIXED state → `_persist_fixed_member_state` in `try`, where an exception gives the :1137 warning and no FAILED marker.
     - EQUAL state → discarded.

  `shared_sample` is the first EQUAL item's amounts whose outcome is not an exception. The `_ensure_shared_child_budget_after_resync` call is unchanged.

Test-first: yes — update the :30 test to assert `effective_max_budget is None` on the batch items and keep `update_provider_metadata.assert_not_awaited()`. Then add failing tests:
- FIXED prep runs before any provider call, and its state is persisted.
- A FIXED persistence exception is logged, with no FAILED write.
- A prep exception or a None `update_allocation` skips the member.
- `shared_sample` skips FIXED and raised items, but takes a FAILED or NOOP EQUAL item.
- When FIXED overrides exceed the project budget, the resync still raises `ExtendedHTTPException` with code 400, and neither `update_allocation` nor `sync_member_allocations` is called.
- [ ] Write the tests, run them (FAIL), implement, run them (PASS).

### T7: Shutdown wiring
**Files:** Modify `src/codemie/rest_api/main.py:765-778`. Test `tests/codemie/rest_api/test_main_lifespan.py`.
- In `_shutdown_services`, `await shutdown_project_member_sync_executor()` before `get_active_llm_proxy_provider().close()`, inside a try/except that logs and continues, like the neighbouring steps. This only shuts the dedicated executor down, off the event loop. There is no admission gate and no coordinator drain.

Test-first: yes — a failing test that patches both functions onto one `Mock` parent and asserts the executor shutdown is awaited before `close()`.
- [ ] Write the test, run it (FAIL), implement, run it (PASS).
