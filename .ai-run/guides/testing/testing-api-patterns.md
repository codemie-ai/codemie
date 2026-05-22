# API Testing Patterns

## Router Tests

Test API behavior through the router/client boundary when request validation, auth, or response shape matters.

| Avoid | Prefer |
|---|---|
| Testing only service methods for endpoint response contracts | API tests for status codes and response bodies |
| Repeating auth setup in every test | Reuse existing fixtures/helpers near API tests |

Evidence: API routers are centralized under `src/codemie/rest_api/routers/`; router tests exist under `tests/unit/routers/`.

## Error Cases

Include negative cases for validation and authorization changes.

| Avoid | Prefer |
|---|---|
| Only testing happy paths for protected routes | Test unauthenticated/unauthorized behavior |
| Asserting raw exception text | Assert structured error response fields |

Evidence: app-level error response shape is defined at `src/codemie/rest_api/main.py:804`.
