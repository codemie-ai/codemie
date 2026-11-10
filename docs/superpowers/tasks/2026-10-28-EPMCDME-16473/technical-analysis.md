# Technical Research

**Task**: standalone config startup-checks dockerfile
**Generated**: 2026-10-28
**Research path**: codegraph

---

## 1. Original Context

Summary: Standalone flag and dependency checks

Context
* The backend has no notion of a standalone deployment. Configuration lives in `config.py`, with defaults that point at local dependencies (Elasticsearch, Mermaid server, MCP Connect, Redis).
* The standalone image (`standalone/Dockerfile`) and its compose example (`standalone/docker-compose.standalone.yml`, which runs only the app and Postgres) ship today without any standalone marker.
* Feature flags returned by `GET /v1/config` are assembled from the customer config and can be overridden. Nothing in that path knows whether the deployment is standalone.
* Startup runs a sequence of checks that warn without blocking. This gives a natural place to run standalone-only logic later.
* Only the `codemie` backend repo is affected. The UI does not change.

Story: As a platform operator deploying CodeMie in standalone mode, I want the backend to know it is running standalone through a single switch so that standalone-specific behavior (dependency checks, feature degradation) activates only for standalone deployments and never affects regular ones.

Background: The standalone image is meant to run with a minimal set of dependencies. Follow-up tickets will check the required dependency configuration and enable or degrade features accordingly. All of them need one reliable switch that says "this is a standalone deployment", with regular deployments staying exactly as they are today. This story delivers that switch, the single gate that standalone-only logic must pass through, and the minimal-dependency defaults of the standalone image.

Acceptance Criteria
* Given no `STANDALONE` value is set anywhere, when the backend loads its configuration, then standalone mode is off.
* Given `STANDALONE=true` is set in the environment or in `.env`, when the backend loads its configuration, then standalone mode is on.
* Given standalone mode is off, when the backend starts and `GET /v1/config` is called, then behavior and the returned feature flags are identical to today.
* Given any standalone-only logic exists, when standalone mode is off, then that logic does not run.
* Given standalone mode is on, when the backend starts, then the standalone-only path is entered through that single gate.
* Given a customer-config response or the UI, when standalone mode is on or off, then the standalone setting is not exposed in customer-config and is not passed to the UI.
* Given the existing defaults in `config.py`, when this change is applied, then no existing default value changes.
* Given the standalone Dockerfile, when the image is built, then it sets `STANDALONE=true` and empty `ELASTIC_URL`, `MERMAID_SERVER_URL`, `MCP_CONNECT_URL` and `REDIS_HOST`.
* Given the standalone compose example, when it is brought up with the image, then it does not reintroduce values for those dependencies, so the minimal-dependency defaults hold.
* Given `STANDALONE` has an unrecognized value (for example `maybe`), when the backend loads its configuration, then it behaves the same way other boolean settings in `config.py` do for invalid values.
* Given unit tests, when they run, then they cover standalone mode on and off, including the unchanged behavior when off.

Out of Scope: dependency-config presence checks; the startup warning listing missing config; enabling/degrading features (incl. MCP_CONNECT_ENABLED / mcpConnect interplay, web search key semantics); any deployment-file change besides the standalone Dockerfile and compose example; any UI change.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/configs/config.py` — `class Config(BaseSettings)` (line 45). Env/`.env`/`.env.local` loaded via `SettingsConfigDict(env_file=(...), extra="ignore")` (~line 964). No `STANDALONE` field exists anywhere in src (grep: only unrelated "STANDALONE TRACE" log in `agents/utils.py`).
- Current defaults (must not change): `ELASTIC_URL="http://localhost:9200"` (l.75), `MERMAID_SERVER_URL="http://localhost:8082"` (l.83), `REDIS_HOST="localhost"` (l.534), `MCP_CONNECT_ENABLED=True` (l.588), `MCP_CONNECT_URL="http://localhost:3000"` (l.589). Boolean example: `SHAREPOINT_PKCE_ENABLED: bool = False` (l.482). `to_safe_dict()` (~l.1150) dumps config for the startup log (ELASTIC_URL masked).
- `src/codemie/rest_api/main.py` — lifespan startup sequence at ~l.1177-1189: `_warm_customer_config()`, `_initialize_optional_features()`, `_check_sharepoint_pkce_redis()`, `_check_usage_attribution_settings()`, `assert_oauth_state_signing_secret_configured()`, `assert_token_vault_available()`, `warn_*`, `_check_model_provider_readiness(app)`, `_initialize_deployment_version()`. Helpers like `_check_sharepoint_pkce_redis` (l.503) self-gate on a config flag with an early `return`, and warn without blocking.
- `GET /v1/config`: `src/codemie/rest_api/routers/customer_config.py:70 get_config` -> `service/customer_config_service.py:157 resolve_components()` (override cache + `component_resolution.resolve_all(customer_config.components, customer_config.get_runtime_components(), ...)`, then LEADERBOARD filter, `_public_view`). `src/codemie/configs/customer_config.py` builds components from YAML, `FEATURE_*` env overrides (`_apply_feature_env_override`, l.131) and runtime components (`_get_runtime_config`, l.149). None reference standalone.
- `standalone/Dockerfile` — final stage ends with `ENV PYTHONPATH=/app/backend/src` (l.119), no other runtime `ENV`, `USER codemie`, `ENTRYPOINT ["/entrypoint.sh"]`.
- `standalone/docker-compose.standalone.yml` — services `codemie`, `postgres`; `env_file: .env.standalone`; inline `environment:` has only `PG_URL` and `KEYCLOAK_LOGOUT_URL`. No ELASTIC/MERMAID/MCP_CONNECT/REDIS/STANDALONE keys.
- `standalone/.env.standalone.example` and `standalone/.env.standalone`, `standalone/docker/entrypoint.sh`: grep finds none of ELASTIC/MERMAID/MCP_CONNECT/REDIS/STANDALONE. `.env.standalone.example` sets `RETRIEVAL_BACKEND=none`.

### Architecture and Layers Affected
Configuration layer (`configs/config.py`); application startup/lifespan (`rest_api/main.py`); deployment artifacts (`standalone/Dockerfile`, compose); tests.

### Integration Points
Config is imported as `from codemie.configs import config` throughout; main.py reads `config.*` directly. `/v1/config` path is separate from `Config` (customer-config components), so a `Config` field is not exposed there unless explicitly added.

### Patterns and Conventions
Typed pydantic-settings fields with literal defaults; invalid bool strings raise pydantic `ValidationError` at `Config()` construction (standard pydantic-settings behavior, as for other `bool` fields). Startup checks are small module-level `_check_*` functions in `main.py`, called from lifespan.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/development/configuration-patterns.md`, `.ai-run/guides/testing/testing-patterns.md`, `.ai-run/guides/quality-gates.md` listed in AGENTS.md (not read in detail). `standalone/README.md` documents the standalone bundle and is an operator file guarded by a contract test.

