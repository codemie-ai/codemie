DONE

## Task 8: Dockerfile.sync — multi-stage build

**Commit**: `531280fc6` (epmcdme-15435)

**File created**: `/Users/lugange/Desktop/work/BJB/codemie-dev/codemie/Dockerfile.sync`

## Summary

Created a multi-stage Dockerfile for the Keycloak sync coordinator that runs as a standalone container:

### Stage 1 (builder)
- Base image: `codemie/codemie-base-python:3.12.12-debian-builder` (matches main Dockerfile)
- Installs all dependencies via Poetry from `pyproject.toml` and `poetry.lock`
- Builds virtualenv with enterprise features enabled (`-E enterprise`)
- Sets secure permissions on venv (chmod 555)

### Stage 2 (final)
- Base image: `codemie/codemie-base-python:3.12.12-debian-runtime` (slim variant)
- Copies venv from builder stage
- Sets `PYTHONPATH=/app/src` for package discovery
- Copies only required source modules:
  - `src/codemie/enterprise/sync/` — coordinator and entrypoint
  - `src/codemie/configs/` — configuration module
  - `src/codemie/rest_api/models/` — user models (required by coordinator)
  - `src/codemie_tools/access_management/keycloak/` — KeycloakClient
  - `src/codemie/utils/leader_lock.py` — advisory lock utility
  - `src/codemie/repository/` — all repositories used by entrypoint
  - `src/codemie/clients/` — postgres client for DB session
  - `src/codemie/core/` — core models and exceptions
  - `src/codemie/triggers/` — trigger models used by repositories
  - Package `__init__.py` files to maintain module hierarchy
- Explicitly excludes: uvicorn, LangChain, LiteLLM, Elasticsearch, NATS, Azure Storage SDK

### Drift check
- RUN step verifies imports work: `python -c "from codemie.enterprise.sync.coordinator import KeycloakSyncCoordinator; from codemie.enterprise.sync.entrypoint import main"`
- Fails the build if required modules are missing or moved

### Entrypoint
- Runs: `CMD ["python", "-m", "codemie.enterprise.sync.entrypoint"]`
- Invokes the standalone sync coordinator process once and exits

### Validation
- License header check passes (make license-check): 2299 files checked, 0 missing headers
