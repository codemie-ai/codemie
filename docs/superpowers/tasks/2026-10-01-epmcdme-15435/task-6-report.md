DONE

## Task 6: Membership, budget, and project creation reconciliation

### What was done

**TDD cycle completed cleanly:**

1. **RED** — Created `tests/codemie/enterprise/sync/test_coordinator_memberships.py` with 5 tests. Initial run: 4 failed, 1 passed trivially (personal-preservation test checked `== 0` which was already 0).
2. **GREEN** — Implemented `_reconcile_memberships` and `_sync_budget` in `coordinator.py`, wired into `run()`. All 13 tests (4 lifecycle + 4 user + 5 membership) pass.
3. **Quality gates** — `make ruff` clean (fixed one unused-variable warning in test); `make license-check` clean.
4. **Committed** — `ecd6e09b4` on `epmcdme-15435`.

### Key design decision

`_reconcile_user` was modified to return `(counts, db_user)` instead of just `counts`. This was necessary to pass the user object to `_reconcile_memberships` without a second `get_by_email` call, which would have broken `test_reconcile_isolates_per_user_failure` (its `side_effect` list would be exhausted). No existing test assertions were affected since all prior tests call `coordinator.run()` and check `SyncResult` fields, not `_reconcile_user`'s return value directly.

### Files changed

- `src/codemie/enterprise/sync/coordinator.py` — `_reconcile_user` return type changed to tuple; `_reconcile_memberships` and `_sync_budget` added; `run()` wired to call membership reconciliation per user.
- `tests/codemie/enterprise/sync/test_coordinator_memberships.py` — new test file (5 tests).

### Test results

```
13 passed in 4.64s
```
