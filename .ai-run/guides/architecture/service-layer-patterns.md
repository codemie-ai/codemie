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

## Shared Pipelines In A Feature Package

When several entry points run the same multi-step flow, put it in a function module of the feature package and keep entry-specific steps in the caller. Do not leave a method on a broad service that only some entry points use.

| Avoid | Prefer |
|---|---|
| Copying the flow into each router, or a `WorkflowService.validate_for_update`-style method on the broad service | A module in the feature package, such as `src/codemie/service/workflow/save_validation.py` |
| Lazy imports in the service to dodge a cycle with `workflows.workflow` | Put the flow where it can import the executor at module level |

Evidence: `src/codemie/service/workflow/save_validation.py::prepare`, used by `src/codemie/rest_api/routers/workflow.py::update_workflow` and `src/codemie/service/workflow/workflow_version_service.py::WorkflowVersionService._validate_restored_workflow`.

