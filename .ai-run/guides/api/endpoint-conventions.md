# Endpoint Conventions

## Request And Response Models

Keep API schemas near the REST API layer and use Pydantic/SQLModel models consistently.

| Avoid | Prefer |
|---|---|
| Unstructured request dictionaries | Typed request models |
| Returning persistence objects without considering API shape | Use explicit response models when behavior is user-facing |
| Defining a schema in a router module that a service or another module imports | Put it in `src/codemie/rest_api/models/<feature>.py`; router and service both import it from there |

Evidence: REST API models live under `src/codemie/rest_api/models/`; budget schemas are imported from `codemie.rest_api.models.budget` by both `src/codemie/rest_api/routers/budget_router.py:25` and `src/codemie/service/budget/budget_service.py:51`.

## Router Size

Use handlers or services when endpoint logic grows beyond request validation and delegation.

| Avoid | Prefer |
|---|---|
| Large endpoint functions with orchestration and persistence | Router validates, service orchestrates |
| Copying helper logic across routers, or importing another router module's private `_helper` | Router helpers shared across modules in `src/codemie/rest_api/routers/utils.py`; request-context utilities in `src/codemie/rest_api/utils/`; business logic in the service layer |
| Putting a helper used by one router package into a route submodule | A package-private underscore module with no `router`: `_common.py`, `_shared.py`, `_<concern>_support.py` |

Evidence: assistant request handling uses `src/codemie/rest_api/handlers/assistant_handlers.py` for non-trivial endpoint behavior; `get_assistant_by_id_or_raise` at `src/codemie/rest_api/routers/utils.py:103` serves `src/codemie/rest_api/routers/ide.py:33` and the assistant package; `_authorize_project_access` at `src/codemie/rest_api/routers/projects/_common.py:47`.
