# REST API Patterns

## Router Registration

Add FastAPI routers under `src/codemie/rest_api/routers/` and register them in the main app. A router too large for one module becomes a package of per-concern submodules.

| Avoid | Prefer |
|---|---|
| Creating routes in service modules | Keep route decorators in router modules |
| Importing routers through side effects | Register explicitly with `app.include_router` |
| Growing one oversized router module | Package `routers/<feature>/`: `__init__.py` builds the parent `APIRouter` (tags, prefix, package-wide dependencies) and includes each submodule's bare `router = APIRouter()`; `main.py` still includes only `<feature>.router` |
| Reordering `include_router` calls or moving a route between submodules without checking overlaps | Keep the recorded order; Starlette serves the first matching route, so a literal path (`/conversations/search`) must stay ahead of a parameterized one (`/conversations/{conversation_id}`) |
| Changing a route, its models or its order without the router guards in view | Expect the OpenAPI snapshot guard to fail on any contract change; see Router Contract Guards in `.ai-run/guides/testing/testing-api-patterns.md` |

Evidence: main app includes routers from `src/codemie/rest_api/main.py:1030`; package assembly at `src/codemie/rest_api/routers/projects/__init__.py:22`; submodule router at `src/codemie/rest_api/routers/projects/crud.py:55`; include-order note at `src/codemie/rest_api/routers/conversation/__init__.py:27`.

## Error Responses

Use shared exception types and central handlers for consistent response bodies.

| Avoid | Prefer |
|---|---|
| Returning ad hoc error dictionaries from every endpoint | Raise shared exceptions handled by the app |
| Leaking internal exception details | Use sanitized messages and log details server-side |

Evidence: `ValidationException` is converted to a structured 400 response at `src/codemie/rest_api/main.py:1192`; `ExtendedHTTPException` is handled at `src/codemie/rest_api/main.py:1208`.

## Authentication Dependencies

Use existing security dependencies for authenticated routes.

| Avoid | Prefer |
|---|---|
| Parsing auth headers directly in endpoint code | Depend on `authenticate` and authorization helpers |
| Duplicating role checks | Reuse admin, maintainer, and project-admin dependencies |

Evidence: authentication provider selection is centralized in `src/codemie/rest_api/security/authentication.py:134`.
