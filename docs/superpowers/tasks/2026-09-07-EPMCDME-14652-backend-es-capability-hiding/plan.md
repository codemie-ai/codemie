# EPMCDME-14652 — Backend ES Capability Hiding: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate ES-dependent Metrics Analytics routes behind `_require_metrics_analytics_enabled()`; fix D16 eager ES coupling in `AnalyticsService`; add `features:metricsAnalytics` to `GET /v1/config`; harden existing committed guards with regression and rewritten tests.

**Already committed on this branch — do not re-implement, describe as absent, or mark RED:**
- `ADMIN_LOG_LOOKUP_ENABLED: bool = True` in `config.py` + `POST /logs` 503 guard in `routers/logs.py`
- `TOOL_SELECTION_ENABLED` call-time guard (early return `[]`) in `get_tools_by_query()`
- `conversationAnalytics` and `smartToolSelection` runtime projections in `_get_runtime_config()`
- `aiChampionsLeaderboard` ceiling (post-resolution suppression when `LEADERBOARD_ENABLED=False`)
- Tests for all of the above

**Architecture:** FastAPI `Depends()` guards per capability group. `has_enterprise()` from `codemie.enterprise` gates Metrics Analytics at route and config-projection layers. Lazy `_repository` property on `AnalyticsService` decouples AI Adoption (PostgreSQL) from `MetricsElasticRepository` construction. `_get_runtime_config()` extended for `features:metricsAnalytics` only; existing projections/ceiling confirmed, not overwritten.

**Tech Stack:** FastAPI `Depends()`, pydantic-settings `BaseSettings`, `importlib.metadata`, `unittest.mock.patch`, pytest-asyncio.

**Implementation mode: inline/sequential** — T1→T8; tasks share `customer_config.py`, `analytics.py`, `AnalyticsService`, and test files. Do not split into parallel subagents.

## Global Constraints

- No `ELASTICSEARCH_ENABLED` global flag; no `METRICS_ANALYTICS_ENABLED` env flag.
- `has_enterprise()` is the canonical enterprise check; no duplicate `importlib.metadata.version("codemie-enterprise")` try/except.
- All new guards return HTTP 503; status code isolated in the guard function (single change point).
- Analytics router not unregistered; per-route `Depends()` guards only.
- Conversation Analytics trigger 400 behavior unchanged.
- Stale-Datasource Detection: no code changes.
- Commit per task using the repository's existing convention.

---

### Task 1: Enterprise availability helper — `has_enterprise()` + export + `customer_config.py` migration

**Test-first: yes — `test_has_enterprise_returns_false_when_patched` fails (function does not exist yet)**

**Files:**
- Modify: `src/codemie/enterprise/loader.py`
- Modify: `src/codemie/enterprise/__init__.py`
- Modify: `src/codemie/configs/customer_config.py`
- Test: `tests/codemie/enterprise/test_loader.py` (extend or create)

**Step 1 — `loader.py`:** Add after the last `HAS_*/has_*` block, following the `HAS_LANGFUSE`/`has_langfuse()` pattern (`importlib.metadata.version` and `PackageNotFoundError` are already imported there):

```python
try:
    version("codemie-enterprise")
    HAS_ENTERPRISE = True
except PackageNotFoundError:
    HAS_ENTERPRISE = False

def has_enterprise() -> bool:
    """Check if the codemie-enterprise package is installed."""
    return HAS_ENTERPRISE
```

**Step 2 — `__init__.py`:** Add `has_enterprise` to the import list and `__all__` alongside `has_langfuse`, `has_litellm`, `has_idp`.

**Step 3 — `customer_config.py`:** Replace the inline `version("codemie-enterprise") / PackageNotFoundError` try/except in `_get_runtime_config()` with a top-of-file `from codemie.enterprise import has_enterprise` import and `is_enterprise = has_enterprise()` at the call site. Remove the now-unused `PackageNotFoundError` import from this file if nothing else in it uses it.

