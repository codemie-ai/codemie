# Budget Assignment Carry-Over Spend Recording

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record a `ProjectSpendTracking` carry-over marker at the moment a budget is assigned so the admin users list reflects current spending without requiring a manual widget open.

**Architecture:** Mirror the `reset_user_budget_spending` carry-over pattern (lines 1323–1355) into both assignment paths. The single-user path (`assign_budget_to_user`) inserts the try block inline after the provider propagation call. The bulk path adds a new private method `_record_bulk_assignment_carry_over` called from `bulk_set_user_budgets` after `_persist_bulk_budget_assignments` returns — `_persist_bulk_budget_assignments` signature is unchanged.

**Tech Stack:** Python/asyncio, SQLModel, pytest-asyncio, `AsyncMock`/`MagicMock`/`SimpleNamespace`.

**Spec:** Requirements provided inline.

## Global Constraints

- All implementation changes are confined to `src/codemie/service/budget/budget_service.py`.
- All test changes are confined to `tests/codemie/service/budget/test_budget_service.py`.
- `_persist_bulk_budget_assignments` signature must not change.
- `LiteLLMSpendCollectorService` must be imported lazily inside the try block.
- Carry-over failure must never propagate: always `await session.rollback()` + `logger.warning` + continue.
- Commit per task using the repository's existing convention.

---

## Acceptance criteria

- After `assign_budget_to_user` completes for a non-null `budget_id`, a `ProjectSpendTracking` row with the user's prior `cumulative_spend` and `budget_period_spend=0` is written when prior spend history with non-zero spend exists.
- No marker row is written when no prior spend history exists for that `budget_id`.
- A carry-over write failure never fails the assignment; `session.rollback()` is awaited and a warning is logged.
- All three behaviours hold identically for `bulk_set_user_budgets`.

---

### Task 1: Carry-over marker in `assign_budget_to_user`

**Files:**
- Modify: `src/codemie/service/budget/budget_service.py:1126–1133` — inside `if budget_id is not None:`, after the existing provider-propagation try/except block
- Test: `tests/codemie/service/budget/test_budget_service.py`

**Test-first: yes — three failing tests: `test_assign_budget_writes_carry_over_marker`, `test_assign_budget_no_marker_when_no_prior_spend`, `test_assign_budget_rolls_back_on_carry_over_failure`**

- [ ] **Step 1: Write three failing tests**

Add to `test_budget_service.py`, modelled exactly on the three reset tests (`test_reset_user_budget_spending_writes_zero_marker_row`, `…_no_marker_when_no_prior_row`, `…_rolls_back_session_on_marker_failure`). Use the existing `_make_budget_tracking_row` helper (line 793).

  - `test_assign_budget_writes_carry_over_marker`: mock `get_latest_by_budget_ids` returning a row with `budget_period_spend=Decimal("5.0")` and `cumulative_spend=Decimal("20.0")`; assert `insert_budget_entries` called once with a one-element list whose entry has `cumulative_spend == Decimal("20.0")` and `budget_period_spend == Decimal("0")`.
  - `test_assign_budget_no_marker_when_no_prior_spend`: mock `get_latest_by_budget_ids` returning `{}`; assert `insert_budget_entries` called with `[]`.
  - `test_assign_budget_rolls_back_on_carry_over_failure`: mock `insert_budget_entries` to raise; assert `session.rollback()` was awaited and no exception escapes `assign_budget_to_user`.

- [ ] **Step 2: Run tests — confirm they fail**

```
pytest tests/codemie/service/budget/test_budget_service.py -k "assign_budget" -v
```

- [ ] **Step 3: Add carry-over try block to `assign_budget_to_user`**

`src/codemie/service/budget/budget_service.py:1133` — immediately after the provider-propagation except clause closes, still inside the `if budget_id is not None:` branch, insert a new try block that follows lines 1323–1355 verbatim with these substitutions: `username` → `db_user.username`; log event key `assignment_carry_over_write_failed`. No other changes to the method.

- [ ] **Step 4: Run tests — confirm they pass**

```
pytest tests/codemie/service/budget/test_budget_service.py -k "assign_budget" -v
```

- [ ] **Step 5: Commit**

---

### Task 2: Carry-over marker in the bulk assignment path

