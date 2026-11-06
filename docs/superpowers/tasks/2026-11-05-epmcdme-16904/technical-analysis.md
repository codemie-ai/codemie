# Technical Research

**Task**: provisioning clients registry access scope member lookup servicenow retirement
**Generated**: 2026-11-05 (sliced from ../../technical-analysis.md)
**Research path**: codegraph (parent run)

---

## 1. Original Context

This slice covers the first child of the parent story (`../../story.md`). It replaces the ServiceNow-specific access mechanism with a config registry of provisioning clients and its permissions. Permission names are `provisioning:read-projects`, `provisioning:manage-projects` and `provisioning:manage-budgets`. `PROVISIONING_API_ENABLED` switches it on, and it still requires `ENABLE_USER_MANAGEMENT`.

Registered clients may only call `/v1/provisioning/*` on the externally authenticated branch, and only registered clients may call it. Registered clients count as service accounts. The slice adds the `/v1/provisioning` router package with `GET /projects?member_email=` (repeatable, union of results). It removes `/v1/integrations/servicenow/*` and `SERVICENOW_*` outright; nothing calls them. Budget writes are the second child.

---

## 2. Codebase Findings

Paths: `sec/` = `src/codemie/rest_api/security/`; `svc` = `src/codemie/service/servicenow_integration_service.py`; `rtr` = `src/codemie/rest_api/routers/servicenow_integration.py`.

### Existing Implementations

**P1. A third-party system calls CodeMie as a service account confined to its own routes**
- `is_servicenow_service_account` `sec/servicenow_access.py:45-50`: flag on and `user.id == SERVICENOW_SERVICE_ACCOUNT_ID`; stored `user_type` ignored (docstring :15-27).
- `_allowed_routes` :53-57: `@cache`d `(method, route.path)` set from `rtr.router.routes`. `enforce_servicenow_scope` :60-81: 403 outside it or with no route; called only at `sec/authentication.py:155`, **external branch** (internal `bind_key` branch :123-133 unscoped).
- `servicenow_integration_access_only` `sec/authentication.py:246-258`: per-route 403 guard.
- `is_service_account` `sec/user_type_validator.py:45-48`: `user_type == "service_account"` OR the ServiceNow predicate; takes `User | UserDB`.
- Siblings: `TEAMS_SERVICE_ACCOUNT_ID` (`config.py:248`; `teams_authentication_resolver.py:36-55` checks id **and** `user_type`); `AuthorizedApplicationsConfig` `src/codemie/configs/authorized_apps_config.py:61-128` loads `config/authorized_applications/authorized-applications-config.yaml` at import (invalid YAML raises → app fails to start); entries `name`, public key, `allowed_resources`; matched by **username** (`index_encrypted_settings_service.py:77`, `permission_creation_service.py:79`); the public key encrypts datasource credentials to the app (`index_encrypted_settings_service.py:56`).

**P3. Find a person's shared projects by email**
- `rtr` `GET /projects` :138-159: repeated `emails`, union sorted; `svc` :384-395 drops service accounts, then `application_repository.list_shared_projects_for_users` (`application_repository.py:547-571`).

**Other touched code**: `main.py:84`, `:1511-1523` (router registration under flag + `ENABLE_USER_MANAGEMENT`); `config.py:259-263`.

### Decisions Already in the Code
- Machine caller identified by token subject id, not `user_type` (`servicenow_access.py:45-50`).
- Scope = allowlist derived from one router object, enforced in `authenticate()` plus per-route guard.
- Router registered only when flag **and** `ENABLE_USER_MANAGEMENT`; empty id only warns (`main.py:1511-1523`).
- Conventions: router package with parent `APIRouter` in `__init__.py` (`rest-api-patterns.md:11`, `routers/projects/__init__.py:22-31`); schemas in `rest_api/models/<feature>.py`; names strip+lowercased before validation.

### Task Statements Checked Against the Code
- "shipped since 2.62.0, unused" — confirmed by the requester: ServiceNow has not integrated; no caller in `codemie-ui`, `codemie-enterprise`, `codemie-mcp-connect-service`.
- "permissions" — no existing permission vocabulary for machine clients; nearest `core/ability.py:30-35` (READ/WRITE/DELETE). Planned Barracuda catalog uses `<area>:<action>` codes (follow-up, out of scope).
- `member_email` keeps the multi-value union semantics of `emails` (test `test_many_emails_are_accepted_on_both_routes`).

