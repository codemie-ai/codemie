# Spec: EPMCDME-13558 — OTel Collector Docker Deployment & Config

> **Update (2026-08-14):** the compose/collector infrastructure this spec describes (merged
> `otelcol` service, bind-mounted config, non-conflicting ports, external ClickHouse) is still
> accurate. Only the **schema summary** under "What Gets Built" → `deployment/clickhouse/schema.sql`
> below has drifted — the schema itself grew substantially after this spec was written. See
> [ClickHouse Schema Design](../2026-07-16-epmcdme-13554-clickhouse-schema-design/spec.md) for the
> up-to-date table/MV list; the "6 tables + 2 materialized views" and `coding_agent_metrics_gauge`
> mentioned below are both stale (there are 9 tables + 5 materialized views + 1 view today, and
> `coding_agent_metrics_gauge` was never actually created — only `coding_agent_metrics_sum` exists).

## Goal

Add the OTel Collector as a service in the existing `docker-compose.yml`, configured to receive OTLP data and write it to an external ClickHouse instance under the `codemie_analytics` schema. ClickHouse is a shared external service — the compose does not manage it.

Zero Python application code changes. This task is pure infrastructure.

---

## Key Design Decisions

### Compose strategy: merged into `docker-compose.yml`

The `otelcol` service is added directly to the existing `docker-compose.yml` rather than a separate compose file. Rationale:

- ClickHouse is an external service (separate deployment stack or managed instance), not a local dev dependency. A dedicated analytics compose file would only contain `otelcol` — adding it to the main compose is simpler and reduces lifecycle friction.
- A separate compose creates network isolation: `otelcol` cannot reference `codemie` by service name, and developers must run two compose commands.
- The port conflict with Jaeger (`:4317`/`:4318`) is solved by non-conflicting host port defaults (`14317`/`14318`) — no file separation is needed.

### Config: bind mount, not baked Dockerfile

`otelcol-config.yaml` is bind-mounted at runtime rather than baked into a custom Docker image via Dockerfile. Rationale:

- Consistent with how other runtime configs are handled: `prometheus.yml` and `litellm_config.yaml` are both bind-mounted from the repo root.
- Eliminates a build step — `docker compose up` uses the upstream `otel/opentelemetry-collector-contrib` image directly.
- Config edits are immediately visible; no rebuild required.

### Config location: repo root

`otelcol-config.yaml` lives at the repo root alongside `prometheus.yml` and `litellm_config.yaml`. The `deployment/` directory holds init/schema artifacts (SQL, smoke tests) — not runtime bind-mount targets.

### Port assignment: non-conflicting defaults

Host ports `${OTLP_GRPC_PORT:-14317}` and `${OTLP_HTTP_PORT:-14318}` avoid conflict with Jaeger (which owns `:4317`/`:4318` in the same compose). Overrideable in `.env`.

### ClickHouse: external

ClickHouse is not included in `docker-compose.yml`. It runs in a separate deployment stack or as a managed service. The `otelcol` service reaches it via `CLICKHOUSE_ENDPOINT`. On local dev, ClickHouse is accessed over `host.docker.internal` via `extra_hosts: host.docker.internal:host-gateway`.

### Schema source of truth

`deployment/clickhouse/schema.sql` implements the agreed schema from EPMCDME-13554. It is the single source of truth for all ClickHouse tables — do not modify without a schema change request against EPMCDME-13554.

The `otelcol-contrib:0.105.0` image is pinned — the ClickHouse exporter's raw-table column layout was captured at this version. Any image version bump must be accompanied by schema verification.

---

## What Gets Built

### `otelcol-config.yaml` (repo root)

Key pipeline:
- **Receiver**: OTLP gRPC (`:4317`) + HTTP (`:4318`)
- **Processor**: batch (5s / 1000 records)
- **Exporter**: ClickHouse (endpoint/credentials/database fully env-driven)
  - `logs_table_name: coding_agent_logs`
  - `traces_table_name: coding_agent_traces`
  - `metrics_table_name: coding_agent_metrics`
  - Durable `file_storage/queue` at `/var/lib/otelcol/queue`
- **Pipelines**: logs, metrics, traces — all three to ClickHouse
- Full inline comments explaining every section for teammates unfamiliar with OTel Collector

### `docker-compose.yml` — `otelcol` service

Added to the existing compose:
- Image: `otel/opentelemetry-collector-contrib:0.105.0` (upstream, no build step)
- Config bind-mounted: `./otelcol-config.yaml:/etc/otelcol/config.yaml:ro`
- `extra_hosts: host.docker.internal:host-gateway` for reaching external ClickHouse from within the container
- `user: "0:0"` — required for `file_storage` named volume writes
- Ports: `127.0.0.1:14317:4317` (gRPC), `127.0.0.1:14318:4318` (HTTP)
- Named volume `otelcol_queue` for durable queue

