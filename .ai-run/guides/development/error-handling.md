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
