# Spec — EPMCDME-13556: Backend Ingest Endpoints

**Ticket:** [EPMCDME-13556](https://jiraeu.epam.com/browse/EPMCDME-13556)  
**Branch:** `EPMCDME-13556_backend-ingest-endpoints`  
**Status:** Shipped — this file describes the state as of the original approval; see the
**Update (2026-08-14)** callouts below for what changed since. Verified directly against
`src/codemie/rest_api/routers/ingest_router.py`.

---

## 1. Scope

Implement write-only ingest endpoints under `/v1/analytics/ingest-coding-agent-telemetry/` on the
CodeMie API, and provide a Makefile target to apply the agreed ClickHouse DDL locally.

> **Update (2026-08-14):** there are now **four** endpoints, not three — `/traces` was added
> alongside `/logs` and `/metrics` as a third Path A OTLP proxy (Claude Code's OTel SDK exports
> traces too, not just logs/metrics; identical proxy behavior to §3.1/§3.2, just forwarding to
> `{ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT}/v1/traces`). §3 below is otherwise unchanged for `/logs`
> and `/metrics`.

---

## 2. Background

Two data paths feed Claude Code usage analytics into ClickHouse:

- **Path A — Native Claude Code OTel**: Claude Code's built-in OTel SDK exports cost/token/session data as OTLP/HTTP protobuf to the CodeMie API, which forwards it unchanged to the OTel Collector.
- **Path B — sdlc-factory bash hooks**: `analytics-hook` writes lifecycle events to a local NDJSON buffer; `analytics-sender` POSTs that buffer to the CodeMie API, which converts the events to OTel log records and forwards them to the Collector.

The OTel Collector (already deployed, EPMCDME-13558) receives all OTLP traffic and writes to ClickHouse tables defined in `deployment/clickhouse/schema.sql` (EPMCDME-13554 — single source of truth).

---

## 3. Endpoints

### 3.1 `POST /v1/analytics/ingest-coding-agent-telemetry/logs`

**Purpose:** Path A — proxy OTLP/HTTP protobuf logs from Claude Code's OTel SDK.

**Request:**
- `Content-Type: application/x-protobuf`
- Body: raw OTLP ExportLogsServiceRequest protobuf bytes (as emitted by Claude Code SDK)

**Behavior:**
1. Authenticate caller via `Depends(authenticate)`.
2. Forward raw bytes to `{ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT}/v1/logs` using `httpx.AsyncClient`, preserving `Content-Type` header.
3. Retry up to 3 times on connection error or 5xx, with brief delay (1 s, 2 s, 3 s).
4. Return `200 OK` on success; `503 Service Unavailable` with JSON error body `{"detail": "Analytics collector unavailable"}` if all retries exhausted.

### 3.2 `POST /v1/analytics/ingest-coding-agent-telemetry/metrics`

**Purpose:** Path A — proxy OTLP/HTTP protobuf metrics from Claude Code's OTel SDK.

Identical to 3.1 except forwarding to `{ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT}/v1/metrics`.

### 3.3 `POST /v1/analytics/ingest-coding-agent-telemetry/event-hooks`

**Purpose:** Path B — receive NDJSON hook events from `analytics-sender`, convert to OTel log records, forward to Collector.

**Request:**
- `Content-Type: application/x-ndjson`
- Body: newline-delimited JSON; each line is a hook event object (see §4).
- Empty lines are silently skipped.
- Maximum body size: 5 MB (configurable via `ANALYTICS_INGEST_MAX_BODY_BYTES`, default `5_242_880`).

> **Note:** The ticket AC and design doc describe this as "JSON array". NDJSON is implemented here to match MR 86's `analytics-sender`, which posts the buffer file directly with `--data-binary`. This decision was confirmed during spec review.

**Behavior:**
1. Authenticate caller via `Depends(authenticate)`.
2. Read and split body by `\n`; parse each non-empty line as JSON.
3. Map each event to an OTLP log record (see §5).
4. Build a single `ExportLogsServiceRequest` JSON envelope with all records.
5. POST to `{ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT}/v1/logs` with `Content-Type: application/json`.
6. Apply the same 3-retry policy as §3.1.
7. Return `200 OK` on success; `503` if all retries exhausted; `400 Bad Request` if no parseable events in the body.

> **Update (2026-08-14):** two behaviors were added that aren't in the numbered list above:
> - The endpoint rejects a `Content-Type` that isn't `application/x-ndjson` with `415`, and an
>   empty body with `400` — both checked before parsing, not folded into "no parseable events."
> - If the request is authenticated as a known user, `user.email` is stamped onto every log record
>   as an extra `LogAttributes` entry (see §5) — hook events don't carry the user's email
>   themselves, so this is the only place it enters the pipeline for Path B data.

---

## 4. Hook Event NDJSON Format

Each line from `analytics-sender` is a JSON object with a `type` field using the `agent.*`
namespace.

> **Update (2026-08-14):** the original table below listed 6 event types with a fixed per-type
> field set. That's no longer how the ingest endpoint works — it doesn't branch on `type` at all
> when reading fields. Since this spec was written, `analytics-hook` grew to emit more event types
> (at least `agent.subagent.start`/`agent.subagent.stop` — the pair the Sessions-tab "Dispatches"
> widget is built on, `agent.turn.error`, `agent.tool.denied`, and notification events), and the
> endpoint now reads a **fixed whitelist of 23 possible attribute keys** from every event
> regardless of `type`, taking whichever ones that event actually set:
>
> ```
> event_type, session_id, prompt_id, developer_name, codemie_project_name, cwd, git_branch,
> repo_remote, permission_mode, source, effort, tool_name, tool_use_id, tool_input, tool_output,
> error_message, error_type, reason, agent_id, agent_type, trigger, denial_reason,
> notification_type, prompt_body
> ```
>
> (`event_type` itself comes from the event's `type` field, not a key named `event_type` on the
> event object — see `_event_to_log_record` in `ingest_router.py`.) A key absent from a given event
> serializes to `""` (empty string), never `null` or an omitted attribute — every log record always
> carries all 23 keys.
>
> The old table's claim that "fields not present are absent (not null)" is backwards from current
> behavior: absent fields are now the empty string, always present. `analytics-hook`'s
> 300-character truncation of `tool_input`/`tool_output` before writing, and the API not
> re-truncating, is still accurate.
>
> `cwd` also gets normalized to just its final path component (`_cwd_basename`) before being
> written — the hook sends a full OS path (needed locally for git operations), but only the
> basename is useful for analytics grouping. This normalization isn't in the original design.

---

## 5. Event → OTel Log Record Mapping

The `type` field from the NDJSON event is written directly to `LogAttributes['event_type']` — no renaming. The ClickHouse `mv_hook_events` materialized view filters on `agent.*` names (updated in `deployment/clickhouse/schema.sql`).

**OTLP log record fields (per event) — as implemented today:**

```
Timestamp:      int64 nanoseconds — from event["timestamp"] (ISO 8601) parsed as UTC
SeverityText:   "INFO" for most types; "ERROR" for agent.tool.error / agent.turn.error;
                "WARN" for agent.tool.denied  (EVENT_SEVERITY map, extensible for future types)
SeverityNumber: 9 (INFO) / 17 (ERROR) / 13 (WARN)
Body:           empty string
LogAttributes:  event_type + all 23 keys from the §4 whitelist (each "" if unset on this event)
                + "user.email" (only present when the ingest request was authenticated —
                  not derived from the event payload itself)
ResourceAttributes: {
    "service.name": "codemie-agent-hooks"
}
```

This replaces the smaller, per-event-type field list the original design had here — the real
implementation doesn't vary the attribute set by `event_type`, it always emits the same 23(+1)
keys. See §4's update note for the full key list and why.

**Timestamp conversion:** `int(datetime.fromisoformat(ts.replace("Z","+00:00")).timestamp() * 1e9)`. If parsing fails, use current time. (Implemented via day/second/microsecond integer arithmetic against the Unix epoch, not a direct float `.timestamp()` multiply — same result, avoids float rounding at nanosecond precision.)

---

## 6. Configuration

Two new env vars added to `src/codemie/configs/config.py`:

```
ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT: str = "http://otelcol:4318"
  # OTel Collector HTTP endpoint. Default resolves inside Docker network.
  # Override in .env for local dev without Docker: http://localhost:14318

ANALYTICS_INGEST_MAX_BODY_BYTES: int = 5_242_880
  # Maximum body size for event-hooks endpoint (default 5 MB).
```

---

## 7. ClickHouse DDL

`deployment/clickhouse/schema.sql` is the single source of truth (EPMCDME-13554) — **no changes**.

Add a Makefile target that reads credentials from env (matching `.env` defaults):

```makefile
clickhouse-schema:
    @echo "Applying ClickHouse schema to local instance..."
    cat deployment/clickhouse/schema.sql | \
        docker exec -i $$(docker compose ps -q clickhouse 2>/dev/null || echo "clickhouse") \
        clickhouse-client \
            --user $${CLICKHOUSE_USER:-analytics} \
            --password $${CLICKHOUSE_PASSWORD:-change_me_secret} \
            --multiquery
```

Reads `CLICKHOUSE_USER` and `CLICKHOUSE_PASSWORD` from the environment (`.env` defaults: `analytics` / `change_me_secret`).

---

## 8. New Files

| File | Purpose |
|---|---|
| `src/codemie/rest_api/routers/ingest_router.py` | All 4 endpoints (`/logs`, `/metrics`, `/traces`, `/event-hooks`) + httpx forwarding + OTel conversion |
| `tests/rest_api/routers/test_ingest_router.py` | Unit tests for all endpoints (mock httpx) |

**Edited files:**

| File | Change |
|---|---|
| `src/codemie/configs/config.py` | Add `ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT`, `ANALYTICS_INGEST_MAX_BODY_BYTES` |
| `src/codemie/rest_api/main.py` | Register `ingest_router` |
| `Makefile` | Add `clickhouse-schema` target |

---

## 9. Acceptance Criteria Mapping

| AC | Implementation |
|---|---|
| POST logs accepts OTLP/HTTP protobuf, proxies to Collector | §3.1 |
| POST metrics accepts OTLP/HTTP protobuf, proxies to Collector | §3.2 |
| POST traces accepts OTLP/HTTP protobuf, proxies to Collector | §3 update note — added after original approval, not in the initial ticket AC |
| POST event-hooks accepts JSON array of hook events, converts to OTel, forwards | §3.3 (NDJSON per MR 86) |
| All endpoints require valid bearer token / cookie | `Depends(authenticate)` at router level |
| HTTP 200 on success; appropriate error codes on failure | §3.1–3.3 |
| DDL implements agreed schema from EPMCDME-13554 exactly | `deployment/clickhouse/schema.sql` unchanged |
| DDL tested against local Docker ClickHouse | `make clickhouse-schema` + Makefile note |

---

## 10. Out of Scope

- Read endpoints (`/v1/analytics/coding-agents/*`) — covered by EPMCDME-13561
- UI dashboard — covered by EPMCDME-13559
- Auth proxy credential store (codemie-cli) — covered by EPMCDME-13553
- Windows cron equivalent — covered by EPMCDME-13560
