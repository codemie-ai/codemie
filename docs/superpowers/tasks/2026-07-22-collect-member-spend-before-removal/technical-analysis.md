# Technical Research

**Task**: budget project-member spend-tracking removal
**Generated**: 2026-07-22T00:00:00Z

---

## 1. Original Context

Option A — Collect spend synchronously before deletion (safest). In _sync_project_budget_member_removed(), before delete_member_allocation(): 1. Fetch member spend from LiteLLM via provider.collect_member_budget_spend_for_refs() for this specific provider_member_ref. 2. Write final row to project_spend_tracking (or update allocation.spend). 3. THEN delete the LiteLLM customer. The bug: when a user is removed from a project mid-budget-period, their accrued spend since the last scheduled collection is lost because the LiteLLM customer is deleted before a final spend snapshot is captured.

---

## 2. Codebase Findings

### Existing Implementations

- `/src/codemie/service/project/project_assignment_service.py` — `ProjectAssignmentService` class. Contains the bug site: `_sync_project_budget_member_removed()` (lines 176–206). Called from both `remove_user_from_project()` (line 697) and `bulk_remove_users_from_project()` (line 628). The method fetches active `ProjectMemberBudgetAssignment` rows for a user/project, calls `provider.delete_member_allocation()` for each, then soft-deletes the allocation. No spend snapshot is captured before deletion.
- `/src/codemie/service/spend_tracking/spend_collector_service.py` — `LiteLLMSpendCollectorService`. Contains `collect_member_budget_reset_window()` (lines 86–169), which is the closest analogue to the fix: it calls `collect_member_budget_spend_for_refs()`, computes delta via `_compute_spend_snapshot()`, and writes rows via `insert_member_budget_entries()`. This is the pattern the fix should replicate.
- `/src/codemie/service/budget/provider.py` — `BudgetEnforcementProvider` protocol. Defines `collect_member_budget_spend_for_refs(provider_member_refs: set[str]) -> list[MemberBudgetSpendSnapshot]` (line 372). Also defines `delete_member_allocation(*, allocation)` (line 344). Both are already in the protocol — no protocol changes needed.
- `/src/codemie/enterprise/litellm/budget_provider_adapter.py` — `LiteLLMBudgetEnforcementProvider`. Implements `collect_member_budget_spend_for_refs()` (lines 1556–1566) by calling `_load_member_spend_from_litellm(provider_member_refs=...)`. Critically, `_load_synced_member_allocations()` (lines 173–198) queries `ProjectMemberBudgetAssignment` WHERE `deleted_at IS NULL` — the allocation must still be active at the time of the spend fetch. Implements `delete_member_allocation()` (lines 1262–1284) which calls `service.delete_project_member_budget_assignment()` via thread executor.
- `/src/codemie/repository/project_spend_tracking_repository.py` — `ProjectSpendTrackingRepository`. `insert_member_budget_entries()` (line 437) delegates to `_insert_budget_subject_entries()` (lines 445–494). Takes `AsyncSession` and manages its own `commit()`.
- `/src/codemie/service/spend_tracking/spend_models.py` — `ProjectSpendTracking` SQLModel. Fields: `id`, `project_name`, `spend_date`, `daily_spend`, `cumulative_spend`, `budget_period_spend`, `budget_id`, `budget_category`, `user_id`, `provider_subject_id`, `spend_subject_type`.
- `/src/codemie/service/budget/budget_models.py` — `ProjectMemberBudgetAssignment` model (line 273). Key fields for this fix: `provider_metadata` (JSONB, line 318) — contains `provider_member_ref` nested under `raw` key as populated by `_build_member_provider_metadata()`; `project_budget_id`, `budget_category`, `user_id`, `project_name`.

### Architecture and Layers Affected

| Layer | Component | Role in This Fix |
|---|---|---|
| Service / Business Logic | `ProjectAssignmentService._sync_project_budget_member_removed()` | Bug site — must be modified to add spend capture before deletion |
| Service / Business Logic | `LiteLLMSpendCollectorService` | Provides `_compute_spend_snapshot()` utility and the existing `collect_member_budget_reset_window()` pattern |
| Provider Protocol | `BudgetEnforcementProvider.collect_member_budget_spend_for_refs()` | Already defined — no changes needed |
| Enterprise Adapter | `LiteLLMBudgetEnforcementProvider.collect_member_budget_spend_for_refs()` | Already implemented — no changes needed |
| Repository / Persistence | `ProjectSpendTrackingRepository.insert_member_budget_entries()` | Already implemented — no changes needed |

