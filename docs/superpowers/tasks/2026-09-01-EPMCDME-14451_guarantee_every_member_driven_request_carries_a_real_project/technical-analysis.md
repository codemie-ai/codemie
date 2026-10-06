# Technical Research

**Task**: auth project assistant proxy
**Generated**: 2026-09-01T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Guarantee every member-driven request carries a real project.

Close the four ways a governed request can arrive with no project:
1. Sign-in project creation failure - ensure_personal_project_async catches Exception, logs, returns False without raising; callers proceed either way
2. Assistant create/update fallbacks - platform_tool.py:206 has `project=assistant.project or ""`; assistant_generator validation utils falls back to user.current_project
3. Proxy router fallbacks - proxy_router.py:291-292 _extract_request_info falls back to username/empty string
4. CLI missing project guard - no project-required check before model selection

Scope:
- Sign-in refusal on project creation failure (authentication_service.py, registration_service.py)
- Assistant project validation (platform_tool.py, assistant_generator validation utils)
- Migration report for empty-project assistants (report only, no auto-move)
- Proxy router project requirement (proxy_router.py)
- CLI project guard
- Exempt background consumers (KB/datasource indexing, skill/workflow generators, toolkit platform-default resolution)

Acceptance Criteria:
- Sign-in refused when platform cannot create personal project
- Assistant create/update attached to project caller belongs to, not fallback
- Existing empty-project assistants reported to admins
- Proxy requests without project refused
- CLI tells user project is required and how to set one
- Background consumers unaffected

---

## 2. Codebase Findings

### Existing Implementations

**Sign-in and Registration (Issue 1)**:
- `src/codemie/service/user/authentication_service.py` — AuthenticationService handles all authentication flows
  - Lines 388-405: `_finalize_authentication()` calls `personal_project_service.ensure_personal_project_async()` after commit
  - Lines 680-746: `authenticate_and_login()` (local login) calls ensure_personal_project_async after commit
  - Lines 514-590: `authenticate_persistent_user()` (IDP/persistent mode) calls _finalize_authentication
  - Lines 592-658: `authenticate_dev_header()` (local dev) calls _finalize_authentication
- `src/codemie/service/user/registration_service.py` — RegistrationService handles user registration
  - Lines 152-278: `register_user_with_flow()` calls ensure_personal_project_async after commit (lines 214, 251)
- `src/codemie/service/project/personal_project_service.py` — PersonalProjectService handles personal project creation
  - Lines 45-108: `ensure_personal_project_async()` catches all exceptions, logs, returns False without raising (line 102-108)
  - Callers check return value but proceed regardless of success/failure

**Assistant Project Validation (Issue 2)**:
- `src/codemie/agents/tools/platform/platform_tool.py:206` — GetAssistantsTool transforms assistant with:
  ```python
  project=assistant.project or ""
  ```
  This falls back to empty string when assistant.project is None
- `src/codemie/workflows/assistant_generator/nodes/validation/utils.py` — Assistant validation utilities
  - Lines 92-128: `get_validated_context_info()` uses `assistant.project or user.current_project` (line 114)
  - Lines 113-115: Falls back to user.current_project when assistant.project is None or empty

**Proxy Router Project Fallback (Issue 3)**:
- `src/codemie/enterprise/litellm/proxy_router.py` — LiteLLM proxy router
  - Lines 290-304: `_extract_request_info()` function extracts request metadata
  - Line 292: `project = headers.get(HEADER_CODEMIE_CLI_PROJECT) or (user.username if user else "")`
  - Falls back to username if project header missing, then to empty string if user is None

**CLI Project Guard (Issue 4)**:
- No explicit project requirement check found before model selection in CLI-facing endpoints
- Proxy router (above) accepts requests without project headers
- CLI request identification: Lines 515-519 in proxy_router.py check for CLI via headers

