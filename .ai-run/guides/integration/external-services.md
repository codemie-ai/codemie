# External Services

## Adapter Boundary

Keep external API details out of routers and prompts.

| Avoid | Prefer |
|---|---|
| Direct SDK calls from endpoint handlers | Service or toolkit adapter |
| Provider-specific response objects crossing multiple layers | Normalize at the boundary |

Evidence: datasource processors live under `src/codemie/datasource/`; tool integrations live under `src/codemie_tools/`.

## Credentials

Read external-service credentials through config/provider mechanisms.

| Avoid | Prefer |
|---|---|
| Hardcoded tokens or URLs | Environment/config-backed setup |
| Logging provider credentials on failure | Sanitized error details |

Evidence: README documents local environment variables and provider configuration at `README.md:47`.
