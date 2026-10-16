# standalone

Builds and runs a standalone codemie container image (backend + frontend + nginx baked into one
image) from local source trees.

## Prerequisites

- **Docker** (BuildKit-enabled, for named build-context support) or **Podman**
- **Bash**
- **git**
- Both the backend (`codemie`, this repository — packaging assets live here under `standalone/`)
  and frontend (`codemie-ui`) repositories checked out locally. The sibling layout below is only
  required when using the default paths; explicit `--backend-root`/`--frontend-root` may point
  anywhere:

  ```
  parent/
    codemie/           ← this repository (backend + packaging assets)
    codemie-ui/        ← frontend
  ```

## Usage

**Minimal** — `--image-tag` is the only required argument; both repo paths default from
`--backend-root` (itself defaulting to the repo containing this script) and the sibling layout
above:

```bash
standalone/build-image.sh --image-tag localhost/codemie:local
```

**Explicit paths** (used by the release pipeline, where the two repos are not siblings):

```bash
standalone/build-image.sh \
  --image-tag    localhost/codemie:local \
  --backend-root /path/to/codemie \
  --frontend-root /path/to/codemie-ui
```

**Force a full rebuild (bypass cache):**

```bash
standalone/build-image.sh --image-tag localhost/codemie:local --no-cache
```

**Use Podman instead of Docker** (the default engine is `docker`):

```bash
standalone/build-image.sh --image-tag localhost/codemie:local --engine podman
```

## Configuring environment variables

```bash
cp standalone/.env.standalone.example standalone/.env.standalone
```

Backend runtime defaults (`ENV`, `FRONTEND_URL`, `RATE_LIMIT_LOGIN`) are set in this file, not baked
into the image. If your `standalone/.env.standalone` predates this, copy those three lines from the
example: the container refuses to start while `ENV` is unset.

Edit `standalone/.env.standalone` and configure:
- Local superadmin credentials
- At least one LLM provider block (Azure OpenAI or AWS Bedrock — see the comments in the example file for both)

### External customer configuration

CodeMie reads customer configuration from `/app/backend/config/customer/customer-config.yaml` inside
the image. By default, this file is bundled during the image build. You can mount a directory
containing an external YAML file to override it without rebuilding the image.

**Getting the full configuration:**

There is no separate minimal example file to copy. Always start from the canonical, version-matched
`config/customer/customer-config.yaml` in the repository — it defines every feature ID, component,
and default value for this exact build, and mounting anything less than the full file disables
every component it omits (see **Full replacement** below).

**Starting with bundled defaults (no external config):**

Run the standalone Compose file (see **Starting the stack** below,
`docker compose -f standalone/docker-compose.standalone.yml up -d`) with no other changes. The container mounts
`standalone/external-config/` by default; since that directory is tracked empty (only a
`.gitkeep`), the entrypoint finds no `customer-config.yaml` there and falls back to the config
bundled in the image. It logs which source it picked, e.g.:

```
customer config: using bundled file (no customer-config.yaml found under /app/external-config)
```

**Starting with an external configuration:**

The `codemie` service always mounts a host directory read-only at `/app/external-config`
(`CODEMIE_EXTERNAL_CONFIG_DIR`, default `standalone/external-config`). At startup, the entrypoint
looks for `customer-config.yaml` inside that directory:

- If it exists, it fully replaces the bundled config, and the entrypoint logs
  `customer config: using external file from /app/external-config/customer-config.yaml`.
- If it does not exist, startup continues with the config bundled in the image.

1. Copy `config/customer/customer-config.yaml` into the default external-config directory and edit
   it there. Keep every section and feature ID exactly as in the canonical file except the values
   you intend to change:

   ```bash
   cp config/customer/customer-config.yaml standalone/external-config/customer-config.yaml
   ```

2. Start (or restart/recreate) the stack as usual — no extra `-f`/`--file` is needed:

   ```bash
   docker compose -f standalone/docker-compose.standalone.yml up -d
   ```

