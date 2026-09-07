# Zero Member `budget_period_spend` on Project Budget Reset (EPMCDME-13567) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zero each project member's `budget_period_spend` in LiteLLM when a project budget is reset, fixing stale spend counters that persist until the next natural period expiry.

**Architecture:** Add `reset_project_member_spending` to the three-layer provider stack (protocol → noop → LiteLLM adapter), then call it in the per-member loop of `reset_project_budget()` after `sync_member_allocation()`. The LiteLLM implementation reuses `reset_customer_spending_in_litellm()` from `budget_helpers.py`. The group reset endpoint is fixed automatically because `reset_project_budget_group()` delegates each category budget entirely to `reset_project_budget()`.

**Tech Stack:** Python, FastAPI, SQLModel, LiteLLM, pytest/AsyncMock

**Spec:** `docs/superpowers/tasks/2026-09-03-epmcdme-13567-budget-reset-member-spend/spec.md`

## Global Constraints

- No changes to `project_budget_router.py`, `reset_project_budget_group()`, `sync_member_allocation()`, or `reset_customer_spending_in_litellm()`.
- No DB schema migration or new DB columns.
- Spend reset runs unconditionally — never gated on `ENFORCE_MEMBER_SPEND_LIMITS`.
- No retry logic for failed member resets; fail-open with `sync_status=FAILED` per member.
- Commit per task using the repository's existing convention.

---

### Task 1: Extend `BudgetEnforcementProvider` protocol and noop provider

**Files:**
- Modify: `src/codemie/service/budget/provider.py`
- Modify: `src/codemie/service/budget/provider_registry.py`

**Interfaces:**
- Produces: `async def reset_project_member_spending(self, user_id: str, budget_id: str) -> None` — exact signature used by all downstream tasks.

**Test-first: no — protocol and noop correctness are exercised transitively by the service tests in Task 3.**

- [ ] **Step 1: Add abstract method to `BudgetEnforcementProvider` protocol**

In `provider.py`, add after the `reset_project_budget_spend` method:

```python
async def reset_project_member_spending(self, user_id: str, budget_id: str) -> None:
    """Zero `budget_period_spend` for one project member in the enforcement provider."""
    ...
```

- [ ] **Step 2: Add noop to `_NoopBudgetEnforcementProvider`**

In `provider_registry.py`, add matching the style of existing noop methods (all kwargs forwarded to `self._noop_result`):

```python
async def reset_project_member_spending(self, user_id: str, budget_id: str) -> None:
    return self._noop_result(user_id=user_id, budget_id=budget_id)
```

---

### Task 2: LiteLLM adapter implementation

**Files:**
- Modify: `src/codemie/enterprise/litellm/budget_provider_adapter.py`

**Interfaces:**
- Consumes: `reset_customer_spending_in_litellm(user_id, budget_id)` from `budget_helpers.py:160` — returns `True` on success, `False` on failure (LiteLLM unavailable or API error).
- Produces: `LiteLLMBudgetProviderAdapter.reset_project_member_spending` — raises `RuntimeError` when the helper returns `False`, following the pattern of `reset_user_budget_spending` at line 637.

**Test-first: no — the adapter is exercised through mocked provider calls in the service tests (Task 3). Existing tests in `tests/enterprise/litellm/test_budget_helpers.py` cover `reset_customer_spending_in_litellm` adequately.**

- [ ] **Step 1: Add method to `LiteLLMBudgetProviderAdapter`**

In `budget_provider_adapter.py`, add alongside `reset_user_budget_spending` (line 637):

```python
async def reset_project_member_spending(self, user_id: str, budget_id: str) -> None:
    ok = await reset_customer_spending_in_litellm(user_id=user_id, budget_id=budget_id)
    if not ok:
        raise RuntimeError(
            f"reset_customer_spending_in_litellm returned False "
            f"for user={user_id!r} budget={budget_id!r}"
        )
```

---

### Task 3: Service wiring and tests

**Files:**
- Modify: `src/codemie/service/budget/project_budget_service.py:1292-1373` (`reset_project_budget()`)
- Modify: `tests/codemie/service/budget/test_project_budget_service.py` (line 331 area)
- Modify: `tests/codemie/service/budget/test_project_budget_service_lifecycle.py` (line 30 area)

**Interfaces:**
- Consumes: `reset_project_member_spending(user_id, budget_id)` from Tasks 1–2.
- Consumes: `member_state.provider_member_ref` (str | None) returned by `sync_member_allocation()`.
- Consumes: `self._effective_member_budget_id(budget.budget_id, allocation)` — returns the effective child `budget_id`.

**Test-first: yes — write all three tests first, verify FAIL, then wire the service.**

- [ ] **Step 1: Write the failing happy-path test**

