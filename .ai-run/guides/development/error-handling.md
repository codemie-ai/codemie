# Error Handling

## Typed Exceptions

Use shared project exceptions for domain and HTTP-facing errors.

| Avoid | Prefer |
|---|---|
| `raise Exception(...)` for user-facing errors | `ValidationException`, `ExtendedHTTPException`, or domain-specific exceptions |
| Returning inconsistent error shapes | Let central handlers format responses |

Evidence: `ValidationException` and `ExtendedHTTPException` handlers are registered at `src/codemie/rest_api/main.py:804`.

## Sanitized Failures

Log internal detail while returning safe client-facing messages.

| Avoid | Prefer |
|---|---|
| Returning raw exception text from auth/provider failures | Return a stable message and log detailed context |
| Swallowing exceptions without context | Log with useful type and operation context |

Evidence: authentication catches unexpected errors and returns a sanitized auth error at `src/codemie/rest_api/security/authentication.py:109`.

## Accurate Error Details

An error message must describe the failure that actually happened. If one code path can
fail for several distinct reasons, branch and produce a distinct message per reason —
incident diagnosis depends on it.

| Avoid | Prefer |
|---|---|
| Interpolating a possibly-absent value into a message (`"... 'None' ..."`) | Checking the value first and naming the real state |
| One catch-all message for unrelated failure modes | One message per failure mode |
| Silently substituting a default when input cannot be processed | Logging the substitution with identifying context (entity/correlation ID) |

Evidence: distinct error paths and a logged fallback at `src/codemie/triggers/bindings/webhook.py:352`.

## Workflow Save Errors

Workflow routers map what the save pipeline raises with three context managers in `src/codemie/rest_api/routers/workflow.py`:

- `_database_errors`: a `SQLAlchemyError` becomes a 503 `Workflow database is unavailable` with fixed details and no SQL, bind values or connection strings. It wraps create, update, both validate routes and version rollback.
- `_wrap_validation_errors(error_format)`, used inside it: `ValidationException`, `NotFoundException` and `ExtendedHTTPException` pass through; every other failure becomes a 400 `Workflow Configuration error`, with the executor's dict as `details` when `error_format=json`.
- `_guardrail_errors`: a failed guardrail assignment, including the guardrail service's own 404, 403 or 400, becomes the same 400 with the error message as `details`, on create, update and the saved-workflow dry-run.

Contract failures arrive as a single `contract_validation` error built by `build_contract_rejection` (see `.ai-run/guides/workflows/langgraph-workflows.md`). The shared credential checks (`CredentialValidator`, `AssistantIntegrationValidator`, `ToolMetadataService.resolve_config`) let a `SQLAlchemyError` through so the workflow routes answer 503; the assistant routes catch it in `_validate_assistant_integrations` and report the integrations as valid.

| Avoid | Prefer |
|---|---|
| Per-handler `try/except` ladders that re-wrap workflow validation errors | `with _wrap_validation_errors(error_format)` inside `with _database_errors()` |
| Putting `str(exc)` of a database error into a response | Letting it reach `_database_errors` |
| Letting a governance `ValidationException` be wrapped into the 400 | Raise it outside the wrapper's catch-all, as the context manager does |

Evidence: `src/codemie/rest_api/routers/workflow.py::_database_errors`, `src/codemie/rest_api/routers/workflow.py::_wrap_validation_errors`, `src/codemie/rest_api/routers/workflow.py::_guardrail_errors`, `src/codemie/rest_api/routers/assistant/management.py::_validate_assistant_integrations`, `src/codemie/workflows/validation/contract_issues.py::build_contract_rejection`.

