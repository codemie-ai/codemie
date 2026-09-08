# Technical Research

**Task**: analytics otel clickhouse docker
**Generated**: 2026-07-20T00:00:00Z
**Updated**: 2026-07-22T00:00:00Z (reflects final implementation decisions)
**Research path**: filesystem

---

## 1. Original Context

Add the OTel Collector to the CodeMie backend as a Docker service. The Collector receives OTLP data from both analytics sources — native OTel telemetry (cost, tokens, metrics) forwarded by the API proxy, and plugin hook events (session context, tool activity) converted to OTel log records by the API — and writes everything to ClickHouse. The Collector exporter must target the tables defined in the agreed ClickHouse schema (EPMCDME-13554). ClickHouse is an external service (not managed by this compose). All development and testing must be done against a local ClickHouse instance first; production uses the same collector image with different env vars.

Key acceptance criteria:
- OTel Collector config targeting the production data model tables per EPMCDME-13554
- OTel Collector added to `docker-compose.yml` using upstream `otelcol-contrib` image (no Dockerfile)
- OTLP receiver for both logs and metrics
- ClickHouse exporter configured with correct connection details and target tables
- Connection settings (host, port, credentials) configurable via environment variables
- Native OTel data (api_request log records, metrics) written to ClickHouse with fields: cost_usd, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, model, session.id, query_source, user.email, organization.id
- Plugin hook events written with fields: session_id, event_type, tool_name, developer_name, cwd, git_branch, permission_mode
- Resource-level attributes preserved: service.name, service.version, os.type, host.arch
- README/documentation for running, testing, configuring locally

---

## 2. Codebase Findings

### Existing Implementations

**Backend repository** (`/Users/mariia_konchak/Documents/codemie-dev/codemie/`) — state after implementation:

- `otelcol-config.yaml` — new file at repo root, alongside `prometheus.yml` and `litellm_config.yaml`. Bind-mounted into the container at runtime.
- `docker-compose.yml` — `otelcol` service added. 11 total services. Uses upstream image `otel/opentelemetry-collector-contrib:0.105.0`; no build step.
- `deployment/clickhouse/` — new directory: `schema.sql`, `smoke.sh`, `README.md`.
- `src/codemie/configs/otel_config.py` — existing FastAPI OTel tracing; unchanged by this task.
- `src/codemie/configs/config.py:530` — `OTEL_ENABLED: bool = False`; unchanged.

**Port conflict with Jaeger — resolved**: Jaeger still owns `:4317`/`:4318`. The `otelcol` service is bound to loopback-only host ports `127.0.0.1:14317:4317` and `127.0.0.1:14318:4318`. No Jaeger config changes needed.

### Architecture and Layers Affected

This task is **pure infrastructure** — it touches no Python application code. The layers involved:

| Layer | Components |
|---|---|
| **Docker / Infra** | `otelcol` service added to `docker-compose.yml`; `otelcol_queue` named volume |
| **OTel Collector** | `otelcol-config.yaml` at repo root (bind-mounted, not baked) |
| **ClickHouse** | `deployment/clickhouse/schema.sql` — external CH; schema applied manually or via smoke.sh |
| **Documentation** | `deployment/clickhouse/README.md` covering schema reference, quick start, E2E test |

No Python source files, no Alembic migrations, no FastAPI routers, no SQLModel models, and no service layer code are created or modified by this task.

### Integration Points

**Upstream (data producers → collector):**
- CodeMie API (`src/`) acts as an OTLP proxy: receives native Claude Code telemetry (Path A) and converts bash hook events to OTel log records (Path B), then forwards both via OTLP/HTTP to `otelcol:4318`. The API's OTLP forwarding endpoint is `OTEL_EXPORTER_OTLP_ENDPOINT` — currently aimed at Jaeger (`:4317`/`:4318`) for local dev; must be redirected to the new collector port (`:14318`) for analytics data to flow.

**Downstream (collector → ClickHouse):**
- ClickHouse `codemie_analytics` database, 3 raw tables written directly by the collector: `coding_agent_logs`, `coding_agent_traces`, `coding_agent_metrics` (exporter creates `_gauge` and `_sum` variants).
- 2 derived tables populated by ClickHouse MVs (no collector config change needed): `coding_agent_hook_events` (from `mv_hook_events`), `coding_agent_cost_daily` (from `mv_cost_daily`).
- ClickHouse connection from within the `otelcol` container to the host: enabled by `extra_hosts: host.docker.internal:host-gateway` in the service definition.

**Future read consumers (out of scope for this task):**
- `GET /v1/analytics/coding-agents/cost` → `coding_agent_cost_daily`
- `GET /v1/analytics/coding-agents/sessions` → `coding_agent_logs` + `coding_agent_hook_events`
- `GET /v1/analytics/coding-agents/tools` → `coding_agent_hook_events` + `coding_agent_traces`
- `GET /v1/analytics/coding-agents/users` → `coding_agent_cost_daily`

### Patterns and Conventions

