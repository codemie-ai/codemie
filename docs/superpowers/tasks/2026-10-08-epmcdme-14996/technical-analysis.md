# Technical Research

**Task**: budget litellm project-member-sync
**Generated**: 2026-10-13
**Research path**: codegraph

---

## 1. Original Context

The task requirements are, verbatim, the full contents of /Users/bohdan_maliar/Projects/codemie-dev/codemie/docs/superpowers/tasks/2026-10-08-epmcdme-14996/core-sdlc-standard-prompt.md — read that file first and treat it as the authoritative task description (EPMCDME-14996: Batch project member budget sync to speed up budget save). Supplementary context in the same folder: technical-brief.md and ticket.xml (read if useful; the prompt file wins on any conflict).
Key research targets: src/codemie/service/budget/provider.py (protocol), provider_registry.py (noop provider), src/codemie/enterprise/litellm/budget_provider_adapter.py (sync_member_allocation, _effective_project_member_budget_id, result-to-state conversion), src/codemie/service/budget/project_budget_service.py (_sync_created_member_allocations, _resync_member_allocations, _resync_member_allocation, _sync_fixed_allocation, shared_sample), src/codemie/configs/config.py LITELLM_* settings, src/codemie/core/otel_tracing.py get_otel_context_for_thread, src/codemie/rest_api/main.py shutdown around get_active_llm_proxy_provider().close(), the installed editable codemie-enterprise LiteLLMService primitives (get_budget_info_map_strict, ensure_project_member_budget, assign_project_member_budget, sync_project_member_budget_assignment, result types), existing tests for these modules, and pyproject.toml enterprise pin.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/service/budget/provider.py` — `BudgetProviderMemberState` (:60; fields provider, provider_member_ref, provider_budget_id, budget_reset_at, sync_status=OK, metadata). `BudgetEnforcementProvider` Protocol (:192); `sync_member_allocation(*, allocation, budget, effective_max_budget=None)` (:340). There is no batch method today.
- `src/codemie/service/budget/provider_registry.py` — `_NoopBudgetEnforcementProvider` (alias `NoopBudgetProvider` :306); `sync_member_allocation` (:222) returns `BudgetProviderMemberState(provider="noop", sync_status=NOOP)`.
- `src/codemie/enterprise/litellm/budget_provider_adapter.py`
  - `_effective_project_member_budget_id` (:116): effective → override → shared → `fixed` mode gives `build_override_project_budget_id`, otherwise `build_shared_project_budget_id`.
  - `LiteLLMBudgetEnforcementProvider._get_service` (:163): an injected service, or `get_litellm_service_or_none()`.
  - `sync_member_allocation` (:1153-1235): resolved max = `effective_max_budget` if it is not None, otherwise `allocation.allocated_max_budget`. An unavailable service gives FAILED (:1174-1182). It then calls `asyncio.to_thread(service.sync_project_member_budget_assignment, project_budget_id, project_name, budget_category, user_id, allocated_max_budget, allocated_soft_budget, budget_duration=budget.budget_duration, budget_reset_at=budget.budget_reset_at, effective_budget_id)` (:1184). Results are converted at :1196-1235: a `None` result gives FAILED (`empty_provider_result`); a missing `budget_id` gives FAILED; otherwise metadata = `dict(result.metadata)` plus `internal_budget=True`, `budget_scope="project_member"` and `provider_budget_id`, with sync_status OK.
- `src/codemie/service/budget/project_budget_service.py`
  - `_effective_member_budget_id` (:337): the same routing as the adapter, used for persistence.
  - `_persist_child_budget_provider_state` (:475) writes the child `budget_reset_at` and `provider_metadata`.
  - `_sync_created_member_allocations` (:501-574) runs one try per member. That try covers the provider call **and** both persistence writes. An exception writes the FAILED marker only (`provider_metadata={"sync_status": FAILED}`).
  - `_sync_created_project_budget` (:576) calls it; there are 3 callers in total.
  - `_sync_fixed_allocation` (:1064): `_ensure_override_child_budget` → `sync_member_allocation(allocation, budget)` with no `effective_max_budget` → persist the child and member metadata.
  - `_resync_member_allocation` (:1104): `update_allocation` → returns None if the row is missing → the FIXED branch calls `_sync_fixed_allocation` and returns None → EQUAL calls `sync_member_allocation` (state discarded) and returns the amounts tuple. One try covers all of it; an exception is logged and returns None.
  - `_resync_member_allocations` (:1168) loads the unordered list, then `_equal_amounts_with_fixed_overrides`, `enforce_limit` from `SettingsService.get_enforce_member_spend_limits`, `shared_sample = shared_sample or sample`, and finally `_ensure_shared_child_budget_after_resync` (:1146).
  - `update_project_budget` (:1207) runs `budget_repository.update(changed_fields)` before `_sync_updated_project_budget`, then resyncs.
  - `rebalance_project_budget` (:1275) is called by the router, `override_member_allocation`, `clear_member_override` and `rebalance_project_budget_group`.
- `src/codemie/repository/project_budget_repository.py` — member `get_active_by_budget_id` (:532) has no ORDER BY. `update_provider_metadata` (:635) skips the reset when it is None. `update_allocation` (:675) does flush + refresh.

### Architecture and Layers Affected
- **Service layer**: `ProjectBudgetService` create and resync flows.
- **Provider abstraction (core)**: the protocol plus the noop registry.
- **Enterprise adapter (core-side)**: `enterprise/litellm/budget_provider_adapter.py`.
- **Config**: `configs/config.py`.
- **App lifecycle**: `rest_api/main.py` `_shutdown_services`.
- **External package**: `codemie-enterprise` `LiteLLMService`.

### Integration Points
- `codemie-enterprise` checkout at `/Users/bohdan_maliar/Projects/codemie-dev/codemie-enterprise`; its `pyproject.toml` version is already `2.3.45`. See §8.
- `src/codemie/rest_api/main.py`: `_shutdown_services` (:765) calls the synchronous `get_active_llm_proxy_provider().close()` at :778. That is the LLM proxy provider (`service/llm_proxy/provider_registry.py:38`), not the budget provider. `close_llm_proxy_client()` follows at :801. `lifespan` is at :828.
- `src/codemie/core/otel_tracing.py`: `get_otel_context_for_thread` (:180), `attach_otel_context` (:201), `detach_otel_context` (:206). The docstring notes that `asyncio.to_thread` copies contextvars but a direct executor submit does not. Existing users: `hedged_handler.py`, `thought_consumer.py`, `base_datasource_processor.py`, `langgraph_agent.py`.
- Single-member callers that stay unchanged: reset (:1324 region), `clear_member_override` (:1504), `budget_service._sync_backfilled_member_allocations`, and `enterprise/litellm/project_member_runtime_sync.py` (`ensure_project_member_runtime_ready`, `resync_project_member_allocations`).

### Patterns and Conventions
- Logging uses structured f-strings: `budget_event=<x> component=<y> key=value!r`.
- The adapter imports enterprise lazily, under TYPE_CHECKING or inside functions. Core never imports `codemie_enterprise` at module top.
- Service code reads fields with `getattr(..., None)`, so tests can pass `SimpleNamespace` doubles.
- No dedicated core executor exists for budget work. The closest bounded-pool pattern is `_WorkerPool` in `src/codemie_tools/data_management/code_executor/tool_call_channel.py:120-150`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/development/performance-patterns.md`: generic advice to isolate blocking work and batch per-item remote calls. It has no executor guidance.
- `.ai-run/guides/development/configuration-patterns.md`: keep settings in central `configs/`. It shows no validator pattern.
- Relevant but not read: `testing/testing-service-patterns.md`, `standards/git-workflow.md`, `quality-gates.md`, `security/README.md`.