**User.current_project Property**:
- `src/codemie/rest_api/security/user.py:113-115` — User.current_project property
  ```python
  @property
  def current_project(self) -> str:
      apps = self.project_names if self.project_names else [DEMO_PROJECT]
      return apps[0]
  ```
  Falls back to DEMO_PROJECT when project_names is empty

**Personal Project Exclusions**:
- `src/codemie/rest_api/security/user_type_validator.py` — User type validation
  - Lines 31-35: `PERSONAL_PROJECT_EXCLUDED_USER_TYPES = {"external", "service_account"}`
  - `is_personal_project_excluded()` returns True for external and service_account user types
  - Personal projects are not created for these user types (recent change from story 2026-08-27)

### Architecture and Layers Affected

**Service Layer**:
- `AuthenticationService` — Sign-in flows (local, IDP, dev header)
- `RegistrationService` — User registration with personal project creation
- `PersonalProjectService` — Personal project management (ensure_personal_project_async)

**API Layer (Routers)**:
- Proxy router (`src/codemie/enterprise/litellm/proxy_router.py`) — LiteLLM model proxy endpoint
- Assistant router (imports platform_tool indirectly) — Assistant CRUD operations

**Agent Tools Layer**:
- Platform tools (`src/codemie/agents/tools/platform/platform_tool.py`) — GetAssistantsTool
- Workflow validation utilities (`src/codemie/workflows/assistant_generator/nodes/validation/utils.py`)

**Security/Auth Layer**:
- User model (`src/codemie/rest_api/security/user.py`) — User.current_project property
- User type validator (`src/codemie/rest_api/security/user_type_validator.py`) — Personal project exclusions

### Integration Points

**Internal Dependencies**:
- Authentication → PersonalProjectService (ensure_personal_project_async)
- Registration → PersonalProjectService (ensure_personal_project_async)
- Platform tools → Assistant model (assistant.project field)
- Validation utils → User model (user.current_project)
- Proxy router → User model (user.username)

**External Service Connections**:
- Database (PostgreSQL) — User, Application, UserProject tables
- Elasticsearch — Assistants index (for migration report)

**Background Consumers to Exempt**:
- Datasource processors: `src/codemie/datasource/base_datasource_processor.py` and subclasses
- Skill generator: `src/codemie/service/skill_generator_service.py`
- Workflow generator: `src/codemie/service/workflow_generator_service.py`
- Assistant generator: `src/codemie/service/assistant_generator_service.py`

### Patterns and Conventions

**Error Handling**:
- `ExtendedHTTPException` — Standard exception class for API errors
- Non-blocking error handling in PersonalProjectService.ensure_personal_project_async (catches all, logs, returns False)

**Project Resolution**:
- Headers → User context → Project name pattern used in proxy_router.py
- user.current_project property provides fallback to first project or DEMO_PROJECT

**Service Layer Patterns**:
- Static methods on service classes (e.g., AuthenticationService.authenticate_and_login)
- Async session management with context managers (get_async_session)
- Separate sessions for personal project creation to prevent rollback affecting auth

---

## 3. Documentation Findings

### Guides and Architecture Docs

**Architecture Guide**: `.ai-run/guides/architecture/layered-architecture.md` — Defines API → Service → Repository pattern

**Related Task Documentation**:
- `docs/superpowers/tasks/2026-08-27-skip-private-project-creation/spec.md` — Recent change to skip personal project creation for external/service_account user types
  - Lines 31-35: Introduced `is_personal_project_excluded()` predicate
  - Lines 37-46: Six call sites where personal project creation is checked

### Architectural Decisions

**Personal Project Auto-Creation**:
- Created automatically after authentication/registration (FR-7.1 from related spec)
- Uses isolated transaction to prevent auth rollback on failure
- Non-blocking: returns False on failure, authentication continues

