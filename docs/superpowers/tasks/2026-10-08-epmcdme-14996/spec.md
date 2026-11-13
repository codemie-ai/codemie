# Spec: EPMCDME-14996: Batch project member budget sync

**Authoritative requirements:** `core-sdlc-standard-prompt.md` (same folder). If this spec and the prompt ever disagree, the prompt wins. If `technical-brief.md` conflicts with either, it loses.
**Research:** `technical-analysis.md`. **Size:** L (25).

## Goal

Make saving a project budget faster. Today a project with about 40 members takes 60–90 s. Stored results and error behavior must stay identical to `main`, including in failure cases. The speedup comes from batching each category's member syncs. Instead of 3–5 sequential LiteLLM calls per member, a category costs:

- one strict budget lookup;
- one write per run of identical payloads;
- bounded-parallel customer assignments.

## Design

### 1. Provider batch method
- Add `sync_member_allocations(items) -> list[BudgetProviderMemberState | Exception]` to `BudgetEnforcementProvider` (`service/budget/provider.py:192`).
- The noop provider (`provider_registry.py`) loops over its existing `sync_member_allocation`.
- The LiteLLM adapter implements the batch algorithm (§2).
- Each item is a frozen scalar snapshot taken during service preparation. Workers never touch ORM objects or a session.

```python
@dataclass(frozen=True)
class MemberSyncItem:
    allocation_id: str; project_budget_id: str; project_name: str
    budget_category: str; user_id: str; allocation_mode: str
    effective_budget_id: str | None; override_budget_id: str | None; shared_budget_id: str | None
    allocated_max_budget: float; allocated_soft_budget: float
    budget_duration: str; budget_reset_at: str | None
    effective_max_budget: float | None   # exactly what today's caller passes
```

Contract:
- The method returns exactly one outcome per item, in input order.
- Empty input returns `[]` and makes no HTTP call.
- An unavailable service, or an empty or missing provider budget ID, keeps today's FAILED mapping.
- Budget ID routing reuses `_effective_project_member_budget_id` (`budget_provider_adapter.py:116`) unchanged. That helper is adapted to read from the item snapshot.
- Today's result-to-state conversion (`budget_provider_adapter.py:1184-1235`) moves into one shared helper. `sync_member_allocation` and every batch path, fallbacks included, use that helper.

### 2. Adapter batching algorithm
This implements prompt §2 exactly:

1. **Duplicate-customer preflight.** If any `(project_name, budget_category, user_id)` repeats anywhere in the batch, the whole batch takes the ordered single-member path.
2. **One strict lookup.** Call `get_budget_info_map_strict` once for all effective IDs. If it raises, the whole batch takes the ordered single-member path. The lookup exception itself is never an item outcome.
3. **Group into runs.** For each effective ID, group consecutive items whose write payload is identical (budget ID, max, soft, duration, reset).
4. **Execute each run in order.**
   - Call `ensure_project_member_budget` once. Pass the lookup snapshot, or for a later run the budget the previous run returned.
   - On `write_succeeded`, assign every member of the run in parallel.
   - Wait for every assignment in the run to complete (result, None or exception) before the next run for that ID starts.
   - Different effective IDs run in parallel.
5. **Fallbacks for one effective ID.** Each trigger first settles the member that was already attempted. That ID's remaining items, later runs included, then go through the ordered single-member path, and the ID never returns to batching in this batch.

   | Trigger | Outcome for the attempted member |
   |---|---|
   | The run's reset timestamp is None | Nothing is attempted on the fast path; the whole run goes to the ordered path. |
   | Failed update | Assigned with the stale budget, then converted normally. |
   | Failed create | Recorded as a "provider returned None" outcome. |
   | Ensure raises | The exception is that member's outcome. It is not replayed and its customer is not assigned. |
6. **Per-item `except Exception`.** One member's error becomes that member's outcome; processing continues. There are no blanket retries, and members that already succeeded are never re-run.

**Ordered single-member path:** call `service.sync_project_member_budget_assignment` once per member, in input order, through the shared execution helper (§3). Never call the async `sync_member_allocation`, whose `asyncio.to_thread` bypasses the cap.

### 3. Bounded execution
These components live in a new core-side module under `src/codemie/enterprise/litellm/`:

- **Executor.** A lazily created, process-wide `ThreadPoolExecutor`. Its size comes from a new setting, `LITELLM_PROJECT_MEMBER_SYNC_WORKERS: int = 8`, which must be in 1–32 and sits next to `config.py:767-773`.
- **Execution helper.** One async helper runs every blocking call: lookup, ensure, assign and fallback.
  - The cap is one standard `asyncio.BoundedSemaphore`, sized to the worker count and shared on the application's main event loop.
  - Capacity is acquired before submit. If submission itself fails, the slot is released immediately. Otherwise it is released only when the blocking call finishes, through a done-callback on the executor future that releases on the owning loop thread-safely. Cancelling the awaiter does not free capacity.
  - Each call runs in `contextvars.copy_context().run`, with the OTel context attached the way `core/otel_tracing.py:180-206` does it.
  - Pool threads never wait on other pool work.
