# Service Layer Patterns

## Orchestration Boundary

Services coordinate repositories, providers, and domain decisions. Keep request parsing and response formatting outside service methods.

| Avoid | Prefer |
|---|---|
| Passing `Request` through business logic without need | Pass explicit user, IDs, and command data |
| Hiding repository writes in router handlers | Put orchestration in a service method |

Evidence: workflow execution creation is coordinated from `WorkflowExecutor.create_executor` at `src/codemie/workflows/workflow.py:112`.

## Feature Services

Follow existing feature service naming and package layout.

| Avoid | Prefer |
|---|---|
| One broad catch-all service | Feature-scoped services like `assistant_service.py`, `workflow_service.py`, or `skill_service.py` |
| Duplicating provider-selection rules | Use existing provider registries and factories |

Evidence: service modules are grouped under `src/codemie/service/`; LLM proxy provider registration is centralized from app startup at `src/codemie/rest_api/main.py:464`.