### Architectural Decisions
- `technical-brief.md` (same folder) splits ownership: enterprise owns LiteLLM requests, IDs and metadata; core owns batching, executor, cap, shutdown and persistence.
- The `Budget` model docstring says `provider_metadata` is opaque and core must not branch on it.

### Derived Conventions
- Results are exposed through public enterprise methods only; core must not call `_project_customer_id` or other private helpers.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/enterprise/litellm/test_budget_provider_adapter.py`: covers `sync_member_allocation` (:37, :223). These tests patch `budget_provider_adapter.asyncio.to_thread` and use a `SimpleNamespace` service.
- `tests/codemie/service/budget/test_project_budget_service.py`: the test at :30 asserts that EQUAL resync passes `effective_max_budget is None` and calls `update_provider_metadata.assert_not_awaited()`. Also includes `test_sync_created_member_allocations_marks_failures` and `..._uses_full_budget_when_enforce_limit_false`.
- `tests/codemie/service/budget/test_project_budget_service_lifecycle.py`: FIXED and resync paths.
- `tests/codemie/service/budget/test_project_budget_service_group_creation.py`: group create.
- `tests/codemie/service/budget/test_provider_registry.py`: `test_noop_budget_provider_accepts_all_protocol_arguments` (:27).
- `tests/enterprise/litellm/test_project_member_runtime_sync.py`: an unchanged caller.
- `tests/codemie/repository/test_project_budget_repository.py`: repository tests.
- `tests/codemie/rest_api/routers/test_project_budget_router*.py`: router tests.

### Testing Framework and Patterns
pytest with `@pytest.mark.asyncio`, `AsyncMock`/`MagicMock`/`patch`, and `SimpleNamespace` doubles for ORM rows, budgets and providers. Repository functions are patched by module path (`codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.*`).

### Coverage Gaps
- No tests for any batch, executor, concurrency, ordering or shutdown behaviour.
- No test asserts the ordering of `_shutdown_services`.
- No config tests for `LITELLM_*` integer bounds were found.

---

## 5. Configuration and Environment

### Environment Variables
`config.py:767-773` holds the LiteLLM cache and optimization settings:
- `LITELLM_CUSTOMER_CACHE_TTL=300`
- `LITELLM_USER_CREDENTIALS_CACHE_TTL=600`
- `LITELLM_MODELS_CACHE_TTL=1800`
- `LITELLM_REQUEST_TIMEOUT=5.0`
- `LITELLM_LIST_REQUEST_TIMEOUT=30.0`
- `LITELLM_FAIL_OPEN_ON_503=True`

`LITELLM_PREMIUM_MODELS_ALIASES` is at :763. `LLM_PROXY_BUDGET_CHECK_ENABLED` gates `budget_router` (`main.py:1079`).

### Configuration Files
- `pyproject.toml:58`: `codemie-enterprise = {version = "2.3.44", source = "codemie-artifact-registry-gcp", optional = true}`.
- `poetry.lock` is tied to that pin.
- `config/budgets` YAML is unrelated.

### Feature Flags and Deployment Concerns
- The task says `compose.enterprise-local.yaml` at the workspace root plus an editable install are used locally and must not be committed.
- Repo rule: an MR that touches dependencies must ask a reviewer to post `/sanity`.

---

## 6. Risk Indicators

- **Test doubles break when the flows switch.** Service tests build the provider as `SimpleNamespace(sync_member_allocation=AsyncMock(...))` (test_project_budget_service.py:46). Once the create and resync flows call the batch method, these doubles fail unless they are updated.
- **Adapter tests patch the wrong path for batch work.** They patch `asyncio.to_thread`, which the batch path deliberately bypasses (dedicated executor), so batch tests need their own harness.
- **Exception scope differs today.** In `_sync_created_member_allocations` (:518-574) a persistence failure is also caught and replaced with the FAILED marker. In `_resync_member_allocation` (:1118-1144) the try covers `update_allocation`, `_ensure_override_child_budget`, the provider call and FIXED persistence. Splitting into prepare / batch / persist must keep each exception policy.
- **FIXED preparation moves.** FIXED inside resync currently does DB prep (`_ensure_override_child_budget`, :1073) between provider calls; the prompt moves it into preparation.
- **`shared_sample` must use prepared amounts.** It uses `or` semantics on the first non-None tuple; that tuple must now come from prepared amounts, excluding FIXED items and items whose provider call raised.
- **Routing logic exists twice.** The adapter uses `_effective_project_member_budget_id` (:116) and the service uses `_effective_member_budget_id` (:337) for persistence; they must stay aligned.
- **Single-member lookup is soft.** `sync_project_member_budget_assignment` uses `get_budget_info`, which returns `[]` on error, so it treats an error as "absent" and creates the budget. The batch path's strict lookup differs; the fallback reproduces the soft behaviour per member.
- **Brief and prompt conflict.** `technical-brief.md:287` says a strict-lookup failure should produce item failures; the prompt (authoritative) requires the ordered single-member fallback. The brief also suggests admission before DB prep and fixed-sum validation, which the prompt excludes.
- **Shutdown ordering is a new, untested path.** `_shutdown_services` is async but calls a synchronous `close()` (:778); executor drain and shutdown must happen off-loop before it.
- **Enterprise pin must be bumped.** It must go from 2.3.44 to 2.3.45 with `poetry.lock` once 2.3.45 is published. Until then, verification relies on the editable install.
- **Duplicate-customer detection uses a tuple, not a customer ID.** Detection must use the `(project_name, budget_category, user_id)` tuple because the enterprise customer-ID builder is private.
- Speculative: a module-level `asyncio.Semaphore` shared "across requests" may be bound to one event loop. pytest-asyncio's per-test loops, or several app loops, could surface this.
- Speculative: contextvars beyond OTel (e.g. logging context set by `set_logging_info`) are also lost in `run_in_executor` unless `copy_context().run` is used. The prompt already requires that.

---

## 7. Summary for Complexity Assessment

The change touches the core provider protocol and noop registry (`service/budget/provider.py`, `provider_registry.py`), the LiteLLM adapter (`enterprise/litellm/budget_provider_adapter.py`), two flows in `project_budget_service.py` (create at :501 and EQUAL/FIXED resync at :1064-1205), one new `LITELLM_*` setting in `config.py:767-773`, application shutdown in `rest_api/main.py:765-778`, and the enterprise pin in `pyproject.toml:58` with `poetry.lock`. All enterprise primitives the prompt names already exist in the local enterprise checkout, which is version 2.3.45 (§8). No enterprise code is changed, and no DB schema change or migration was found in the requirements.

The new parts are an async coordinator over a dedicated, lazily created and process-shared `ThreadPoolExecutor`; a semaphore cap that releases only when the blocking call finishes; contextvars/OTel propagation; and a shutdown drain. Nothing like this exists in the budget domain; the closest pattern is `_WorkerPool` in `tool_call_channel.py`. The adapter algorithm is new logic with several ordered-fallback branches:
- duplicate-customer preflight;
- strict-lookup failure;
- reset=None;
- failed create or update;
- ensure raising an exception.

It also has run-grouping and input-order outcome mapping.

Tests exist for each touched module (adapter, service create/resync, noop registry, lifecycle, group creation), but they are single-member. Several mock the provider with only `sync_member_allocation`, or patch `asyncio.to_thread`, so they need updates. No coverage exists for concurrency, ordering, fallbacks or shutdown. The main risks are keeping each flow's exception and persistence contract exactly as in main while splitting each flow into prepare, provider batch and ordered persist, and adding shared executor state to the process lifecycle.

---

## 8. External References

- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/docs/superpowers/tasks/2026-10-08-epmcdme-14996/core-sdlc-standard-prompt.md` — **resolved**, read in full. It is the authoritative spec: provider batch method contract, adapter batching algorithm, bounded execution, the flow table and behaviour that must not change.
- `.../technical-brief.md` — **resolved**. It is supplementary; it conflicts with the prompt on strict-lookup failure (item failures vs ordered fallback) and on admission and fixed-sum validation. The prompt wins.
- `ticket.xml` — not read; the prompt supersedes it.
- `/Users/bohdan_maliar/Projects/codemie-dev/codemie-enterprise/src/codemie_enterprise/litellm/service.py` — **resolved**. The checkout's `pyproject.toml` version is `2.3.45`.
  - `get_budget_info(budgets)` (:131): soft; returns `[]` on any error.
  - `get_budget_info_map_strict(budget_ids)` (:172): `_normalize_budget_ids` dedupes and drops empty IDs (:195); empty input returns `{}` with no HTTP call. It POSTs `/budget/info` once. httpx errors propagate; a non-list body, an invalid `BudgetTable` or an empty `budget_id` raises `ValueError`. Missing IDs are omitted.
  - `sync_project_member_budget_assignment(*, project_budget_id, project_name, budget_category, user_id, allocated_max_budget, allocated_soft_budget, budget_duration, budget_reset_at=None, effective_budget_id=None)` (:624). The effective branch (:647-670) does a soft `get_budget_info([id])` → `ensure_project_member_budget(existing_budget=...)` → **`if ensured.budget is None: return None`** → `assign_project_member_budget(..., budget=ensured.budget, budget_reset_at=budget_reset_at)`. The legacy branch without `effective_budget_id` is at :672-709.
  - `ensure_project_member_budget(*, budget_id, max_budget, soft_budget, budget_duration, budget_reset_at, existing_budget)` (:711). `existing_budget=None` gives `create_managed_budget` → `ProjectMemberBudgetEnsureResult(budget=created, write_succeeded=created is not None)`. Otherwise `update_managed_budget` → `budget=updated or existing_budget, write_succeeded=updated is not None`.
  - `assign_project_member_budget(*, project_name, budget_category, user_id, budget_id, budget, budget_reset_at)` (:752) runs `_ensure_project_customer_budget_assignment` (:796): `get_customer_info`; when the customer is missing, `create_customer` (exceptions propagate); when it points at another budget, `set_customer_budget_assignment` (False → returns None), then refresh. It returns `ProjectMemberBudgetProvisioningResult(provider_member_ref, budget_id=<arg>, budget_reset_at=budget.budget_reset_at or budget_reset_at, metadata={"internal_budget": True, "budget_scope": "project_member", "effective_budget_id": budget_id})`, or None.
- `.../codemie_enterprise/litellm/__init__.py` — **resolved**. It exports `BudgetTable`, `ProjectMemberBudgetEnsureResult`, `ProjectMemberBudgetProvisioningResult` and `LiteLLMService`.
