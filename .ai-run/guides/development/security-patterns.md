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

## Untrusted Input Handling

Parse external input exactly as received; reject what cannot be parsed. Any lossy
normalization before a security decision means the decision is made on data that was
never actually sent.

| Avoid | Prefer |
|---|---|
| Lossy repair before parsing (`errors="ignore"`/`"replace"`, stripping, silent coercion) | Strict parsing that fails closed on malformed input |
| Guessing what a malformed payload meant | Reject it explicitly (error or `None`) |

Evidence: strict UTF-8 decode with explicit rejection at `src/codemie/triggers/bindings/gitlab_webhook_security.py:68`.
