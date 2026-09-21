# Technical Research

**Task**: analytics ai-adoption endpoints handler routers
**Generated**: 2026-08-28
**Research path**: filesystem

---

## 1. Original Context

Title: Backend: remove AI/Run Adoption endpoints, service, and scoring modules

Description: Delete all /v1/analytics/ai-adoption-* REST endpoints, the service facade methods behind them, the handler, and the query/scoring package — so calls fail as unknown endpoints rather than returning stale or empty data.

Repo: codemie (backend)

Context:
- src/codemie/rest_api/routers/analytics.py — 12 route declarations under /v1/analytics/ai-adoption-* (~lines 2197–2816): overview, maturity (GET+POST), config, user-engagement (+users), asset-reusability (+assistants/workflows/datasources), expertise-distribution, feature-adoption.
- src/codemie/service/analytics/analytics_service.py — facade methods get_ai_adoption_* (lines ~1158–1449).
- src/codemie/service/analytics/handlers/ai_adoption_handler.py — 1556 lines, core handler logic.
- src/codemie/service/analytics/queries/ai_adoption_framework/ package — 6 files, ~3455 lines total (config.py, query_builder.py, dimension_queries.py, base_queries.py, column_definitions.py, composite_queries.py).

Scope:
- Remove all 12 route declarations from analytics.py.
- Remove get_ai_adoption_* facade methods from analytics_service.py.
- Delete ai_adoption_handler.py entirely.
- Delete the ai_adoption_framework/ query package entirely.
- Confirm no other module imports from this package before deletion (grep for ai_adoption_framework, ai_adoption_handler).
- Inspect src/codemie/agents/tools/platform/tools_vars.py for an ai_adoption reference found during triage — not yet characterized; determine whether it's an agent-facing tool schema that needs removal too.
- Confirm removed routes return a standard 404/unknown-route response, not a custom feature removed body.

Out of scope: aiAdoption customer feature flag in customer-config.yaml, docs/ai-adoption-framework.md, Python SDK get_ai_adoption_* methods (separate repo), QA/auditor test suite TC-017 (separate repo, not found).