In `test_project_budget_service.py`, add beside `test_reset_project_budget_persists_provider_budget_id_for_each_member` (line 331). Use the same `SimpleNamespace`/`patch.object` arrangement as that test. Add `reset_project_member_spending = AsyncMock()` to the provider stub and assert it is awaited once per member with the correct `user_id` (from `provider_member_ref`) and `budget_id`:

```python
@pytest.mark.asyncio
async def test_reset_project_budget_resets_member_spend_for_each_member(
    project_budget_service, mock_provider
):
    mock_provider.reset_project_member_spending = AsyncMock()
    # ... same member/budget arrangement as the existing test ...
    await project_budget_service.reset_project_budget(budget_id=..., ...)
    assert mock_provider.reset_project_member_spending.await_count == <member_count>
    mock_provider.reset_project_member_spending.assert_any_await(
        user_id=<expected_ref>, budget_id=<expected_budget_id>
    )
```

- [ ] **Step 2: Write the failing failure-case and None-ref tests**

In `test_project_budget_service_lifecycle.py`, add beside `test_reset_project_budget_uses_provider_reset_when_provider_budget_ref_missing` (line 30). Use the same fixture and stub patterns as that test:

```python
@pytest.mark.asyncio
async def test_reset_project_budget_member_spend_failure_marks_sync_status_failed(
    project_budget_service, mock_provider
):
    mock_provider.reset_project_member_spending = AsyncMock(
        side_effect=RuntimeError("provider failure")
    )
    result = await project_budget_service.reset_project_budget(budget_id=..., ...)
    failed_member = next(m for m in result.members if m.user_id == <target_user_id>)
    assert failed_member.sync_status == SyncStatus.FAILED


@pytest.mark.asyncio
async def test_reset_project_budget_skips_spend_reset_when_no_provider_ref(
    project_budget_service, mock_provider
):
    mock_provider.reset_project_member_spending = AsyncMock()
    # arrange sync_member_allocation to return provider_member_ref=None for the member
    await project_budget_service.reset_project_budget(budget_id=..., ...)
    mock_provider.reset_project_member_spending.assert_not_awaited()
```

- [ ] **Step 3: Run tests — verify all three fail**

```
pytest tests/codemie/service/budget/test_project_budget_service.py::test_reset_project_budget_resets_member_spend_for_each_member tests/codemie/service/budget/test_project_budget_service_lifecycle.py::test_reset_project_budget_member_spend_failure_marks_sync_status_failed tests/codemie/service/budget/test_project_budget_service_lifecycle.py::test_reset_project_budget_skips_spend_reset_when_no_provider_ref -v
```

Expected: all three FAIL (method not yet called in service).

- [ ] **Step 4: Wire `reset_project_budget()` in the service**

In `project_budget_service.py:1292-1373`, inside the per-member loop, immediately after the block that calls `sync_member_allocation()` and captures `member_state`:

1. Guard — if `provider_member_ref` is absent, log a structured warning (`budget_event=project_member_spend_reset_skipped_no_ref`) and `continue`.
2. `try:` — `await provider.reset_project_member_spending(user_id=member_state.provider_member_ref, budget_id=self._effective_member_budget_id(budget.budget_id, allocation))`
3. `except Exception as exc:` — log warning with `budget_event=project_member_spend_reset_failed`, set `member_state.sync_status = SyncStatus.FAILED`, continue loop.

Follow the existing per-member exception handling pattern already present in the same loop.

- [ ] **Step 5: Run tests — verify all three pass**

```
pytest tests/codemie/service/budget/test_project_budget_service.py::test_reset_project_budget_resets_member_spend_for_each_member tests/codemie/service/budget/test_project_budget_service_lifecycle.py::test_reset_project_budget_member_spend_failure_marks_sync_status_failed tests/codemie/service/budget/test_project_budget_service_lifecycle.py::test_reset_project_budget_skips_spend_reset_when_no_provider_ref -v
```

Expected: all three PASS.

---

<!-- negative-constraints:
  Non-Goal 1 (no router changes): no task touches project_budget_router.py. ✓
  Non-Goal 2 (no change to reset_project_budget_group()): fix propagates via existing delegation. ✓
  Non-Goal 3 (no change to sync_member_allocation()): service calls it as before; signature untouched. ✓
  Non-Goal 4 (no change to reset_customer_spending_in_litellm()): Task 2 calls it, does not modify it. ✓
  Non-Goal 5 (no DB schema migration): confirmed in global constraints. ✓
  Non-Goal 6 (no ENFORCE_MEMBER_SPEND_LIMITS gate): Task 3 Step 4 has no flag conditional. ✓
  Non-Goal 7 (no retry logic): catch block marks FAILED and continues; no retry. ✓
  Non-Goal 8 (no change to personal budget reset path): reset_user_budget_spending untouched. ✓
  Non-Goal 9 (no new API endpoints): confirmed. ✓
-->
