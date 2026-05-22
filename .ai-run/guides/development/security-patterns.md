# Security Patterns

## Authentication And Authorization

Use the central authentication dependency and role helpers.

| Avoid | Prefer |
|---|---|
| Reimplementing bearer or bind-key parsing in endpoints | Use `authenticate` |
| Inline role checks with inconsistent messages | Use admin/maintainer/project-admin helper dependencies |

Evidence: `authenticate` handles internal bind-key and external provider flows at `src/codemie/rest_api/security/authentication.py:59`.

## Secret Handling

Keep credentials in configuration and never log sensitive values.

| Avoid | Prefer |
|---|---|
| Hardcoded model provider keys | Environment/config-backed provider setup |
| Logging tokens, API keys, or full auth headers | Log sanitized operation context |

Evidence: README documents environment variables for Azure OpenAI local setup at `README.md:47`.