3. To keep the external directory somewhere else, set `CODEMIE_EXTERNAL_CONFIG_DIR` to its
   absolute path before running the same Compose command; the file inside it must still be named
   `customer-config.yaml`:

   ```bash
   export CODEMIE_EXTERNAL_CONFIG_DIR=/path/to/your/config-dir
   ```

**Important notes:**

- **Mount is read-only (`:ro`)** — the container cannot modify the directory or its contents.
- **Default source path** — when `CODEMIE_EXTERNAL_CONFIG_DIR` is unset, Compose resolves
  `./external-config` relative to the `standalone/` Compose file, and the entrypoint looks for
  `customer-config.yaml` inside it.
- **Full replacement, no merge** — when found, the external file completely replaces the bundled
  file. No merge, overlay, or cascading defaults. All sections and values must be present in the
  external YAML.
- **No image rebuild required** — put the file in the mounted directory and restart/recreate the
  container.
- **YAML changes require restart** — after editing your config file, restart or recreate the
  container for changes to take effect:

  ```bash
  docker compose -f standalone/docker-compose.standalone.yml down
  docker compose -f standalone/docker-compose.standalone.yml up -d
  ```

- **Removing the external file falls back cleanly** — the entrypoint always resets
  `customer-config.yaml` from a pristine image-bundled copy before re-checking for the external
  file on every start. So if you delete `customer-config.yaml` from the external-config directory
  and restart the container (`docker compose -f standalone/docker-compose.standalone.yml restart
  codemie`, or a full `down`/`up`), it correctly falls back to the bundled config instead of
  keeping the last external content.

**Disable Budget Management in standalone:**

The `features:budgetManagement` component requires LiteLLM, which is not included in the
standalone Compose stack. Leaving it enabled exposes Budget Management controls in the UI even
though the corresponding LiteLLM-backed admin endpoints are unavailable. Disable it for standalone
deployments:

- With an external customer configuration, set `features:budgetManagement` to `enabled: false` in
  the mounted YAML.
- With the bundled customer configuration, add `FEATURE_BUDGET_MANAGEMENT=false` to
  `standalone/.env.standalone`. The generic `FEATURE_<ID>` override mechanism also takes precedence
  over the mounted YAML, so the same override can be used with an external configuration.

Restart or recreate the `codemie` container after changing either setting. No image rebuild is
required.

**Disable AI Champions Leaderboard in standalone without Elasticsearch:**

Leaderboard reads stored snapshots from PostgreSQL. Computation combines PostgreSQL data with
Elasticsearch activity, tool usage, CLI and cost metrics. Elasticsearch is not included in the
standalone Compose stack. The collector handles Elasticsearch failures and can continue with
incomplete metrics, so disabling Leaderboard is recommended to avoid presenting an incomplete
ranking. The bundled customer configuration enables `aiChampionsLeaderboard`; explicitly disable
it to hide the tab and disable its API endpoints in this deployment.

Use the full external customer configuration described above. In that file, change the existing
`aiChampionsLeaderboard` entry to `settings.enabled: false`, keeping its other settings and every
other component intact. The relevant YAML fragment is:

```yaml
- id: "aiChampionsLeaderboard"
  settings:
    enabled: false
```

`LEADERBOARD_ENABLED=false` only disables the nightly computation scheduler; it does not hide the
tab or disable the API endpoints. The generic `FEATURE_<ID>` environment override applies only
to components whose IDs start with `features:`, so it cannot disable `aiChampionsLeaderboard`.
Even when starting from bundled defaults, mount a full external customer configuration to make
this change. Restart the `codemie` container after editing the YAML; no image rebuild is required:

```bash
docker compose -f standalone/docker-compose.standalone.yml restart codemie
```

## Starting the stack

```bash
docker compose -f standalone/docker-compose.standalone.yml up -d
# or
podman compose -f standalone/docker-compose.standalone.yml up -d
```