### Integration Points

- **Async/sync boundary**: `_sync_project_budget_member_removed()` is synchronous (uses `Session`) but must call async provider and repository methods. The class already has `_run_budget_provider_coro()` (lines 62–76) for this purpose — it handles both "no running loop" (uses `asyncio.run()`) and "inside async context" (uses `run_coroutine_threadsafe` with 30s timeout) cases.
- **`provider_member_ref` access**: Located inside `allocation.provider_metadata` under the `raw` sub-dict. The adapter's `_metadata_value(metadata, "provider_member_ref")` helper (line 61) handles both top-level and `raw`-nested access. Core code should read `allocation.provider_metadata.get("raw", {}).get("provider_member_ref")` or replicate the `_metadata_value` pattern without importing enterprise code.
- **Separate DB transactions**: `insert_member_budget_entries()` opens its own `get_async_session()` and commits independently. This is separate from the sync `Session` managing the member removal — an intentional design.
- **`collect_member_budget_spend_for_refs()` filters active allocations only**: The provider implementation queries `deleted_at IS NULL`. The spend fetch must happen before `allocation.deleted_at = now` is set. In current code the order is `delete_member_allocation()` → `allocation.deleted_at = now` → `session.add()`. The fix must insert the spend fetch before `delete_member_allocation()`.
- **`budget_repository.get_all_keyed_by_id()`**: Used by `collect_member_budget_reset_window()` to look up `Budget` records for `_compute_spend_snapshot()`. The fix will need the same lookup (or a simplified approach for the terminal case).

### Patterns and Conventions

