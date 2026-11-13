---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: bc781ee4a2d2e2b7ef4d0755047c8550fb72e384
---

# Code review — 2026-10-26-bulk-budget-override-batch

**Decision:** approve (check round, confidence final high · check high)  
**Reviewed head:** `bc781ee` · **Rounds:** final → check

**Closed:** 4 fixed · 0 moot · 0 decided

Targeted check of all 4 prior blocking findings (kind code): each is resolved in current source (typing fixed and endpoint annotated, ok results omit code/message via wrap serializer, bulk logic moved to bulk_member_override.py, budgets loaded once and reused). No new findings discovered by design.

## Open — 0

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| AC1: 200 with one result per item in input order; budgets for touched budgets in first-appearance order | pass | Router orders results by index; budgets via dict.fromkeys; covered by router test. |
| AC2: Missing budget gives failed 404 for all its items; other budgets processed | pass | Router missing map plus service 404 mapping; tests cover both. |
| AC3: Per-item 400 only for soft>max; missing member 404 with today's message | pass | _bulk_screen_items reuses _validate_member_amounts and the existing 404 message. |
| AC4: Project-limit check once per budget on final state; over-limit fails remaining items with 400 | pass | _bulk_check_overrides applies copies then one _equal_amounts_with_fixed_overrides per budget. |
| AC5: Permission denied on any budget returns 403 for whole request, no writes | pass | Router checks every distinct budget before the service call. |
| AC6: Repeated (budget,user): later item wins; each accepted item gets result and event | pass | Copies updated in input order; Phase 4 per item. |
| AC7: Post-flush recheck once per budget on reloaded data; failure gives 400, no rebalance/provider | pass | flush, populate_existing reload and refresh before any rebalance. |
| AC8: rebalance_project_budget exactly once per touched budget, unmodified | pass | Loop over loaded budgets only; rebalance untouched in diff. |
| AC9: One MEMBER_ALLOCATION_OVERRIDDEN event and log per accepted item | pass | _bulk_record_override mirrors PATCH event attributes and log format. |
| AC10: All items failing gives 200, all failed, budgets [], no writes or events | pass | Service returns early on empty accepted; router returns empty budgets. |
| AC11: Phase 1 performs no writes, provider calls or ORM mutation | pass | Phase 1 mutates SimpleNamespace copies only. |
| AC12: Phase 2b/3 400 or unexpected error leaves nothing committed | pass | Only commit is in router after service returns; get_async_session closes without commit. No router-level rollback test. |
| AC13: PATCH quirks preserved: null reason clears; accepted items set member FIXED | pass | _bulk_write_override passes reason unchanged and mode FIXED. |
| AC14: 0 items, >1000 items or negative amounts give 422 | pass | min_length=1, max_length=1000, ge=0 on amounts. |
| AC15: PATCH unchanged; repo default unchanged; baselines regenerated with new route ahead of /{budget_id} | pass | populate_existing defaults False; openapi baseline updated. |
| Data shape: Result carries code and message only on failure | partial (final round) | Ok results serialize code:null and message:null; no exclude_none; test asserts nulls (CR-002). |
| Service Phase 1: load each budget, assignment and allocations once | partial (final round) | bulk_override_member_allocations reloads each accepted budget after _bulk_check_overrides (CR-004). |

Standards: commit-format pass · code-quality fail (final round) · security pass