The compose file only references an image tag — it never builds it itself. By default it uses
`localhost/codemie:local`, the tag produced by `build-image.sh` above; rebuild with that script to
refresh it, then re-run compose up.

**Running a published image instead of a local build:** set `CODEMIE_IMAGE` to the exact image tag
before running compose:

```bash
export CODEMIE_IMAGE=<registry>/<published-codemie-image>:<released-tag>
docker compose -f standalone/docker-compose.standalone.yml up -d
```

A published standalone release bundle must set `CODEMIE_IMAGE` to that bundle's released image
tag — release ZIP generation and image publishing are handled separately and are out of scope for
this change. Leave `CODEMIE_IMAGE` unset for the local `build-image.sh` workflow.

Other useful commands:

```bash
# Status
docker compose -f standalone/docker-compose.standalone.yml ps

# Follow logs
docker compose -f standalone/docker-compose.standalone.yml logs -f

# Stop and remove containers
docker compose -f standalone/docker-compose.standalone.yml down
```

(swap `docker` for `podman` as needed).

### Non-root container and existing volumes

The image is built on the shared `codemie/codemie-base-python` base image and runs both nginx and the
backend as its unprivileged `codemie` user (uid/gid 1001), not as root. New volumes inherit that
ownership. A `codemie_storage` / `codemie_repos` volume created by an earlier image that ran as root
is still owned by root, so the container stops at startup with
`ERROR: /app/codemie-storage is not writable by uid 1001`. Change the ownership once and start again;
the data is kept:

```bash
docker compose -f standalone/docker-compose.standalone.yml down
for v in codemie-standalone_codemie_storage codemie-standalone_codemie_repos; do
  docker run --rm --user 0 --entrypoint chown -v "$v":/data "${CODEMIE_IMAGE:-localhost/codemie:local}" -R 1001:1001 /data
done
docker compose -f standalone/docker-compose.standalone.yml up -d
```

Check the volume names with `docker volume ls` (they are prefixed with the Compose project name).
The external customer configuration directory is mounted read-only, so its
`customer-config.yaml` only has to be readable by uid 1001.

## CLI Analytics (optional)

CLI Analytics needs no extra service: the backend receives telemetry directly and stores it in the
stack's PostgreSQL. The default Compose stack (`codemie` and `postgres`) is all that is required.

**To enable CLI Analytics:**

1. CLI Analytics is gated by `features:cliAnalytics` in the customer configuration, not by a
   dedicated environment variable of its own. Copy `config/customer/customer-config.yaml` (see
   **External customer configuration** above) and set that component's `enabled` to `true`. Note
   that the generic backend `FEATURE_<ID>` override mechanism still applies to every `features:*`
   component, including this one: if `FEATURE_CLI_ANALYTICS` is set in your environment, it
   overrides whatever the mounted YAML says for `enabled`. Check for and unset it if CLI Analytics
   does not behave as configured.

2. Start (or recreate) the stack as usual:

   ```bash
   docker compose -f standalone/docker-compose.standalone.yml up -d
   ```

3. Check that the jobs were scheduled. This only shows the scheduler was started; the schema
   migration runs in the background after it, so it does not mean Analytics is ready:

   ```bash
   docker compose -f standalone/docker-compose.standalone.yml logs codemie | grep cli_analytics
   ```

   Expect `cli_analytics: scheduled cli_analytics_rollup_refresh` and
   `cli_analytics: scheduled cli_analytics_maintenance`. A failed migration is logged as
   `analytics schema migration failed` and retried by the maintenance job.