- Async provider calls from sync code use `_run_budget_provider_coro(coro)` — already present in `ProjectAssignmentService`. Follow this pattern exactly.
- Fail-open on provider errors: the current `delete_member_allocation()` call is wrapped in `try/except` that logs and continues (lines 193–200). The spend capture call should follow the same fail-open pattern.
- Structured logging: `budget_event=...` key-value format used throughout. New log lines should use `budget_event=project_member_spend_snapshot_captured` / `_failed` / `_skipped`.
- `uuid4()` for new `ProjectSpendTracking` row `id`.
- `spend_subject_type="member_budget"` for member allocation rows.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/architecture/layered-architecture.md` — confirms Service → Repository layering. Business logic in services; persistence in repositories.
- `.ai-run/guides/architecture/service-layer-patterns.md` — not read in detail; relevant for orchestration patterns.
- `.ai-run/guides/data/repository-patterns.md` — governs repository access.
- `.ai-run/guides/development/logging-patterns.md` — governs the `budget_event=` structured log format used in this domain.

### Architectural Decisions

- Provider protocol isolation: core services (`project_assignment_service.py`) must not import from `codemie.enterprise.litellm` directly — they go through `get_active_provider()` from `provider_registry`. This constraint is stated in the docstring of `budget_provider_adapter.py` and is a hard architectural rule.
- `provider_member_ref` is an opaque value stored in `allocation.provider_metadata["raw"]["provider_member_ref"]`. Core must not construct or decode this value — only pass it through. This is enforced by the comment in `budget_models.py`: "Core must not branch on its contents."
- Async session pattern: `ProjectSpendTrackingRepository` uses `AsyncSession` and manages its own commits. When called from sync code, the async operation must be bridged via `_run_budget_provider_coro()`.

### Derived Conventions

- `ProjectSpendTrackingRepository` is instantiated as a module-level singleton in the scheduler and used via dependency injection in services. The assignment service currently has no direct reference to it — a new instance may need to be instantiated or injected.
- The `LiteLLMSpendCollectorService` instance is not accessible from `ProjectAssignmentService`. If a new method is added to `LiteLLMSpendCollectorService` (e.g., `collect_terminal_member_spend()`), the assignment service cannot call it directly without coupling the two service layers. The cleanest approach is to inline the spend-capture logic in `project_assignment_service.py` as a new private async helper, following the pattern established by `collect_member_budget_reset_window()`.

---

## 4. Testing Landscape

### Existing Coverage

- `/tests/codemie/service/project/test_project_assignment_service.py` — comprehensive test file covering:
  - `_sync_project_budget_member_added()` (lines 705–851): tests allocation amounts, provider-not-called invariants
  - `remove_user_from_project()` (lines 527–611): tests success, creator rejection, not-assigned case
  - `bulk_remove_users_from_project()` (lines 433–524): tests success, creator rejection, not-assigned case
  - `_validate_user_id_format()`, `_reject_if_creator()`, assignment/update flows
  - **Gap**: `_sync_project_budget_member_removed()` has NO dedicated tests. The removal tests assert on member removal mechanics but do not mock or verify `get_active_provider().delete_member_allocation()` or any spend snapshot behaviour.
- `/tests/codemie/service/spend_tracking/test_spend_collector_service.py` — covers `LiteLLMSpendCollectorService` including `collect_member_budget_reset_window()` logic.
- `/tests/codemie/repository/test_project_spend_tracking_repository.py` — covers repository query methods.

### Testing Framework and Patterns

- **Framework**: pytest with `MagicMock` / `@patch` from `unittest.mock`.
- **Pattern**: Sync unit tests. Provider calls are patched at the module import path: `@patch("codemie.service.project.project_assignment_service.get_active_provider")` (see line 792 in test file).
- **Session mock**: `MagicMock()` session with `session.exec.side_effect` list pattern for chained query results (see lines 799–803).
- **No fixtures or factories** used in `test_project_assignment_service.py` — test data is constructed inline.

### Coverage Gaps

- `_sync_project_budget_member_removed()` has no test coverage at all. The fix will introduce new logic that is entirely untested.
- The new spend-capture path needs tests for:
  1. Happy path: `collect_member_budget_spend_for_refs()` returns a snapshot → `insert_member_budget_entries()` is called with the correct row.
  2. Skip when allocation has no `provider_member_ref` in `provider_metadata`.
  3. Fail-open: `collect_member_budget_spend_for_refs()` raises → spend capture is logged and skipped, deletion continues.
  4. Fail-open: `insert_member_budget_entries()` raises → spend capture is logged and skipped, deletion continues.
  5. Spend capture is called before `delete_member_allocation()` (ordering assertion).
  6. Bulk removal path: `_sync_project_budget_member_removed()` called in a loop, spend capture fires for each member with a valid ref.

---

## 5. Configuration and Environment

### Environment Variables

- `LITELLM_SPEND_COLLECTOR_ENABLED` — gates the scheduled collector. Not directly relevant to the fix (fix is event-driven, not scheduled).
- `LITELLM_SPEND_COLLECTOR_SCHEDULE` — cron expression for the scheduled collector. Not directly relevant.
- `LITELLM_BUDGET_RESET_TRACKER_ENABLED` / `LITELLM_BUDGET_RESET_TRACKER_SCHEDULE` — govern the reset-window tracker. Not relevant.

### Configuration Files

- `/src/codemie/configs/budget_config.py` — budget-related configuration. Should be checked for any `collect_member_budget_spend_for_refs`-specific timeouts or toggles.
- `DB_INSERT_BATCH_SIZE` and `DB_IN_CLAUSE_BATCH_SIZE` from config — used by `ProjectSpendTrackingRepository`. The terminal snapshot inserts at most one row per allocation, so batching is not a concern.

### Feature Flags and Deployment Concerns

- No feature flag currently gates the spend capture on removal. The fix itself could be gated by a new flag (e.g., `LITELLM_CAPTURE_SPEND_ON_MEMBER_REMOVAL`) for safe rollout, but this is a design decision rather than a requirement.
- The `_run_budget_provider_coro()` timeout is hard-coded at 30 seconds. Each allocation in a bulk removal will add up to 30s of potential wait. For a project with N members, bulk removal latency could become N × 30s in a worst case (all LiteLLM calls time out). This is a deployment concern for projects with many members.

---

## 6. Risk Indicators

- **No test coverage for `_sync_project_budget_member_removed()`**: The method being modified has zero dedicated tests. The fix adds non-trivial logic (async provider call + DB write) to a completely untested function. New tests are mandatory.
- **Async/sync boundary complexity**: `_run_budget_provider_coro()` re-uses the event loop in async contexts via `run_coroutine_threadsafe`. Wrapping an additional async operation (the DB write via `insert_member_budget_entries()`) inside the same coroutine is feasible but must be done carefully. Incorrect nesting can cause deadlocks if the coroutine tries to acquire the same event loop resources.
- **`_load_synced_member_allocations()` filters `deleted_at IS NULL`**: The spend fetch must happen before `allocation.deleted_at = now` is written. Current code does the soft-delete immediately after `delete_member_allocation()` in the loop. The fix must restructure the loop order: fetch spend first, then call `delete_member_allocation()`, then soft-delete.
- **Provider metadata access from core**: Reading `provider_member_ref` from `allocation.provider_metadata` must not import enterprise code. Use dict-navigation on `provider_metadata` — but the field naming (`raw.provider_member_ref` vs top-level `provider_member_ref`) is inconsistent. The `_build_member_provider_metadata()` helper in `project_assignment_service.py` itself puts `provider_member_ref` inside `raw` (line 82). The adapter's `_metadata_value()` handles both levels — this logic should be replicated in the new code or a core-side equivalent helper added.
- **Bulk removal performance regression**: `bulk_remove_users_from_project()` calls `_sync_project_budget_member_removed()` once per user in a loop (line 628). With the new spend fetch step, each iteration becomes a potentially blocking LiteLLM API call (up to 30s). For large teams this degrades responsiveness significantly.
- **`ProjectSpendTrackingRepository` not currently injected into `ProjectAssignmentService`**: The repository must be instantiated (or made accessible) in the assignment service. A new module-level singleton or constructor injection is needed — no existing pattern for this in the assignment service.
- **Terminal snapshot delta computation**: `_compute_spend_snapshot()` in `LiteLLMSpendCollectorService` requires a previous row for accurate delta. For the terminal case, if no previous row exists (bootstrap), the entire `budget_period_spend` is used as `daily_spend`. This is acceptable — but the `budget_repository` lookup (for reset detection) is also needed. This adds a DB query per allocation in the removal path.
- **Double-spend risk if removal rolls back after snapshot commit**: `insert_member_budget_entries()` commits its own transaction. If the outer sync session (member removal) subsequently fails and rolls back, the spend row is committed but the allocation is not deleted — the next scheduled collection will re-collect the same spend. The `_compute_spend_snapshot()` delta logic will deduplicate this correctly (delta from the terminal row will be zero), so this is low-risk but should be noted.

---

## 7. Summary for Complexity Assessment

The fix is scoped to a single method — `_sync_project_budget_member_removed()` in `project_assignment_service.py` — but it touches three distinct layers: the service layer (where the bug sits), the provider protocol (already complete, no changes needed), and the repository layer (already capable, but not yet wired into the assignment service). The primary file change is in `project_assignment_service.py`. If the terminal spend collection logic is factored into `LiteLLMSpendCollectorService` as a new method, that file also changes. Realistically this is a 2–3 file change (assignment service, optionally spend collector service, and a new test class for `_sync_project_budget_member_removed`).

The task follows a largely established pattern — `collect_member_budget_reset_window()` in `spend_collector_service.py` already does fetch-then-persist for member spend — but the execution environment is novel: it must run from a synchronous method that holds an open `Session`, bridged to async via the existing `_run_budget_provider_coro()` helper. The async helper must be structured to open its own `get_async_session()` for the DB write, as the outer sync session cannot be passed into an async context manager. The `_compute_spend_snapshot()` delta logic can be reused or replicated inline (though coupling to `LiteLLMSpendCollectorService` from `ProjectAssignmentService` would be a layering violation — the spend logic should either be extracted to a shared utility or duplicated in a minimal terminal-snapshot form).

Test coverage posture for the affected code is weak: `_sync_project_budget_member_removed()` has no existing tests. The existing removal tests mock away the budget sync entirely. New tests must cover the happy path, the no-ref skip path, the fail-open provider-error path, and the ordering constraint (spend before delete). The performance risk in bulk removal (serial LiteLLM calls per member) is a significant concern for large projects and should be flagged to the reviewer for possible mitigation (e.g., parallel fan-out using `asyncio.gather`).
