# Configuration Patterns

## Central Config

Use `src/codemie/configs/` and existing YAML config trees for runtime settings.

| Avoid | Prefer |
|---|---|
| Reading environment variables directly in feature code | Add or reuse central config values |
| Hardcoding model provider choices | Use `MODELS_ENV` and provider config files |

Evidence: README describes `MODELS_ENV` and `config/llms/` provider files at `README.md:61`.

## Feature Flags

Gate optional behavior through config at assembly points or service boundaries.

| Avoid | Prefer |
|---|---|
| Scattered feature flag checks in unrelated modules | Centralize checks near app/router/service assembly |
| Assuming enterprise features always exist | Use enterprise loader/provider abstractions |

Evidence: user-management routers are gated by config in `src/codemie/rest_api/main.py:706`.

### Workflow Contract Linking

`WORKFLOW_CONTRACT_LINKING_ENABLED` (bool, `False` in `config.py`) turns on save-time contract linking and validation for workflows. Validation reads it in one place, `contract_linking_enabled()`, and the pipeline passes the result down. It is also exposed to the UI as the `features:workflowContracts` runtime component (`customer_config.py`), which hides contract editing and Check while linking is off; that component reads `config.WORKFLOW_CONTRACT_LINKING_ENABLED` directly, like the other runtime components. A new flag is set in all three places: `config.py`, `.env.example` (`true` for local development) and the Helm `extraEnv` (`"false"`).

| Avoid | Prefer |
|---|---|
| `config.WORKFLOW_CONTRACT_LINKING_ENABLED` checks in routers or the executor | Call `save_validation.contract_linking_enabled()`, or pass `link_contracts` into `WorkflowExecutor.validate_workflow_with_report` |

Evidence: `src/codemie/configs/config.py::WORKFLOW_CONTRACT_LINKING_ENABLED`, `src/codemie/service/workflow/save_validation.py::contract_linking_enabled`, `src/codemie/configs/customer_config.py::workflowContracts`, `.env.example::WORKFLOW_CONTRACT_LINKING_ENABLED`, `deploy-templates/values.yaml::WORKFLOW_CONTRACT_LINKING_ENABLED`.

## Chat Contextual Naming

| Setting | Type | Default | Toggle mechanism |
|---|---|---|---|
| `CHAT_CONTEXTUAL_NAMING_ENABLED` | bool | `False` | `DynamicConfigService.get_bool_value_safe`, runtime-togglable via admin REST API `PUT /v1/dynamic-config/CHAT_CONTEXTUAL_NAMING_ENABLED` — no code deploy |
| `CHAT_CONTEXTUAL_NAMING_LLM_MODEL` | str | `"gpt-5-nano-2025-08-07"` | static `config.py` / env var only — requires restart/redeploy to change |

On any failure (LLM error, timeout, empty output) or when the flag is off, the conversation keeps its legacy truncated-first-message name — no user-facing error, no API/frontend surface change.

Known limitation: conversations created via `background_task=True` requests never get the LLM name — the naming task is scheduled from inside `_background_generate`, which itself runs in a separate thread-pool executor after the original request's `BackgroundTasks` object has already finished running, so the scheduled task is never invoked. They still fall back to the legacy name safely.

Evidence: `src/codemie/service/chat_naming_service.py`, `src/codemie/service/conversation_service.py:236-243`.
