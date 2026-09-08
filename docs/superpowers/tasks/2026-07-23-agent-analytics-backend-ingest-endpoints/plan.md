# Plan — EPMCDME-13556: Backend Ingest Endpoints

**Spec:** `spec.md` in this directory  
**Branch:** `EPMCDME-13556_backend-ingest-endpoints`

---

## Task 1 — ClickHouse schema: update `mv_hook_events` filter to `agent.*`

**Files:** `deployment/clickhouse/schema.sql`  
**Test-first:** no — DDL change, validated manually against local ClickHouse  
**Status:** ✅ Already done in this branch (part of pre-spec analysis)

Change `claude.*` → `agent.*` in the `mv_hook_events` materialized view WHERE clause and the inline comment in `coding_agent_logs`. Done.

---

## Task 2 — Config: add `ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT` and `ANALYTICS_INGEST_MAX_BODY_BYTES`

**Files:** `src/codemie/configs/config.py`  
**Test-first:** yes — test that config has correct defaults before adding the fields

**Failing test:** `test_analytics_ingest_config_defaults` — asserts `config.ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT` and `config.ANALYTICS_INGEST_MAX_BODY_BYTES` exist and have the correct defaults (`"http://otelcol:4318"`, `5_242_880`).

**Implementation:** Add two fields to the `Config(BaseSettings)` class:
```python
ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT: str = "http://otelcol:4318"
ANALYTICS_INGEST_MAX_BODY_BYTES: int = 5_242_880
```

---

## Task 3 — Ingest router: scaffold + auth + OTLP proxy for `/logs` and `/metrics`

**Files:** `src/codemie/rest_api/routers/ingest_router.py` (new)  
**Test-first:** yes

**Failing tests (RED before implementation):**
- `test_logs_returns_200_on_collector_ok` — POST with valid protobuf body, collector mock returns 200
- `test_logs_requires_auth` — POST without auth → 401/403
- `test_logs_forwards_content_type` — verify `Content-Type: application/x-protobuf` forwarded to collector
- `test_logs_forwards_raw_body_unchanged` — verify collector receives exact bytes sent to API
- `test_metrics_returns_200_on_collector_ok` — POST to metrics, collector mock returns 200
- `test_metrics_requires_auth` — POST without auth → 401/403
- `test_logs_collector_unreachable_returns_503` — httpx.ConnectError → 503
- `test_logs_collector_5xx_retries_and_503` — collector returns 500 three times → 503
- `test_logs_collector_5xx_then_ok_returns_200` — collector returns 500 twice, 200 third → 200
- `test_logs_empty_body_forwarded` — empty body still proxied, not rejected at API layer

**Implementation sketch:**
```python
router = APIRouter(
    prefix="/v1/analytics/ingest-coding-agent-telemetry",
    dependencies=[Depends(authenticate)],
)

async def _forward(url: str, body: bytes, content_type: str) -> Response:
    # httpx with 3-attempt retry on ConnectError or 5xx
    # Return 503 if all attempts fail

@router.post("/logs", status_code=200)
async def ingest_logs(request: Request) -> Response:
    body = await request.body()
    return await _forward(
        f"{config.ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT}/v1/logs",
        body,
        request.headers.get("content-type", "application/x-protobuf"),
    )

@router.post("/metrics", status_code=200)
async def ingest_metrics(request: Request) -> Response:
    # same pattern, different path
```

---

## Task 4 — Ingest router: `event-hooks` endpoint — NDJSON parsing + OTel conversion

**Files:** `src/codemie/rest_api/routers/ingest_router.py`  
**Test-first:** yes

**Failing tests (RED before implementation):**

*Happy path:*
- `test_event_hooks_returns_200_single_event` — one valid NDJSON line, collector returns 200
- `test_event_hooks_returns_200_multi_event` — 10 events in batch, collector returns 200
- `test_event_hooks_event_type_passed_through` — `agent.session.start` arrives in OTel `LogAttributes.event_type` unchanged
- `test_event_hooks_severity_info_for_session_start` — SeverityText == "INFO", SeverityNumber == 9
- `test_event_hooks_severity_error_for_tool_error` — `agent.tool.error` → SeverityText == "ERROR", SeverityNumber == 17
- `test_event_hooks_timestamp_converted_to_nanoseconds` — ISO8601 timestamp → nanosecond int in OTel record
- `test_event_hooks_all_log_attribute_fields_present` — session.start event → all `LogAttributes` keys present (`session_id`, `developer_name`, `cwd`, `git_branch`, `permission_mode`, `turn_number`, `tool_name`, `tool_use_id`, `tool_input`, `tool_output`, `error_message`, `prompt_body`)
- `test_event_hooks_resource_attributes_service_name` — `ResourceAttributes.service.name == "codemie-agent-hooks"`
- `test_event_hooks_posts_to_otlp_logs_path` — collector receives POST to `/v1/logs`