- [ ] Write failing test: `patch("codemie.enterprise.loader.HAS_ENTERPRISE", False)`; assert `has_enterprise() == False`. Also `patch(..., True)`; assert `True`.
- [ ] Run test: `pytest tests/codemie/enterprise/test_loader.py -v` — expect FAIL (function not found)
- [ ] Implement `HAS_ENTERPRISE` / `has_enterprise()` in `loader.py`
- [ ] Export from `__init__.py`
- [ ] Migrate `customer_config.py` — no private helper; only the `from codemie.enterprise import has_enterprise` import
- [ ] Run: `pytest tests/codemie/enterprise/ tests/codemie/service/test_customer_config_service.py -v`

---

### Task 2: Lazy `_repository` property in `AnalyticsService` (D16 fix)

**Test-first: yes — `test_analytics_service_init_does_not_construct_metrics_elastic_repository` fails (`MetricsElasticRepository()` called eagerly in `__init__`)**

**Files:**
- Modify: `src/codemie/service/analytics/analytics_service.py`
- Test: `tests/codemie/service/analytics/test_analytics_service.py` (extend or create)

In `analytics_service.py` `__init__`: remove `self._repository = MetricsElasticRepository()`. Add `self._repository_instance: MetricsElasticRepository | None = None`. Add lazy property following the existing handler lazy pattern (lines 79–83 and 183–188):

```python
@property
def _repository(self) -> "MetricsElasticRepository":
    if self._repository_instance is None:
        self._repository_instance = MetricsElasticRepository()
    return self._repository_instance
```

- [ ] Write failing test: `patch("...MetricsElasticRepository.__init__", return_value=None)` as spy; construct `AnalyticsService(mock_user)`; assert `__init__` **not** called
- [ ] Run test: expect FAIL (eager construction fires)
- [ ] Remove eager construction; add lazy property
- [ ] Run: `pytest tests/codemie/service/analytics/ -v`

---

### Task 3: `_require_metrics_analytics_enabled()` + Group A route decoration

**Test-first: yes — `test_get_summaries_returns_503_when_enterprise_absent` fails (no guard on routes yet)**

**Files:**
- Modify: `src/codemie/rest_api/routers/analytics.py`
- Test: covered by Task 5 parametrized suite

Add near the top of `analytics.py`, after router-level helpers and before the first route handler (the `has_enterprise` import is deferred inside the function to avoid circular imports — follow any existing deferred-import pattern in this file):

```python
def _require_metrics_analytics_enabled() -> None:
    """Dependency: raises 503 when codemie-enterprise is not installed."""
    from codemie.enterprise import has_enterprise
    if not has_enterprise():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Metrics Analytics is not available",
        )
```

Add `dependencies=[Depends(_require_metrics_analytics_enabled)]` to every Group A `@router.get(...)` decorator. The complete and exhaustive Group A function list:

**Lines 677–799:** `get_summaries`, `get_assistants_chats`, `get_workflows`

**Lines 802–2094:** `get_tools_usage`, `get_agents_usage`, `get_power_users`, `get_knowledge_sharing`, `get_top_agents_usage`, `get_top_workflow_usage`, `get_published_to_marketplace`, `get_webhooks_invocation`, `get_mcp_servers`, `get_mcp_servers_by_users`, `get_projects_spending`, `get_llms_usage`, `get_embeddings_usage`, `get_users_spending`, `get_budget_soft_limit`, `get_budget_hard_limit`, `get_users_activity`, `get_users_unique_daily`, `get_users_list`, `get_projects_activity`, `get_projects_unique_daily`, `get_cli_summary`, `get_cli_agents`, `get_cli_llms`, `get_cli_users`, `get_cli_errors`, `get_cli_repositories`, `get_cli_top_performers`, `get_cli_top_versions`, `get_cli_top_proxy_endpoints`, `get_cli_tools_usage`, `get_cli_insights_weekday_pattern`, `get_cli_insights_hourly_usage`, `get_cli_insights_session_depth`, `get_cli_insights_user_classification`, `get_cli_insights_top_users_by_cost`, `get_cli_insights_top_spenders`, `get_cli_insights_all_users`, `get_cli_insights_user_detail`, `get_cli_insights_user_key_metrics`, `get_cli_insights_user_tools`, `get_cli_insights_user_models`, `get_cli_insights_user_workflow_intent`, `get_cli_insights_user_classification_detail`, `get_cli_insights_user_category_breakdown`, `get_cli_insights_user_repositories`, `get_cli_insights_project_classification`, `get_cli_insights_top_projects_by_cost`