### Consumers of Changed Contracts
- Routes: `tests/codemie/rest_api/router_guards/baselines/openapi.json` (`/v1/integrations/servicenow/projects`, `/budgets`); router tests.
- `is_servicenow_service_account`: `authentication.py:28,248`, `servicenow_access.py:62`, `user_type_validator.py:46`.
- `is_service_account` (meaning widens to registry clients): `authentication_service.py:625` (Teams sender; test `test_authentication_service.py:1921`), `svc` :392, :516.
- `enforce_servicenow_scope`: `authentication.py:155`.
- `SERVICENOW_*`: `config.py:262-263`, `main.py:1511-1523`, `tests/.../router_guards/guarded_app.py:25,55`, `mock_config` in 5 test files.

### Architecture and Layers Affected
API (provisioning router package, lookup models); Security (`authentication.py`, new provisioning access module replacing `servicenow_access.py`, `user_type_validator.py`); Config/wiring (`config.py`, registry YAML + loader, `main.py`); router guards.

### Integration Points
`application_repository.list_shared_projects_for_users`, `user_repository.get_by_emails` (`user_repository.py:354-375`), Teams sender resolution.

### Patterns and Conventions
Classmethod service singleton; sync work via `asyncio.to_thread`; `ExtendedHTTPException(code, message, details, help)`; `key=value` logs with `domain=`; `authenticate` must precede the guard (asserted, router test :438-439).

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/api/rest-api-patterns.md`, `.ai-run/guides/api/endpoint-conventions.md`, `.ai-run/guides/testing/testing-api-patterns.md:26-42` (guards; regen `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`). The provisioning pattern guide belongs to the budget-writes child.

### Architectural Decisions
Historic run `docs/superpowers/tasks/2026-10-19-servicenow-budget-integration/`.

### Derived Conventions
See Decisions Already in the Code.

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/rest_api/routers/test_servicenow_integration_router.py` (403s, allowlist :442-449, real `authenticate` scope :452); `tests/codemie/rest_api/security/test_servicenow_access.py`; `test_authentication.py:570-602`; `test_authentication_service.py:1921`; `tests/codemie/configs/test_authorized_apps_config.py` (registry loader template); router guards `test_openapi_snapshot.py`, `test_route_precedence.py`.

### Testing Framework and Patterns
pytest ^8.3.1, pytest-asyncio ^1.4.0; `TestClient` on a bare app, `dependency_overrides[authenticate]`, `patch(CONFIG_PATH)`.

### Coverage Gaps
`test_guarded_app_profile.py:31-39` omits the ServiceNow router; no scope test on the `bind_key` branch.

---

## 5. Configuration and Environment

### Environment Variables
Removed: `SERVICENOW_INTEGRATION_ENABLED`, `SERVICENOW_SERVICE_ACCOUNT_ID` (`config.py:262-263`). Kept: `ENABLE_USER_MANAGEMENT` (:254); `AUTHORIZED_APPS_CONFIG_DIR` (:131) as the precedent for a mounted config dir. New: `PROVISIONING_API_ENABLED`, registry location.

### Configuration Files
`config/authorized_applications/authorized-applications-config.yaml` (precedent). No deployment manifest in this repo references `SERVICENOW_*`.

### Feature Flags and Deployment Concerns
Guard profile sets `SERVICENOW_INTEGRATION_ENABLED=true` (`guarded_app.py:55`) — switch to the new flag.

---

## 6. Risk Indicators
- Scope enforced only on the external auth branch (kept, child D7); `_allowed_routes` is `@cache`d from a single router object — build it from the whole provisioning router package.
- `is_service_account` is shared with Teams sender resolution (`authentication_service.py:625`).
- A second registry sits beside `AuthorizedApplicationsConfig` (different purpose: credential encryption).
- `src/codemie_tools/itsm/servicenow/` (agent tool) is unrelated; keep the rename sweep away from it.
- OpenAPI asserted verbatim; baseline regen required.
- Between this child and budget-writes, no budget endpoint exists (no callers).

---

## 7. Summary for Complexity Assessment

Touches API (new router package with one GET route and models), Security (authenticate scope hook, route guard, shared `is_service_account`), Config/wiring (`config.py`, registry YAML + loader, `main.py`) and router contract guards. The lookup logic exists (`svc` :384-395) and moves. Novel: a config registry with permission codes keyed by token subject, and a namespace-wide lock built from a router package. Key risks: shared `is_service_account` used by Teams auth, scope allowlist caching, verbatim OpenAPI assertions.

---

## 8. External References

