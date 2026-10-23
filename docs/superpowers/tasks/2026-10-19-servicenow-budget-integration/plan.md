# ServiceNow Budget Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two endpoints that let ServiceNow set, adjust or cancel a project's budget group, resolving the project and cost center and assigning admins along the way. A scoped, non-admin service account calls them.

**Architecture:** A thin router (`/v1/integrations/servicenow`) delegates to `ServiceNowIntegrationService`, which runs two transactions: a sync one (project, cost center, admins) and an async one (budget group). It reuses the existing project, cost center, assignment and budget-group services; two small public methods are added for permission-free creation. Access is enforced in two directions: inside `authenticate()` (the account is limited to the integration routes) and by a route guard (only the account can call them).

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLModel / SQLAlchemy (sync `Session` + `AsyncSession`), pytest + pytest-asyncio / anyio, `unittest.mock`.

**Spec:** `docs/superpowers/tasks/2026-10-19-servicenow-budget-integration/spec.md`

## Global Constraints

- All paths are relative to `repos/codemie`. Run commands from there.
- Test command: `poetry run pytest <path> -v`. Lint: `make ruff` (see `.ai-run/guides/quality-gates.md`).
- Commit subject format: `EPMCDME-<ticket>: <summary>`. Ask the user for the ticket before the first commit. Commits happen only with the user's go-ahead (`AGENTS.md`).
- Route prefix `/v1/integrations/servicenow`; tag `"ServiceNow Integration"`.
- Settings: `SERVICENOW_INTEGRATION_ENABLED: bool = False`, `SERVICENOW_SERVICE_ACCOUNT_ID: str = ""`.
- Service account check: integration enabled AND `user.id == SERVICENOW_SERVICE_ACCOUNT_ID` (non-empty) AND `user.user_type == SERVICE_ACCOUNT_USER_TYPE`.
- Group defaults on create: `budget_duration="30d"`, `name=f"ServiceNow {request_id}"[:100]`, soft limit omitted (80% default).
- Email lists: max 20 entries. `request_id`: 1–64 chars. Names: 3–100 chars, lowercased.
- Every budget call uses the project's stored `Application.name`, never the lowercased request value; the budget service matches names case-sensitively.
- The router is registered only when both `SERVICENOW_INTEGRATION_ENABLED` and `ENABLE_USER_MANAGEMENT` are true.
- Log format: `event_name: key=value, ..., domain=servicenow_integration`, with `!r` on names and emails.
- Errors: `ExtendedHTTPException(code, message, details, help)`. Schema errors surface as 422 through the app's existing `RequestValidationError` handler.
- Methods that skip permission checks state "caller must authorize" in their docstring.
- No comments by default; match each file's existing docstring and comment density.

## Review Focus

1. **Emails with stray whitespace or capitals** (`" Requester@EPAM.com "`), in the POST body or the GET query, must match the stored user, not fail with 422 or land in `admins_pending`. Tested in Task 5.
2. **Names that differ only by case** (`epam_project_code="NBS-CMPA"`, `codemie_project_name="nbs-cmpa"`) are equal after lowercasing, so no cost center is involved. Tested in Task 5 (normalization) and Task 7 (the equal-names path).
3. **An `adjust` that resends the current amounts** must leave soft limits untouched, not reset them to 80%. Tested in Task 6.
4. **A retry after a partial failure** (project and admins committed, budget failed) must reuse the project, not re-add admins, and then apply the budget. Tested in Task 8.
5. **The scope allowlist must match the real router's paths**, including the prefix. A typo would lock the account out of its own routes or let it into others. Tested in Task 9 against the real router.

---

### Task 1: Repository queries for active users by email and shared projects for users

Test-first: yes — `get_active_by_emails` / `list_shared_projects_for_users` don't exist, so the new repository tests fail with `AttributeError`.

**Files:**
- Modify: `src/codemie/repository/user_repository.py` (next to `get_by_emails`, ~line 350)
- Modify: `src/codemie/repository/application_repository.py` (next to `get_project_types_bulk`, ~line 514)
- Test: `tests/codemie/repository/test_user_repository_crud.py` (next to the `get_by_emails` tests, ~line 443)
- Test: `tests/codemie/repository/test_application_repository_extended.py`

**Interfaces:**
- Produces: `UserRepository.get_active_by_emails(self, session: Session, emails: list[str]) -> dict[str, UserDB]`. Keys are lowercase emails; only `is_active` users with `deleted_at IS NULL`; matching is case-insensitive.
- Produces: `ApplicationRepository.list_shared_projects_for_users(self, session: Session, user_ids: list[str]) -> list[Application]`. `UserProject` joined on `UserProject.project_name == Application.name`; `user_id IN`, `project_type == 'shared'`, `deleted_at IS NULL`; distinct; ordered by `Application.name`.

- [ ] **Step 1: Write the failing tests**

Follow the existing mocked-session style. For filters, capture the statement passed to `session.exec` and compile it with `literal_binds`.

```python
class TestGetActiveByEmails:
    def test_returns_empty_without_query_for_empty_input(self, user_repository, db_session):
        assert user_repository.get_active_by_emails(db_session, []) == {}
        db_session.exec.assert_not_called()

    def test_maps_lowercased_email_to_user(self, user_repository, db_session):
        alice = UserDB(id="u1", email="Alice@Example.com", username="alice", name="Alice")
        db_session.exec.return_value.all.return_value = [alice]
        assert user_repository.get_active_by_emails(db_session, ["ALICE@example.com"]) == {"alice@example.com": alice}

    def test_filters_inactive_and_deleted_users(self, user_repository, db_session):
        db_session.exec.return_value.all.return_value = []
        user_repository.get_active_by_emails(db_session, ["a@example.com"])
        sql = str(db_session.exec.call_args.args[0].compile(compile_kwargs={"literal_binds": True}))
        assert "lower(users.email) IN ('a@example.com')" in sql
        assert "users.is_active IS true" in sql
        assert "users.deleted_at IS NULL" in sql
```

