# Remove AI/Run Adoption Backend Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the entire AI/Run Adoption backend vertical slice (router → service facade → handler → query package) plus its tests, so former `/v1/analytics/ai-adoption-*` endpoints fail as unknown routes (404) instead of returning stale or empty data.

**Architecture:** Four-layer removal, top-down: router layer first (removes the public surface and proves 404 behavior), then service facade, then handler, then the query/scoring package — each step leaves the test suite green because nothing downstream is still referenced by a deleted upstream layer.

**Tech Stack:** Python 3.12, FastAPI, pytest, Poetry.

## Global Constraints

- No other module in the repo imports `ai_adoption_handler` or `ai_adoption_framework` outside the four files/dirs being deleted and their own tests (confirmed via repo-wide grep in research).
- Do not touch: `config/customer/customer-config.yaml`'s `aiAdoption` feature flag entry (line 119), `docs/ai-adoption-framework.md` (methodology doc), the Python SDK (separate repo, not checked out here), QA auditor suite TC-017 (separate repo, not found).
- `src/codemie/agents/tools/platform/tools_vars.py` needs no code change — its "AI adoption" phrase is unrelated prose inside `GET_RAW_CONVERSATIONS_TOOL`'s description (lines ~39-48), not a schema tied to these endpoints. Document this exclusion explicitly (Task 5) to satisfy AC #3.
- Preserve unrelated code/tests in every file that gets a partial edit (`analytics.py`, `analytics_service.py`, `test_analytics_service.py`, `test_analytics.py`, `test_analytics_auditor.py`) — these files serve other analytics domains too.
- Run `poetry run pytest <changed test path>` after each task to confirm the suite stays green before moving to the next task.

---

### Task 1: Remove the router layer and prove 404 behavior

**Files:**
- Modify: `src/codemie/rest_api/routers/analytics.py:57` (import), `:75-290` (request models + helpers), `:2196-2850` (route block)
- Modify: `tests/codemie/rest_api/routers/test_analytics.py:1086-1670` (drill-down test classes)
- Modify: `tests/codemie/rest_api/routers/test_analytics_auditor.py:71-141` (four adoption-specific auditor tests, keep the class and `test_leaderboard_user_detail_allows_auditor`)
- Test: `tests/codemie/rest_api/routers/test_analytics.py` (new 404 test)

**Interfaces:**
- Consumes: nothing new — this task only removes code.
- Produces: `router` (the `APIRouter` instance at `analytics.py:343`) with the 11 `ai-adoption-*` paths no longer registered. Later tasks (2-4) rely on `AnalyticsService` no longer being called by these routes.

- [ ] **Step 1: Write the failing 404 test**

Add to `tests/codemie/rest_api/routers/test_analytics.py`, after the imports at the top (extend the existing import line 39 to also import `router`):

```python
from codemie.rest_api.routers.analytics import _create_response, handle_analytics_errors, router
```

