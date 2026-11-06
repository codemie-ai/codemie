# Provisioning API: Clients, Lock, Member Lookup — Plan (EPMCDME-16904)

**Goal:** Replace the ServiceNow service account with the provisioning client registry and lock clients to `/v1/provisioning`. Serve `GET /v1/provisioning/projects` and remove `/v1/integrations/servicenow` and `SERVICENOW_*`.
**Architecture:**
- Registry under `configs/`, read only through `find_client`.
- Access module: lock in `authenticate()`, clients-only guard on the parent router, per-route `RequirePermission`.
- `main.py` mounts the router package and hands the parent router to the lock.

**Spec:** `spec.md` (this directory). Commit per task using the repository's existing convention (`EPMCDME-16904: Capitalized description`).

## Global Constraints
- Permission codes: `provisioning:read-projects`, `provisioning:manage-projects`, `provisioning:manage-budgets`. Only read-projects is checked.
- The registry is enforced whatever `PROVISIONING_API_ENABLED` says. The router is mounted only when the flag and `ENABLE_USER_MANAGEMENT` are both on.
- Logs: `key=value`, `domain=provisioning`, plus the client name. The lookup carries no vendor names.
- Stay unchanged: the `bind_key` branch (`authentication.py:123-133`), `teams_authentication_resolver.py`, `authorized_apps_config.py`, `src/codemie_tools/itsm/servicenow/`, and the budget logic plus `ASSIGN_ADMINS_ACTION` in `servicenow_integration_service.py` with its tests.

## Review Focus
1. A registry `user_id` whose letter case differs from the token `sub` must still match, so the lock stays on (T1).
2. An id equal to `TEAMS_SERVICE_ACCOUNT_ID` would escape the lock through impersonation, so startup rejects it (T1).
3. With the API off, the allowlist is empty and a client gets 403 on every route (T3).
4. A client request with no matched `route` in scope gets 403 (T3).
5. An admin gets 403 on `/v1/provisioning/*` (T4).

---

### Task 1: Provisioning client registry
**Test-first: yes — `test_provisioning_clients_config.py` fails because `codemie.configs.provisioning_clients_config` does not exist.**

**Files:**
- Create `src/codemie/configs/provisioning_clients_config.py`, `config/provisioning_clients/provisioning-clients-config.yaml`, `tests/codemie/configs/provisioning_clients_support.py` and `tests/codemie/configs/test_provisioning_clients_config.py`.
- `config.py`:
  - After `:131`, add `PROVISIONING_CLIENTS_CONFIG_DIR: Path = Path(__file__).absolute().parents[3] / "config/provisioning_clients"`.
  - Before `:259`, add a "Provisioning API" section with `PROVISIONING_API_ENABLED: bool = False`.

**Produces:**
```python
class ProvisioningPermission(StrEnum): READ_PROJECTS, MANAGE_PROJECTS, MANAGE_BUDGETS  # the three codes
class ProvisioningClient(BaseModel):  # extra="forbid", frozen
    name: str; user_id: str  # name stripped non-empty; user_id stored as str(UUID(value))
    permissions: frozenset[ProvisioningPermission] = frozenset()
class ProvisioningClientsConfig(BaseModel):
    clients: list[ProvisioningClient] = []
    @classmethod
    def load(cls, config_path: Path) -> "ProvisioningClientsConfig": ...  # ValueError on any defect
    def find_client(self, user_id: str) -> ProvisioningClient | None: ...  # sole lookup, case-insensitive
    @property
    def is_empty(self) -> bool: ...
provisioning_clients_config = ProvisioningClientsConfig.load(config.PROVISIONING_CLIENTS_CONFIG_DIR / "provisioning-clients-config.yaml")
```
Test support:
- `CLIENT_USER_ID`: a fixed UUID.
- `make_client(name="support-portal", user_id=CLIENT_USER_ID, permissions=frozenset({READ_PROJECTS}))`.
- `registered_clients(*clients)`: `patch.object(provisioning_clients_config, "clients", list(clients))`.