```python
class TestListSharedProjectsForUsers:
    def test_returns_empty_without_query_for_empty_input(self, db_session):
        assert ApplicationRepository().list_shared_projects_for_users(db_session, []) == []
        db_session.exec.assert_not_called()

    def test_query_joins_membership_and_filters_shared_active(self, db_session):
        db_session.exec.return_value.all.return_value = []
        ApplicationRepository().list_shared_projects_for_users(db_session, ["u1", "u2"])
        sql = str(db_session.exec.call_args.args[0].compile(compile_kwargs={"literal_binds": True}))
        assert "JOIN user_projects" in sql
        assert "user_projects.user_id IN ('u1', 'u2')" in sql
        assert "applications.project_type = 'shared'" in sql
        assert "applications.deleted_at IS NULL" in sql
        assert "DISTINCT" in sql
        assert "ORDER BY applications.name" in sql
```

Adjust the literal table and column names in the assertions to what the models actually compile to (check `__tablename__`); the assertions must pin each filter.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/repository/test_user_repository_crud.py tests/codemie/repository/test_application_repository_extended.py -k "ActiveByEmails or SharedProjectsForUsers" -v`
Expected: FAIL with `AttributeError: ... has no attribute 'get_active_by_emails'` / `'list_shared_projects_for_users'`.

- [ ] **Step 3: Implement both methods**

`get_active_by_emails` mirrors `get_by_emails` and adds the `is_active` / `deleted_at` filter from `get_active_by_id`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/user_repository.py src/codemie/repository/application_repository.py tests/codemie/repository/test_user_repository_crud.py tests/codemie/repository/test_application_repository_extended.py
git commit -m "EPMCDME-<ticket>: Add active-by-email user lookup and shared projects for users query"
```

---

### Task 2: `CostCenterService.get_or_create`

Test-first: yes — `TestGetOrCreate` fails with `AttributeError: 'get_or_create'`.

**Files:**
- Modify: `src/codemie/service/cost_center_service.py` (`create`, lines ~71-96)
- Test: `tests/codemie/service/test_cost_center_service.py`

**Interfaces:**
- Produces: `CostCenterService.get_or_create(cls, session: Session, *, name: str, created_by: str) -> tuple[CostCenter, bool]`. The bool is `True` when created.
  - Calls `ensure_feature_enabled()` (403 when `costCenters` is off) and `validate_name(name)` (400).
  - Lookup: `cost_center_repository.get_by_name_case_insensitive`. An active match returns `(match, False)`. A match with `deleted_at` set raises 409, message `f"Cost center '{match.name}' has been deleted"`, help `"Restore the cost center or use a different EPAM project code."`
  - No admin check, flush only, no commit. `IntegrityError` from the insert **propagates** (the caller retries).
  - Logs `cost_center_created` in the same format as `create`.
- `create` keeps its behavior (admin check, 409 on an existing name, `IntegrityError` → 409). The insert and `cost_center_created` log move into one private classmethod `_insert(cls, session, *, name, description, created_by) -> CostCenter` that both methods call.

- [ ] **Step 1: Write the failing tests**

```python
class TestGetOrCreate:
    def test_returns_existing_active_cost_center(self, ...):  # repo match with deleted_at=None
        assert CostCenterService.get_or_create(session, name="nbs-cmpa", created_by="sa-1") == (existing, False)
        mock_repo.create.assert_not_called()

    def test_creates_when_missing(self, ...):
        cost_center, created = CostCenterService.get_or_create(session, name="nbs-cmpa", created_by="sa-1")
        assert created is True
        mock_repo.create.assert_called_once_with(session, name="nbs-cmpa", description=None, created_by="sa-1")

    def test_soft_deleted_match_raises_409(self, ...):  # match.deleted_at = datetime(...)
        with pytest.raises(ExtendedHTTPException) as exc:
            CostCenterService.get_or_create(session, name="nbs-cmpa", created_by="sa-1")
        assert exc.value.code == 409

    def test_feature_disabled_raises_403(self, ...):  # customer_config.is_feature_enabled -> False
        ...  # assert code == 403, repo not touched

    def test_invalid_name_raises_400(self, ...):  # name="nbs--cmpa"
        ...  # assert code == 400, repo not touched

    def test_does_not_require_admin(self, ...):
        ...  # succeeds without any user argument; ensure_admin not called

    def test_integrity_error_propagates(self, ...):  # repo.create side_effect=IntegrityError(...)
        with pytest.raises(IntegrityError):
            CostCenterService.get_or_create(session, name="nbs-cmpa", created_by="sa-1")
```

Patch targets: `codemie.service.cost_center_service.cost_center_repository` and `codemie.service.cost_center_service.customer_config`, as the existing tests in this file do.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_cost_center_service.py -v`
Expected: the new tests FAIL with `AttributeError: ... 'get_or_create'`; existing tests PASS.

- [ ] **Step 3: Implement `get_or_create` and `_insert`; refactor `create` to call `_insert`**

- [ ] **Step 4: Run the whole file to verify it passes**

Run: the command from Step 2. Expected: all PASS, existing tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/cost_center_service.py tests/codemie/service/test_cost_center_service.py
git commit -m "EPMCDME-<ticket>: Add permission-free cost center get-or-create"
```

---

### Task 3: `ProjectService.create_shared_project_in_session`

Test-first: yes — `TestCreateSharedProjectInSession` fails with `AttributeError: 'create_shared_project_in_session'`.

**Files:**
- Modify: `src/codemie/service/project/project_service.py` (`create_shared_project` lines ~132-206; `_validate_shared_project_name` ~465 and its callers at ~141, ~365)
- Test: `tests/codemie/service/project/test_project_service.py`

**Interfaces:**
- Produces: `ProjectService.validate_shared_project_name(cls, name: str) -> str`. The existing `_validate_shared_project_name` is renamed (made public) and its 3 references in `src` updated; no test references it. Behavior is unchanged.
- Produces: `ProjectService.create_shared_project_in_session(cls, session: Session, *, actor: User, name: str, cost_center_id: UUID | None = None, display_name: str | None = None, description: str | None = None) -> Application`. Inside the caller's session, in this order:
  1. `validate_shared_project_name(name)`
  2. `cost_center_service.ensure_exists_for_project(session, cost_center_id)`
  3. duplicate check via `get_by_name_case_insensitive`: 409 with `_duplicate_project_message`, which includes soft-deleted matches
  4. `application_repository.create(... project_type=SHARED, created_by=actor.id[, cost_center_id])`
  5. allowed and default models initialized from `llm_service.get_allowed_chat_models(actor, include_all=False)`, exactly as today
  6. PROJECT_CREATED activity event with `actor_id=actor.id`

  It does **not** add a membership, check the project limit, commit, expunge or invalidate the cache. `IntegrityError` propagates. The docstring says "caller must authorize".
