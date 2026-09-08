# Technical Analysis — EPMCDME-13556: Backend Ingest Endpoints

**Task:** Implement three write-only ingest endpoints under `/v1/analytics/ingest-coding-agent-telemetry/` and apply ClickHouse DDL from EPMCDME-13554.
**Branch:** `EPMCDME-13556_backend-ingest-endpoints`
**Date:** 2026-07-23

---

## Codebase Findings

### 1. Where to add new endpoints

The analytics router (`src/codemie/rest_api/routers/analytics.py`) is 3400+ lines — all read-only analytics data queried from Elasticsearch. Adding write-only ingest endpoints there would mix two unrelated concerns and create a reviewer burden.

**Recommendation:** create a dedicated router `src/codemie/rest_api/routers/ingest_router.py` with prefix `/v1/analytics/ingest-coding-agent-telemetry` and `dependencies=[Depends(authenticate)]`. Register it in `src/codemie/rest_api/main.py` alongside the other routers.

Evidence: `activity_events_router.py` follows the same pattern — a standalone router file for a distinct concern, registered at `main.py:658`.

### 2. Authentication

`Depends(authenticate)` is the existing FastAPI dependency used by all analytics endpoints. Declare it at router level so all three endpoints inherit it without repetition. No additional RBAC needed per the AC ("valid bearer token").

Evidence: `src/codemie/rest_api/security/authentication.py` — `authenticate` is the base dependency; all analytics routes use it.

### 3. OTLP Proxy (logs and metrics endpoints)

Claude Code SDK sends OTLP/HTTP **protobuf** payloads (`Content-Type: application/x-protobuf`) to the two proxy endpoints. The API must forward the raw bytes unchanged to the OTel Collector.

OTel Collector HTTP endpoint:
- Inside Docker network: `http://otelcol:4318`
- On host (for local dev without Docker): `http://localhost:14318`
- Mapped at `docker-compose.yml` → `"127.0.0.1:${OTLP_HTTP_PORT:-14318}:4318"` (analytics profile)

Implementation pattern: use `httpx.AsyncClient` to forward the raw request body. Pass through the `Content-Type` header. Return the collector's response status to the caller. FastAPI already has `httpx` available (`pyproject.toml` dependency).

Config change needed: add `ANALYTICS_OTLP_ENDPOINT: str = "http://otelcol:4318"` to `src/codemie/configs/config.py`. Comment that local dev without Docker overrides it to `http://localhost:14318` via `.env`.

The collector must be running (analytics/full profile) for ingest to succeed. Return `503` if the collector is unreachable rather than crashing the API.

### 4. Hook Events → OTel Conversion (event-hooks endpoint)

The `analytics-sender` script POSTs a JSON array of hook event objects. The API must convert each to an OTLP log record and forward to the collector as OTLP/HTTP JSON.

Expected input shape (from Path B design and `codemie-ai-factory-analytics` plugin handlers):
```json
[
  {
    "event_type": "claude.session.start",
    "session_id": "abc123",
    "developer_name": "user@example.com",
    "timestamp": "2026-07-23T10:00:00.000Z",
    "cwd": "/home/user/project",
    "git_branch": "main",
    "permission_mode": "default"
  },
  {
    "event_type": "claude.tool.start",
    "session_id": "abc123",
    "developer_name": "user@example.com",
    "timestamp": "2026-07-23T10:00:05.000Z",
    "tool_name": "Bash",
    "tool_use_id": "toolu_01X...",
    "tool_input": "git status"
  }
]
```

OTLP JSON format expected by the collector (match ClickHouse schema `LogAttributes` keys exactly):
- `LogAttributes` map: `event_type`, `session_id`, `developer_name`, `cwd`, `git_branch`, `permission_mode`, `turn_number`, `tool_name`, `tool_use_id`, `tool_input`, `tool_output`, `error_message`, `prompt_body`
- `ResourceAttributes` map: `service.name` = `"codemie-agent-hooks"`, `team.name` from input if present
- `Timestamp`: from event's `timestamp` field → nanosecond Unix epoch
- `SeverityText`: `"INFO"` for all events except `agent.tool.error` which maps to `"ERROR"`
- `SeverityNumber`: 9 for INFO, 17 for ERROR

