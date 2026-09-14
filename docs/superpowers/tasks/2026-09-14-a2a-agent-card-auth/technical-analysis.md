# Technical Research

**Task**: security backend router auth
**Generated**: 2026-09-14T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

The endpoint `GET /v1/a2a/assistants/{assistant_id}/.well-known/agent.json` exposes assistant metadata without authentication or permission checks. Anyone with access to the endpoint can query any assistant ID and receive an agent card with assistant name, description, capabilities, and tool-derived skills.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/routers/a2a.py` — contains the router definition and the following endpoints:
  - `get_assistant_agent_card(assistant_id: str, request: Request)`: No authentication or Ability checks. It fetches the assistant by ID and returns the agent card directly via `assistant_to_agent_card`.
  - `execute_a2a_request(assistant_id: str, request_body: A2ARequestBody, user: User = Depends(authenticate))`: This endpoint correctly enforces authentication using `Depends(authenticate)` and performs a permission check using `Ability(user).can(Action.READ, assistant)`.
- `src/codemie/rest_api/a2a/utils.py` — contains `assistant_to_agent_card` which translates an `Assistant` to an `AgentCard` model containing descriptive metadata.
- `src/codemie/core/ability.py` — defines the `Ability` class and `PERMISSIONS` dictionary. For `Assistant` entity, the READ action requires `[Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN]`.
- `src/codemie/rest_api/models/assistant.py` — defines `Assistant` model containing `is_global` (marketplace assistant) and `shared` fields. `is_shared_with(user: User)` handles permission validation based on whether the assistant is global or shared.

### Architecture and Layers Affected

- **API/Router Layer**: `src/codemie/rest_api/routers/a2a.py` needs to have its `get_assistant_agent_card` endpoint updated from synchronous to asynchronous (`async def`) and integrated with optional or resolved authentication.
- **Security/Auth Layer**: Centralized `authenticate` from `src/codemie/rest_api/security/authentication.py` can be used to authenticate requests.

### Integration Points

- Bypassing authentication must be explicitly restricted to marketplace/global assistants (where `assistant.is_global` is `True`), representing "intentionally public" behavior. All other private/project-specific assistants require an authenticated caller with READ permission.

### Patterns and Conventions

- Standard FastAPI dependency injection: `user: User = Depends(authenticate)` or calling `await authenticate(request)` inside an async function.
- Permission enforcement: `Ability(user).can(Action.READ, assistant)`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/security-patterns.md` states: "Use the central authentication dependency and role helpers... Reimplementing bearer or bind-key parsing in endpoints -> Use `authenticate`".
- `.ai-run/guides/api/rest-api-patterns.md` guides endpoint and router creation.

---

## 4. Testing Landscape

### Existing Coverage

- There is currently no test file targeting `src/codemie/rest_api/routers/a2a.py` or `get_assistant_agent_card`.
- General auth tests exist in `tests/codemie/rest_api/routers/test_auth_router.py` and `test_permission.py`.

### Testing Framework and Patterns

- `pytest` with `pytest.mark.asyncio` for async tests.
- Standard router testing using `client` or `async_client` with mocked or standard dependency overrides for authentication.

### Coverage Gaps

- A completely new test suite or test cases under `tests/codemie/rest_api/routers/test_a2a.py` must be written to cover all acceptance criteria:
  - Anonymous request attempting to access a private assistant -> 401 Unauthorized.
  - Anonymous request attempting to access a public (global) assistant -> 200 OK.
  - Authenticated caller with READ access accessing a private assistant -> 200 OK.
  - Authenticated caller without READ access accessing a private assistant -> 403 Forbidden.
  - Intentionally public (global) assistant accessed by any caller -> 200 OK.

---

## 5. Configuration and Environment

### Environment Variables

- Standard authentication flags (`ENABLE_USER_MANAGEMENT`) are respected by the central `authenticate` dependency.