- [ ] **Step 1: Failing tests** (YAML written under `tmp_path`):
  - `test_loads_clients_and_finds_by_user_id`: the upper-cased id is found; an unknown id returns `None`.
  - `test_empty_permissions_list_is_valid`
  - `test_shipped_registry_is_empty`
  - `test_default_dir_is_anchored_to_repo_root`
  - `test_invalid_entry_fails_naming_it`, parametrized with `beta` as the second entry. Each case raises `ValueError` matching `provisioning_clients\[1\] \(beta\)`, or `<unnamed>` when the name is missing. Cases:
    - missing or empty `name` or `user_id`
    - `user_id: beta-client`
    - an unknown permission or an unknown key
    - a duplicate `name`
    - a duplicate `user_id` that differs only in case
    - an id equal to `TEAMS_SERVICE_ACCOUNT_ID`, patched to a UUID
  - `test_unreadable_registry_fails`: invalid YAML, a missing file, a root that is not a mapping, or a list key that is not a list. Each raises a `ValueError` tagged `[ProvisioningClientsConfig]`.
- [ ] **Step 2: Implement.**
  - Error message: `[ProvisioningClientsConfig] provisioning_clients[<0-based i>] (<name|<unnamed>>): <reason>`.
  - Wrap each entry's `ValidationError`, then check duplicates and the Teams id. Parse as `authorized_apps_config.py:116-125` does.
  - Header of the shipped file: it matches the CodeMie user id (token `sub`, a UUID), not the username. It also carries the spec Design 1 "Lifecycle" text and a commented example.
  - Body of the shipped file: `provisioning_clients: []`.
- [ ] **Step 3: Verify:** `poetry run pytest tests/codemie/configs/test_provisioning_clients_config.py -q` passes.

### Task 2: Registered clients count as service accounts
**Test-first: yes — `is_service_account` returns False for a registered client stored as `regular`.**

**Files:**
- Modify `user_type_validator.py:41-48`, `authentication_service.py:392-395` and `personal_project_service.py:31,125`.
- Tests: `test_user_type_validator.py`, `test_personal_project_service.py` and `test_authentication_service.py:1904-1927`.
- `test_servicenow_integration_service.py:117-129`: the fixture `servicenow_account` becomes `registered_client` (`CLIENT_USER_ID` under `registered_clients(make_client())`). Rename the tests at `:324` and `:512`.

**Produces:** `is_personal_project_excluded_account(user_id: str, user_type: str) -> bool`. `is_service_account(user)` keeps its signature.

- [ ] **Step 1: Failing tests:**
  - `test_registered_client_is_service_account_whatever_its_type`, over three types
  - an unregistered `regular` user is not a service account
  - `is_personal_project_excluded_account`: registered `regular` gives True; unregistered `regular` gives False; `external` gives True
  - rename `:1904` to `test_raises_401_when_resolved_user_is_registered_client_of_regular_type`
  - `test_finalize_authentication_skips_personal_project_for_registered_client`
  - `test_reconcile_on_email_change_skips_registered_client`
- [ ] **Step 2: Implement.**
  - A module-level import of `provisioning_clients_config` replaces the lazy import at `:46`.
  - `is_service_account`: stored type OR `find_client(user.id) is not None`.
  - The new function: `is_personal_project_excluded(user_type)` OR registered.
  - Switch only `authentication_service.py:395` and `personal_project_service.py:125`. Leave `registration_service.py:214,251`, `authentication_service.py:787` and `cli_analytics_project_gate.py` as they are.
- [ ] **Step 3: Verify:** the four test files pass.

### Task 3: Access lock and guards
**Test-first: yes — `authenticate()` returns a registered client on a non-provisioning route instead of raising 403.**

**Files:**
- Create `src/codemie/rest_api/security/provisioning_access.py` and `tests/codemie/rest_api/security/test_provisioning_access.py`.
- `authentication.py:28,155`: `enforce_servicenow_scope` becomes `enforce_provisioning_scope`. `is_servicenow_service_account` stays until T5.
- Delete the orphaned `servicenow_access.py:53-81`.
- Delete the tests of the replaced lock: `test_servicenow_access.py:90-123`, plus `TestAllowlistMatchesRouter`, `TestRealAuthenticateScope` and the imports at `:47-48` in `test_servicenow_integration_router.py`.
- In `test_authentication.py`, replace `TestAuthenticateServiceNowScope` (`:569-…`) with `TestAuthenticateProvisioningScope`.

