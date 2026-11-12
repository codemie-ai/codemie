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

## Identity Providers

An IdP declares `validates_token = True` only when its tokens are verified before they reach it: by its own signature check (`JwksValidatingIdp`), or by the gateway in front of it (`EnterpriseIdpWrapper`, which logs `idp_snapshot_trust` at startup when IDP-claims mode runs without JWKS validation). Only such sign-ins store the IDP-claims snapshot that scheduled and webhook runs later replay as the owner's memberships, so a provider that trusts a header nothing verified must keep the default `False`.

| Avoid | Prefer |
|---|---|
| Setting `validates_token = True` on a provider that reads identity from a header (`LocalIdp`) | Leave the base default; wrap it with JWKS validation if its tokens must be trusted |
| Writing or reading the claims snapshot from a new call site | Write only on the token-validated sign-in path; read only through `service/user/user_by_id_loader.py` |

Evidence: default at `src/codemie/rest_api/security/idp/base.py:27`; JWKS wrapper at `src/codemie/rest_api/security/idp/jwks_validating.py:57`; enterprise wrapper and its startup warning at `src/codemie/enterprise/idp/dependencies.py:62` and `src/codemie/enterprise/idp/dependencies.py:164`; snapshot write gated at `src/codemie/rest_api/security/user_providers/legacy_jwt.py:65`. Trust window and retention: `.ai-run/guides/integration/budget-attribution.md`.
