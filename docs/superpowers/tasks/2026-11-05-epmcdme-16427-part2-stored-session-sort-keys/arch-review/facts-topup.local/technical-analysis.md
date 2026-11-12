## Top-up facts

### cost_available
- Defined: `codemie/rest_api/models/cli_analytics.py:261` (`cost_available: bool = True`); rule `has_priced_model()` in `codemie/service/analytics/handlers/session_money.py:73` (true when at least one model has a catalog price); `priced_session_ids()` applies it per session.
- List path today: `cli_analytics_handler.py` creates a per-request `CatalogPricer()` (:956), computes `priced_sessions` (:978) from the page's `session_model_rows`, sets `s["cost_available"]` (:999) for page rows only. Computed at read time, not stored. Sessions with dimension events but no `cost_daily` row list with cost 0.0 and `cost_available` false (`reader.py:1513`).
- Detail path: :1102 uses `has_priced_model` over `model_cost_rows`. Consumers: Sessions list and detail responses, frontend contract.

### bloat_pct
- Defined: `cli_analytics.py:98, :263, :418`; computed as `_pct(cache_read_cost, total_cost)`.
- List path today: `cli_analytics_handler.py:1000` from `price_session_models(...)` on the page's (session, model) token rows at catalog prices. Read-time, not stored. Same pattern at :561, :1104, :1361. Guide `cli-analytics-storage.md:342`: all money values are tokens times catalog, priced at read time.

### catalog_source
- Defined: `LLMService.catalog_source()` (`llm_service.py:73`): "litellm" when `LLM_PROXY_ENABLED` and default litellm models non-empty, else "yaml".
- List path today: `CatalogPricer.__init__` (`coding_agent_pricing.py:134`) captures it once per request; router `_metadata()` (`routers/cli_analytics.py:403-411`) writes `catalog_source` and `unpriced_models` for every money endpoint, even for an empty selection. Detail route returns it even for an unknown session.
- Scope per guide (:355): page rows for start_time, ctx_per_call and search; whole filtered selection otherwise. It is a runtime property of the request's pricer, not stored data; a write-time stored cost key does not reflect it.

### lock_timeout
- Postgres `SET LOCAL lock_timeout`, several independent definitions: `migrations.py:39` `LOCK_TIMEOUT = "2min"` (runner, with advisory xact lock); revision `a1c1a0000010` sets `INDEX_LOCK_TIMEOUT = "5s"` first, adds columns, per index `drop_if_invalid` + CREATE, then restores `LOCK_TIMEOUT` (pattern for part 2); `maintenance.py` `DDL_LOCK_TIMEOUT_MS = 2000` (:71, applied :362, :480); `rollups.py` `_LOCK_TIMEOUT_MS = 30_000` (:104, applied :2296); `session_purge.py` `PURGE_LOCK_TIMEOUT_MS = 2000` (:45). Runbook in `cli-analytics-storage.md` ~:424.