**Produces:**
```python
def set_provisioning_routes(router: APIRouter) -> None  # frozenset((method, route.path))
def enforce_provisioning_scope(user: User, request: Request) -> None
async def provisioning_clients_only(request: Request) -> None
class RequirePermission:  # .permission; __call__(request) -> ProvisioningClient, 403 if not held
    def __init__(self, permission: ProvisioningPermission) -> None: ...
```
- [ ] **Step 1: Failing tests** (`monkeypatch` resets the stored routes):
  - Lock:
    - an unregistered user passes
    - a client passes on a stored (method, path)
    - a client gets 403 on another route, on another method, with no `route` in scope, or when no router is set
  - Clients-only guard: a client of any `user_type` passes; an unregistered regular user or an admin gets 403.
  - `RequirePermission(READ_PROJECTS)`: a holder gets the client back; empty `permissions` or an unregistered caller gets 403.
  - `TestAuthenticateProvisioningScope`:
    - External branch: a client gets 403 on `/v1/other` and the user back on a stored route; a regular user gets the user back.
    - `bind_key` branch (patch `_verify_internal_request` and `LocalIdp`): a client on `/v1/other` gets the user back.
- [ ] **Step 2: Implement.**
  - The lock reads no flag.
  - Import `ACCESS_DENIED_MESSAGE` inside the function, as `servicenow_access.py:65` does, because of the import cycle.
  - Logs, each with `domain=provisioning`:
    - `access_denied_provisioning_scope`, with `client`, `actor_user_id`, `method` and `route`
    - `access_denied_provisioning_client`, with `actor_user_id`
    - `access_denied_provisioning_permission`, with `client` and `permission`
  - 403 details carry no vendor names.
- [ ] **Step 3: Verify:** these pass: the new test file, `test_authentication.py`, `test_servicenow_access.py` and `test_servicenow_integration_router.py`.

### Task 4: Member lookup moves to `/v1/provisioning`
**Test-first: yes — `GET /v1/provisioning/projects` returns 404 instead of 200 with the union.**

**Files:**
- Create `rest_api/models/provisioning.py`, `rest_api/routers/provisioning/__init__.py`, `rest_api/routers/provisioning/projects.py` and `service/provisioning_service.py`.
- Create tests `tests/codemie/rest_api/routers/test_provisioning_router.py` and `tests/codemie/service/test_provisioning_service.py`.
- `models/servicenow_integration.py`:
  - `:25-29,82`: import `strip_and_lowercase` and `NormalizedEmail` from `provisioning`.
  - `:104-110`: remove `ServiceNowProjectResponse`.
- `routers/servicenow_integration.py`: remove the old route at `:76-83,138-159` and its now-unused imports. Remove its `PROJECTS_URL` cases in `test_servicenow_integration_router.py`.
- `servicenow_integration_service.py`: remove `:383-395`. Move `TestListProjects` and its fixture (`test_servicenow_integration_service.py:486-540`) to the new service test.
- `main.py`: call `mount_provisioning_api(app)` after `:1509`.
- Router guards:
  - `guarded_app.py:22-25,52-56`: add `"PROVISIONING_API_ENABLED": "true"` and a docstring line.
  - `test_guarded_app_profile.py:31-39`: add a `provisioning` parameter.

**Produces:**
- `strip_and_lowercase(value: Any) -> Any`.
- `NormalizedEmail`.
- `ProvisioningProjectResponse(project_name: str, display_name: str | None = None)`.
- `provisioning_service.list_member_projects(emails: list[str]) -> list[Application]`, with the body moved from `servicenow_integration_service.py:384-395`.
- `router = APIRouter(prefix="/v1/provisioning", tags=["Provisioning"], dependencies=[Depends(authenticate), Depends(provisioning_clients_only)])`, which includes `projects.router`.
- `mount_provisioning_api(app: FastAPI) -> None`:
  1. flag off: return
  2. user management off: warn `provisioning_api_disabled` and return
  3. empty registry: warn `provisioning_api_no_clients`
  4. `app.include_router(router)`, then `set_provisioning_routes(router)`

The route `GET /projects`:
- Parameters: `member_email: list[NormalizedEmail] = Query(...)` and `client = Depends(RequirePermission(ProvisioningPermission.READ_PROJECTS))`.
- Returns `list[ProvisioningProjectResponse]`.
- Logs `provisioning_member_lookup` with `client` and the counts of `emails` and `projects`.
- Copy is adapted from the removed route and model. The vendor line becomes "Callable only by registered provisioning clients holding `provisioning:read-projects`."

