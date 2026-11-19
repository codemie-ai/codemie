# Task 7 Report — Dry-run mode + sync entrypoint

DONE

## Files created

| File | Purpose |
|---|---|
| `src/codemie/enterprise/sync/entrypoint.py` | Standalone sync process: env-var validation, wires up KeycloakClient + repos + coordinator, exits 1 on error |
| `tests/codemie/enterprise/sync/test_coordinator_dryrun.py` | 2 tests verifying coordinator issues no DB writes with `dry_run=True` |
| `tests/codemie/enterprise/sync/test_entrypoint.py` | 3 tests verifying `main()` exits 0 on ok/skipped, exits 1 on error |

## TDD cycle

| Phase | Result |
|---|---|
| RED | `test_entrypoint.py` → `ModuleNotFoundError`; `test_coordinator_dryrun.py` → 2 passed (coordinator dry_run logic already correct from Task 6) |
| GREEN | All 5 new tests passed after creating `entrypoint.py` |
| Full suite | 18/18 sync tests passed |

## Quality gates

- `make ruff`: fixed `T201` (replaced `print` with `sys.stderr.write`) and `F841` (dropped unused `result` assignment); clean on second run
- `make license-check`: 0 missing headers

## Key decisions

- Module-level imports in `entrypoint.py` so tests can patch `codemie.enterprise.sync.entrypoint.KeycloakClient` and `codemie.enterprise.sync.entrypoint.get_async_session` cleanly
- `_REQUIRED_ENV_VARS` includes the four Keycloak admin vars (all default to empty string) plus `POSTGRES_HOST`
- `_validate_env` uses `sys.stderr.write` instead of `print` to satisfy ruff T201
- `personal_project_service=None` and `dynamic_config_service=None` — these services are not needed for the sync path

## Commit

`ff98c5e1b` — `EPMCDME-15435: Add dry-run validation and sync entrypoint`