- **Bind-mount config**: `otelcol-config.yaml` is bind-mounted, consistent with `prometheus.yml` and `litellm_config.yaml`. No Dockerfile or image build step.
- **Repo-root placement**: runtime bind-mount configs belong at the repo root alongside other service configs. `deployment/` holds init/schema artifacts only.
- **Image pinning**: `otelcol-contrib:0.105.0` is pinned to match the ClickHouse exporter table layout. Bumping requires re-verifying `schema.sql` column compatibility. Documented in `deployment/clickhouse/README.md`.
- **Root user for collector**: `user: "0:0"` in compose is needed because `otelcol-contrib` non-root uid 10001 cannot write to a root-owned named volume for the file_storage queue.
- **ClickHouse external**: ClickHouse is not in `docker-compose.yml`. On local dev it runs standalone (separate `docker run` or separate deployment stack). `CLICKHOUSE_ENDPOINT` defaults to `tcp://host.docker.internal:9000`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `/Users/mariia_konchak/Documents/codemie-dev/codemie/.ai-run/guides/` — contains Docker/infra guidance. Bind-mount pattern is consistent with existing compose conventions.

### Architectural Decisions

- **Collector is a thin passthrough** — no enrichment or routing processors. All attribute injection is done upstream by the CodeMie API; all derived-table routing is via ClickHouse MVs. Documented in `otelcol-config.yaml` inline comments.
- **Bind mount over baked image** — eliminates build step, consistent with repo conventions. Same `otelcol-contrib` image runs locally and in production; only env vars differ.
- **ClickHouse is external** — not a Docker compose service. Eliminates `depends_on` / healthcheck coupling and makes the analytics write path independent of ClickHouse's lifecycle in local dev.
- **Dual session key normalization** — `coding_agent_logs.session_id` is a MATERIALIZED column using `ifNull(COALESCE(nullIf(...), nullIf(...)), '')` to normalize `LogAttributes['session.id']` (Path A) and `LogAttributes['session_id']` (Path B). The `ifNull` wrapper is a bug fix over the original `COALESCE` alone, which returned NULL when both attributes were absent (breaking insert into non-Nullable String).
- **Schema-governed TTL** — collector config intentionally omits `ttl`; retention is fixed in `schema.sql` (90d raw tables, 365d rollup). Recorded in `otelcol-config.yaml` comments.
- **otelcol-contrib:0.105.0 pinned** — ClickHouse exporter table layouts captured from this version. Bump image + re-verify schema together.

### Derived Conventions

- Port assignment: `14317`/`14318` as host defaults avoid collision with Jaeger (`:4317`/`:4318`) in the same compose. No per-developer override needed for standard setups.
- `restart: unless-stopped` on `otelcol` — consistent with other stateless services in the compose.
- ClickHouse schema applied manually (not via `docker-entrypoint-initdb.d`), since ClickHouse is external.

---

## 4. Testing Landscape

### Existing Coverage

- **Smoke test script**: `deployment/clickhouse/smoke.sh` — validates schema creation, all 6 tables + 2 MVs exist, end-to-end MV pipeline for both Path A (api_request → cost_daily) and Path B (hook event → hook_events), cleanup. Run via `docker cp` into a running container (no local `clickhouse-client` required):
  ```bash
  docker cp deployment/clickhouse/smoke.sh clickhouse:/tmp/smoke.sh
  docker exec clickhouse bash /tmp/smoke.sh
  ```
- **Manual E2E curl test**: documented in `deployment/clickhouse/README.md` — POST OTLP/HTTP log record to `:14318`, query `coding_agent_logs` and `coding_agent_cost_daily`. Tests the full otelcol → ClickHouse path.
- **No Python unit tests**: pure infrastructure. No pytest tests exist or are expected.

### Testing Framework and Patterns

- Shell-based integration test (`smoke.sh`) using `clickhouse-client` inside the container. Pure bash with `set -euo pipefail` and `exit 1` on failure.
- End-to-end MV path: insert row → sleep 1s → count in derived table → assert count = 1.
- Idempotent: applies schema (IF NOT EXISTS), inserts test data, validates, then ALTERs tables to delete test rows.

### Coverage Gaps

- No automated test for the collector container itself. The full OTLP → otelcol → ClickHouse path is covered by a manual curl test (documented in README), not a script. Could be scripted alongside `smoke.sh`.
- No test for the metrics pipeline (`coding_agent_metrics_gauge` / `_sum` tables).
- No test for the traces pipeline (`coding_agent_traces` table).
- No test verifying env var substitution in `otelcol-config.yaml` works at container startup.

---

## 5. Configuration and Environment

### Environment Variables

Analytics env vars live in the main `.env` under a clearly marked section:

| Variable | Default (local) | Purpose |
|---|---|---|
| `CLICKHOUSE_DATABASE` | `codemie_analytics` | Target database name |
| `CLICKHOUSE_USER` | `analytics` | ClickHouse credentials |
| `CLICKHOUSE_PASSWORD` | `change_me_secret` | ClickHouse credentials (must be overridden for prod) |
| `CLICKHOUSE_ENDPOINT` | `tcp://host.docker.internal:9000` | Collector → ClickHouse connection; override for prod |
| `OTLP_GRPC_PORT` | `14317` | Host port for OTLP gRPC receiver |
| `OTLP_HTTP_PORT` | `14318` | Host port for OTLP HTTP receiver |