- [ ] **Step 1: Failing tests.** Use a bare `FastAPI` app with `mount_provisioning_api`. Use the real `authenticate`, with the provider and IdP patched as at `test_servicenow_integration_router.py:455-488`, and mock the service.
  - `test_client_gets_sorted_union_for_repeated_member_email`: ` A@EPAM.com ` and `b@epam.com` give 200. The service gets `["a@epam.com", "b@epam.com"]`. The body is `[{project_name, display_name}]`.
  - `test_missing_or_invalid_member_email_is_422`
  - `test_client_without_read_projects_is_403`
  - `test_non_client_and_admin_are_403`
  - `test_client_reaches_lookup_but_not_other_routes`
  - `test_api_off_or_user_management_off_gives_404`, plus the warning
  - `test_empty_registry_logs_warning`
  - `test_every_provisioning_route_declares_one_permission_after_guards` (AC11):
    - every `APIRoute` in `router.routes` has exactly one `RequirePermission` whose `.permission` is in `ProvisioningPermission`
    - in `[d.call for d in route.dependant.dependencies]`, `authenticate` comes before `provisioning_clients_only`, which comes before that permission
  - `test_lookup_openapi_is_vendor_free`: tag `Provisioning`, and the lowercased operation JSON has no `servicenow`.
  - The moved service tests, plus a registered-client case.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Verify.**
  - Run `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`.
  - The baseline diff removes `/v1/integrations/servicenow/projects` and adds `/v1/provisioning/projects` and its schema.
  - The new tests, the router guards and both servicenow test files pass.

### Task 5: Remove the ServiceNow routes and settings
**Test-first: yes — `test_no_vendor_integration_path_is_served` (`test_guarded_app_profile.py`) fails while `/v1/integrations/servicenow/budgets` is still served.**

**Files:**
- Delete `routers/servicenow_integration.py`, `security/servicenow_access.py` and their test files (`test_servicenow_integration_router.py`, `test_servicenow_access.py`).
- `authentication.py:28,246-258`: remove the guard and its import.
- `models/servicenow_integration.py:113-142`: remove the 3 budget response models.
- Remove `config.py:259-263` and `main.py:84,1511-1523`.
- `guarded_app.py:22-25,55`: drop the ServiceNow flag.
- Keep `ServiceNowBudgetAction`, `ServiceNowBudgetRequest`, the service and its tests.

- [ ] **Step 1: Failing test:** no `dump["openapi"]["paths"]` key starts with `/v1/integrations/servicenow`.
- [ ] **Step 2: Remove, then regenerate baselines** (the command from T4).
- [ ] **Step 3: Verify:**
  - The router guards and `test_servicenow_integration_service.py` pass.
  - `grep -rnE "SERVICENOW_INTEGRATION_ENABLED|SERVICENOW_SERVICE_ACCOUNT_ID|servicenow_access|integrations/servicenow" src tests` hits only `servicenow_integration_service.py:67`. That line is `ASSIGN_ADMINS_ACTION`, left for 16905.
  - `git diff --stat main -- src/codemie_tools/itsm/servicenow/` is empty (AC10).

---

## Negative constraints (checked)
- The registry is enforced whatever the flag says: T1 loads it at import, and T2 and T3 read no flag.
- The `bind_key` branch is untouched: T3 hooks only `:155` and pins the branch with a test.
- Admins get 403: T3 and T4.
- Registration and local login keep the type check: T2.
- Non-goals hold:
  - no budget route and no manage-* check is added
  - budget logic is not moved; only the lookup moves (Design 5)
  - the Teams resolver and authorized applications are untouched (T1 only reads `TEAMS_SERVICE_ACCOUNT_ID`)
  - the agent tool is untouched (T5 check)
  - no UI and no reload
- No task violates a constraint.

**Arch-review plan notes:**
- Fixture retarget: T2.
- `NormalizedEmail`: T4.
- `ASSIGN_ADMINS_ACTION` is left: T5.
- One `StrEnum`, one `find_client`, and the registry under `configs/`: T1 and T2.
- Anchored default directory and Teams id rejection: T1.
- Router hand-off from `main.py`: T3 and T4.
- Checksum annotation and validate command: not planned. Spec Design 1 chose a documented rollout restart, and the Helm chart is outside the spec.
