DONE

## Task 2: Service-layer guards + public config exposure

### Commit
`EPMCDME-15435: Add IDP guard to service layer and expose in public config`  
Branch: `epmcdme-15435`

### Changes made

**`src/codemie/service/user/authentication_service.py`**
- Added `from fastapi import HTTPException` import
- Added `if config.IDP_MANAGED_ENABLED: raise HTTPException(403, ...)` at entry of `create_user_from_idp`

**`src/codemie/service/user/user_access_service.py`**
- Added `from fastapi import HTTPException` and `from codemie.configs import config` imports
- Added 403 guard at entry of: `grant_project_access`, `revoke_project_access`, `grant_kb_access`, `revoke_kb_access`

**`src/codemie/core/models.py`**
- Added `idp_managed_enabled: bool = False` field to `InfoResponse` (serializes as `idpManagedEnabled` via ConfiguredModel camelCase alias)

**`src/codemie/rest_api/routers/common.py`**
- Added `idp_managed_enabled=config.IDP_MANAGED_ENABLED` to `InfoResponse(...)` constructor in `app_info()`

**`tests/codemie/service/user/test_idp_managed_service_guards.py`** (new)
- 6 tests: create_user_from_idp raises/no-raise, grant/revoke project raises, grant/revoke KB raises

**`tests/codemie/rest_api/routers/test_common_router.py`**
- Added `test_app_info_includes_idp_managed_enabled` asserting `idpManagedEnabled` is present and boolean False

### Test results
- Service guard tests: 6/6 passed
- Common router tests: 5/5 passed (including new test)
- `make ruff`: all checks passed (1 file auto-formatted)
- `make license-check`: 0 missing headers
