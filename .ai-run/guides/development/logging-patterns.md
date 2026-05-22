# Logging Patterns

## Context In Messages

Use concise contextual log messages and avoid leaking secrets or tokens.

| Avoid | Prefer |
|---|---|
| Logging raw request headers, tokens, or provider credentials | Log operation, IDs, status, and sanitized details |
| Splitting one failure across several low-context messages | One actionable message with relevant context |

Evidence: auth logs unexpected error type and message server-side at `src/codemie/rest_api/security/authentication.py:114`.

## Severity

Match log severity to operational impact.

| Avoid | Prefer |
|---|---|
| Error logs for expected validation failures | Warning or structured client error |
| Warning-only logs for server failures | Error with exception info when debugging requires stack context |

Evidence: `ExtendedHTTPException` handler logs 5xx errors differently from lower-status exceptions at `src/codemie/rest_api/main.py:826`.
