DONE

## Task 4: KeycloakSyncCoordinator scaffold — advisory lock, skip logic

### Files created
- `src/codemie/enterprise/sync/__init__.py` — package init with Apache 2.0 header
- `src/codemie/enterprise/sync/coordinator.py` — `CodemieSyncDeps`, `SyncResult`, `KeycloakSyncCoordinator`
- `tests/codemie/enterprise/__init__.py` — package init (was missing)
- `tests/codemie/enterprise/sync/__init__.py` — package init
- `tests/codemie/enterprise/sync/test_coordinator_lifecycle.py` — 4 lifecycle tests

### TDD cycle
- RED: `ModuleNotFoundError: No module named 'codemie.enterprise.sync'` confirmed before implementation
- GREEN: 4/4 tests passed after implementation

### Quality gates
- `make ruff`: passed (auto-removed unused `field` import)
- `make license-check`: 2289 files checked, 0 missing headers

### Commit
`43f681b87` — `EPMCDME-15435: Add KeycloakSyncCoordinator scaffold with advisory lock`

### Notes
- `_LOCK_ID = hash("keycloak_sync_coordinator") & 0x7FFFFFFFFFFFFFFF` — distinct from migration coordinator's hard-coded `7734269302`
- Tests use `asynccontextmanager` pattern to mock `async_leader_lock` as specified
- Per-user reconciliation deferred to Task 5 as instructed
