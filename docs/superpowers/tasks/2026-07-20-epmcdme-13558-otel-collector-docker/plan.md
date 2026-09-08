# Plan: EPMCDME-13558 — OTel Collector Docker Deployment & Config

Spec: `docs/superpowers/tasks/2026-07-20-epmcdme-13558-otel-collector-docker/spec.md`  
Technical analysis: `docs/superpowers/tasks/2026-07-20-epmcdme-13558-otel-collector-docker/technical-analysis.md`  
Branch: `EPMCDME-13558_otel-collector-docker-deployment`

---

## Architectural decisions (vs. original plan)

| Original | Final | Reason |
|---|---|---|
| `deployment/otelcol/Dockerfile` | **Removed** | Baking config into an image adds build overhead with no benefit. Bind mount is equivalent and consistent with how Prometheus and LiteLLM work. |
| `deployment/otelcol/otelcol-config.yaml` | **Moved** to `otelcol-config.yaml` (repo root) | Runtime configs that are bind-mounted belong at root alongside `prometheus.yml` and `litellm_config.yaml`. `deployment/` is for init/schema artifacts. |
| `deployment/docker-compose.analytics.yml` | **Removed** | Separate compose file creates network isolation and lifecycle friction. otelcol merged into main `docker-compose.yml`. |
| `deployment/.env.analytics.example` | **Removed** | Analytics env vars merged into the main `.env` under a clearly marked section. |
| ClickHouse in analytics compose | **External** | ClickHouse is a shared infrastructure service — it runs in a separate deployment stack or as a managed service. It is not owned by this compose. otelcol connects via `CLICKHOUSE_ENDPOINT`. |
| `deployment/README.md` covering the full stack | **Narrowed** | README now documents only the `deployment/` directory artifacts (ClickHouse schema). OTel Collector setup is documented in the root-level README. |

---

## Implementation tasks

### Task 1 — OTel Collector config ✓

**File**: `otelcol-config.yaml` (repo root)

- OTLP receivers: gRPC `:4317`, HTTP `:4318`
- Batch processor: 5 s / 1 000 records
- ClickHouse exporter: all three signals (logs, metrics, traces); connection fully env-driven
- Durable `file_storage/queue` at `/var/lib/otelcol/queue`
- Full inline comments explaining every section for teammates unfamiliar with OTel Collector

---

### Task 2 — ClickHouse schema ✓

**File**: `deployment/clickhouse/schema.sql`

Implements the canonical EPMCDME-13554 schema. Single source of truth — do not modify table names, column definitions, TTLs, or MV logic without a schema change request.

**Bug fix applied**: `session_id` (logs) and `user_email` (traces) MATERIALIZED columns used
`COALESCE(nullIf(...), nullIf(...))` which returns `NULL` when both attributes are absent.
Wrapped with `ifNull(..., '')` so missing values store as empty string rather than failing the insert.

---

### Task 3 — ClickHouse smoke test ✓

**File**: `deployment/clickhouse/smoke.sh`

Validates the full schema idempotently:
1. Applies `schema.sql`
2. Checks all 6 tables + 2 MVs exist
3. Inserts test `api_request` log → asserts row in `coding_agent_cost_daily` (MV Path A)
4. Inserts test hook event → asserts row in `coding_agent_hook_events` (MV Path B)
5. Cleans up test rows

---

### Task 4 — ClickHouse README ✓

**File**: `deployment/clickhouse/README.md`

Comprehensive schema reference: table descriptions, engine choices, MV logic, design rationale, and local apply instructions.

---

### Task 5 — Merge otelcol into main docker-compose.yml ✓

**File**: `docker-compose.yml`

Added `otelcol` service:
- Image: `otel/opentelemetry-collector-contrib:0.105.0` (upstream, no build step)
- Config bind-mounted: `./otelcol-config.yaml:/etc/otelcol/config.yaml:ro`
- `extra_hosts: host.docker.internal:host-gateway` so the container can reach external ClickHouse
- `CLICKHOUSE_ENDPOINT` defaults to `tcp://host.docker.internal:9000`; override in `.env` for any environment
- Named volume `otelcol_queue` for durable file_storage queue
- Ports: `127.0.0.1:14317:4317` (gRPC), `127.0.0.1:14318:4318` (HTTP) — avoids conflict with Jaeger

---

### Task 6 — Analytics env vars in main .env ✓

**File**: `.env`

Added analytics section:
```
CLICKHOUSE_DATABASE=codemie_analytics
CLICKHOUSE_USER=analytics
CLICKHOUSE_PASSWORD=change_me_secret
CLICKHOUSE_ENDPOINT=tcp://host.docker.internal:9000
OTLP_GRPC_PORT=14317
OTLP_HTTP_PORT=14318
```

---

### Task 7 — deployment/README.md ✓

**File**: `deployment/README.md`

Documents the `deployment/` directory: ClickHouse schema artifacts, how to start ClickHouse standalone, schema smoke test, and E2E test for the full otelcol → ClickHouse pipeline.

---

## Files changed

| Action | Path |
|---|---|
| New | `otelcol-config.yaml` |
| New | `deployment/clickhouse/schema.sql` |
| New | `deployment/clickhouse/smoke.sh` |
| New | `deployment/clickhouse/README.md` |
| New | `deployment/README.md` |
| Modified | `docker-compose.yml` — added `otelcol` service and `otelcol_queue` volume |
| Modified | `.env` — added analytics section |
| Removed | `deployment/otelcol/Dockerfile` |
| Removed | `deployment/otelcol/otelcol-config.yaml` |
| Removed | `deployment/docker-compose.analytics.yml` |
| Removed | `deployment/.env.analytics.example` |