**Project Fallback Hierarchy**:
1. Explicit project assignment (headers, assistant.project field)
2. User's first project (user.current_project)
3. DEMO_PROJECT constant (when user has no projects)
4. Username (proxy router specific)
5. Empty string (last resort in several places)

**User Type-Based Exclusions**:
- External and service_account users excluded from personal project creation
- Regular users get personal project auto-created

### Derived Conventions

**Project Requirement**:
- No explicit project requirement validation at entry points
- Fallbacks allow requests to proceed with empty or default projects
- Project governance relies on implicit presence rather than explicit validation

**Failure Handling**:
- Personal project creation failures are logged but do not block authentication
- Empty project values propagate through the system rather than being rejected

---

## 4. Testing Landscape

### Existing Coverage

**Authentication Service Tests**: `tests/codemie/service/user/test_authentication_service.py`
- Lines 55-93: Test authenticate_local success case
- Lines 95-100: Test authenticate_local user not found
- Contains tests for IDP user creation, profile sync, persistent authentication

**Registration Service Tests**: `tests/codemie/service/user/test_registration_service.py`
- Tests for registration flows with personal project creation

**Personal Project Service Tests**: `tests/codemie/service/project/test_personal_project_service.py`
- Tests for ensure_personal_project_async

**Proxy Router Tests**: `tests/enterprise/litellm/test_proxy_router.py`
- Lines 63-94: Test _read_request_body with various inputs
- Contains tests for request extraction, header handling

### Testing Framework and Patterns

**Framework**: pytest with asyncio support
- `@pytest.mark.asyncio` decorator for async tests
- `pytest.fixture` for test setup (autouse fixture for cache clearing, line 39-44)

**Mocking Patterns**:
- `unittest.mock.AsyncMock` for async method mocking
- `unittest.mock.MagicMock` for sync method mocking
- `patch` decorator for dependency injection

**Test Structure**:
- Class-based test organization (e.g., `TestAuthenticateLocal`)
- AAA pattern (Arrange, Act, Assert)
- Descriptive test method names describing behavior

### Coverage Gaps

Speculative: Areas this task will touch that need test coverage:
- Authentication failure when personal project creation fails (currently logs but proceeds)
- Assistant create/update with missing project validation
- Proxy router requests without project header rejection
- CLI project requirement validation
- Migration report generation for empty-project assistants
- Background consumer exemption verification

---

## 5. Configuration and Environment

### Environment Variables

**Authentication and IDP**:
- `IDP_PROVIDER` — Identity provider type (keycloak, local, oidc, entraid-oidc) — config.py:186-191
- `ENABLE_USER_MANAGEMENT` — Master switch for user management system — config.py:199
- `ADMIN_USER_ID` — Admin user identifier — config.py:193
- `EXTERNAL_USER_TYPE` — External user type identifier (referenced in user.py:110)

