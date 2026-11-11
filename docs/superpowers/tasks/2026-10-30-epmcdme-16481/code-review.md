---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: bf6b13cb54dbd724e952b78e73cc97d7b58ecb93
---

# Code review — 2026-10-30-epmcdme-16481

**Decision:** approve (check round, confidence final medium · check high)  
**Reviewed head:** `bf6b13c` · **Rounds:** final → check

**Closed:** 6 fixed · 0 moot · 0 decided

All 6 prior blocking findings (CR-001..CR-006) are resolved in the current source at HEAD bf6b13cb5 (fix-up 693984b40, the only src/tests change since reviewed head d1030dda5), including CR-002, where has_more is now forced false when (page + 2) * per_page > 10000, which matches the 400 guard's next-page boundary. business_review and standards_review are carried forward unchanged from the final round, so their AC8 fail and code-quality partial rows describe the pre-fix state that CR-006 and CR-001 have since closed. The deferred backlog (code-review-deferred.local.md) is not re-checked, and the brief was written.

## Open — 0

## Pre-existing (not blocking) — 1

- **Result-window guard missing from other search callers** — `src/codemie/service/analytics/query_pipeline.py:78` — `execute_search_rows` has no 10,000-row window guard, so deep pages from other callers (e.g. the routing decision timeline) still reach ES and fail generically. Pre-existing: the decision timeline's deep-paging behaviour predates this change, and the task requires the routing/decisions ES path to stay unchanged.

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| Read-only GET /v1/analytics/assistants-requests next to assistants-chats, same decorators and envelope | pass | usage.py route with TabularResponse and @handle_analytics_errors |
| Route uses Depends(authenticate), AnalyticsQueryParams, client_source and optional assistant_id | pass | route signature; forwarding test present |
| AnalyticsService.get_assistants_requests delegates to AssistantHandler | pass | delegation test present |
| Handler calls execute_search_rows with conversation_assistant_usage filter, row parser and column list | pass | assistant_handler.py get_assistants_requests |
| AnalyticsQueryFilters gains assistant_id; term on attributes.assistant_id.keyword only when set | pass | query_pipeline.py and query_builder.py; set/empty/None tests |
| execute_search_rows gains optional sort and track_total_hits | pass | forwards only non-None options; tests present |
| MetricsElasticRepository.execute_search_query adds sort/track_total_hits to body only when provided | pass | repository tests assert exact body |
| Row columns from attribute names, timestamp from @timestamp | pass | _parse_assistants_requests |
| Paging from=page*per_page; over-window request rejected with HTTP 400 telling caller to narrow date range | pass | handler guard; decorator re-raises ExtendedHTTPException (_common.py) |
| Access scoping unchanged from SecureQueryBuilder/AccessFilter | pass | shared _build_query path |
| Jira write-up of Reconciliation and Field availability posted at handoff | na | handoff action, not representable in the diff |
| AC1: endpoint exists, read-only, authenticated, TabularResponse with exactly 19 columns in order | pass | ASSISTANTS_REQUESTS_COLUMNS; exact-order test |
| AC2: one row per usage document; total_tokens = input + output; missing attributes null | pass | parser and null-handling test |
| AC3: standard filters plus exact-term assistant_id narrow the ES query as for assistants-chats | pass | query-shape test |
| AC4: sorted by @timestamp desc; exact total_count via track_total_hits | pass | sort and track_total_hits=True; no tiebreaker (CR-003) |
| AC5: (page+1)*per_page > 10000 returns HTTP 400 without querying ES | pass | guard precedes ES call; rejection and boundary tests |
| AC6: access scoping matches other analytics endpoints | pass | no SecureQueryBuilder access-logic change |
| AC7: routing/decisions ES body unchanged, asserted by a test | pass | test asserts body keys == {query,size,from} |
| AC8: row count and money_spent/total_tokens sums reconcile with assistants-chats, covered by handler tests on shared fixtures | fail (final round) | test compares only hand-written constants; fixture doc lacking output_tokens counts 100 here but is dropped by ES\|QL SUM(input+output) (CR-006) |
| AC9: OpenAPI description covers chat vs request, error detail, paging bound, reconciliation; baseline regenerated; router-guard tests pass | pending-stage-7 | description and baseline updated; test pass is execution evidence |
| AC10: tests for handler, pipeline/repository optional params, service delegation and route | pass | tests added across five test modules |
| Non-goal: no changes to metric emission | pass | no monitoring files in diff |
| Non-goal: no changes to assistants-chats aggregation | pass | get_assistants_chats untouched |
| Non-goal: no change to GET /v1/assistants/metrics/{conversation_id} or its auth | pass | not in diff |
| Non-goal: no conversation_id/request_id filter, no free-text search, no search_after paging | pass | only assistant_id filter; from/size paging |
| Non-goal: no workflow per-execution analytics | pass | not in diff |
| Non-goal: no SDK or docs-site changes | pass | diff confined to codemie src/tests |
| Non-goal: no PostgreSQL reads and no new index or mapping | pass | ES search on existing index only |
| Non-goal: no UI | pass | not in diff |

Standards: commit-format pass · code-quality partial (final round) · security pass
