# Layered Architecture

## API To Service To Repository

Keep HTTP concerns in routers, business orchestration in services, and persistence in repositories.

| Avoid | Prefer |
|---|---|
| Calling database code directly from routers | Delegate from `src/codemie/rest_api/routers/` to `src/codemie/service/` |
| Returning HTTP response objects from repositories | Return domain/data objects and let upper layers format |

Evidence: routers are registered from `src/codemie/rest_api/main.py:658`; service files live under `src/codemie/service/assistant_service.py`; repositories live under `src/codemie/repository/application_repository.py`.

## Startup And Router Ownership

Register API surfaces centrally and keep feature startup side effects explicit.

| Avoid | Prefer |
|---|---|
| Hidden route registration in service modules | Add routers through the main FastAPI app registration area |
| Feature flags scattered across routers | Gate optional routers where the app is assembled |

Evidence: the FastAPI app is created at `src/codemie/rest_api/main.py:629`; optional user-management routers are gated at `src/codemie/rest_api/main.py:706`.

## Shared Core

Put cross-cutting exceptions, constants, config, and utilities in core/config modules instead of feature packages.

| Avoid | Prefer |
|---|---|
| Defining duplicate exception types per feature | Use shared exceptions from `codemie.core` |
| Reading environment variables directly throughout code | Use config modules under `src/codemie/configs/` |

Evidence: API exception handlers consume shared exceptions at `src/codemie/rest_api/main.py:804`.
