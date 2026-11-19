DONE
commit: c4d5ca635
files:
  src/codemie/configs/config.py
  src/codemie/rest_api/routers/user_management_router.py
  tests/codemie/rest_api/test_user_management_router_idp_guard.py
test_command: poetry run pytest tests/codemie/rest_api/test_user_management_router_idp_guard.py
result: 9 passed, 0 failed
concerns: none

## Summary

### Config change
Added `IDP_MANAGED_ENABLED: bool = False` after `KEYCLOAK_MIGRATION_ENABLED` in config.py (line 237).

### Router change
- Added `from fastapi import HTTPException, status` to the existing FastAPI import line.
- Added `require_no_idp_lock()` dependency function that raises `HTTP 403` with
  `detail="User management is controlled by the identity provider."` when
  `config.IDP_MANAGED_ENABLED` is `True`.
- Wired `__: None = Depends(require_no_idp_lock)` to 9 mutation handlers:
  `create_user`, `update_user`, `deactivate_user`, `admin_change_password`,
  `add_project_access`, `update_project_access`, `remove_project_access`,
  `add_knowledge_base_access`, `remove_knowledge_base_access`.
- Budget endpoints (`bulk_set_user_budgets`, `reset_user_budget_spending`,
  `set_user_budgets`) and all GET handlers are untouched.

### Tests (TDD)
- Red phase: tests failed with `ImportError: cannot import name 'require_no_idp_lock'`.
- Green phase: all 9 tests pass after implementing the feature.
- Tests verify guard raises 403 + handler wiring via `inspect.signature` + GET exemption.