Add a new test class near the end of the file, after `class TestAuthorizeAdminBudgetView:` (or any existing class — exact placement doesn't matter, this appends a new top-level class):

```python
class TestAiAdoptionRoutesRemoved:
    """EPMCDME-14465: former /ai-adoption-* routes must no longer be registered."""

    REMOVED_PATHS = {
        "/v1/analytics/ai-adoption-overview",
        "/v1/analytics/ai-adoption-maturity",
        "/v1/analytics/ai-adoption-config",
        "/v1/analytics/ai-adoption-user-engagement",
        "/v1/analytics/ai-adoption-user-engagement/users",
        "/v1/analytics/ai-adoption-asset-reusability",
        "/v1/analytics/ai-adoption-asset-reusability/assistants",
        "/v1/analytics/ai-adoption-asset-reusability/workflows",
        "/v1/analytics/ai-adoption-asset-reusability/datasources",
        "/v1/analytics/ai-adoption-expertise-distribution",
        "/v1/analytics/ai-adoption-feature-adoption",
    }

    def test_no_ai_adoption_routes_registered(self):
        """Removed routes must not appear in the router — a request to them 404s like any unknown path."""
        registered_paths = {route.path for route in router.routes}
        overlap = registered_paths & self.REMOVED_PATHS
        assert overlap == set(), f"ai-adoption routes still registered: {overlap}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_analytics.py::TestAiAdoptionRoutesRemoved -v`
Expected: FAIL — `overlap` is non-empty because the 11 routes are still registered.

- [ ] **Step 3: Delete the route block from `analytics.py`**

Delete lines 2196-2850 (the full `@router.post("/ai-adoption-overview", ...)` block through `post_ai_adoption_feature_adoption`, including the trailing blank line at 2850 — this preserves the existing double-blank-line spacing before the next route, `@router.get("/spending", ...)` at the old line 2851).

- [ ] **Step 4: Delete the ai-adoption-only request models and helper functions from `analytics.py`**

Delete lines 75-290: the `# Request models for AI Adoption Framework queries` comment, the six request model classes (`AiAdoptionQueryRequest`, `AiAdoptionTabularQueryRequest`, `UserEngagementUsersQueryRequest`, `AssistantReusabilityDetailRequest`, `WorkflowReusabilityDetailRequest`, `DatasourceReusabilityDetailRequest`), and the two helper functions `_parse_config_from_request` / `_format_config_for_log`. Do not delete `handle_analytics_errors` (starts at the old line 293) — it is used by all analytics routes.

- [ ] **Step 5: Delete the now-unused import from `analytics.py`**

Delete line 57: `from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig`

- [ ] **Step 6: Run the 404 test again to verify it passes**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_analytics.py::TestAiAdoptionRoutesRemoved -v`
Expected: PASS

- [ ] **Step 7: Remove the now-dead drill-down test classes from `test_analytics.py`**

Delete lines 1086-1670 of `tests/codemie/rest_api/routers/test_analytics.py` — this removes `TestUserEngagementUsersDrillDown`, `TestAssistantReusabilityDrillDown`, `TestWorkflowReusabilityDrillDown`, and `TestDatasourceReusabilityDrillDown` in full (they import the now-deleted route functions), while preserving the two blank lines before them (old lines 1084-1085) and the `# Tests for /analytics/spending endpoint` comment block that follows (old line 1671 onward, now immediately after the preserved blank lines).

- [ ] **Step 8: Remove the four adoption-specific tests from `test_analytics_auditor.py`, keep the rest**

In `tests/codemie/rest_api/routers/test_analytics_auditor.py`, delete only the four test methods that import the removed route functions — `test_user_engagement_users_allows_auditor_for_unowned_project` (lines 71-87), `test_assistant_reusability_detail_allows_auditor_for_unowned_project` (89-105), `test_workflow_reusability_detail_allows_auditor_for_unowned_project` (107-123), and `test_datasource_reusability_detail_allows_auditor_for_unowned_project` (125-141). Keep the class `TestAnalyticsAuditorCrossProjectAccess` and keep `test_leaderboard_user_detail_allows_auditor` (143-154) — it does not reference any removed route.

Update the class docstring (lines 66-69), which currently reads:

```python
class TestAnalyticsAuditorCrossProjectAccess:
    """EPMCDME-10930 spec 5.2: auditor must reach the service call (no 403) for the
    remaining five inline guards (593 is covered above), even for a project the
    auditor has no explicit membership in.
    """
```

to:

```python
class TestAnalyticsAuditorCrossProjectAccess:
    """EPMCDME-10930 spec 5.2: auditor must reach the service call (no 403), even for
    a project the auditor has no explicit membership in.

    The four ai-adoption drill-down guard tests that used to live here were removed
    under EPMCDME-14465 along with the routes they covered; only the leaderboard
    guard remains.
    """
```

- [ ] **Step 9: Run the full router test file**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_analytics.py tests/codemie/rest_api/routers/test_analytics_auditor.py -v`
Expected: PASS, no import errors, no leftover references to removed route functions.

- [ ] **Step 10: Commit**

```bash
git add src/codemie/rest_api/routers/analytics.py tests/codemie/rest_api/routers/test_analytics.py tests/codemie/rest_api/routers/test_analytics_auditor.py
git commit -m "EPMCDME-14465: remove ai-adoption routes from analytics router"
```

---

### Task 2: Remove the adoption facade layer from `AnalyticsService`

**Files:**
- Modify: `src/codemie/service/analytics/analytics_service.py:27` (import), `:42` (import), `:61` (field), `:78-83` (lazy property), `:1157-1450` (facade methods)
- Modify: `tests/codemie/service/analytics/test_analytics_service.py:604-799` (drop the adoption test class)

**Interfaces:**
- Consumes: nothing from Task 1 directly — `AnalyticsService` had no callers left in the router after Task 1, so its adoption methods are now dead code.
- Produces: `AnalyticsService` with no `_adoption_handler` property and no `get_ai_adoption_*` / drill-down delegate methods. Task 3 relies on `AIAdoptionHandler` having zero remaining importers once this task is done.

- [ ] **Step 1: Delete the adoption facade methods**

Delete lines 1157-1450 of `src/codemie/service/analytics/analytics_service.py`: the `# Adoption endpoints` comment, `get_ai_adoption_overview`, `get_ai_adoption_maturity`, `get_ai_adoption_user_engagement`, `get_user_engagement_users`, `get_assistant_reusability_detail`, `get_workflow_reusability_detail`, `get_datasource_reusability_detail`, `get_ai_adoption_asset_reusability`, `get_ai_adoption_expertise_distribution`, `get_ai_adoption_feature_adoption`, `get_ai_adoption_config`, including the trailing blank line at 1450 — this preserves the single blank-line spacing before the next method block's `# Engagement endpoint: weekly histogram (ignores time filter)` comment (old line 1451).

- [ ] **Step 2: Delete the lazy-loaded `_adoption_handler` property**

Delete lines 78-83 (the `@property` / `_adoption_handler` block), including its trailing blank line, so the next property (`_summary_handler` at the old line 85) is reached directly after the `__init__` method with the same single-blank-line spacing used between every other property pair in this class.

- [ ] **Step 3: Delete the `_adoption_handler_instance` field**

Delete line 61: `self._adoption_handler_instance: AIAdoptionHandler | None = None`

- [ ] **Step 4: Delete the now-unused imports**

Delete line 27: `from codemie.service.analytics.handlers.ai_adoption_handler import AIAdoptionHandler`
Delete line 42: `from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig`

- [ ] **Step 5: Delete the dead adoption test class**

Delete lines 604-799 of `tests/codemie/service/analytics/test_analytics_service.py` — this is `class TestAIAdoptionDrillDownDelegation` through end of file (it is the last class in the file), including its two leading blank lines (604-605) since nothing follows it.

- [ ] **Step 6: Run the service test file**

Run: `poetry run pytest tests/codemie/service/analytics/test_analytics_service.py -v`
Expected: PASS, no `NameError`/`ImportError` for `AIAdoptionHandler` or `AIAdoptionConfig`.

- [ ] **Step 7: Run the full test suite for the analytics service + router to confirm nothing else broke**

Run: `poetry run pytest tests/codemie/service/analytics/ tests/codemie/rest_api/routers/test_analytics.py tests/codemie/rest_api/routers/test_analytics_auditor.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/codemie/service/analytics/analytics_service.py tests/codemie/service/analytics/test_analytics_service.py
git commit -m "EPMCDME-14465: remove ai-adoption facade methods from AnalyticsService"
```

---

### Task 3: Delete the `ai_adoption_handler.py` module and its tests

**Files:**
- Delete: `src/codemie/service/analytics/handlers/ai_adoption_handler.py`
- Delete: `tests/codemie/service/analytics/handlers/test_ai_adoption_handler.py`

**Interfaces:**
- Consumes: confirmation from Task 2 that `AnalyticsService` no longer imports `AIAdoptionHandler` (zero remaining importers repo-wide, per research grep).
- Produces: nothing consumed by other tasks — Task 4 deletes the query package independently, verified by its own grep.

- [ ] **Step 1: Confirm no remaining importers before deleting**

Run: `grep -rn "ai_adoption_handler\|AIAdoptionHandler" src/ tests/ --include="*.py"`
Expected: only `src/codemie/service/analytics/handlers/ai_adoption_handler.py` itself (the module being deleted) and `tests/codemie/service/analytics/handlers/test_ai_adoption_handler.py` (the test file being deleted) appear.

- [ ] **Step 2: Delete the handler module**

```bash
git rm src/codemie/service/analytics/handlers/ai_adoption_handler.py
```

- [ ] **Step 3: Delete its test file**

```bash
git rm tests/codemie/service/analytics/handlers/test_ai_adoption_handler.py
```

- [ ] **Step 4: Run the handlers test directory**

Run: `poetry run pytest tests/codemie/service/analytics/handlers/ -v`
Expected: PASS — no collection errors, remaining handler tests unaffected.

- [ ] **Step 5: Commit**

```bash
git commit -m "EPMCDME-14465: delete ai_adoption_handler.py and its tests"
```

---

### Task 4: Delete the `ai_adoption_framework` query package and its tests

**Files:**
- Delete: `src/codemie/service/analytics/queries/ai_adoption_framework/` (8 files: `__init__.py`, `base_queries.py`, `column_definitions.py`, `composite_queries.py`, `config.py`, `dimension_queries.py`, `query_builder.py`, `score_expressions.py`)
- Delete: `tests/codemie/service/analytics/queries/` (entire directory — contains only the `ai_adoption_framework/` subpackage and its own `__init__.py`, nothing else)
- Delete: `tests/unit/service/analytics/queries/` (entire directory — contains only `test_config_security.py` and the `ai_adoption_framework/` subpackage with `test_column_definitions.py`, nothing else; sibling `tests/unit/service/analytics/handlers/` is untouched)

**Interfaces:**
- Consumes: confirmation from Tasks 1-3 that no source file outside this package imports from it any more.
- Produces: nothing — this is the last layer of the vertical slice.

- [ ] **Step 1: Confirm no remaining importers before deleting**

Run: `grep -rn "ai_adoption_framework" src/ tests/ --include="*.py"`
Expected: only paths under `src/codemie/service/analytics/queries/ai_adoption_framework/`, `tests/codemie/service/analytics/queries/`, and `tests/unit/service/analytics/queries/` appear (the directories being deleted in this task).

- [ ] **Step 2: Delete the source package**

```bash
git rm -r src/codemie/service/analytics/queries/ai_adoption_framework/
```

- [ ] **Step 3: Delete the two test trees**

```bash
git rm -r tests/codemie/service/analytics/queries/
git rm -r tests/unit/service/analytics/queries/
```

- [ ] **Step 4: Run the full analytics test suite**

Run: `poetry run pytest tests/codemie/service/analytics/ tests/unit/service/analytics/ tests/codemie/rest_api/routers/test_analytics.py tests/codemie/rest_api/routers/test_analytics_auditor.py -v`
Expected: PASS — no collection errors, no leftover imports.

- [ ] **Step 5: Commit**

```bash
git commit -m "EPMCDME-14465: delete ai_adoption_framework query package and its tests"
```

---

### Task 5: Document the tools_vars.py exclusion and verify no references remain

**Files:**
- Modify: none (documentation-only — no code change to `src/codemie/agents/tools/platform/tools_vars.py`)
- Verify: repo-wide grep

**Interfaces:**
- Consumes: nothing.
- Produces: nothing — this is the final verification task satisfying AC #2 and AC #3.

- [ ] **Step 1: Confirm the tools_vars.py reference is unrelated prose, not a code path**

Run: `grep -n "ai_adoption\|AI adoption" src/codemie/agents/tools/platform/tools_vars.py`
Expected: one match, inside `GET_RAW_CONVERSATIONS_TOOL`'s description string (around lines 39-48), reading "...analyzing conversation content and understanding tool usage patterns, user's AI adoption and the maturity of talking to AI agents in general." This is free-text tool description for the unrelated `get_raw_conversations` tool — not a schema or import tied to the deleted endpoints/handler/package.

- [ ] **Step 2: Document the exclusion in the MR description**

When opening the MR/PR for this ticket, include this line in the description (satisfies AC #3's "explicitly excluded with a documented reason"):

> `tools_vars.py`'s `GET_RAW_CONVERSATIONS_TOOL` description contains the phrase "AI adoption" in unrelated prose describing an LLM-facing tool for conversation analysis. It is not a schema or code path tied to the ai-adoption endpoints/handler/query-package removed by this ticket. No code change made here — excluded from this ticket's scope.

- [ ] **Step 3: Final repo-wide verification for AC #2**

Run: `grep -rn "ai_adoption_handler\|ai_adoption_framework" --include="*.py" --include="*.md" --include="*.yaml" --include="*.yml" .`
Expected: zero matches, or matches only inside `docs/ai-adoption-framework.md` (explicitly out of scope) and any changelog/release-notes files. If any other match appears, resolve it before considering this ticket done.

- [ ] **Step 4: Run the full backend test suite one more time**

Run: `poetry run pytest tests/ -v`
Expected: PASS, no collection errors anywhere in the repo.

- [ ] **Step 5: Commit (if the grep step required any doc/comment fix)**

```bash
git add -A
git commit -m "EPMCDME-14465: verify no ai-adoption references remain outside excluded docs"
```

(If Step 3 found nothing to fix, skip this commit — there is nothing to commit.)
