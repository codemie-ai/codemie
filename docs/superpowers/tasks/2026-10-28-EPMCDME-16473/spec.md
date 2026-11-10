# EPMCDME-16473 Standalone flag and gate

## Goal
Give the backend one reliable switch meaning "this is a standalone deployment", one gate that standalone-only startup logic passes through, and minimal-dependency defaults in the standalone image. Regular deployments stay exactly as today.

## Design
1. **Switch.** Add `STANDALONE: bool = False` to `Config` in `src/codemie/configs/config.py`, next to other plain bool settings (e.g. `SHAREPOINT_PKCE_ENABLED`, l.482). It is read from env, `.env` and `.env.local` by the existing `SettingsConfigDict`. An unrecognized value (e.g. `maybe`) raises pydantic `ValidationError` at `Config()` construction, as for every other bool field. No special parsing. No existing default is touched.
2. **Gate.** Add one function in `src/codemie/rest_api/main.py`, `_run_standalone_startup()`, following the self-gating `_check_*` pattern (`_check_sharepoint_pkce_redis`, l.503): it returns immediately when `config.STANDALONE` is false, and otherwise is the only entry point to standalone-only logic. It is called once from the lifespan startup sequence (~l.1177-1189). In this ticket its body is empty apart from a single info log line; follow-up tickets add logic behind it. Nothing standalone-specific may be reachable except through it.
3. **No exposure.** The flag is not added to customer-config, `GET /v1/config`, `_public_view` or anything passed to the UI. The `/v1/config` path is independent of `Config`, so no change is needed there. It will appear in `Config.to_safe_dict()` startup log output, which is acceptable (not customer-facing).
4. **Image.** `standalone/Dockerfile` final stage sets `ENV STANDALONE=true ELASTIC_URL= MERMAID_SERVER_URL= MCP_CONNECT_URL= REDIS_HOST=` (empty values, alongside the existing `PYTHONPATH` ENV, l.119).
5. **Compose.** `standalone/docker-compose.standalone.yml` must not set `STANDALONE`, `ELASTIC_URL`, `MERMAID_SERVER_URL`, `MCP_CONNECT_URL` or `REDIS_HOST` in `environment:` (currently only `PG_URL`, `KEYCLOAK_LOGOUT_URL`). Likewise `.env.standalone.example` stays free of them. The compose file needs no edit unless it regresses; it is guarded by a test.

## Acceptance criteria
- AC1: With no `STANDALONE` set anywhere, `Config(_env_file=None).STANDALONE` is False.
- AC2: `STANDALONE=true` in env or `.env` gives True.
- AC3: An invalid value (`maybe`) raises `ValidationError`, same as another bool setting.
- AC4: With the flag off, `_run_standalone_startup()` does nothing (no log, no side effects); lifespan behavior and `GET /v1/config` flags are unchanged.
- AC5: With the flag on, the standalone path is entered exactly through `_run_standalone_startup()`, once per startup.
- AC6: The flag is absent from customer-config components and from the `/v1/config` response.
- AC7: No existing `Config` default value changes.
- AC8: `standalone/Dockerfile` sets `STANDALONE=true` and empty `ELASTIC_URL`, `MERMAID_SERVER_URL`, `MCP_CONNECT_URL`, `REDIS_HOST`.
- AC9: The compose example and `.env.standalone.example` contain none of those five keys.
- AC10: Unit tests cover flag on and off, including unchanged behavior when off.

## Tests
- `tests/codemie/configs/test_config.py`: default False; True from env; True from env file; invalid value raises; a guard that the defaults of `ELASTIC_URL`, `MERMAID_SERVER_URL`, `MCP_CONNECT_URL`, `REDIS_HOST` are unchanged.
- New small test for `_run_standalone_startup` in the tests tree for `rest_api`: off means nothing runs (patch the standalone body/logger and assert not called); on means it runs. Patch `config.STANDALONE`, no app boot.
- `tests/codemie/standalone/test_standalone_compose_contract.py`: extend to assert the five keys are absent from compose `environment` and `.env.standalone.example`, and add a text check that the Dockerfile sets `STANDALONE=true` and the four empty values.
- Off-behavior for `/v1/config`: assert the customer-config resolution output has no `standalone` key with flag on and off (single parametrized check).

## Non-goals
- Dependency presence checks and the missing-config startup warning.
- Enabling or degrading features: `MCP_CONNECT_ENABLED`/`mcpConnect` interplay, web search key semantics (deferred).
- Any change to other deployment files (helm, other compose files, entrypoint.sh, `.env.standalone`).
- Any UI change; any exposure through customer-config or `/v1/config`.
- Changing any existing `config.py` default, or making consumers tolerate empty-string URLs.
- Updating `standalone/README.md`.

## Risks
- Empty `ELASTIC_URL`/`MERMAID_SERVER_URL`/`MCP_CONNECT_URL`/`REDIS_HOST` in the image are not verified against their consumers at runtime; follow-up tickets own that.
- `.env.standalone` is a local file; it must not be edited here.