Referenced in `otelcol-config.yaml` via `${env:VARIABLE_NAME}` syntax and in `docker-compose.yml` via `${VARIABLE_NAME:-default}` syntax.

**Backend config.py** additionally exposes:
- `OTEL_ENABLED: bool = False` (line 530) — controls whether FastAPI tracing is bootstrapped
- `OTEL_EXPORTER_OTLP_ENDPOINT` — read by `otel_config.py`; currently targets Jaeger; must be pointed at `:14318` for analytics data to flow through the new collector

### Configuration Files

- `otelcol-config.yaml` — OTel Collector pipeline definition (repo root, bind-mounted).
- `deployment/clickhouse/schema.sql` — canonical schema DDL; implements the EPMCDME-13554 agreed schema.
- `deployment/clickhouse/smoke.sh` — schema and MV pipeline validation script.
- `deployment/clickhouse/README.md` — schema reference, quick start, E2E test.
- `.env` — analytics section with 6 variables.

### Feature Flags and Deployment Concerns

- **No feature flag**: the collector is always running when the compose stack is up. No Python code gate is needed.
- **Port conflict with Jaeger — resolved**: `otelcol` uses host ports `14317`/`14318`; Jaeger retains `4317`/`4318`. No conflict.
- **Root user requirement**: `user: "0:0"` on `otelcol` is necessary for the `file_storage` queue volume. Documented.
- **Production deployment**: same `otelcol-contrib` image; override `CLICKHOUSE_ENDPOINT` and credentials in env. Helm chart (`deploy-templates/`) is out of scope.
- **Schema apply is manual**: since ClickHouse is external, schema must be applied via `smoke.sh` or manually. No `docker-entrypoint-initdb.d` auto-apply. Schema changes on an existing instance require `ALTER TABLE` — no migration tooling exists yet.

---

## 6. Risk Indicators

- **Port conflict — jaeger vs otelcol**: **Resolved.** `otelcol` uses loopback-bound host ports `127.0.0.1:14317` / `127.0.0.1:14318`; Jaeger continues to own `:4317`/`:4318`. No disruption to existing dev setups.
- **otelcol-contrib:0.105.0 pinned** — schema column layout is specific to this exporter version. Any future version bump requires re-verifying every table layout in `schema.sql`. Documented in `deployment/clickhouse/README.md`.
- **NULL safety bug in MATERIALIZED columns** — **Fixed.** `COALESCE(nullIf(...), nullIf(...))` returns NULL when both attributes are absent, failing non-Nullable String insert. Fixed with `ifNull(..., '')` wrapper on `session_id` (logs) and `user_email` (traces). Applied to both `schema.sql` and any running instance.
- **No ClickHouse migration mechanism**: schema changes on an existing ClickHouse instance must be applied manually via `ALTER TABLE`. For local dev, volume delete + recreate is acceptable. Production gap — deferred to a follow-up.
- **OTLP ingest is unauthenticated**: `otelcol` binds to `127.0.0.1` (loopback only). Production deployment must use a reverse proxy or mTLS — out of scope for this task.
- **Smoke test bypasses the collector**: `smoke.sh` inserts directly via `clickhouse-client` inside the container, not via OTLP. Full collector path is covered by a manual curl test (documented in README). Scripting this gap is a low-priority follow-up.
- **Shell env vars override `.env`**: Docker Compose reads `.env` but any env var already set in the shell environment takes precedence. Developers must `unset` stale vars (e.g. `CLICKHOUSE_ENDPOINT`) or pass them explicitly if defaults diverge. Documented in README.
- **codemie_analytics read API does not exist yet**: this task covers the write path only (OTELCOL → CH). Read API development (`GET /v1/analytics/...` endpoints) is a separate ticket.

---

## 7. Summary for Complexity Assessment

This task is **pure infrastructure** with zero Python code changes. The deliverables are: `otelcol-config.yaml` at repo root (bind-mounted), `otelcol` service in `docker-compose.yml`, `deployment/clickhouse/` directory with `schema.sql` + `smoke.sh` + `README.md`, and analytics env vars in `.env`.

The primary architectural decision resolved during implementation: no separate compose file, no Dockerfile. OTel Collector uses the upstream image with a bind-mounted config, consistent with Prometheus and LiteLLM conventions in this repo.

The port conflict with Jaeger was resolved via non-conflicting host port defaults (`14317`/`14318`) — no Jaeger reconfiguration needed.

A schema NULL-safety bug was discovered and fixed: `MATERIALIZED` columns using `COALESCE(nullIf(...))` alone failed on inserts when both attribute sources were absent. Fixed with `ifNull(..., '')` wrapper on `session_id` (logs) and `user_email` (traces).

Complexity: **low** on implementation effort (config files + compose edits + SQL DDL). **Medium** on integration/coordination: ClickHouse is now the first external non-compose dependency in the backend repo, which has downstream implications for production secrets management and schema migration tooling — deferred but documented.