4. Check that Analytics is ready. The migration is complete when the head revision is recorded:

   ```bash
   docker compose -f standalone/docker-compose.standalone.yml exec postgres \
     psql -U postgres -c "SELECT version_num FROM codemie_analytics.cli_analytics_alembic_version"
   ```

   Expect `a1c1a0000006`. Then make an authenticated read as an administrator. Log in with
   `SUPERADMIN_EMAIL` and `SUPERADMIN_PASSWORD` from `standalone/.env.standalone` (login is limited
   to 5 attempts per 15 minutes) and request the Overview:

   ```bash
   TOKEN=$(curl -s -X POST http://localhost:8080/api/v1/local-auth/login \
     -H 'Content-Type: application/json' \
     -d '{"email":"<SUPERADMIN_EMAIL>","password":"<SUPERADMIN_PASSWORD>"}' \
     | sed -E 's/.*"access_token":"([^"]+)".*/\1/')
   curl -s -H "Authorization: Bearer $TOKEN" \
     'http://localhost:8080/api/v1/analytics/cli-analytics/overview?time_period=last_7_days'
   ```

   Before any telemetry is ingested, a ready Analytics answers HTTP 200 with
   `data.kpis.total_sessions` and `data.kpis.total_cost_usd` equal to `0` and an empty
   `data.model_breakdown`. HTTP 503 means the analytics database is not ready or not reachable
   (for example the migration has not finished yet): check the `codemie` logs and retry. HTTP 404
   means `features:cliAnalytics` is disabled; 401/403 means the login failed or the user is not a
   global or project administrator.

**Storage.** The analytics tables live in the schema `codemie_analytics` of the same database as the
application. The `codemie` service already passes `PG_URL` pointing at the `postgres` service, and
CLI Analytics falls back to it, so no analytics-specific variable is set. To use another database,
set `CLI_ANALYTICS_PG_URL` in `standalone/.env.standalone`. The `CLI_ANALYTICS_*` retention and
rollup settings are documented in `.ai-run/guides/integration/cli-analytics-storage.md`.

**Telemetry endpoints.** CLI clients send to the CodeMie backend; no OpenTelemetry Collector is
involved. The backend accepts, under `/v1/analytics/cli-analytics/`, OTLP (protobuf or JSON) on
`logs`, `metrics` and `traces`, and newline-delimited JSON on `event-hooks`. Through the bundled
nginx these are reachable at `http://localhost:8080/api/v1/analytics/cli-analytics/<endpoint>`
(or without the `/api` prefix). They require the same authentication as the rest of the API, and
answer 404 while `features:cliAnalytics` is disabled and 503 while the analytics database is
unavailable. The body limit is `ANALYTICS_INGEST_MAX_BODY_BYTES` (5 MiB by default).

Open CodeMie at `http://localhost:8080` once `codemie` reports healthy; the Analytics menu item is
visible to a global/project admin when `features:cliAnalytics` is enabled.

## What the build script does

1. Validates all required source files exist before invoking the builder.
2. Verifies the chosen engine is installed and its daemon is reachable.
3. Prints and logs the git branch, SHA, and dirty status for the backend and frontend repos.
4. Runs `<engine> build` with the backend repo (`--backend-root`) as the main context and the
   frontend repo (`--frontend-root`) passed as the named build context `frontend`
   (`--build-context frontend=<path>`). The Dockerfile copies only the specific files needed for
   the production build from each context — never a secret, cache, or `.git` directory from
   either source repository. The final image contains no `.git` directory: the Python
   dependencies, including `nemo-switchyard`, are installed from prebuilt wheels.
5. Writes a timestamped log to `standalone/build-<timestamp>.log`.
6. Exits with the engine's exact build exit code.

The build uses `--ulimit nofile=65536:65536` to avoid environment-dependent open-file limits
during the Vite/Tailwind frontend build, which reads many source files concurrently.

## Generated files (gitignored)

| Path | Contents |
|---|---|
| `standalone/build-*.log` | Per-run timestamped build log |
| `standalone/.env.standalone` | Local environment configuration (copy of the example, filled in) |
| `standalone/external-config/customer-config.yaml` | Local full customer configuration mounted into `/app/external-config` (directory itself is tracked empty via `.gitkeep`) |