The `mv_hook_events` materialized view in ClickHouse filters on `LogAttributes['event_type'] IN (...)` — the conversion must set exactly these keys.

### 5. ClickHouse DDL

`deployment/clickhouse/schema.sql` already exists and is the agreed schema (EPMCDME-13554). The AC requires:
1. A way to **apply** it to the local Docker ClickHouse
2. Validation that the tables match after applying

The schema uses `CREATE ... IF NOT EXISTS` — fully idempotent. A `Makefile` target (e.g., `make clickhouse-schema`) using `clickhouse-client --query` or the ClickHouse HTTP interface is the standard pattern.

There is no existing Makefile target for this. Check the Makefile for patterns to follow.

### 6. HTTP client for forwarding

`httpx` is already in `pyproject.toml`. Use `httpx.AsyncClient` with `async with` context or a long-lived client injected via FastAPI lifespan. For simplicity, a per-request client is fine given low expected request volume at this stage.

For the event-hooks endpoint, build the OTLP/HTTP JSON payload in-memory and POST to `{ANALYTICS_OTLP_ENDPOINT}/v1/logs` with `Content-Type: application/json`.

For logs/metrics proxy, forward to `{ANALYTICS_OTLP_ENDPOINT}/v1/logs` and `{ANALYTICS_OTLP_ENDPOINT}/v1/metrics` respectively, preserving the original `Content-Type: application/x-protobuf`.

### 7. Design Doc vs MR 86 — Reconciliation

The approved solution design (2026-07-14) and MR 86 (EPMCDME-13555/13557 implementation) have two known discrepancies:

| Point | Design doc (§5/§6) | MR 86 reality | Resolution in this spec |
|---|---|---|---|
| event-hooks payload format | "simple JSON array" | `application/x-ndjson` (buffer posted directly) | Accept NDJSON — matches actual sender |
| Hook event type prefix | `claude.*` | `agent.*` | Pass `agent.*` through unchanged; ClickHouse schema updated to `agent.*` |

The design doc is the authoritative reference for intent. Adaptations above are required for compatibility with the actual sender implementation.

---

## Risk Indicators

| Risk | Level | Mitigation |
|---|---|---|
| Collector unreachable (analytics profile not running) | Medium | Catch `httpx.ConnectError`; return HTTP 503 with clear message |
| Oversized tool_input/tool_output in hook events | Medium | Truncate to 1 KB per field (matching the analytics POC pattern in `plugin/handlers/post-tool-use.js`) |
| Content-Type passthrough for protobuf proxy | Low | Forward `Content-Type` header from the incoming request; do not re-encode |
| OTLP timestamp precision | Low | Convert ISO8601 to nanosecond int: `int(datetime.fromisoformat(ts).timestamp() * 1e9)` |
| Missing `session_id` in hook events | Low | Default to empty string; downstream materialized view handles it |
| ClickHouse DDL already applied (idempotent) | Low | `IF NOT EXISTS` guards; applying twice is safe |

---

## Implementation Files

| File | Change type | Notes |
|---|---|---|
| `src/codemie/rest_api/routers/ingest_router.py` | New | 3 endpoints + httpx forwarding |
| `src/codemie/configs/config.py` | Edit | Add `ANALYTICS_OTLP_ENDPOINT` |
| `src/codemie/rest_api/main.py` | Edit | Register new router |
| `deployment/clickhouse/schema.sql` | Edit | Update `mv_hook_events` filter: `claude.*` → `agent.*` |
| `Makefile` | Edit | Add `clickhouse-schema` target |
| `tests/test_ingest_router.py` | New | Unit tests for the 3 endpoints |

---

## Open Issues from Design (relevant to this ticket)

Per `project_agent_analytics_design.md`:
- **`event-hooks` JSON → OTel mapping not yet specified**: resolved above — use `LogAttributes` keys from `coding_agent_hook_events` schema.
- **Tool output truncation strategy (1 KB)**: implement at 1 KB matching analytics POC.
- **OTel Collector not running**: return 503, log the error server-side.