### Architectural Decisions
None found about a standalone flag.

### Derived Conventions
New settings sit in `Config` as simple typed fields; startup helpers early-return when their flag is off.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/standalone/test_standalone_compose_contract.py` — loads `docker-compose.standalone.yml` with yaml; asserts services == {codemie, postgres}, volumes, no removed-analytics tokens in compose/.env example/README, and that the compose `environment` list is `KEY=value` entries (parsed via `split("=",1)`) and omits `CLI_ANALYTICS_PG_URL`. Natural home for a "compose does not set dependency keys" assertion.
- `tests/codemie/configs/test_config.py`, `test_config_cli_analytics.py` — Config tests; pattern: `Config(_env_file=None)` with `monkeypatch.delenv` fixture (`defaults_only`), `Config(FIELD=value)`, `pytest.raises(ValidationError)`.
- No direct unit tests for `main.py` startup `_check_*` helpers (prior task docs note they are treated as integration-level).
- No Dockerfile tests exist.

### Testing Framework and Patterns
pytest 8.4.2 (from pycache), monkeypatch, pydantic ValidationError assertions.

### Coverage Gaps
No test of `/v1/config` unchanged-ness w.r.t. a Config flag; no Dockerfile ENV test (a text/regex parse of the Dockerfile would be new).

---

## 5. Configuration and Environment

### Environment Variables
Existing: `ELASTIC_URL`, `MERMAID_SERVER_URL`, `MCP_CONNECT_URL`, `MCP_CONNECT_ENABLED`, `REDIS_HOST`, `RETRIEVAL_BACKEND`. `STANDALONE` does not exist.

### Configuration Files
`standalone/Dockerfile` (ENV), `standalone/docker-compose.standalone.yml` (env_file `.env.standalone` + inline env), `standalone/.env.standalone.example`. Image ENV values are overridable by compose `environment`/`env_file`; none of them currently set the dependency keys.

### Feature Flags and Deployment Concerns
`FEATURE_*` env overrides apply only to `features:*` customer-config components; unrelated to `Config`.

---

## 6. Risk Indicators

- Empty-string env values (`ELASTIC_URL=` etc.) override defaults via pydantic-settings; consumers of REDIS_HOST/MERMAID/MCP_CONNECT/ELASTIC with "" are not verified here (`create_redis_client`, mermaid, MCP Connect callers) — out of scope for flag work but an image-runtime behavior risk.
- `Config.to_safe_dict()` dumps all fields to the startup log; a new field will appear there (not customer-config/UI).
- `.env.standalone` is a real file in the dir (may be user-local); verify it stays free of the dependency keys.
- Speculative: the "single gate" form (e.g. a helper vs. direct `config.STANDALONE` check) is a design decision; main.py startup helpers are not unit-tested today.
- `standalone/build-*.log` files are untracked build artifacts in the standalone directory.

---

## 7. Summary for Complexity Assessment

The work touches the configuration layer (`configs/config.py`), the startup lifespan in `rest_api/main.py`, and two deployment artifacts (`standalone/Dockerfile`, the compose example), plus tests. The existing surface is small and well-patterned: typed `Config` bool fields, self-gated `_check_*` startup helpers, and an existing contract test for the standalone compose file.

Technical novelty is low; there is no existing standalone concept, and the `/v1/config` path (customer_config_service / customer_config) is independent of `Config`, so non-exposure is the default. Test posture: Config tests have a clear pattern (`Config(_env_file=None)`, ValidationError); the compose contract test can be extended; startup helpers and Dockerfile have no existing tests.

Key risks are modest: empty-string dependency URLs at runtime in the image, and verifying that no existing default changes. Small file surface (about 3-5 source/test files).

---

## 8. External References

None named by the task.