- `create_shared_project` keeps its public signature and behavior: description and display_name validation, limit check, then `create_shared_project_in_session`, then the creator membership, expunge, commit, cache invalidation, the `project_created` log, and `IntegrityError` → 409.

- [ ] **Step 1: Write the failing tests**

```python
class TestCreateSharedProjectInSession:
    def test_creates_project_without_creator_membership(self, ...):
        project = ProjectService.create_shared_project_in_session(session, actor=service_account, name="epm-aimf")
        mock_application_repository.create.assert_called_once()
        assert mock_application_repository.create.call_args.kwargs["created_by"] == service_account.id
        mock_user_project_repository.add_project.assert_not_called()
        session.commit.assert_not_called()

    def test_skips_project_limit(self, ...):
        ...  # service_account.is_admin is False; application_repository.count_shared_projects_created_by_user not called

    def test_links_cost_center(self, ...):
        ...  # cost_center_id passed through: ensure_exists_for_project(session, cc_id) called; create kwargs["cost_center_id"] == cc_id

    def test_soft_deleted_name_raises_409(self, ...):  # get_by_name_case_insensitive returns app with deleted_at set
        ...  # code == 409, create not called

    def test_initializes_allowed_and_default_models(self, ...):
        ...  # project.allowed_models == validated list; project.default_model == list[0]

    def test_writes_project_created_activity_event(self, ...):
        ...  # activity_event_repository.insert called with event_type=PROJECT_CREATED, actor_id=service_account.id

    def test_invalid_name_raises_400_before_db(self, ...):  # name="EPM AIMF"
        ...  # code == 400; application_repository not touched
```

