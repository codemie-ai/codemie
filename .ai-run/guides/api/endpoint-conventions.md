# Endpoint Conventions

## Request And Response Models

Keep API schemas near the REST API layer and use Pydantic/SQLModel models consistently.

| Avoid | Prefer |
|---|---|
| Unstructured request dictionaries | Typed request models |
| Returning persistence objects without considering API shape | Use explicit response models when behavior is user-facing |

Evidence: REST API models live under `src/codemie/rest_api/models/`.

## Router Size

Use handlers or services when endpoint logic grows beyond request validation and delegation.

| Avoid | Prefer |
|---|---|
| Large endpoint functions with orchestration and persistence | Router validates, service orchestrates |
| Copying helper logic across routers | Shared helpers under `src/codemie/rest_api/utils/` or service layer |

Evidence: assistant request handling uses `src/codemie/rest_api/handlers/assistant_handlers.py` for non-trivial endpoint behavior.