### `deployment/clickhouse/schema.sql`

Creates the `codemie_analytics` database and 6 tables + 2 materialized views per EPMCDME-13554:
- `coding_agent_logs` — raw OTLP log records; `session_id` MATERIALIZED from either `LogAttributes['session.id']` or `LogAttributes['session_id']`; bug-fixed with `ifNull(COALESCE(...), '')` to handle NULL when both attributes are absent
- `coding_agent_traces` — raw OTLP spans; `user_email` MATERIALIZED column also bug-fixed with `ifNull` wrapper
- `coding_agent_metrics_gauge`, `coding_agent_metrics_sum` — raw metric data points (exporter creates both from `metrics_table_name`)
- `coding_agent_hook_events` — populated by `mv_hook_events` MV from `coding_agent_logs`
- `coding_agent_cost_daily` — populated by `mv_cost_daily` MV from `coding_agent_logs`

### `deployment/clickhouse/smoke.sh`

Shell script that validates end-to-end schema + MV pipeline. Runs via `docker cp` into a running ClickHouse container (no local `clickhouse-client` required):
1. Applies `schema.sql`
2. Checks all 6 tables + 2 MVs exist
3. Inserts a test `api_request` log → asserts row in `coding_agent_cost_daily` (MV Path A)
4. Inserts a test hook event log → asserts row in `coding_agent_hook_events` (MV Path B)
5. Cleans up test rows

### `deployment/clickhouse/README.md`

Comprehensive schema reference and quick-start guide:
- Architecture diagram (data flow: API → otelcol → ClickHouse)
- Quick start: `docker run` ClickHouse from repo root + apply schema
- Schema validation via `docker cp smoke.sh` approach
- E2E pipeline test: curl OTLP → verify ClickHouse row
- Table descriptions, engine choices, MV logic, design rationale
- Configuration table and port reference

### `.env` — analytics section

Six variables added under a clearly marked section:
```
CLICKHOUSE_DATABASE=codemie_analytics
CLICKHOUSE_USER=analytics
CLICKHOUSE_PASSWORD=change_me_secret
CLICKHOUSE_ENDPOINT=tcp://host.docker.internal:9000
OTLP_GRPC_PORT=14317
OTLP_HTTP_PORT=14318
```

### Directory structure (final)

```
otelcol-config.yaml              # OTel Collector pipeline config (bind-mounted)
docker-compose.yml               # otelcol service added
.env                             # analytics section added
deployment/
└── clickhouse/
    ├── schema.sql               # codemie_analytics schema (source of truth: EPMCDME-13554)
    ├── smoke.sh                 # Schema + MV pipeline validation script
    └── README.md                # ClickHouse schema reference + quick start
```

Files removed vs. original plan: `deployment/otelcol/Dockerfile`, `deployment/otelcol/otelcol-config.yaml`, `deployment/docker-compose.analytics.yml`, `deployment/.env.analytics.example`, `deployment/README.md` (content moved to `deployment/clickhouse/README.md`).

---

## Out of Scope

- Helm/Kubernetes deployment (`deploy-templates/`) — deferred
- ClickHouse schema migration tooling (Flyway/Liquibase) — deferred; manual `ALTER TABLE` acceptable for now
- Analytics read API (ClickHouse → FastAPI endpoints) — separate ticket
- OTLP authentication / mTLS — noted as future concern; out of scope here
- Modifying `src/codemie/configs/otel_config.py` or `OTEL_EXPORTER_OTLP_ENDPOINT` — not part of this task

---

## Acceptance Criteria Cross-Reference

| AC | Covered by |
|---|---|
| Config implements ticket requirements, design decisions documented | `otelcol-config.yaml` inline comments |
| OTel Collector deployed as Docker service | `docker-compose.yml` — `otelcol` service using upstream `otelcol-contrib:0.105.0` |
| otelcol merged into main compose | `docker-compose.yml` |
| All dev/testing against local or external CH | `CLICKHOUSE_ENDPOINT` in `.env`; `extra_hosts` for host networking |
| Exporter targets EPMCDME-13554 tables | `logs_table_name`, `traces_table_name`, `metrics_table_name` in config |
| OTLP receiver for logs and metrics | `receivers: otlp` with `logs` + `metrics` pipelines |
| ClickHouse exporter with correct tables | `exporters: clickhouse` with table names |
| Connection settings via env vars | All 4 CH vars + 2 port vars in `.env` |
| Native OTel fields preserved | Passed through by collector; no enrichment/filtering |
| Plugin hook event fields preserved | Passed through by collector |
| Both sources join by `session_id` | Schema MATERIALIZED column + MVs (no collector change needed) |
| Resource attributes preserved | Passed through by collector |
| `docker compose up` → send test data → query CH | Documented in `deployment/clickhouse/README.md` |
| README for local run, test, config | `deployment/clickhouse/README.md` |
