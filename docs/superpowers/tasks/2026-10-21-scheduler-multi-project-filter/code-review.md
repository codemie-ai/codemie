---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 4b111ef1edc9873d6b0180264d1eff57cfdd9016
---

# Code review — 2026-10-21-scheduler-multi-project-filter

**Decision:** approve (final round, confidence high)  
**Reviewed head:** `4b111ef` · **Rounds:** final

All four lenses and standards ran; every acceptance criterion passes and the only callers of the renamed service kwargs (the router) were verified. 2 pre-existing items deferred (list_schedulers/resource_ids SQL never executed by tests; independent resource_type/resource_ids EXISTS clauses) in code-review-deferred.local.md; 8 candidates dismissed as noise or below the emission floor (unbounded list length, whitespace padding, test-style nits).

## Open — 0

## Pre-existing (not blocking) — 2

- **No test executes list_schedulers or the new resource_ids ANY clause** — `src/codemie/service/settings/scheduler_settings_service.py:520` — router tests mock the service and service tests only assert `_build_list_filters` condition strings; the positional call and SQL execution are unexercised. Pre-existing: list_schedulers had no execution-level test before this change, and the positional call order was read and is correct.
- **resource_type and resource_ids use independent EXISTS clauses** — `src/codemie/service/settings/scheduler_settings_service.py:488` — a scheduler can match type and id from different credential_values entries. Pre-existing: the independent-EXISTS shape predates this change; the diff only widens the id match to a list.

## Coverage

| Acceptance criterion | Status | Notes |
|---|---|---|
| AC1: Two or more projectId values return schedulers of all those projects | pass | list[str] Query alias projectId; ANY(:project_ids) |
| AC2: Dropping one value from the request leaves the remaining values applied | pass | list passed through as-is to ANY(:project_ids) |
| AC3: Result contains schedulers belonging to any selected project (OR) | pass | s.project_name = ANY(:project_ids) |
| AC4: AC1-3 hold for resourceId (any selected resource) | pass | EXISTS subquery uses cv->>'value' = ANY(:resource_ids) |
| AC5: Non-admin never receives schedulers outside their access, incl. mixed accessible/inaccessible projectIds | pass | project_names scope block untouched and ANDed |
| AC6: No projectId/resourceId (or empty/blank value) returns unfiltered list | pass | blank values dropped before the filter conditions |
| AC7: A single projectId/resourceId behaves exactly as before | pass | single value becomes a one-element list under ANY |
| AC8: Pagination count reflects the same filtered set as the rows | pass | one conditions/params pair shared by count and rows queries |
| Service signature renamed to project_ids/resource_ids, both call sites updated | pass | argument positions unchanged; router is the only src caller |
| Tests: service tests updated and cases added | pass | single, several, empty, blank, resource ids, non-admin outside access |
| Tests: router tests renamed kwargs and a TestClient test shows repeated params reach service as lists | pass | test_router_list_schedulers_binds_repeated_params_as_lists |
| Non-goals: no run-history, other filters, scope logic or response model changes | pass | diff limited to list endpoint, filter builder and tests |

Standards: commit-format pass · code-quality pass · security pass