**Database**:
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` — PostgreSQL connection — config.py:84-88
- `PG_URL` — PostgreSQL connection URL — config.py:90

**Elasticsearch**:
- `ELASTIC_URL`, `ELASTIC_USERNAME`, `ELASTIC_PASSWORD` — Elasticsearch connection — config.py:74-76
- `ASSISTANTS_INDEX` — Assistants index name — config.py:159

**LLM Proxy**:
- `LLM_PROXY_ENABLED` — LiteLLM proxy enabled flag (referenced in proxy_router.py)
- `LITE_LLM_PROXY_APP_KEY`, `LITE_LLM_APP_KEY` — LiteLLM authentication keys (referenced in proxy_router.py:201)

### Configuration Files

**Main Configuration**: `src/codemie/configs/config.py` — Central configuration class using pydantic-settings
**Project Constants**: `src/codemie/core/constants.py` — Contains DEMO_PROJECT and header constants

**Header Constants** (constants.py, referenced in proxy_router.py):
- `HEADER_CODEMIE_CLI_PROJECT` — CLI project header name
- `HEADER_CODEMIE_CLI` — CLI version header
- `HEADER_CODEMIE_CLIENT` — Client type header
- `HEADER_CODEMIE_SESSION_ID` — Session ID header
- `HEADER_CODEMIE_REQUEST_ID` — Request ID header

### Feature Flags and Deployment Concerns

**User Management Feature Flag**: `ENABLE_USER_MANAGEMENT` (config.py:199)
- Controls whether persistent user management is active
- Affects authentication flows and personal project creation

**LLM Proxy**: `LLM_PROXY_ENABLED` (referenced but not defined in config.py excerpt)
- Gates LiteLLM proxy endpoint registration

**CLI Version Enforcement**: `CODEMIE_MIN_CLI_VERSION` (proxy_router.py:249)
- Minimum CLI version check before proxying requests
- Rejects outdated CLI versions with 426 status code

---

## 6. Risk Indicators

- **Multiple fallback chains**: Four distinct fallback paths (sign-in, assistant, proxy, CLI) make consistent enforcement difficult
- **Silent failure propagation**: ensure_personal_project_async catches exceptions and returns False, allowing authentication to proceed with no project
- **Empty string fallbacks**: Multiple locations default to empty string `""` when project is missing (platform_tool.py:206, proxy_router.py:292)
- **No entry point validation**: No explicit project requirement check at API entry points before request processing
- **User.current_project implicit fallback**: Property returns DEMO_PROJECT when user has no projects — changes may break this assumption
- **Background consumer identification**: No explicit marker to identify background consumers — exemption relies on code path analysis
- **Migration complexity**: Empty-project assistants need reporting to admins without auto-migration — requires ES query and admin notification mechanism
- **CLI project guard scope**: Unclear if CLI guard should be backend enforcement or CLI-side validation
- **External/service_account users**: Recent exclusion from personal project creation (2026-08-27 story) may affect project availability assumptions
- **Test coverage debt**: Authentication failure path for personal project creation not covered by tests

---

## 7. Summary for Complexity Assessment

This task spans four architectural layers (Service, API/Router, Agent Tools, Security) and requires coordinated changes across authentication flows, assistant validation, proxy routing, and CLI request handling. The primary technical challenge is replacing multiple fallback-to-empty-string patterns with explicit validation that fails early while preserving the non-blocking personal project creation behavior for backward compatibility.

The authentication and registration services (service layer) currently allow personal project creation to fail silently via PersonalProjectService.ensure_personal_project_async, which catches all exceptions and returns False. Changing this to fail authentication introduces a critical behavior change: users whose personal project creation fails will be unable to sign in. This requires careful handling of edge cases (e.g., external/service_account user types, which are already excluded from personal project creation per the 2026-08-27 story).

The assistant project validation (agent tools layer) has two fallback locations: platform_tool.py:206 falls back to empty string, and validation utils.py:114 falls back to user.current_project. Both need to be replaced with explicit validation that rejects empty/missing projects, but this must preserve the user.current_project fallback for legitimate cases where the assistant has no explicit project assignment.

The proxy router (API layer) currently falls back from HEADER_CODEMIE_CLI_PROJECT → user.username → empty string (proxy_router.py:292). This needs to be replaced with validation that rejects requests without a real project header. However, identifying background consumers (datasource indexing, skill/workflow generators) that should be exempted from this requirement is non-trivial, as there is no explicit marker in the codebase.

The CLI project guard is underspecified: it's unclear whether this should be enforced in the backend (proxy router rejection) or via CLI-side validation (user education). The acceptance criterion "CLI tells user project is required and how to set one" suggests user-facing messaging, but the backend must still enforce the requirement to close the gap.

Key risk factors: Silent failure propagation in authentication flows, multiple fallback chains with different semantics, no explicit background consumer marker, potential breaking changes for users with personal project creation failures, and the migration reporting requirement for empty-project assistants (requires Elasticsearch query and admin notification mechanism).

---

## 8. External References

None named by the task