Reuse the decorator-patch style of `test_create_shared_project_success` (patch `codemie.service.project.project_service.*`, including `llm_service`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/project/test_project_service.py -v`
Expected: new tests FAIL with `AttributeError: ... 'create_shared_project_in_session'`; existing tests PASS.

- [ ] **Step 3: Rename the validator and implement `create_shared_project_in_session`; refactor `create_shared_project` to call it**

- [ ] **Step 4: Run the project service test suite to verify it passes**

Run: `poetry run pytest tests/codemie/service/project/ -v`
Expected: all PASS. The existing `create_shared_project` tests pass unchanged, which shows public behavior is preserved.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/project/project_service.py tests/codemie/service/project/
git commit -m "EPMCDME-<ticket>: Extract in-session shared project creation without creator membership"
```

---

### Task 4: Config, service-account scope check and route guard

Test-first: yes — `test_servicenow_access.py` fails with `ModuleNotFoundError: codemie.rest_api.security.servicenow_access`, and `TestAuthenticateServiceNowScope` fails because a blocked route returns the user instead of raising 403.

**Files:**
- Modify: `src/codemie/configs/config.py` (new banner block after the user-management block, ~line 252)
- Create: `src/codemie/rest_api/security/servicenow_access.py`
- Modify: `src/codemie/rest_api/security/authentication.py` (call after the Teams block at ~line 150; new guard next to `maintainer_access_only` ~line 229)
- Test: `tests/codemie/rest_api/security/test_servicenow_access.py` (create)
- Test: `tests/codemie/rest_api/security/test_authentication.py` (new class next to `TestAuthenticateTeamsSenderBranch`, ~line 569)

**Interfaces:**
- Produces (config): `SERVICENOW_INTEGRATION_ENABLED: bool = False` and `SERVICENOW_SERVICE_ACCOUNT_ID: str = ""` under a `# ServiceNow Integration` banner.
- Produces: `is_servicenow_service_account(user: User) -> bool` in `servicenow_access.py`, following the Global Constraints rule.
- Produces: `_allowed_routes() -> frozenset[tuple[str, str]]`, decorated with `functools.cache`. It lazily imports `codemie.rest_api.routers.servicenow_integration.router` and returns `{(method, route.path) for route in router.routes for method in route.methods}`. The router is created in Task 9; tests here patch this function.
- Produces: `enforce_servicenow_scope(user: User, request: Request) -> None`. A no-op unless `is_servicenow_service_account(user)`. Otherwise, a request whose `request.scope.get("route")` is missing, or whose `(request.method, route.path)` isn't in `_allowed_routes()`, logs `access_denied_servicenow_scope: actor_user_id=..., method=..., route=..., domain=servicenow_integration` and raises 403 (`message=ACCESS_DENIED_MESSAGE`, `details="This service account may only call the ServiceNow integration endpoints."`).
- Produces: `async def servicenow_integration_access_only(request: Request) -> None` in `authentication.py`. It raises 403 (`ACCESS_DENIED_MESSAGE`, `details="This endpoint is reserved for the ServiceNow integration service account."`) and logs `access_denied_servicenow_integration: actor_user_id=..., domain=servicenow_integration` unless `is_servicenow_service_account(request.state.user)`.
- Wiring: `authenticate()`'s external branch imports `enforce_servicenow_scope` lazily, as the Teams resolver is imported, and calls it right after the Teams swap.
- Module docstring for `servicenow_access.py` follows the style of `teams_authentication_resolver.py`: why it's standalone (one caller in `authenticate()`, one guard).

- [ ] **Step 1: Write the failing tests**

`test_servicenow_access.py` (patch `codemie.rest_api.security.servicenow_access.config` and `..._allowed_routes`):

```python
ALLOWED = frozenset({("GET", "/v1/integrations/servicenow/projects"), ("POST", "/v1/integrations/servicenow/budgets")})

def _sa(user_id="sn-sa-1", user_type="service_account"):
    return User(id=user_id, email="sn@svc.example.com", user_type=user_type, project_names=[])

def _request(method, path):
    request = MagicMock()
    request.method = method
    request.scope = {"route": SimpleNamespace(path=path)} if path else {}
    return request

class TestIsServiceNowServiceAccount:
    # enabled + matching id + service_account -> True
    # disabled -> False; id mismatch -> False; user_type "regular" -> False
    # configured id "" and user.id "" -> False

class TestEnforceServiceNowScope:
    def test_allowed_route_passes(self, ...): enforce_servicenow_scope(_sa(), _request("POST", "/v1/integrations/servicenow/budgets"))
    def test_same_path_other_method_is_403(self, ...): ...  # ("DELETE", ".../budgets") -> code 403
    def test_other_route_is_403(self, ...): ...  # ("GET", "/v1/admin/project-budget-groups") -> 403
    def test_missing_route_is_403(self, ...): ...  # scope without "route" -> 403
    def test_other_users_are_not_checked(self, ...): ...  # regular user on "/v1/admin/..." -> no raise, _allowed_routes not called
    def test_disabled_integration_is_not_checked(self, ...): ...  # SA on "/v1/admin/..." with ENABLED False -> no raise

class TestServiceNowIntegrationAccessOnly:   # anyio
    # service account -> returns None
    # regular user, admin (is_admin=True), other service account id, right id + user_type "regular" -> code 403
```

In `test_authentication.py`, add `TestAuthenticateServiceNowScope`. Reuse the `_authenticate_as` pattern, setting `request.method` and `request.scope["route"]`:
- the ServiceNow account on a blocked route: `authenticate()` raises `ExtendedHTTPException` with code 403;
- the ServiceNow account on an allowed route: returns the user;
- a regular user on the same blocked route: returns the user.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/security/test_servicenow_access.py tests/codemie/rest_api/security/test_authentication.py -v`
Expected: FAIL with `ModuleNotFoundError: codemie.rest_api.security.servicenow_access` (the new file) and failures in the new class.

- [ ] **Step 3: Add the settings, the module, the guard and the `authenticate()` call**

- [ ] **Step 4: Run the tests to verify they pass**

Run: the command from Step 2. Expected: all PASS, including the existing authentication and Teams tests.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/configs/config.py src/codemie/rest_api/security/ tests/codemie/rest_api/security/
git commit -m "EPMCDME-<ticket>: Scope the ServiceNow service account to its integration routes"
```

---

### Task 5: Request and response models

Test-first: yes — `TestServiceNowBudgetRequest` fails with `ModuleNotFoundError: codemie.rest_api.models.servicenow_integration`.

**Files:**
- Create: `src/codemie/rest_api/models/servicenow_integration.py`
- Test: `tests/codemie/rest_api/routers/test_servicenow_integration_router.py` (create; this task adds the `TestServiceNowBudgetRequest` class)

**Interfaces:**
- Produces: `NormalizedEmail = Annotated[EmailStr, BeforeValidator(<strip and lowercase when str>)]`, used by `admin_emails` here and by the GET `emails` query in Task 9.
- Produces: `class ServiceNowBudgetAction(str, Enum)` with `SET = "set"`, `ADJUST = "adjust"`, `CANCEL = "cancel"`.
- Produces: `class ServiceNowBudgetRequest(BaseModel)`:
  - `action: ServiceNowBudgetAction`
  - `request_id: str = Field(min_length=1, max_length=64)`
  - `epam_project_code: str = Field(min_length=3, max_length=100)`
  - `codemie_project_name: Optional[str] = Field(default=None, min_length=3, max_length=100)`
  - `admin_emails: list[NormalizedEmail] = Field(default_factory=list, max_length=20)`
  - `amounts: Optional[dict[BudgetCategory, float]] = None` (values `ge=0`, via `Annotated[float, Field(ge=0)]`)

  `@field_validator("epam_project_code", "codemie_project_name", mode="before")` strips and lowercases. `@field_validator("admin_emails", mode="after")` deduplicates in order, after normalization. `@model_validator(mode="after")`:
  - fills `codemie_project_name` from `epam_project_code` when it's `None`;
  - `set` / `adjust` require a non-empty `amounts` (`ValueError("amounts is required for set and adjust")`);
  - `cancel` forbids `amounts` (`ValueError("amounts must not be sent for cancel")`).
- Produces: `ServiceNowProjectResponse(project_name: str, display_name: Optional[str] = None)`.
- Produces: `ServiceNowBudgetCategoryResponse(category: BudgetCategory, max_budget: float, soft_budget: float, budget_reset_at: Optional[str] = None)`.
- Produces: `ServiceNowBudgetGroupResponse(total_amount: float, budget_duration: str, categories: list[ServiceNowBudgetCategoryResponse])`.
- Produces: `ServiceNowBudgetResponse(project_name: str, cost_center: Optional[str], created_project: bool, created_cost_center: bool, budget: Optional[ServiceNowBudgetGroupResponse], admins_assigned: list[str], admins_pending: list[str])`.
- `BudgetCategory` comes from `codemie.service.budget.budget_enums`.

- [ ] **Step 1: Write the failing tests**

```python
def _payload(**overrides):
    return {"action": "set", "request_id": "RITM0012345", "epam_project_code": "nbs-cmpa",
            "admin_emails": [], "amounts": {"platform": 500.0}} | overrides

class TestServiceNowBudgetRequest:
    def test_project_name_defaults_to_lowercased_code(self):
        request = ServiceNowBudgetRequest(**_payload(epam_project_code=" NBS-CMPA "))
        assert request.epam_project_code == "nbs-cmpa"
        assert request.codemie_project_name == "nbs-cmpa"

    def test_emails_are_trimmed_lowercased_and_deduplicated(self):   # Review Focus 1
        request = ServiceNowBudgetRequest(**_payload(admin_emails=[" Requester@EPAM.com ", "requester@epam.com", "b@epam.com"]))
        assert request.admin_emails == ["requester@epam.com", "b@epam.com"]

    def test_normalized_email_type_for_query_params(self):   # Review Focus 1, GET path
        assert TypeAdapter(list[NormalizedEmail]).validate_python([" A@EPAM.com "]) == ["a@epam.com"]

    def test_names_differing_only_by_case_become_equal(self):   # Review Focus 2
        request = ServiceNowBudgetRequest(**_payload(epam_project_code="NBS-CMPA", codemie_project_name="nbs-cmpa"))
        assert request.epam_project_code == request.codemie_project_name == "nbs-cmpa"

    @pytest.mark.parametrize("overrides", [
        {"amounts": None},                                   # set without amounts
        {"action": "adjust", "amounts": {}},                 # adjust with empty amounts
        {"action": "cancel"},                                # cancel with amounts (from _payload)
        {"amounts": {"gpu": 10.0}},                          # unknown category
        {"amounts": {"platform": -1.0}},                     # negative
        {"admin_emails": [f"u{i}@epam.com" for i in range(21)]},
        {"admin_emails": ["not-an-email"]},
        {"request_id": ""},
        {"epam_project_code": "x" * 101},
        {"epam_project_code": "ab"},
        {"codemie_project_name": " a "},
    ])
    def test_invalid_payload_is_rejected(self, overrides):
        with pytest.raises(ValidationError):
            ServiceNowBudgetRequest(**_payload(**overrides))

    def test_cancel_without_amounts_is_valid(self):
        assert ServiceNowBudgetRequest(**_payload(action="cancel", amounts=None)).amounts is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_servicenow_integration_router.py -v`
Expected: FAIL with `ModuleNotFoundError: codemie.rest_api.models.servicenow_integration`.

- [ ] **Step 3: Implement the models**

- [ ] **Step 4: Run the tests to verify they pass**

Run: the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/models/servicenow_integration.py tests/codemie/rest_api/routers/test_servicenow_integration_router.py
git commit -m "EPMCDME-<ticket>: Add ServiceNow integration request and response models"
```

---

### Task 6: Budget amount resolution and group category specs

Pure functions; no I/O.

Test-first: yes — `TestBudgetMath` fails with `ModuleNotFoundError: codemie.service.servicenow_integration_service`.

**Files:**
- Create: `src/codemie/service/servicenow_integration_service.py` (this task adds only the module-level functions)
- Test: `tests/codemie/service/test_servicenow_integration_service.py` (create; class `TestBudgetMath`)

**Interfaces:**
- Produces: `resolve_target_amounts(action: ServiceNowBudgetAction, requested: dict[BudgetCategory, float], current: dict[BudgetCategory, float]) -> dict[BudgetCategory, float]`. Always returns all three categories. `SET`: `requested`, with missing categories = 0. `ADJUST`: `current` (missing = 0) overlaid with `requested`.
- Produces: `build_category_specs(amounts: dict[BudgetCategory, float], current: dict[BudgetCategory, Budget]) -> tuple[float, dict[str, CategoryBudgetSpec]]`:
  - Raises 400 when `amounts[PLATFORM] <= 0` (`message="Platform budget must be greater than 0"`). The total being 0 is covered by that check.
  - Returns `total = sum(amounts)` and a spec for **every** category. For non-platform categories, `pct = float(Decimal(str(amount)) * 100 / Decimal(str(total)))`, not rounded; platform's pct is `100 - sum(others)`.
  - `soft_budget` = `current[cat].soft_budget` when `cat in current` and `abs(current[cat].max_budget - amount) < 0.005`; otherwise `None`.
- `current` is keyed by category and built by the caller from `ProjectBudgetGroupFullResult.categories` (Task 8). `CategoryBudgetSpec` comes from `codemie.rest_api.models.project_budget`.

- [ ] **Step 1: Write the failing tests**

```python
P, C, M = BudgetCategory.PLATFORM, BudgetCategory.CLI, BudgetCategory.PREMIUM_MODELS

class TestBudgetMath:
    def test_set_replaces_missing_categories_with_zero(self):
        assert resolve_target_amounts(ServiceNowBudgetAction.SET, {P: 500.0}, {P: 100.0, C: 50.0, M: 10.0}) == {P: 500.0, C: 0.0, M: 0.0}

    def test_adjust_overlays_current(self):
        assert resolve_target_amounts(ServiceNowBudgetAction.ADJUST, {C: 200.0}, {P: 500.0, C: 100.0, M: 50.0}) == {P: 500.0, C: 200.0, M: 50.0}

    def test_adjust_zero_removes_category(self):
        assert resolve_target_amounts(ServiceNowBudgetAction.ADJUST, {M: 0.0}, {P: 500.0, M: 50.0})[M] == 0.0

    def test_specs_cover_all_categories_and_sum_to_100(self):
        total, specs = build_category_specs({P: 500.0, C: 100.0, M: 0.0}, {})
        assert total == 600.0
        assert set(specs) == {"platform", "cli", "premium_models"}
        assert specs["premium_models"].pct == 0
        assert abs(sum(s.pct for s in specs.values()) - 100) < 1e-9

    @pytest.mark.parametrize("amounts", [{P: 1.0, C: 1.0, M: 1.0}, {P: 999_999.99, C: 0.01, M: 333_333.33}, {P: 0.1, C: 0.2, M: 0.3}])
    def test_amounts_derive_back_within_a_cent(self, amounts):
        total, specs = build_category_specs(amounts, {})
        for cat, amount in amounts.items():
            assert abs(total * specs[cat.value].pct / 100 - amount) < 0.01

    def test_platform_zero_is_400(self):
        with pytest.raises(ExtendedHTTPException) as exc:
            build_category_specs({P: 0.0, C: 100.0, M: 0.0}, {})
        assert exc.value.code == 400

    def test_unchanged_amount_keeps_soft_limit(self):   # Review Focus 3
        current = {P: SimpleNamespace(max_budget=500.0, soft_budget=250.0), C: SimpleNamespace(max_budget=100.0, soft_budget=90.0)}
        _, specs = build_category_specs({P: 500.0, C: 200.0, M: 0.0}, current)
        assert specs["platform"].soft_budget == 250.0
        assert specs["cli"].soft_budget is None
        assert specs["premium_models"].soft_budget is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_servicenow_integration_service.py -v`
Expected: FAIL with `ModuleNotFoundError: codemie.service.servicenow_integration_service`.

- [ ] **Step 3: Implement both functions**

- [ ] **Step 4: Run the tests to verify they pass**

Run: the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/servicenow_integration_service.py tests/codemie/service/test_servicenow_integration_service.py
git commit -m "EPMCDME-<ticket>: Add ServiceNow budget amount resolution"
```

---

### Task 7: Sync step — project listing, project and cost center resolution, admins

Test-first: yes — `TestResolveProject`, `TestAssignAdmins`, `TestSyncStep` and `TestListProjects` fail with `ImportError` / `AttributeError` for `ServiceNowIntegrationService`.

**Files:**
- Modify: `src/codemie/service/servicenow_integration_service.py`
- Test: `tests/codemie/service/test_servicenow_integration_service.py` (classes `TestListProjects`, `TestResolveProject`, `TestAssignAdmins`, `TestSyncStep`)

**Interfaces:**
- Consumes: Task 1 `get_active_by_emails`, `list_shared_projects_for_users`; Task 2 `cost_center_service.get_or_create`; Task 3 `project_service.create_shared_project_in_session`, `ProjectService.validate_shared_project_name`; `CostCenterService.validate_name`; `user_project_repository.get_by_users_and_project`; `ProjectAssignmentService.bulk_assign_users_to_project`; `invalidate_user_from_cache`; `cost_center_repository.get_by_id`.
- Produces: `@dataclass class ProjectResolution: project_name: str; cost_center: str | None; created_project: bool; created_cost_center: bool; admins_assigned: list[str]; admins_pending: list[str]`. `project_name` is the stored `Application.name` of the resolved project, which Task 8 passes to every budget call.
- Produces: `class ServiceNowIntegrationService` (classmethods) and `servicenow_integration_service = ServiceNowIntegrationService()`, with:
  - `async def list_projects(cls, emails: list[str]) -> list[Application]`: `await asyncio.to_thread(...)` over one `get_session()`: emails → `get_active_by_emails`, excluding `user_type == SERVICE_ACCOUNT_USER_TYPE` → user ids → `list_shared_projects_for_users`.
  - `def _resolve_and_assign(cls, actor: User, *, project_name: str, epam_project_code: str, admin_emails: list[str]) -> ProjectResolution`: one `get_session()`; runs `_resolve_project` then `_assign_admins`, commits once, then invalidates the cache for changed users. On `IntegrityError` it rolls back and runs once more in a new session; a second `IntegrityError` propagates.
  - `def _resolve_project(cls, session: Session, actor: User, *, project_name: str, epam_project_code: str) -> tuple[Application, str | None, bool, bool]` returns `(project, cost_center_name, created_project, created_cost_center)`:
    - validates `project_name` first, and `epam_project_code` with `CostCenterService.validate_name` only when the names differ;
    - looks up with `application_repository.get_by_name_case_insensitive`. A soft-deleted match → 409 (`message=f"Project '{name}' has been deleted"`). A personal match → 403 (`message="Budgets cannot be managed for personal projects"`). An active shared match → reused; its `cost_center` comes from `cost_center_repository.get_by_id` when `cost_center_id` is set;
    - when missing: names equal → create with no cost center; names differ → `get_or_create` the cost center, then create linked to it.
  - `def _assign_admins(cls, session: Session, actor: User, project: Application, emails: list[str]) -> tuple[list[str], list[str], list[str]]` returns `(admins_assigned_emails, admins_pending_emails, changed_user_ids)`. It calls `bulk_assign_users_to_project` only when someone needs a change, with `users=[{"user_id": id, "is_project_admin": True}, ...]` and `action="POST /v1/integrations/servicenow/budgets"`.

- [ ] **Step 1: Write the failing tests**

Mock the collaborators at `codemie.service.servicenow_integration_service.*`, including `get_session`.

```python
class TestResolveProject:
    def test_existing_shared_project_reused_and_cost_center_untouched(self, ...):
        # existing app with cost_center_id=cc_id; get_by_id -> name "other-cc"
        project, cost_center, created_project, created_cc = Svc._resolve_project(session, sa, project_name="epm-aimf", epam_project_code="nbs-cmpa")
        assert (cost_center, created_project, created_cc) == ("other-cc", False, False)
        mock_cost_center_service.get_or_create.assert_not_called()
        mock_project_service.create_shared_project_in_session.assert_not_called()

    def test_soft_deleted_project_is_409(self, ...): ...
    def test_personal_project_is_403(self, ...): ...
    def test_missing_with_equal_names_creates_without_cost_center(self, ...):
        ...  # create_shared_project_in_session(session, actor=sa, name="nbs-cmpa", cost_center_id=None); get_or_create not called
    def test_missing_with_different_names_links_cost_center(self, ...):
        ...  # get_or_create(session, name="nbs-cmpa", created_by=sa.id) -> (cc, True); create called with cost_center_id=cc.id; returns ("nbs-cmpa", True, True)
    def test_invalid_project_name_is_400_before_db(self, ...): ...
    def test_invalid_cost_center_name_is_400_only_when_names_differ(self, ...): ...

class TestAssignAdmins:
    def test_unknown_and_service_account_emails_are_pending(self, ...): ...
    def test_existing_admin_is_skipped_and_reported_assigned(self, ...):
        ...  # bulk_assign not called; assigned == ["a@epam.com"]; changed == []
    def test_member_promoted_and_non_member_added_in_one_bulk_call(self, ...):
        ...  # bulk_assign called once with both user ids, is_project_admin=True, actor=sa
    def test_no_emails_makes_no_calls(self, ...): ...

class TestSyncStep:
    def test_commits_once_and_invalidates_changed_users_after_commit(self, ...):
        ...  # session.commit called once; invalidate_user_from_cache called for changed ids only, after commit (use a manager mock to assert order)
    def test_resolution_carries_stored_project_name(self, ...):
        ...  # existing project stored as "EPM-Legacy"; request name "epm-legacy"; result.project_name == "EPM-Legacy"
    def test_integrity_error_retries_once_and_reuses_rows(self, ...):
        ...  # first run raises IntegrityError, rollback called; second run finds the project; result created_project False
    def test_second_integrity_error_propagates(self, ...): ...

class TestListProjects:   # anyio
    async def test_unions_projects_across_emails_excluding_service_accounts(self, ...):
        ...  # get_active_by_emails returns a regular user and a service account; list_shared_projects_for_users called with [regular.id] only
    async def test_no_matching_users_returns_empty_without_project_query(self, ...): ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_servicenow_integration_service.py -v`
Expected: the new classes FAIL with `AttributeError` / `ImportError` for `ServiceNowIntegrationService`; `TestBudgetMath` PASS.

- [ ] **Step 3: Implement `ProjectResolution`, `list_projects`, `_resolve_and_assign`, `_resolve_project`, `_assign_admins`**

- [ ] **Step 4: Run the tests to verify they pass**

Run: the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/servicenow_integration_service.py tests/codemie/service/test_servicenow_integration_service.py
git commit -m "EPMCDME-<ticket>: Resolve ServiceNow project, cost center and admins"
```

---

### Task 8: Budget step and `apply_budget_request` orchestration

Test-first: yes — `TestApplyBudget` and `TestApplyBudgetRequest` fail with `AttributeError: '_apply_budget'` / `'apply_budget_request'`.

**Files:**
- Modify: `src/codemie/service/servicenow_integration_service.py`
- Test: `tests/codemie/service/test_servicenow_integration_service.py` (classes `TestApplyBudget`, `TestApplyBudgetRequest`)

**Interfaces:**
- Consumes: Task 6 `resolve_target_amounts`, `build_category_specs`; Task 7 `_resolve_and_assign`, `ProjectResolution`; `project_budget_service.create_project_budget_group`, `update_project_budget_group`, `delete_project_budget_group`, `get_project_budget_group`; `project_budget_group_repository.get_active_by_project`; `get_async_session`; `ProjectBudgetGroupCreateRequest`, `ProjectBudgetGroupUpdateRequest`, `CategoryBudgetSpecUpdate`.
- Produces: `@dataclass class ServiceNowBudgetResult: project_name: str; cost_center: str | None; created_project: bool; created_cost_center: bool; budget_group: ProjectBudgetGroupFullResult | None; admins_assigned: list[str]; admins_pending: list[str]`.
- Produces: `async def apply_budget_request(cls, actor: User, request: ServiceNowBudgetRequest) -> ServiceNowBudgetResult`:
  - logs `servicenow_budget_request_started`;
  - `resolution = await asyncio.to_thread(cls._resolve_and_assign, actor, ...)`, then `budget_group = await cls._apply_budget(actor, resolution.project_name, request)`;
  - logs `servicenow_budget_request_completed` with the fields listed in the spec's Conventions section;
  - on any exception, logs `servicenow_budget_request_failed: request_id=..., action=..., error=...` and re-raises.
- Produces: `async def _apply_budget(cls, actor: User, project_name: str, request: ServiceNowBudgetRequest) -> ProjectBudgetGroupFullResult | None`. One `get_async_session()`, commit once, and after a write return `get_project_budget_group(session, group_id)`:
  - `current` comes from the active group's categories, keyed by `BudgetCategory(assignment.budget_category)` → `budget`.
  - `CANCEL`: no group → `None`. Group → `delete_project_budget_group(session, group_id=..., actor_id=actor.id)`, then `None`.
  - `ADJUST` with no group → 404 (`message=f"Project '{project_name}' has no budget to adjust"`, `help="Send action 'set' to create a budget."`).
  - `SET` with no group → `create_project_budget_group(session, ProjectBudgetGroupCreateRequest(project_name=..., name=f"ServiceNow {request.request_id}"[:100], total_amount=total, budget_duration="30d", categories=specs), actor_id=actor.id)`. If it raises `ExtendedHTTPException` with code 409, or `IntegrityError` (the unique active-group index), `await session.rollback()`, re-read the group and apply an update once.
  - `project_name` is always the stored name from `ProjectResolution.project_name`.
  - `SET` / `ADJUST` with a group → `update_project_budget_group(session, group_id=..., data=ProjectBudgetGroupUpdateRequest(total_amount=total, categories={k: CategoryBudgetSpecUpdate(pct=s.pct, soft_budget=s.soft_budget)}), actor_id=actor.id)`. Name, duration, description and notification fields are left unset.

- [ ] **Step 1: Write the failing tests**

Mock `project_budget_service` and `project_budget_group_repository` with `AsyncMock`, and `get_async_session` as an async context manager.

```python
class TestApplyBudget:   # anyio
    async def test_set_without_group_creates_with_defaults(self, ...):
        await Svc._apply_budget(sa, "epm-aimf", _request(action="set", amounts={"platform": 500.0, "cli": 100.0}))
        data = mock_budget_service.create_project_budget_group.call_args.args[1]
        assert (data.project_name, data.name, data.total_amount, data.budget_duration) == ("epm-aimf", "ServiceNow RITM0012345", 600.0, "30d")
        assert data.categories["premium_models"].pct == 0
        session.commit.assert_awaited_once()

    async def test_set_with_group_replaces_whole_budget(self, ...):
        ...  # current P=500,C=100,M=50; request {platform: 300}; update data.total_amount == 300; cli and premium pct == 0
    async def test_adjust_merges_into_current(self, ...):
        ...  # current P=500,C=100,M=50; request {cli: 200}; update total == 750; derived amounts 500/200/50
    async def test_adjust_resending_current_amounts_keeps_all_soft_limits(self, ...):   # Review Focus 3, seam
        ...  # every CategoryBudgetSpecUpdate.soft_budget equals the current soft_budget
    async def test_adjust_without_group_is_404(self, ...): ...
    async def test_adjust_removing_platform_is_400(self, ...): ...  # {platform: 0}
    async def test_cancel_with_group_deletes(self, ...): ...  # delete awaited with group id and actor_id=sa.id; returns None
    async def test_cancel_without_group_is_noop(self, ...): ...  # no write calls, no commit, returns None
    @pytest.mark.parametrize("conflict", [ExtendedHTTPException(code=409, message="exists"), IntegrityError("insert", {}, Exception())])
    async def test_create_conflict_falls_back_to_update(self, conflict, ...):
        ...  # create raises `conflict`; session.rollback awaited; get_active_by_project then returns a group; update awaited once
    async def test_update_does_not_send_name_or_duration(self, ...):
        ...  # data.name is None and data.budget_duration is None

class TestApplyBudgetRequest:   # anyio
    async def test_returns_combined_result(self, ...): ...  # fields copied from ProjectResolution plus budget_group
    async def test_budget_failure_is_logged_and_reraised(self, ...): ...  # _apply_budget raises; logger.warning or error called with request_id; exception propagates
    async def test_retry_after_partial_failure_reuses_project_and_applies_budget(self, ...):   # Review Focus 4
        ...  # first call: _apply_budget raises. Second call: project exists with requester already admin
        ...  # second result: created_project False, bulk_assign not called, create_project_budget_group awaited once
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_servicenow_integration_service.py -v`
Expected: the new classes FAIL with `AttributeError: ... '_apply_budget'` / `'apply_budget_request'`; earlier classes PASS.

- [ ] **Step 3: Implement `ServiceNowBudgetResult`, `_apply_budget`, `apply_budget_request`**

- [ ] **Step 4: Run the tests to verify they pass**

Run: the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/servicenow_integration_service.py tests/codemie/service/test_servicenow_integration_service.py
git commit -m "EPMCDME-<ticket>: Apply ServiceNow budget requests through budget groups"
```

---

### Task 9: Router, app registration and router guards

Test-first: yes — `TestListProjectsEndpoint`, `TestBudgetsEndpoint` and `TestAllowlistMatchesRouter` fail with `ModuleNotFoundError: codemie.rest_api.routers.servicenow_integration`.

**Files:**
- Create: `src/codemie/rest_api/routers/servicenow_integration.py`
- Modify: `src/codemie/rest_api/main.py` (import with the other routers; gate before `app.include_router(get_mcp_auth_router())`, ~line 1099)
- Modify: `tests/codemie/rest_api/router_guards/guarded_app.py` (`PROFILE_ENV` ~line 52 and the module docstring's router list)
- Modify (regenerated): `tests/codemie/rest_api/router_guards/baselines/`
- Test: `tests/codemie/rest_api/routers/test_servicenow_integration_router.py` (classes `TestListProjectsEndpoint`, `TestBudgetsEndpoint`, `TestAllowlistMatchesRouter`)

**Interfaces:**
- Consumes: Task 4 `servicenow_integration_access_only`, `_allowed_routes`; Task 5 models; Task 7 `servicenow_integration_service.list_projects`; Task 8 `apply_budget_request`, `ServiceNowBudgetResult`.
- Produces: `router = APIRouter(tags=["ServiceNow Integration"], prefix="/v1/integrations/servicenow", dependencies=[])` with:
  - `@router.get("/projects", response_model=list[ServiceNowProjectResponse])` → `async def list_servicenow_projects(emails: list[NormalizedEmail] = Query(min_length=1, max_length=20), user: User = Depends(authenticate), _: None = Depends(servicenow_integration_access_only))`. Passes `emails` straight to the service; normalization happens in the type.
  - `@router.post("/budgets", response_model=ServiceNowBudgetResponse)` → `async def apply_servicenow_budget(payload: ServiceNowBudgetRequest, user: User = Depends(authenticate), _: None = Depends(servicenow_integration_access_only))`.
  - A private `_to_budget_response(result: ServiceNowBudgetResult) -> ServiceNowBudgetResponse` mapping `budget_group.categories[*].budget` → `ServiceNowBudgetCategoryResponse(category, max_budget, soft_budget, budget_reset_at)`, sorted in `BudgetCategory` order; `total_amount` from `budget_group.total_amount`; `budget_duration` from `budget_group.group.budget_duration`.
- `main.py`:

  ```python
  if config.SERVICENOW_INTEGRATION_ENABLED:
      if not config.ENABLE_USER_MANAGEMENT:
          logger.warning("servicenow_integration_disabled: requires ENABLE_USER_MANAGEMENT=true, router not registered, domain=servicenow_integration")
      else:
          if not config.SERVICENOW_SERVICE_ACCOUNT_ID:
              logger.warning("servicenow_integration_misconfigured: SERVICENOW_SERVICE_ACCOUNT_ID is empty, every request will be rejected, domain=servicenow_integration")
          app.include_router(servicenow_integration.router)
  ```
- `PROFILE_ENV` gets `"SERVICENOW_INTEGRATION_ENABLED": "true"`.

- [ ] **Step 1: Write the failing tests**

Call the handlers directly with the service patched at `codemie.rest_api.routers.servicenow_integration.servicenow_integration_service`, as `test_cost_centers_router.py` does.

```python
class TestListProjectsEndpoint:   # anyio
    async def test_returns_project_names_and_display_names(self, ...):
        mock_service.list_projects = AsyncMock(return_value=[SimpleNamespace(name="epm-aimf", display_name="AI Multimodal Framework")])
        response = await list_servicenow_projects(emails=["a@epam.com"], user=sa, _=None)
        mock_service.list_projects.assert_awaited_once_with(["a@epam.com"])
        assert response == [ServiceNowProjectResponse(project_name="epm-aimf", display_name="AI Multimodal Framework")]

class TestBudgetsEndpoint:   # anyio
    async def test_maps_result_to_response(self, ...):
        ...  # categories sorted platform, cli, premium_models; budget_reset_at passed through; cost_center/created flags/admin lists copied
    async def test_cancel_result_has_null_budget(self, ...): ...
    @pytest.mark.parametrize("code", [400, 403, 404, 409])
    async def test_service_errors_propagate(self, code, ...):
        ...  # apply_budget_request raises ExtendedHTTPException(code=code, message="x"); handler re-raises the same exception

class TestAllowlistMatchesRouter:   # Review Focus 5, seam against the real router
    def test_allowlist_is_exactly_the_two_integration_routes(self):
        _allowed_routes.cache_clear()
        assert _allowed_routes() == {("GET", "/v1/integrations/servicenow/projects"), ("POST", "/v1/integrations/servicenow/budgets")}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_servicenow_integration_router.py -v`
Expected: the new classes FAIL with `ModuleNotFoundError: codemie.rest_api.routers.servicenow_integration`; `TestServiceNowBudgetRequest` PASS.

- [ ] **Step 3: Implement the router, register it in `main.py`, and add the flag to `PROFILE_ENV` and its docstring**

- [ ] **Step 4: Run the router tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_servicenow_integration_router.py -v`
Expected: PASS.

- [ ] **Step 5: Regenerate the router-guard baselines and review the diff**

Run: `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`, then `git diff --numstat tests/codemie/rest_api/router_guards/baselines/` and `git diff -U0 tests/codemie/rest_api/router_guards/baselines/ | grep -c '^-[^-]'`
Expected: the removal count is `0` (pure additions), and the added lines only mention `/v1/integrations/servicenow`, `ServiceNow*` schemas or the `ServiceNow Integration` tag. `openapi.json` is ~1.9 MB, so check with these commands rather than reading the diff. Any other change means an unintended contract change; fix the code, not the baseline.

- [ ] **Step 6: Run the router guards and every test touched by this plan**

Run: `poetry run pytest tests/codemie/rest_api/router_guards/ tests/codemie/rest_api/routers/test_servicenow_integration_router.py tests/codemie/service/test_servicenow_integration_service.py tests/codemie/rest_api/security/ tests/codemie/service/project/ tests/codemie/service/test_cost_center_service.py tests/codemie/repository/test_user_repository_crud.py tests/codemie/repository/test_application_repository_extended.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/rest_api/routers/servicenow_integration.py src/codemie/rest_api/main.py tests/codemie/rest_api/router_guards/ tests/codemie/rest_api/routers/test_servicenow_integration_router.py
git commit -m "EPMCDME-<ticket>: Expose ServiceNow budget integration endpoints"
```