- **Shutdown.**
  - A shutdown function shuts the dedicated executor down off-loop. Preserve existing per-call timeouts and retries; no new overall shutdown deadline is introduced.
  - There is no admission gate and no tracking or draining of outstanding coordinators.
  - `_shutdown_services` (`rest_api/main.py:765`) calls it before `get_active_llm_proxy_provider().close()` (:778).
  - After shutdown starts, the executor is never recreated.
- **Unchanged.** No admission limit, queue timeout or overload response is added, and per-call timeouts and retries stay as they are.

### 4. Service flows (`project_budget_service.py`)
For each category, still sequentially, the flow is:

1. Prepare each member in order. Preparation is specific to each flow:
   - **Create** (`_sync_created_member_allocations`): capture snapshots of the allocations that were already created. It calls neither `update_allocation` nor `_ensure_override_child_budget`, so it adds no DB writes or child budgets.
   - **Resync:** run the existing `update_allocation` for each member, then `_ensure_override_child_budget` for FIXED members only. Both move out of the provider-call loop into this step, keeping their order and per-member exception handling. A member whose preparation fails or returns None is skipped.
2. Run the provider batch.
3. Persist outcomes one by one in input order.
   - Keep existing per-member exception handling around persistence.
   - Preserve the router's single final commit and existing transaction boundaries.
   - If the DB transaction fails, its changes roll back while completed LiteLLM changes remain.

The arguments and the persistence and failure policy for each flow stay exactly as in the prompt's table:

| Flow | Arguments | Persistence and failure policy |
|---|---|---|
| Create (`_sync_created_member_allocations` :501) | as today | A returned state persists child and member state, FAILED or NOOP included. An exception gets the FAILED marker only. |
| EQUAL resync (`_resync_member_allocations` :1168) | as today | The state is discarded. An exception is logged. |
| FIXED resync (`_sync_fixed_allocation` :1064) | no `effective_max_budget` | Persists the override child and member metadata. An exception is logged and the member is not marked FAILED. |

- `shared_sample` takes the prepared amounts of the first non-FIXED item, in input order, whose provider outcome is not an exception.
- Single-member callers (reset, clear override, backfill, `project_member_runtime_sync.py`) are untouched.

### 5. Dependency
- Develop against the editable codemie-enterprise 2.3.45.
- Bump `pyproject.toml:58` and `poetry.lock` to 2.3.45 only after that version is published. The bump must land before the change is delivered for merge.
- Never commit local path dependencies or `compose.enterprise-local.yaml`.

## Acceptance criteria
1. For any input, the batch method returns one outcome per item in input order, and `[]` for empty input with no HTTP call.
2. The noop provider's batch matches looping over `sync_member_allocation`.
3. A normal category costs one strict lookup, one write per run, and one assignment per member, with at most N blocking calls in flight across the process.
4. A duplicate customer, or a strict-lookup exception, sends the whole batch through the ordered single-member path with each member's normal mapping. No item is marked FAILED because of the lookup itself.
5. Each fallback trigger (reset is None, failed update, failed create, ensure raises) produces the outcome listed in §2.5. That effective ID's remaining items go through the ordered path, and other IDs stay on the fast path.
6. One item's exception never cancels or fails another item, and later items are still attempted.
7. Runs for one effective ID execute in input order, and each run waits for every assignment in the previous run.
8. Capacity is acquired before submit and released immediately if submission fails. Otherwise it is released only when the worker finishes, even if the awaiter is cancelled. Normal completion frees it. Context and OTel propagate into workers.
9. Shutdown shuts the dedicated executor down off the event loop before the LLM proxy closes. The executor is never recreated afterwards.
10. Values outside 1–32 for `LITELLM_PROJECT_MEMBER_SYNC_WORKERS` are rejected.
11. All three flows produce the same DB writes, `shared_sample`, skipped members, logs and failure markers as on `main`, for success, FAILED, NOOP and exception outcomes.
12. Existing single-member callers and their tests are unchanged. Existing tests whose provider doubles break are updated, and new tests cover the cases listed in AC 1–10.
13. On the local enterprise stack with about 40 members and 3 categories, budget save time is measured before and after the change, and the two timings are reported.

## Non-goals
- Cancellation, progress reporting, locking between concurrent saves, and UI changes.
- Running categories in parallel.
- Validating fixed sums before provider calls.
- A request-admission limit, queue-wait timeout or overload rejection.
- `/customer/list` and bulk delete.
- Changes to the reset, delete, backfill or clear-override flows, or to `project_member_runtime_sync.py`.
- Changes to the enterprise package or its HTTP client, and copying enterprise's private customer-ID logic.
- Fixing the existing mismatch between the shared amounts core stores and the amounts sent to the provider, changing reset semantics, or adding an ORDER BY to the resync query.
- Skipping members that look unchanged, and adding blanket retries.
- Publishing codemie-enterprise, and pushing or opening an MR without asking the user.
