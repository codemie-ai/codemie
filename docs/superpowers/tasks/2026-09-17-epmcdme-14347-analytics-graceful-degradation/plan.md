# EPMCDME-14347 Analytics Graceful Degradation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Enterprise-package-based 503 gate on Metrics Analytics with connector-based graceful
degradation: ES-backed and ClickHouse-backed read endpoints return HTTP 200 with schema-valid empty payloads
when their connector is absent/unavailable, while auth/authz/validation and the real-data happy path are
untouched.

**Architecture:** Both connectors already have a single call-time chokepoint that every read endpoint funnels
through — `MetricsElasticRepository.execute_{esql,aggregation,search}_query` for ES, and `ch_query` (bound
into `LocalAnalyticsRepository`) for ClickHouse. `MetricsElasticRepository` already has a precedent for this
exact pattern: `execute_aggregation_query`/`execute_search_query` catch `NotFoundError` and return an
empty-shaped ES response instead of raising. We extend that same branch to config-absence and
connection-failure conditions — but **only when the caller opted in**. `MetricsElasticRepository` is not
exclusive to Analytics UI reads: `LeaderboardService`/`LeaderboardScheduler`/`LeaderboardHandler`'s background
computation and `StaleDatasourceService`/its scheduler also construct it directly, and those background/state
-mutating consumers must keep treating an ES outage as a failure, not as "zero data" (an outage misread as
zero data there risks incorrectly persisting empty leaderboard snapshots or deleting datasources that are only
unreachable, not actually stale). So the fallback is a constructor-level opt-in flag defaulting to OFF
(current fail-closed behavior unchanged everywhere by default); only `AnalyticsService`'s lazy `_repository`
property turns it ON. Because every downstream handler already tolerates "no data" (empty ES buckets, empty
ClickHouse row lists — confirmed via `AnalyticsQueryPipeline`'s bucket/row handling and
`cli_analytics_handler.py`'s `_first`/`_i`/`_f` helpers, which already default missing/empty values to zero),
no per-endpoint empty-response builder is needed: fixing the two chokepoints propagates a schema-valid empty
response through the existing `ResponseFormatter`/handler code for all affected endpoints. Then the
`has_enterprise()`-based 503 gate is deleted outright since the two chokepoints now degrade gracefully (for
the callers that opted in) on their own.

**Tech Stack:** FastAPI, Pydantic, `elasticsearch`/`elastic_transport` async client, `clickhouse_connect`, pytest/httpx (AsyncClient + ASGITransport), unittest.mock.

## Global Constraints

- Backend repo (`codemie`) only. Do not touch `codemie-sdk`.
- Do not modify `FEATURE_CLI_ANALYTICS`, `standalone/.env.standalone`, `standalone/.env.standalone.example`,
  or the `features:cliAnalytics` entry in `customer-config.yaml` — reading them is fine, relocating them is not.
- Scope is read/query endpoints only; ingestion endpoints (`/logs`, `/metrics`, `/traces`, `/event-hooks` in
  `cli_analytics.py`) are untouched.
- Never turn 401/403/422 into 200.
- Never change the payload returned when ES/ClickHouse ARE available.
- Preserve the lazy `AnalyticsService._repository` property (`analytics_service.py:78-83`) — PostgreSQL-only
  and ClickHouse-only paths must never eagerly construct `MetricsElasticRepository`.
- ES connector-unavailable fallback (return empty instead of raising) is opt-in and Analytics-HTTP-read-path
  only. `LeaderboardService`/`LeaderboardScheduler`/`LeaderboardHandler`'s background computation and
  `StaleDatasourceService`/its scheduler must keep their existing fail-closed behavior unchanged — they must
  never construct `MetricsElasticRepository` with the fallback enabled.
- Every commit uses the repo's existing commit convention; stage only the specific files each task touches
  (`git add <path> <path>`, never `git add -A`/`git add .` — the worktree has 6 pre-existing unrelated dirty
  files that must not be staged).

---

## Acceptance criteria

- [ ] `_require_metrics_analytics_enabled` and its `has_enterprise()` coupling are removed from all 59
      `dependencies=[Depends(_require_metrics_analytics_enabled)]` attachment points in
      `src/codemie/rest_api/routers/analytics.py`; Metrics Analytics availability no longer depends on the
      `codemie-enterprise` package being installed.
- [ ] ES-backed Analytics read endpoints return HTTP 200 with a schema-valid empty payload (built through
      each endpoint's real response model/pipeline, not a generic empty object) when Elasticsearch is not
      configured or unreachable.
- [ ] The ES fallback is opt-in and scoped to the Analytics HTTP read path: only the repository instance built
      by `AnalyticsService._repository` returns empty results on ES-unavailable. `LeaderboardService`,
      `LeaderboardScheduler`, `LeaderboardHandler`'s background computation, `StaleDatasourceService`, and its
      scheduler continue to construct `MetricsElasticRepository` in its default (fail-closed) mode and continue
      to propagate/convert connection failures exactly as before.
- [ ] ClickHouse-backed CLI Analytics read endpoints (`cli_analytics.py`) return HTTP 200 with a schema-valid
      empty payload, including required non-defaulted fields (`LocalAnalyticsCostKPIs`), when
      `features:cliAnalytics` is enabled but ClickHouse is not configured or unreachable.
- [ ] Auth (401), authorization (403), and request-validation (422) errors are unchanged and never converted
      to 200.
- [ ] Real-data payloads are byte-identical to before this change when ES/ClickHouse ARE available.
- [ ] AI/Run Adoption PostgreSQL paths never construct/connect to Elasticsearch (lazy `_repository` property
      preserved and regression-tested).
- [ ] `tests/codemie/rest_api/routers/test_analytics_capability_guards.py` is rewritten to the new
      connector-based contract with no coverage silently dropped (Suite 1 replaced, Suites 2/3 preserved).

---

### Task 1: Opt-in Elasticsearch connector-unavailable fallback in `MetricsElasticRepository`

**Files:**
- Modify: `src/codemie/clients/elasticsearch.py:22-56`
- Modify: `src/codemie/repository/metrics_elastic_repository.py:64-127` (`__init__`, new helper),
  `:129-185` (`execute_aggregation_query`), `:187-` (`execute_search_query`), and `execute_esql_query`
- Modify: `src/codemie/service/analytics/analytics_service.py:82` (opt the Analytics repository in)
- Test: `tests/codemie/repository/test_metrics_elastic_repository.py`

**Call-site enumeration (verified via grep, `MetricsElasticRepository(` across `src/`):**
1. `src/codemie/service/analytics/analytics_service.py:82` — `AnalyticsService._repository` lazy property.
   **This is the only site that turns the opt-in flag ON** — it backs every Analytics HTTP read handler.
2. `src/codemie/service/leaderboard/leaderboard_service.py:90` — `LeaderboardService.__init__` default
   argument (`es_repository or MetricsElasticRepository()`). Stays on the default (fail-closed).
3. `src/codemie/service/leaderboard/scheduler.py:86` — nightly leaderboard computation job. Stays default.
4. `src/codemie/service/analytics/handlers/leaderboard_handler.py:645` —
   `LeaderboardHandler._run_computation`, the background task started by the (HTTP-reachable)
   `trigger_computation` endpoint; it performs a state-mutating snapshot write, not a read, so it must not
   silently treat an ES outage as "compute from zero data". Stays default.
5. `src/codemie/service/stale_datasource/scheduler.py:74` — nightly stale-datasource detection/deletion job.
   Stays default.

Sites 2-5 require **no code change** — they already call `MetricsElasticRepository()` with no argument, so
they inherit whatever the new parameter's default is. The default must be `False` (fail-closed) precisely so
these four sites are unaffected by construction alone.

**Interfaces:**
- Produces: `ElasticSearchClient.is_configured() -> bool`, returning
  `retrieval_available(config) and bool(config.ELASTIC_URL)` — not `bool(config.ELASTIC_URL)` alone, because
  `ELASTIC_URL` defaults to `http://localhost:9200` even when `RETRIEVAL_BACKEND=none` (the standalone
  no-ES configuration). `retrieval_available` already exists at `src/codemie/configs/config.py:1081-1082`.
- Produces: `MetricsElasticRepository.__init__(self, *, fail_open_on_unavailable: bool = False)`. When
  `is_configured()` is `False` at construction time, the repository stores `self._client = None` instead of
  calling `ElasticSearchClient.get_async_client()` — so an absent/invalid `ELASTIC_URL` never reaches (and
  cannot fail inside) the ES client, regardless of which mode the repository is in.
- Produces: the three `execute_*` methods route every "client is `None`" and `(ConnectionError,
  ConnectionTimeout)` condition through one shared helper. When `fail_open_on_unavailable` is `True` the
  helper returns an empty-shaped dict appropriate to each method: `execute_aggregation_query`/
  `execute_search_query` return the same empty-shaped dict they already return on `NotFoundError`
  (`hits`/`aggregations`-style), while `execute_esql_query` returns ES|QL's own native empty response shape,
  `{"columns": [], "values": []}` — not the aggregation shape, since ES|QL responses never carry
  `hits`/`aggregations`. When `False` it raises the existing `ExtendedHTTPException(503, ...)` shape (same
  message/details/help constants the `ApiError` branch already uses), i.e. exactly the failure conversion
  that happens today.

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/repository/test_metrics_elastic_repository.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest
from elasticsearch import ConnectionError as ESConnectionError

from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository


@pytest.mark.anyio
async def test_opt_in_repository_returns_empty_when_es_not_configured():
    with patch("codemie.repository.metrics_elastic_repository.ElasticSearchClient") as mock_client_cls:
        mock_client_cls.is_configured.return_value = False
        repo = MetricsElasticRepository(fail_open_on_unavailable=True)
        agg = await repo.execute_aggregation_query({"query": {"match_all": {}}})
        esql = await repo.execute_esql_query("FROM x")
        search = await repo.execute_search_query({"match_all": {}})
    assert agg == {"hits": {"hits": [], "total": {"value": 0}}, "aggregations": {}}
    assert esql == {"columns": [], "values": []}
    assert search == {"hits": {"hits": [], "total": {"value": 0}}}


@pytest.mark.anyio
async def test_opt_in_repository_returns_empty_on_connection_error():
    with patch("codemie.repository.metrics_elastic_repository.ElasticSearchClient") as mock_client_cls:
        mock_client_cls.is_configured.return_value = True
        mock_async_client = AsyncMock()
        mock_async_client.search.side_effect = ESConnectionError("refused")
        mock_client_cls.get_async_client.return_value = mock_async_client
        repo = MetricsElasticRepository(fail_open_on_unavailable=True)
        result = await repo.execute_aggregation_query({"query": {"match_all": {}}})
    assert result == {"hits": {"hits": [], "total": {"value": 0}}, "aggregations": {}}


@pytest.mark.anyio
async def test_default_repository_still_raises_when_es_not_configured():
    """Default (opt-out) mode — the mode Leaderboard/StaleDatasource callers use — stays fail-closed."""
    with patch("codemie.repository.metrics_elastic_repository.ElasticSearchClient") as mock_client_cls:
        mock_client_cls.is_configured.return_value = False
        repo = MetricsElasticRepository()
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await repo.execute_aggregation_query({"query": {"match_all": {}}})
    assert exc_info.value.code == 503


@pytest.mark.anyio
async def test_default_repository_still_raises_on_connection_error():
    with patch("codemie.repository.metrics_elastic_repository.ElasticSearchClient") as mock_client_cls:
        mock_client_cls.is_configured.return_value = True
        mock_async_client = AsyncMock()
        mock_async_client.search.side_effect = ESConnectionError("refused")
        mock_client_cls.get_async_client.return_value = mock_async_client
        repo = MetricsElasticRepository()
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await repo.execute_aggregation_query({"query": {"match_all": {}}})
    assert exc_info.value.code == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/repository/test_metrics_elastic_repository.py -k "opt_in_repository or default_repository" -v`
Expected: FAIL (`TypeError: unexpected keyword argument 'fail_open_on_unavailable'` / `AttributeError:
is_configured` / real client construction attempted).

- [ ] **Step 3: Implement the fallback**

In `elasticsearch.py`, add to `ElasticSearchClient` (import `retrieval_available` from
`codemie.configs.config`):

```python
    @classmethod
    def is_configured(cls) -> bool:
        """ES is configured only when the retrieval backend is enabled and a URL is set."""
        return retrieval_available(config) and bool(config.ELASTIC_URL)
```

In `metrics_elastic_repository.py`: import `ConnectionError as ESConnectionError, ConnectionTimeout` from
`elasticsearch` (Python's builtin `ConnectionError` must not be shadowed — alias it). Change `__init__` to
take the opt-in flag and avoid constructing the client when unconfigured:

```python
    def __init__(self, *, fail_open_on_unavailable: bool = False):
        """Initialize repository with async Elasticsearch client.

        Args:
            fail_open_on_unavailable: When True, an unconfigured/unreachable ES returns an
                empty-shaped result instead of raising. Only AnalyticsService's Analytics HTTP
                read path opts in; every other caller (LeaderboardService/Scheduler,
                LeaderboardHandler's background computation, StaleDatasourceService/Scheduler)
                keeps the default fail-closed behavior.
        """
        self._fail_open_on_unavailable = fail_open_on_unavailable
        self._index = config.ELASTIC_METRICS_INDEX
        self._client = ElasticSearchClient.get_async_client() if ElasticSearchClient.is_configured() else None
```

Add one shared helper used by all three `execute_*` methods:

```python
    def _handle_unavailable(self, empty_result: dict, *, reason: str):
        if self._fail_open_on_unavailable:
            logger.warning(f"Elasticsearch unavailable ({reason}), returning empty results")
            return empty_result
        logger.exception(f"Elasticsearch unavailable ({reason})")
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=ANALYTICS_QUERY_FAILED_MSG,
            details=f"{ES_SERVICE_ERROR_DETAILS_PREFIX}: {reason}",
            help=ANALYTICS_QUERY_HELP_MSG,
        )
```

At the top of each of the three `execute_*` methods, before the `try:` block, add
`if self._client is None: return self._handle_unavailable(<method's empty shape>, reason="not configured")`.
For `execute_aggregation_query`/`execute_search_query`, `<method's empty shape>` is the same
`hits`/`aggregations`-style dict each already returns on `NotFoundError`. For `execute_esql_query`,
`<method's empty shape>` is ES|QL's own native empty response, `{"columns": [], "values": []}` — do not reuse
the aggregation shape there. Then add a new `except (ESConnectionError, ConnectionTimeout) as e:` branch in
each method, placed before the existing `except ApiError` branch, calling
`return self._handle_unavailable(<method's empty shape>, reason=str(e))` with that same per-method shape.
Do not touch the existing `NotFoundError`/`ApiError`/generic `Exception` branches — the connector-unavailable
branch is additive only.

Then, in `analytics_service.py:82`, change the single construction call to opt in:

```python
            self._repository_instance = MetricsElasticRepository(fail_open_on_unavailable=True)
```

Do not change any of the other four construction sites (Task 1's call-site enumeration above) — they keep
calling `MetricsElasticRepository()` with no arguments, which now resolves to `fail_open_on_unavailable=False`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/repository/test_metrics_elastic_repository.py -v`
Expected: PASS, including all pre-existing tests in the file (no regression).

Run: `poetry run pytest tests/codemie/service/leaderboard tests/codemie/service/stale_datasource -v`
Expected: PASS unchanged — these suites construct `MetricsElasticRepository()` with no flag (or mock it
directly) and must show no behavior change from this task.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/clients/elasticsearch.py src/codemie/repository/metrics_elastic_repository.py src/codemie/service/analytics/analytics_service.py tests/codemie/repository/test_metrics_elastic_repository.py
```
Commit per the repository's existing convention.

---

### Task 2: ClickHouse connector-unavailable fallback in `ch_query`

**Files:**
- Modify: `src/codemie/clients/clickhouse.py`
- Test: `tests/codemie/clients/test_clickhouse.py`

**Interfaces:**
- Produces: `is_clickhouse_configured() -> bool` (checks `bool(config.CLICKHOUSE_HOST)`).
- Produces: `ch_query` returns `[]` (instead of raising) when ClickHouse is unconfigured or unreachable. Every
  `LocalAnalyticsRepository.get_*` method already returns whatever `ch_query` returns unmodified, and
  `cli_analytics_handler.py`'s `_first()`/`_i()`/`_f()` helpers (lines 49-83) already default an empty row
  list to zero-valued fields, so this single change is sufficient for the CLI Analytics empty-payload
  contract — including `LocalAnalyticsCostKPIs`'s required fields. Unlike Task 1, this fallback is
  unconditional/centralized (not opt-in) because `ch_query` is currently scoped exclusively to the CLI
  Analytics repository — no background/state-mutating consumer of `ch_query` exists today.

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/clients/test_clickhouse.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from codemie.clients.clickhouse import ch_query, is_clickhouse_configured


def test_is_clickhouse_configured_false_when_host_blank():
    with patch("codemie.clients.clickhouse.config") as mock_config:
        mock_config.CLICKHOUSE_HOST = ""
        assert is_clickhouse_configured() is False


@pytest.mark.anyio
async def test_ch_query_returns_empty_list_when_not_configured():
    with patch("codemie.clients.clickhouse.is_clickhouse_configured", return_value=False):
        result = await ch_query("SELECT 1", {})
    assert result == []


@pytest.mark.anyio
async def test_ch_query_returns_empty_list_on_connection_failure():
    with (
        patch("codemie.clients.clickhouse.is_clickhouse_configured", return_value=True),
        patch("codemie.clients.clickhouse.get_client") as mock_get_client,
    ):
        mock_get_client.return_value.query.side_effect = OSError("connection refused")
        result = await ch_query("SELECT 1", {})
    assert result == []


@pytest.mark.anyio
async def test_ch_query_propagates_sql_and_programming_errors():
    """The connector-unavailable fallback must not swallow SQL/query/programming errors."""
    with (
        patch("codemie.clients.clickhouse.is_clickhouse_configured", return_value=True),
        patch("codemie.clients.clickhouse.get_client") as mock_get_client,
    ):
        mock_get_client.return_value.query.side_effect = ValueError("malformed SQL")
        with pytest.raises(ValueError):
            await ch_query("SELECT 1", {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/clients/test_clickhouse.py -k "clickhouse_configured or ch_query_returns_empty or ch_query_propagates" -v`
Expected: FAIL (`ImportError: is_clickhouse_configured` / exception propagates or is wrongly swallowed).

- [ ] **Step 3: Implement the fallback**

In `clickhouse.py`, add:

```python
def is_clickhouse_configured() -> bool:
    """ClickHouse is considered configured only when a host is set."""
    return bool(config.CLICKHOUSE_HOST)
```

Rewrite `ch_query` to check configuration first, then wrap the blocking call and catch **only**
connection/transport-level exceptions — verify the exact `clickhouse_connect` exception class against the
version pinned in `pyproject.toml`/`poetry.lock` (its docs name
`clickhouse_connect.driver.exceptions.OperationalError` as the connection/auth failure type, distinct from
`DatabaseError`/`ProgrammingError`-style query errors, which must keep propagating):

```python
async def ch_query(sql: str, params: dict) -> list[dict]:
    if not is_clickhouse_configured():
        logging.getLogger(__name__).warning("ClickHouse not configured, returning empty results")
        return []

    def _run() -> list[dict]:
        return list(get_client().query(sql, parameters=params).named_results())

    try:
        return await asyncio.to_thread(_run)
    except (OSError, clickhouse_connect.driver.exceptions.OperationalError) as e:
        logging.getLogger(__name__).warning("ClickHouse unreachable, returning empty results: %s", e)
        return []
```

Add a module-level `logger = logging.getLogger(__name__)` instead of the inline call if the file doesn't
already have one, and `import logging` at the top. Confirm during implementation that
`OperationalError`/`OSError` do not also cover SQL syntax or programming errors in the installed
`clickhouse_connect` version — if the library raises a shared base class for both connection and query
errors, narrow the `except` to the connection-specific subclass/attribute instead of widening it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/clients/test_clickhouse.py -v`
Expected: PASS, including pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/clients/clickhouse.py tests/codemie/clients/test_clickhouse.py
```
Commit per the repository's existing convention.

---

### Task 3: Remove the `has_enterprise()`-based Metrics Analytics guard and rewrite the capability-guard test suite

**Files:**
- Modify: `src/codemie/rest_api/routers/analytics.py` (delete `_require_metrics_analytics_enabled` at
  `:677-685`; remove the `dependencies=[Depends(_require_metrics_analytics_enabled)]` line from all 59
  decorator sites at lines 695,777,803,829,855,881,907,933,959,985,1011,1037,1063,1089,1115,1141,1167,1193,
  1219,1245,1271,1326,1411,1437,1498,1526,1552,1578,1604,1630,1656,1682,1708,1734,1760,1786,1812,1838,1864,
  1890,1916,1942,1975,2008,2041,2074,2107,2140,2173,2206,2232,2301,2315,2329,2343,2357,3408,3438,3466 — if a
  decorator's only `dependencies=[...]` entry is this one, remove the whole `dependencies=[...]` kwarg line;
  if others are present, remove only this entry)
- Test: `tests/codemie/rest_api/routers/test_analytics_capability_guards.py` (full rewrite of Suite 1;
  Suites 2 and 3 keep their existing assertions unchanged)

**Interfaces:**
- Consumes: `ElasticSearchClient.is_configured` (Task 1) as the mock seam for "ES absent" in the rewritten
  Suite 1. Because every Group A router path is served through `AnalyticsService._repository`, which Task 1
  now constructs with `fail_open_on_unavailable=True`, mocking `is_configured() -> False` at this layer is
  sufficient to exercise the opt-in empty-payload path through the router.

- [ ] **Step 1: Write the failing test (rewritten Suite 1)**

Replace the docstring and Suite 1 block (lines 15-26 and 67-155) in
`tests/codemie/rest_api/routers/test_analytics_capability_guards.py`:

```python
"""Parametrized regression suites for analytics capability guards.

Suite 1 — Group A (59 paths): every Metrics Analytics route returns 200 with a
          schema-valid empty payload when Elasticsearch is unconfigured — no
          Enterprise-package gate exists anymore. Exercises the opt-in fallback
          via AnalyticsService's repository construction.

Suite 2 — Group B/C/D (7 entries): routes outside Group A are unaffected by
          the (now-removed) enterprise guard.

Suite 3 — AI Adoption lazy construction: accessing the AI Adoption path does
          not trigger MetricsElasticRepository construction.

Suite 4 — Auth/authz/validation are unchanged when ES is unavailable.
"""
```

Keep `GROUP_A_PATHS` and its `assert len(GROUP_A_PATHS) == 59` as-is. Replace
`test_group_a_returns_503_when_enterprise_absent` with:

```python
@pytest.mark.anyio
@pytest.mark.parametrize("path", GROUP_A_PATHS)
async def test_group_a_returns_200_empty_when_elasticsearch_unconfigured(path):
    """Every Group A path returns 200 with an empty payload when ES is unconfigured."""
    with patch(
        "codemie.repository.metrics_elastic_repository.ElasticSearchClient.is_configured",
        return_value=False,
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(path)

    assert response.status_code == 200
    body = response.json()
    assert "data" in body
```

Add a Suite 4 auth/authz/validation regression test appended to the file:

```python
@pytest.mark.anyio
async def test_group_a_path_still_401s_without_auth_when_es_unavailable():
    """Auth failures are not converted to 200 by the ES-unavailable fallback."""
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    # No dependency_overrides for `authenticate`: the real dependency runs and must 401/403.

    with patch(
        "codemie.repository.metrics_elastic_repository.ElasticSearchClient.is_configured",
        return_value=False,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/analytics/summaries")

    assert response.status_code in (401, 403)


@pytest.mark.anyio
async def test_group_a_path_still_422s_on_invalid_query_param_when_es_unavailable():
    """Validation failures are not converted to 200 by the ES-unavailable fallback."""
    with patch(
        "codemie.repository.metrics_elastic_repository.ElasticSearchClient.is_configured",
        return_value=False,
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/analytics/summaries?page=not-a-number")

    assert response.status_code == 422
```

(If `page` is not query-validated on `/summaries`, substitute any Group A path with a typed query
param — check the router signature and use that path/param instead; the assertion contract is what matters.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_analytics_capability_guards.py -k test_group_a_returns_200_empty -v`
Expected: FAIL with 503 (guard still active).

- [ ] **Step 3: Remove the guard**

In `analytics.py`, delete the `_require_metrics_analytics_enabled` function definition and its docstring
(`:677-685`), and delete the `dependencies=[Depends(_require_metrics_analytics_enabled)]` line from each of
the 59 decorator sites listed above. Remove the now-unused `has_enterprise` import from `analytics.py` if
nothing else in the file uses it (grep the file for other `has_enterprise` references first).

- [ ] **Step 4: Run the full capability-guard file to verify it passes**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_analytics_capability_guards.py -v`
Expected: PASS — all of Suite 1 (59 parametrized), Suite 2 (7 tests, unchanged), Suite 3 (1 test, unchanged),
and the new Suite 4 (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/routers/analytics.py tests/codemie/rest_api/routers/test_analytics_capability_guards.py
```
Commit per the repository's existing convention.

---

### Task 4: End-to-end response-shape regression tests (ES happy path + CLI Analytics ClickHouse fallback)

**Files:**
- Test: `tests/codemie/rest_api/routers/test_analytics_capability_guards.py` (ES happy-path-unchanged
  assertion, one representative summary + one tabular endpoint)
- Test: `tests/codemie/rest_api/routers/test_cli_analytics_router.py` (new — or the closest existing router
  test file for `cli_analytics.py`; check for one before creating it)

**Interfaces:**
- Consumes: `is_clickhouse_configured` (Task 2) as the mock seam for "ClickHouse absent".
- Consumes: the opt-in `MetricsElasticRepository(fail_open_on_unavailable=True)` construction (Task 1) that
  `AnalyticsService._repository` now performs — the happy-path test below exercises that same construction
  path with ES reachable, proving the opt-in flag never alters the real-data response.

- [ ] **Step 1: Write the failing tests**

Append to `test_analytics_capability_guards.py` — happy path must stay byte-identical when ES IS available:

```python
@pytest.mark.anyio
async def test_summaries_payload_unchanged_when_elasticsearch_available():
    """Real data payload is untouched by the fallback boundary when ES is configured and reachable."""
    mock_result = {
        "data": {"metrics": [{"name": "total_conversations", "value": 42, "unit": "count"}]},
        "metadata": {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "data_as_of": "2026-01-01T00:00:00+00:00",
            "filters_applied": {},
            "execution_time_ms": 12.3,
        },
    }
    with (
        patch(
            "codemie.repository.metrics_elastic_repository.ElasticSearchClient.is_configured",
            return_value=True,
        ),
        patch(
            "codemie.service.analytics.analytics_service.AnalyticsService.get_summaries",
            new_callable=AsyncMock,
            return_value=mock_result,
        ),
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/analytics/summaries")

    assert response.status_code == 200
    assert response.json()["data"]["metrics"] == [{"name": "total_conversations", "value": 42, "unit": "count"}]
```

Locate the CLI Analytics router's cost-KPI endpoint path in `cli_analytics.py` (one of the `@router.get(...)`
decorators at lines 463-694) and its handler method on `LocalAnalyticsHandler`, then write (adjust names to
match what's actually there):

```python
@pytest.mark.anyio
async def test_cli_analytics_cost_kpis_empty_when_clickhouse_unavailable():
    """CLI Analytics cost endpoint returns 200 with zero-valued KPIs when ClickHouse is unreachable."""
    with patch("codemie.clients.clickhouse.is_clickhouse_configured", return_value=False):
        transport = ASGITransport(app=<cli_analytics_app_or_APP>)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/analytics/cli-analytics/<cost-endpoint-path>")

    assert response.status_code == 200
    body = response.json()
    kpis = body["data"]["kpis"]
    assert kpis["total_sessions"] == 0
    assert kpis["total_cost_usd"] == 0.0
```

Build the test app the same way the existing `cli_analytics.py` router tests do (reuse an existing fixture if
one exists in the test suite — check for `tests/codemie/rest_api/routers/test_cli_analytics*.py` before
writing a new harness) with `features:cliAnalytics` enabled and the admin gate satisfied via
`dependency_overrides`, matching the pattern already in this file for `authenticate`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_analytics_capability_guards.py -k unchanged_when_elasticsearch_available -v`
and the new CLI Analytics test file/target.
Expected: FAIL before Tasks 1-3 land in the working tree only if run standalone; if run after Tasks 1-3 this
step instead confirms the CLI Analytics test fails first (Task 2's fallback exists but no test exercised it
at the router layer yet).

- [ ] **Step 3: No production code change required**

This task only adds coverage confirming Tasks 1-3's mechanism holds end-to-end through the router layer for
both connectors' representative response shapes (summary/tabular for ES, KPI-with-required-fields for
ClickHouse). If a gap is found (e.g. a response model field not zero-defaulted), fix it at its source file
(`metrics_elastic_repository.py`, `clickhouse.py`, or the specific handler) before proceeding — do not weaken
the test.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_analytics_capability_guards.py tests/codemie/rest_api/routers/test_cli_analytics_router.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/codemie/rest_api/routers/test_analytics_capability_guards.py tests/codemie/rest_api/routers/test_cli_analytics_router.py
```
Commit per the repository's existing convention.

---

## Risk / open-question record

- **Requirement 6 (LeaderboardHandler Enterprise import) — resolved, non-blocking.** Direct grep of
  `src/codemie/service/analytics/handlers/leaderboard_handler.py` for `enterprise` found no match:
  `LeaderboardHandler` has no direct Enterprise-package import on its read path. No task in this plan
  modifies `LeaderboardHandler` for this reason. Leaderboard's own `LEADERBOARD_ENABLED` guard (unrelated to
  `_require_metrics_analytics_enabled`, already covered by Suite 2 in
  `test_analytics_capability_guards.py`) is untouched.
- **`clickhouse_connect`'s exact connection-exception class** could not be verified against an installed
  package in this environment (not present under the local `site-packages`). Task 2 names
  `clickhouse_connect.driver.exceptions.OperationalError` as the expected type based on the library's public
  documentation; the implementer must confirm the exact class name against the version pinned in
  `pyproject.toml`/`poetry.lock` during Task 2 and adjust the `except` clause accordingly — never widen it to
  bare `Exception`. Task 2 now also carries an explicit test
  (`test_ch_query_propagates_sql_and_programming_errors`) proving a non-connection exception (e.g.
  `ValueError` standing in for a query/programming error) is NOT swallowed by the fallback.
- **`MetricsElasticRepository` is shared with non-Analytics-UI consumers** — resolved by making the fallback
  an opt-in constructor flag (Task 1) defaulting to fail-closed. `LeaderboardService`/`LeaderboardScheduler`/
  `LeaderboardHandler`'s background computation and `StaleDatasourceService`/its scheduler (enumerated by
  direct grep in Task 1) all construct the repository with no argument and are therefore structurally
  incapable of receiving empty-on-outage behavior; only `AnalyticsService._repository` passes
  `fail_open_on_unavailable=True`. Task 1 adds direct tests for both modes at the repository level.
- **negative-constraints:** (1) "never turn 401/403/422 into 200" — honored because Tasks 1-2 only change
  behavior inside `MetricsElasticRepository`/`ch_query`, which run strictly after FastAPI's own
  auth/validation dependency resolution; Task 3's Suite 4 tests assert this directly. (2) "do not broadly
  swallow programming errors" — honored because both fallback branches catch only the specific
  library connection-exception types (`ConnectionError`/`ConnectionTimeout` for `elasticsearch`,
  `OperationalError`/`OSError` for `clickhouse_connect`), placed before the existing generic `except
  Exception` branches, which remain untouched and still surface as 500s; Task 2 adds a direct test that a
  non-connection exception propagates unmodified. (3) "never change the happy path" — honored because the
  config-absence/connection-failure checks are additive branches; Task 4 adds a direct regression test for
  the ES happy path. (4) "scope is read endpoints only" — honored, no task touches `cli_analytics.py`'s
  `/logs`, `/metrics`, `/traces`, `/event-hooks` ingestion endpoints. (5) codemie-sdk and the
  `.env.standalone`/`customer-config.yaml` CLI Analytics migration are out of scope — no task in this plan
  touches any file under `codemie-sdk`, `standalone/.env.standalone*`, or the `features:cliAnalytics` entry in
  `customer-config.yaml`. (6) "the ES fallback must not leak into non-Analytics-UI consumers of
  `MetricsElasticRepository`" — honored because the fallback is off by default and only `AnalyticsService`'s
  single construction site turns it on; Task 1 enumerates every other construction site by grep and leaves
  each one's call unmodified, and adds tests proving the default mode still raises.