None named by the task.

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- `_allowed_routes()` :53-57 — `@functools.cache`; `frozenset((method, route.path))` over `servicenow_integration.router.routes`. `route.path` already carries the router prefix `/v1/integrations/servicenow` (`routers/servicenow_integration.py:35-39`); asserted equal to `{GET …/projects, POST …/budgets}` (`test_servicenow_integration_router.py:442-449`). Tests call `cache_clear()` (:444, :469, :489).
- Path today: `authenticate()` external branch: provider → IdP → Teams impersonation (`sec/authentication.py:152-153`) → `enforce_servicenow_scope` (:155). The check therefore sees the impersonated user when a Teams swap happened. `bind_key` branch (:123-133) never calls it.
- No `account_id` exists on the auth/config path today (searched `src/`, `config/`, `.ai-run/guides/`). Unrelated existing uses of the name: Atlassian OAuth user id (`service/oauth/provider_adapters.py:369,379,389`; mapped to `provider_user_id` in `service/oauth/settings_base.py:128`); OTel CLI-analytics attribute `user.account_id` (`repository/cli_analytics/vocabulary.py:141`, `postgres/rollups.py:837,852`, migration `alembic_cli_analytics/versions/a1c1a0000006…:175`, `cli-analytics-storage.md:301`).
- The concept today is a config-held user id compared to `user.id`: `SERVICENOW_SERVICE_ACCOUNT_ID` (`config.py:263`, default `""`), siblings `TEAMS_SERVICE_ACCOUNT_ID` (`config.py:248`, default `"codemie-teams-bot"`, compared at `teams_authentication_resolver.py:48` together with `user_type` :41) and `ADMIN_USER_ID` (`config.py:247`, compared at `security/user.py:121`).
- How `user.id` is produced: enterprise IdPs set `id=payload["sub"]` (`ent/keycloak.py:92`, `ent/oidc.py:69`, `ent/entraid_oidc.py:118`) → `EnterpriseIdpWrapper` copies `idp_user.id` (`enterprise/idp/dependencies.py:100-101`); local IdP `user_id = claims["sub"]` (`user_providers/persistent.py:121`); ENV=local dev header supplies it raw (:105-108).
- Persistent mode (`ENABLE_USER_MANAGEMENT=true`): id must parse as UUID or 422 (`authentication_service.py:545`, `_validate_user_id_uuid` :413-425); DB row fetched/created by that id (:550-557); `UserDB.id` `max_length=36`, comment "IDP JWT 'sub' claim (UUID format)" (`models/user_management.py:43`). Legacy mode: `User.id` = `sub`, no UUID check (`user_providers/legacy_jwt.py:53`).
- Identity by subject id alone, not `user_type` (`servicenow_access.py:17-19,45-50`); alternative used by Teams: id **and** `user_type == service_account` (`teams_authentication_resolver.py:41-48`). Reason recorded: the account is provisioned as a regular client-credentials client.
- Single "is service account" check composes stored type OR id predicate (`user_type_validator.py:45-48`, lazy import :46).
- "account … the CodeMie user id, i.e. the token subject" (spec :11, :69) — **holds**: `sub` → `User.id` → `UserDB.id` (N2 citations). With user management on, a non-UUID id never authenticates (422 at `authentication_service.py:545`), so a non-UUID `account_id` could never match a caller on the mounted router.
- "registered clients count as service accounts everywhere (lookup, admin assignment, Teams sender resolution)" — **holds** for the three named: `is_service_account` callers are exactly `authentication_service.py:625`, `svc:392`, `svc:516`. Story D5 "wherever service accounts are excluded" is wider than the predicate's reach: `is_personal_project_excluded(user_type)` reads stored `user_type` only (`authentication_service.py:395,787`, `registration_service.py:214,251`, `personal_project_service.py:125`, `cli_analytics_project_gate.py:223,299,346`), and so does Teams `_is_allowlisted` (`:41`). Same as today for the ServiceNow account.
- "same as `servicenow_access.py:53-70`" method+route matching (spec decision) — **holds**; the prefix is part of `route.path`.
- `sec/authentication.py:28` (import), `:155` (scope), `:248` (`servicenow_integration_access_only`).
- `sec/user_type_validator.py:46-48` → callers `authentication_service.py:625`, `svc:392`, `svc:516`.
- `main.py:1511-1523` (`SERVICENOW_*`, router mount).
- Tests patching the module: `tests/codemie/rest_api/security/test_servicenow_access.py` (whole file); `test_authentication.py:569-604` (patches `servicenow_access.config` and `_allowed_routes`); `test_servicenow_integration_router.py:47-54,444,469,489`; `test_authentication_service.py:1917-1922`; **`tests/codemie/service/test_servicenow_integration_service.py:121-129`** — fixture `servicenow_account` patches `codemie.rest_api.security.servicenow_access.config`, used by `test_servicenow_account_email_is_pending_regardless_of_user_type` (:324) and `test_servicenow_account_contributes_no_projects` (:512). This file is kept by spec item 7; its patch target is the removed module. Not named in the spec.