**Lines 2146–2193 — enriched-user routes (5 handlers; add `dependencies=` to each `@router.get(...)` decorator; `_run_enriched_user_insight` itself cannot take `Depends()`):** `get_cli_insights_by_enriched_user_primary_skill`, `get_cli_insights_by_enriched_user_country`, `get_cli_insights_by_enriched_user_city`, `get_cli_insights_by_enriched_user_job_title`, `get_cli_insights_by_enriched_user_job_title_group`

**Lines 3225–3309:** `get_engagement_weekly_histogram`, `get_spending_by_users_platform`, `get_spending_by_users_cli`

**Excluded — must NOT receive this dependency:**
- Group B (lines 2196–2890, `/ai-adoption-*`): PostgreSQL-only
- Group C (lines 3312–3618, `/leaderboard/*`): already has `_require_leaderboard_enabled`
- Group D (lines 2926, 3057, 3131, 3181): no `AnalyticsService` dependency

- [ ] Add `_require_metrics_analytics_enabled()` function to `analytics.py`
- [ ] Add `dependencies=[Depends(_require_metrics_analytics_enabled)]` to all 59 Group A routes
- [ ] Verify zero Group B/C/D routes received the dependency: `grep -c "_require_metrics_analytics_enabled" src/codemie/rest_api/routers/analytics.py` — count must equal 60 (1 definition + 59 route decorators)
- [ ] Run: `pytest tests/codemie/rest_api/routers/test_analytics.py -v` (existing tests must pass)

---

### Task 4: `features:metricsAnalytics` config projection

**Test-first: yes — `test_metrics_analytics_absent_from_config_when_enterprise_absent` fails (`features:metricsAnalytics` not in `CONFIG_IDS` yet)**

**Files:**
- Modify: `src/codemie/configs/customer_config.py`
- Test: `tests/codemie/service/test_customer_config_service.py`

**`features:metricsAnalytics` only — add, do not touch existing entries:**

1. Verify `metricsAnalytics` is absent from `CONFIG_IDS`; add `"metricsAnalytics": "features:metricsAnalytics"`.
2. In `_get_runtime_config()`, add one entry: `features:metricsAnalytics` with `enabled=has_enterprise()` (uses the import added in Task 1).

**Existing projections/ceiling — confirm, do not overwrite:** `conversationAnalytics`, `smartToolSelection` projections and the `aiChampionsLeaderboard` ceiling are already present and tested. Do not touch them.

- [ ] Write failing test: `patch("codemie.enterprise.loader.HAS_ENTERPRISE", False)`; call `resolve_components()`; assert `features:metricsAnalytics` absent from result
- [ ] Run test: expect FAIL
- [ ] Add `CONFIG_IDS` entry + `_get_runtime_config()` entry for `features:metricsAnalytics`
- [ ] Run: `pytest tests/codemie/service/test_customer_config_service.py -v`

---

### Task 5: Parametrized regression suites (Suite 1, Suite 2, Suite 3)

**Test-first: yes — all assertions fail before Tasks 2–4 complete**

**Files:**
- Create: `tests/codemie/rest_api/routers/test_analytics_capability_guards.py`

**Suite 1 — Group A: 503 before AnalyticsService/MetricsElasticRepository**

`@pytest.mark.parametrize("path", GROUP_A_PATHS)` — module-level list of all 59 URL path strings corresponding to the Group A functions in Task 3. For routes with required query params (e.g. `user_name`), include a minimal valid query string in the path. Test setup: `patch("codemie.enterprise.loader.HAS_ENTERPRISE", False)`. Assert per entry:
- `response.status_code == 503`
- `AnalyticsService.__init__` not called (spy via `patch`)
- `MetricsElasticRepository.__init__` not called (spy via `patch`)

**Suite 2 — Group B/C/D: unaffected by Metrics Analytics guard**

All entries run under `patch("codemie.enterprise.loader.HAS_ENTERPRISE", False)`:

| method | path | expected_status | mock / notes |
|---|---|---|---|
| POST | `/v1/analytics/ai-adoption-overview` | 200 | mock `AIAdoptionHandler.get_overview` |
| GET | `/v1/analytics/leaderboard/summary` | 503 | `LEADERBOARD_ENABLED=False`; assert `detail == "Leaderboard is not enabled"` |
| GET | `/v1/analytics/leaderboard/entries` | 503 | same assertion on detail |
| GET | `/v1/analytics/spending` | 200 | mock `get_customer_spending` at `codemie.enterprise.litellm.dependencies` |
| GET | `/v1/analytics/budget_usage` | 200 | mock `budget_usage_service.get_budget_usage` |
| GET | `/v1/analytics/user-project-spending` | 200 | mock `member_spend_service.get_user_project_spend`; include valid required params |
| GET | `/v1/analytics/project-member-spending` | 200 | mock `member_spend_service.get_project_member_spend`; include valid required params |

For the two leaderboard entries, additionally assert `response.json()["detail"] == "Leaderboard is not enabled"` — confirming the source is `_require_leaderboard_enabled`, not `_require_metrics_analytics_enabled`.

**Suite 3 — AI Adoption lazy construction**

Single async test `test_ai_adoption_does_not_construct_metrics_elastic_repository`:
1. `patch("codemie.enterprise.loader.HAS_ENTERPRISE", False)`
2. Patch `MetricsElasticRepository.__init__` as spy (returns `None`)
3. Construct real `AnalyticsService(mock_user)` — no mock on `__init__`
4. Call `service.get_ai_adoption_overview(projects=None, config=None)` with only the inner `AIAdoptionHandler` DB call mocked via `patch.object`
5. Assert `MetricsElasticRepository.__init__` was never called

- [ ] Create file; build `GROUP_A_PATHS` list (59 entries)
- [ ] Implement Suite 1
- [ ] Implement Suite 2 (7 entries; all 4 Group D routes covered with the mocks above)
- [ ] Implement Suite 3
- [ ] Run: `pytest tests/codemie/rest_api/routers/test_analytics_capability_guards.py -v`

---

### Task 6: Config projection tests

**Test-first: no — regression/extension; existing behavior already passes**

**Files:**
- Modify: `tests/codemie/service/test_customer_config_service.py`

Add to the existing file:

**`features:metricsAnalytics` parametrized test:**
`@pytest.mark.parametrize("has_ent,expected_present", [(True, True), (False, False)])` — patch `codemie.enterprise.loader.HAS_ENTERPRISE` to the given value; call `resolve_components()`; assert `features:metricsAnalytics` present/absent in the resolved list accordingly.

**Existing projections regression — assert already-committed entries still resolve correctly:**
- `conversationAnalytics`: absent when `CONVERSATION_ANALYSIS_ENABLED=False`; present when `True`
- `smartToolSelection`: absent when `TOOL_SELECTION_ENABLED=False`; present when `True`
- `aiChampionsLeaderboard` ceiling: absent when `LEADERBOARD_ENABLED=False`; present when `True` (the `True` case requires the YAML fixture to define `aiChampionsLeaderboard` — check how existing tests load `customer-config.yaml` in this file and replicate that fixture pattern)

- [ ] Add `features:metricsAnalytics` parametrized test
- [ ] Add regression assertions for `conversationAnalytics`, `smartToolSelection`, `aiChampionsLeaderboard`
- [ ] Run: `pytest tests/codemie/service/test_customer_config_service.py -v`

---

### Task 7: CR-3 — Rewrite `test_get_tools_by_query_handles_malformed_data`

**Test-first: no — test rewrite; corrects existing test**

**Files:**
- Modify: `tests/codemie/service/tools/test_toolkit_lookup_service.py`

Delete the existing `test_get_tools_by_query_handles_malformed_data`. Write its replacement:

> **Critical design constraint:** The production path reads `doc.metadata` **before** the per-document reconstruction `try` block. A plain dict raises `AttributeError` at that read and never enters the reconstruction path — it does not test malformed-metadata handling. The test must use a real `Document` object.