*Edge cases — input validation:*
- `test_event_hooks_empty_body_returns_400` — empty body → 400
- `test_event_hooks_all_lines_invalid_json_returns_400` — body with no parseable JSON lines → 400
- `test_event_hooks_empty_lines_skipped` — NDJSON with blank lines between valid events → processes valid events, returns 200
- `test_event_hooks_mixed_valid_invalid_lines` — some lines parseable, some not → processes valid, skips malformed, returns 200
- `test_event_hooks_body_too_large_returns_413` — body > `ANALYTICS_INGEST_MAX_BODY_BYTES` → 413
- `test_event_hooks_requires_auth` — no auth → 401/403

*Edge cases — missing / malformed fields:*
- `test_event_hooks_missing_session_id_defaults_to_empty` — event without `session_id` → `LogAttributes["session_id"] == ""`
- `test_event_hooks_missing_developer_name_defaults_to_empty` — no `developer_name` → `""`
- `test_event_hooks_missing_timestamp_uses_current_time` — event without `timestamp` field → record uses a non-zero timestamp (current time), no crash
- `test_event_hooks_invalid_timestamp_uses_current_time` — `timestamp: "not-a-date"` → no crash, uses current time
- `test_event_hooks_non_numeric_turn_number_defaults_to_zero` — `turn_number: "x"` → `LogAttributes["turn_number"] == "0"`
- `test_event_hooks_unknown_event_type_passes_through` — `type: "custom.event"` → `LogAttributes["event_type"] == "custom.event"`, SeverityText == "INFO"
- `test_event_hooks_tool_input_not_truncated_by_api` — 500-char `tool_input` passes through unchanged (hook already truncates to 300)

*Edge cases — collector errors:*
- `test_event_hooks_collector_unreachable_returns_503` — httpx.ConnectError → 503
- `test_event_hooks_collector_5xx_retries_and_503` — 3× 500 → 503
- `test_event_hooks_collector_5xx_then_ok_returns_200` — 2× 500, then 200 → 200

**Implementation sketch:**

```python
EVENT_SEVERITY = {
    "agent.tool.error": ("ERROR", 17),
}
DEFAULT_SEVERITY = ("INFO", 9)

def _parse_ndjson(body: bytes) -> list[dict]:
    # split by \n, skip empty, parse JSON, skip malformed

def _event_to_log_record(event: dict, now_ns: int) -> dict:
    # build OTLP log record dict

def _build_otlp_logs_payload(records: list[dict]) -> bytes:
    # wrap in ExportLogsServiceRequest JSON

@router.post("/event-hooks", status_code=200)
async def ingest_event_hooks(request: Request) -> Response:
    raw = await request.body()
    if len(raw) > config.ANALYTICS_INGEST_MAX_BODY_BYTES:
        raise HTTPException(413, "Request body too large")
    if not raw.strip():
        raise HTTPException(400, "Empty body")
    events = _parse_ndjson(raw)
    if not events:
        raise HTTPException(400, "No parseable events")
    records = [_event_to_log_record(e, now_ns) for e in events]
    payload = _build_otlp_logs_payload(records)
    return await _forward(
        f"{config.ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT}/v1/logs",
        payload,
        "application/json",
    )
```

---

## Task 5 — Register router in `main.py`

**Files:** `src/codemie/rest_api/main.py`  
**Test-first:** no — registration is structural, covered by the router tests via `app` fixture  

Add import and `app.include_router(ingest_router.router)` following the same pattern as `coding_agents_analytics`.

---

## Task 6 — Makefile: `clickhouse-schema` target

**Files:** `Makefile`  
**Test-first:** no — shell target, validated manually  

```makefile
clickhouse-schema:
	@echo "Applying ClickHouse schema..."
	cat deployment/clickhouse/schema.sql | \
	    docker exec -i $$(docker compose ps -q clickhouse 2>/dev/null || echo "clickhouse") \
	    clickhouse-client \
	        --user $${CLICKHOUSE_USER:-analytics} \
	        --password $${CLICKHOUSE_PASSWORD:-change_me_secret} \
	        --multiquery
```

---

## Test file layout

```
tests/codemie/rest_api/routers/test_ingest_router.py
```

Mirrors the structure of `test_coding_agents_analytics.py` from MR 3823:
- `mock_user` fixture (MagicMock User)
- `app` fixture (isolated FastAPI with router + `authenticate` override)
- `client` fixture (TestClient)
- Group tests by endpoint and category using comments

---

## Commit plan

| Commit | Content |
|---|---|
| `EPMCDME-13556: Update mv_hook_events to agent.* event types` | `deployment/clickhouse/schema.sql` (already done) |
| `EPMCDME-13556: Add analytics ingest config vars` | `config.py` |
| `EPMCDME-13556: Add ingest router — OTLP proxy endpoints` | `ingest_router.py` (logs + metrics) + tests |
| `EPMCDME-13556: Add ingest router — event-hooks endpoint` | `ingest_router.py` (event-hooks) + tests |
| `EPMCDME-13556: Register ingest router in main app` | `main.py` |
| `EPMCDME-13556: Add clickhouse-schema Makefile target` | `Makefile` |
