# Deferred from code review — 2026-10-13-epmcdme-15633-delete-global-budget (2026-10-13)

- **Concurrent PATCH after delete may 500** — `src/codemie/service/budget/budget_service.py` — update_budget reads the row without a lock, so a PATCH racing a committed delete can flush an UPDATE against a gone row (StaleDataError -> 500 instead of 404). Pre-existing: the same race already exists with the provider-sync orphan delete; update_budget is not part of this change. Reachability not verified.