Replacement test steps:
1. Patch `config.TOOL_SELECTION_ENABLED = True` — prevents the committed early-return guard from short-circuiting before `SearchAndRerankTool` is reached
2. Patch `SearchAndRerankTool` at the class level so `mock_class.return_value` is the mock instance
3. Set `mock_instance.execute.return_value` to a list containing one real `Document` object constructed with malformed/incomplete metadata — specifically, missing the keys that the per-document reconstruction code reads (inspect the reconstruction loop to identify those keys; omit them from the `Document`'s `metadata` dict)
4. Call `service.get_tools_by_query("test query")`
5. Assert `mock_class.assert_called_once()` — class instantiated exactly once
6. Assert `mock_instance.execute.assert_called_once()` — `.execute()` called exactly once
7. Assert return value `== []` — malformed Document metadata produces empty list

- [ ] Delete existing test
- [ ] Identify the metadata keys the reconstruction loop reads (read `toolkit_lookup_service.py` reconstruction block; do not guess)
- [ ] Write replacement per the 7-step design above
- [ ] Run: `pytest tests/codemie/service/tools/test_toolkit_lookup_service.py::test_get_tools_by_query_handles_malformed_data -v`

---

### Task 8: CR-4 — Retire `test_ai_adoption_overview_200_without_enterprise`; add D16 isolation test; `ADMIN_LOG_LOOKUP_ENABLED` deployment note

**Test-first: no — test rewrite; corrects existing test**

**Files:**
- Modify: file containing `test_ai_adoption_overview_200_without_enterprise` (locate with `grep -rn "test_ai_adoption_overview_200_without_enterprise" tests/`)
- Modify: `src/codemie/configs/config.py` (comment only)

**Test replacement:**

Delete `test_ai_adoption_overview_200_without_enterprise`. Add `test_ai_adoption_overview_without_enterprise_does_not_construct_es_repo`:
1. `patch("codemie.enterprise.loader.HAS_ENTERPRISE", False)`
2. Patch `MetricsElasticRepository.__init__` as spy (returns `None`)
3. Do **not** mock `AnalyticsService.__init__` — real construction is required to prove D16 is fixed
4. Mock only `AIAdoptionHandler`'s inner DB call via `patch.object` on the handler method
5. Construct real `AnalyticsService(mock_user)`; call `get_ai_adoption_overview(projects=None, config=None)`
6. Assert `MetricsElasticRepository.__init__` was **never** called
7. Assert AI Adoption result matches the mocked handler's return value

**`ADMIN_LOG_LOOKUP_ENABLED` deployment note (`config.py`):**

The `ADMIN_LOG_LOOKUP_ENABLED` field is already committed. Add an inline comment noting its `True` default is a backward-compatibility exception — all other capability flags default `False` (opt-in); standalone/PostgreSQL-only deployments must explicitly set this to `False`, a process restart is required for the change to take effect. This is a documentation-only change; no behavioral impact.

- [ ] Locate and delete `test_ai_adoption_overview_200_without_enterprise`
- [ ] Write replacement test per the 7-step design above
- [ ] Add deployment comment to `ADMIN_LOG_LOOKUP_ENABLED` in `config.py`
- [ ] Run: `pytest` on the containing test file — full suite must pass

---

## Negative-constraint pass

| Non-goal from spec | Task that honors it |
|---|---|
| No `ELASTICSEARCH_ENABLED` global flag | No task introduces one |
| No `METRICS_ANALYTICS_ENABLED` env flag | T3/T4 use `has_enterprise()` only |
| No new capability framework or registry | Each task adds minimal targeted changes |
| No dynamic ES health-check or ping | No task calls `ElasticSearchClient` for probing |
| Stale-Datasource Detection: no code changes | No task modifies stale-datasource files |
| Conversation Analytics trigger stays 400 | No task touches the trigger handler |
| No `adminLogLookup`/`staleDatasourceDetection` in `GET /v1/config` | T4 adds exactly `features:metricsAnalytics` |
| Analytics router not unregistered | T3 adds `Depends()` only; `main.py` untouched |
| No DB migrations | No task adds migration files |
| No `SettingDeclaration` override for `aiChampionsLeaderboard` | Ceiling already committed; T4 confirms, not re-adds |
| No private enterprise helper in `customer_config.py` | T1 places `has_enterprise()` in `loader.py` |
| No changes to existing `GET /v1/config` components | T4/T6 add/verify new entries only |
| No 400→503 standardization | No task modifies Conversation Analytics status code |
| D16 fix included | T2 makes `_repository` lazy |
| Already-committed guards not re-implemented | No task re-adds `ADMIN_LOG_LOOKUP_ENABLED` flag, `POST /logs` guard, or `get_tools_by_query()` early return |

**negative-constraints: all stated non-goals verified; no task violates any.**
