DONE

## Task 3: Sync config vars + KeycloakClient token reuse and pagination

### Files changed

- `src/codemie/configs/config.py` — added 4 new vars in a new `# Keycloak Sync` section after the existing `KEYCLOAK_MIGRATION_WAIT_INTERVAL_SECONDS` line:
  - `KEYCLOAK_SYNC_ENABLED: bool = False`
  - `KEYCLOAK_SYNC_SCHEDULE: str = "*/10 * * * *"`
  - `KEYCLOAK_SYNC_BATCH_SIZE: int = 100`
  - `KEYCLOAK_SYNC_DRY_RUN: bool = False`

- `src/codemie_tools/access_management/keycloak/keycloak_client.py` — rewrote `execute_request` and added `list_users`:
  - `execute_request` now caches the admin token in `self._access_token`; reuses it across calls; clears and re-acquires on a 401 and retries once; returns parsed JSON when the response is JSON (return type widened to `Any`); GET params forwarded as URL query params, non-GET params still sent as JSON body
  - `list_users(batch_size=100)` paginates via `first`/`max`/`enabled` query params until an empty page is returned

- `tests/codemie_tools/access_management/keycloak/test_keycloak_client_pagination.py` — new test file (Apache 2.0 header):
  - `test_list_users_paginates_until_empty_page` — mocks `execute_request` directly, returns page1 (3 items) + page2 (2 items) + empty; asserts 5 users and 3 calls
  - `test_list_users_token_reused_across_pages` — patches `_get_admin_token` and `httpx.Client`; verifies token fetched exactly once across a two-page run

### TDD cycle

1. Created test file — both tests failed with `AttributeError: 'KeycloakClient' object has no attribute 'list_users'` (RED confirmed)
2. Implemented config vars and `KeycloakClient` changes
3. Both tests passed (GREEN confirmed)

### Quality gates

- `make ruff` — 2575 files unchanged, all checks passed
- `make license-check` — 2288 files checked, 0 missing headers

### Commit

`EPMCDME-15435: Add sync config vars and KeycloakClient pagination` (3dddfe973)