**Files:**
- Modify: `src/codemie/service/budget/budget_service.py:1190` — insert one `await` call in `bulk_set_user_budgets`
- Create (new method, same file): `_record_bulk_assignment_carry_over` added after `_propagate_bulk_budget_assignments` (line 1270)
- Test: `tests/codemie/service/budget/test_budget_service.py`

**Interfaces:**
- Produces: `async def _record_bulk_assignment_carry_over(self, session: AsyncSession, db_users: dict, assignments: dict[BudgetCategory, str | None]) -> None`

**Test-first: yes — three failing tests: `test_bulk_set_budgets_writes_carry_over_markers`, `test_bulk_set_budgets_no_marker_when_no_prior_spend`, `test_bulk_set_budgets_rolls_back_on_carry_over_failure`**

- [ ] **Step 1: Write three failing tests**

Mirror the Task 1 tests but drive `bulk_set_user_budgets` with two users. Assert `insert_budget_entries` is called once per user with prior non-zero spend, called with `[]` per user with no history, and `session.rollback()` is awaited per failure with no exception propagating.

- [ ] **Step 2: Run tests — confirm they fail**

```
pytest tests/codemie/service/budget/test_budget_service.py -k "bulk_set_budgets" -v
```

- [ ] **Step 3: Add `_record_bulk_assignment_carry_over`**

New method after line 1270, following the Task 1 carry-over block pattern looped over all users and categories:

```python
async def _record_bulk_assignment_carry_over(
    self,
    session: AsyncSession,
    db_users: dict,
    assignments: dict[BudgetCategory, str | None],
) -> None:
    for user_id, db_user in db_users.items():
        for category, budget_id in assignments.items():
            if budget_id is None:
                continue
            try:
                from codemie.service.spend_tracking.spend_collector_service import (
                    LiteLLMSpendCollectorService,
                )

                now = datetime.now(timezone.utc)
                prev_rows = await project_spend_tracking_repository.get_latest_by_budget_ids(
                    session, [budget_id], db_user.username
                )
                prev_row = prev_rows.get(budget_id)
                marker: list[ProjectSpendTracking] = []
                if prev_row is not None and LiteLLMSpendCollectorService._quantize_spend(
                    prev_row.budget_period_spend
                ) > Decimal("0"):
                    marker.append(
                        ProjectSpendTracking(
                            id=uuid4(),
                            project_name=db_user.username,
                            user_id=user_id,
                            budget_id=budget_id,
                            budget_category=category.value,
                            spend_subject_type="budget",
                            spend_date=now,
                            budget_period_spend=Decimal("0"),
                            daily_spend=Decimal("0"),
                            cumulative_spend=prev_row.cumulative_spend,
                        )
                    )
                await project_spend_tracking_repository.insert_budget_entries(session, marker)
            except Exception as exc:
                await session.rollback()
                logger.warning(
                    f"budget_event=assignment_carry_over_write_failed component=budget_service "
                    f"user_id={user_id!r} category={category.value!r}: {exc}"
                )
```

- [ ] **Step 4: Wire into `bulk_set_user_budgets`**

`src/codemie/service/budget/budget_service.py:1190–1191` — add `await self._record_bulk_assignment_carry_over(session, db_users, assignments)` on the line between `_persist_bulk_budget_assignments` and `_propagate_bulk_budget_assignments`.

- [ ] **Step 5: Run tests — confirm they pass**

```
pytest tests/codemie/service/budget/test_budget_service.py -k "bulk_set_budgets" -v
```

- [ ] **Step 6: Run full test file**

```
pytest tests/codemie/service/budget/test_budget_service.py -v
```

- [ ] **Step 7: Commit**

---

## Negative-constraint audit

| Constraint | Honoring task |
|---|---|
| "never let this failure raise or propagate to the assignment itself" | Tasks 1 & 2: bare `except Exception` with no re-raise; assignment DB write precedes the carry-over block |
| "`_persist_bulk_budget_assignments` signature must not change" | Task 2: carry-over called from `bulk_set_user_budgets`; `_persist_bulk_budget_assignments` untouched |
| "A failure … must NEVER cause the budget assignment to fail" | Tasks 1 & 2: carry-over block is placed after upsert and activity-event writes; `session.rollback()` clears only the failed marker from session state |

negative-constraints: all three stated constraints verified above; no task satisfies a requirement by a forbidden mechanism.
