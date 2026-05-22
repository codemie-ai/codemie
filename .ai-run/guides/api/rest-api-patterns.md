# REST API Patterns

## Router Registration

Add FastAPI routers under `src/codemie/rest_api/routers/` and register them in the main app.

| Avoid | Prefer |
|---|---|
| Creating routes in service modules | Keep route decorators in router modules |
| Importing routers through side effects | Register explicitly with `app.include_router` |

Evidence: main app includes router modules at `src/codemie/rest_api/main.py:658`.

## Error Responses

Use shared exception types and central handlers for consistent response bodies.

| Avoid | Prefer |
|---|---|
| Returning ad hoc error dictionaries from every endpoint | Raise shared exceptions handled by the app |
| Leaking internal exception details | Use sanitized messages and log details server-side |

Evidence: `ValidationException` is converted to a structured 400 response at `src/codemie/rest_api/main.py:804`; `ExtendedHTTPException` is handled at `src/codemie/rest_api/main.py:812`.

## Authentication Dependencies

Use existing security dependencies for authenticated routes.

| Avoid | Prefer |
|---|---|
| Parsing auth headers directly in endpoint code | Depend on `authenticate` and authorization helpers |
| Duplicating role checks | Reuse admin, maintainer, and project-admin dependencies |

Evidence: authentication provider selection is centralized in `src/codemie/rest_api/security/authentication.py:59`.