Acceptance Criteria:
- Former /v1/analytics/ai-adoption-* endpoints return 404 unknown endpoint after removal, not stale/empty/partial data.
- No references to ai_adoption_handler or ai_adoption_framework remain outside the methodology doc and changelog/release notes.
- tools_vars.py ai_adoption reference is confirmed adoption-related and removed, or explicitly excluded with a documented reason.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/routers/analytics.py` (3536 lines) — the `/ai-adoption-*` route block spans **lines 2196–2849** (ends exactly before the unrelated `/spending` route at line 2851). Confirmed **11** async route handlers (ticket said 12 — the "maturity GET variant" described in the ticket does not exist as a separate route; only a POST variant exists at line 2253. Re-verify exact count during implementation, don't rely on the ticket's "12"):
  - `post_ai_adoption_overview` (2196)
  - `post_ai_adoption_maturity` (2253)
  - `get_ai_adoption_config` (2294)
  - `post_ai_adoption_user_engagement` (2355)
  - `post_ai_adoption_user_engagement_users` (2391)
  - `post_ai_adoption_assistant_reusability_detail` (2480)
  - `post_ai_adoption_workflow_reusability_detail` (2567)
  - `post_ai_adoption_datasource_reusability_detail` (2653)
  - `post_ai_adoption_asset_reusability` (2743)
  - `post_ai_adoption_expertise_distribution` (2779)
  - `post_ai_adoption_feature_adoption` (2815)
- Import to remove: `analytics.py:57` — `from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig`
- Pydantic request models used exclusively by the ai-adoption block, safe to delete: `AiAdoptionQueryRequest` (76), `AiAdoptionTabularQueryRequest` (83), `UserEngagementUsersQueryRequest` (92), `AssistantReusabilityDetailRequest` (131), `WorkflowReusabilityDetailRequest` (169), `DatasourceReusabilityDetailRequest` (209).
- Helper functions used only by the ai-adoption routes, safe to delete: `_parse_config_from_request` (250–269), `_format_config_for_log` (272–290) — both reference `AIAdoptionConfig`.
- **Keep**: `AnalyticsDetailResponse`, `UsersListResponse`, `TabularResponse` imports — used extensively (90+ places) by unrelated, non-adoption routes in the same file.
- `src/codemie/service/analytics/analytics_service.py` (~1660+ lines) — **deviation from ticket scope**: the adoption block actually spans **lines 1157–1449**, not just `get_ai_adoption_*`-prefixed methods. It also includes 4 non-prefixed facade methods that exist purely to delegate to `self._adoption_handler` and are only called from the ai-adoption drill-down routes: `get_user_engagement_users` (1214), `get_assistant_reusability_detail` (1254), `get_workflow_reusability_detail` (1291), `get_datasource_reusability_detail` (1328). These must also be deleted, or `AnalyticsService` retains dead references to the deleted handler.
  - Also remove: `from codemie.service.analytics.handlers.ai_adoption_handler import AIAdoptionHandler` (27), `from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig` (42), field `self._adoption_handler_instance: AIAdoptionHandler | None = None` (61), lazy-load property `_adoption_handler` (78–83).
- `src/codemie/service/analytics/handlers/ai_adoption_handler.py` — 1556 lines, delete entirely. Contains function-local (not module-level) imports from `ai_adoption_framework` throughout; irrelevant once the file is deleted.
- `src/codemie/service/analytics/queries/ai_adoption_framework/` — **8 files, not 6 as the ticket states**: `__init__.py` (60), `base_queries.py` (371), `column_definitions.py` (601), `composite_queries.py` (99), `config.py` (1167), `dimension_queries.py` (1100), `query_builder.py` (1520), plus an extra file the ticket did not mention: **`score_expressions.py`** (276 lines). Total 6750 lines. Delete the whole directory.

### Architecture and Layers Affected

Single vertical slice, four layers, one direction of dependency:
`rest_api/routers/analytics.py` → `service/analytics/analytics_service.py` → `service/analytics/handlers/ai_adoption_handler.py` → `service/analytics/queries/ai_adoption_framework/*`

Confirmed via repo-wide grep: **no other module** imports `ai_adoption_handler` or `ai_adoption_framework` outside this chain and its own tests. Safe to delete the whole chain in one pass.

### Integration Points

- No third-party dependencies specific to the adoption modules beyond what the rest of `analytics.py`/`analytics_service.py` already use (fastapi, pydantic).
- `tools_vars.py` open question (from ticket) — **resolved, not a real integration point**: `src/codemie/agents/tools/platform/tools_vars.py` has zero functional references to `ai_adoption` (case-insensitive grep). The only match is prose inside `GET_RAW_CONVERSATIONS_TOOL`'s LLM-facing description (lines 39–48): "...analyzing conversation content and understanding tool usage patterns, user's AI adoption and the maturity of talking to AI agents in general." This is a coincidental use of the words in an unrelated tool's description (wired into `platform_toolkit.py`/`platform_tool.py`), not a schema or code path tied to the endpoints/handler/package being removed. **Recommendation: exclude from this ticket, document the exclusion reason in the MR/plan** per AC #3's "explicitly excluded with a documented reason" clause.

### Patterns and Conventions

- Service facade uses lazy-loaded handler properties per domain (`_adoption_handler_instance` / `@property _adoption_handler`), matching the pattern used by all other handlers (`_summary_handler`, `_assistant_handler`, etc.). Removal should mirror the same shape as other handler-wiring blocks for consistency — delete the whole block, don't leave partial scaffolding.
- Router groups routes by domain with a shared `@handle_analytics_errors` decorator and `_create_response` helper — both are used across the file, keep them.

---

## 3. Documentation Findings

### Guides and Architecture Docs

No P0 guide directly covers "endpoint deletion." Closest relevant guides per AGENTS.md task classifier: `.ai-run/guides/api/rest-api-patterns.md` (API layer) and `.ai-run/guides/architecture/service-layer-patterns.md` (service facade layer) — load before editing.

### Architectural Decisions

`docs/ai-adoption-framework.md` exists — **explicitly out of scope**, ticket says to leave it untouched (methodology reference doc).

### Derived Conventions

Deletion should follow the existing handler-wiring removal shape (import + field + lazy property + facade methods removed together) to avoid dead references.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/analytics/handlers/test_ai_adoption_handler.py` (1660 lines) — unit tests for the handler being deleted; delete entirely.
- `tests/codemie/service/analytics/queries/ai_adoption_framework/test_config_security.py`, `test_dimension_queries.py`, `test_query_builder.py`, `test_score_expressions.py` — tests for the query package being deleted; delete entirely.
- **Duplicate/parallel test tree**: `tests/unit/service/analytics/queries/test_config_security.py` and `tests/unit/service/analytics/queries/ai_adoption_framework/test_column_definitions.py` (454 lines) also cover the same package under a different tree (`tests/unit/...` vs `tests/codemie/...`) — both trees need cleanup.
- `tests/codemie/service/analytics/test_analytics_service.py` (799 lines) — covers `AnalyticsService` broadly, likely including `get_ai_adoption_*` and the 4 drill-down facade methods; needs partial edits, not full deletion.
- `tests/codemie/rest_api/routers/test_analytics.py` — 30 occurrences of `ai-adoption`/`ai_adoption`; needs partial edits (covers all analytics routes) — should also gain/verify a test asserting removed routes now return 404.
- `tests/codemie/rest_api/routers/test_analytics_auditor.py` (155 lines) — references ai-adoption; needs review for auditor-role-specific ai-adoption route tests.

### Testing Framework and Patterns

pytest, per `.ai-run/guides/testing/testing-patterns.md` (not independently re-verified in this research pass — load if/when tests are written).

### Coverage Gaps

No existing test asserts that the removed `/ai-adoption-*` routes return a plain 404. This is new behavior to add per AC #1.

---

## 5. Configuration and Environment

### Environment Variables

None found specific to ai-adoption.

### Configuration Files

`config/customer/customer-config.yaml:119` — `- id: "aiAdoption"` feature flag entry. **Explicitly out of scope per ticket** — do not touch.

### Feature Flags and Deployment Concerns

`aiAdoption` flag stays as-is (out of scope). No Dockerfile/CI references to `ai_adoption` found.

---

## 6. Risk Indicators

- **Ticket scope is understated in two places**: `analytics_service.py`'s adoption block includes 4 additional non-`get_ai_adoption_*`-prefixed facade methods (drill-down delegates) that must also be deleted, and the query package has 8 files (not 6) — an extra `score_expressions.py` (276 lines) that must also be deleted. Missing either leaves dead code / dangling imports.
- **Route count mismatch**: ticket says 12 routes, only 11 were found; ticket's "GET+POST maturity" description doesn't match reality (POST only). Re-verify exact route boundaries at implementation time rather than trusting the ticket's line numbers, which have likely drifted.
- **Duplicate test tree**: two parallel test directory structures (`tests/codemie/...` and `tests/unit/...`) both cover the query package — both need cleanup or AC #2 ("no references remain") will be violated.
- **No existing 404 test** — must be added to satisfy AC #1; can't verify "fails as unknown endpoint" behavior via existing coverage alone.
- **`tools_vars.py` false positive** — the ticket flags this as an open question; research confirms it's unrelated prose, not code to remove. Must still be documented as an explicit exclusion per AC #3's wording.

---

## 7. Summary for Complexity Assessment

This is a well-bounded deletion task touching one vertical slice across 4 layers (router → service facade → handler → query package), plus corresponding test files across two parallel test trees. The main technical work is straightforward removal (no new logic), but ticket's stated scope undercounts real scope in two concrete ways: the service facade has 4 extra delegate methods beyond the `get_ai_adoption_*` prefix, and the query package has 2 more files than stated (8 vs 6, ~6750 total lines vs the ticket's ~3455 estimate for a 6-file package). Both must be included or the deletion leaves dead imports/dangling references that violate AC #2.

Risk is low-to-moderate: no other module imports from the chain being deleted (confirmed via repo-wide grep), so there's no fan-out risk. The main verification burden is behavioral — a new test must assert 404 on the removed routes (no such coverage exists today), and the `tools_vars.py` false-positive needs an explicit documented exclusion to satisfy AC #3. Test coverage cleanup also needs to touch two duplicate test-tree locations, not one.
