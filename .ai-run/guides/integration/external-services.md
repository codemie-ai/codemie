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

## Inbound webhook providers

Providers we host inbound webhooks for (GitHub, GitLab, …) have provider-specific delivery semantics that must be respected even when it looks like a plain HTTP endpoint.

| Concern | Rule |
|---|---|
| Response codes for benign filtering | `2xx` for "received, filtered out"; `4xx` for auth failures only. Providers auto-deactivate endpoints after repeated non-2xx. |
| Mixed event types on one URL | A single webhook URL can receive multiple event types. A filter for one event type must not reject the others — pass unknown/off-topic events through. |
| Verification code location | Provider-specific verification (signature/token, event filter, metadata extraction) lives in a per-provider class under `triggers/bindings/*_webhook_security.py`. `webhook.py` composes them and owns logging/metrics tied to `Settings`. |
| Dispatch signal | Every path of `WebhookService.verify_security_header` returns `bool` — `True` dispatches, `False` ACKs 200 without invoking the resource. `None` returns are a bug. |
| Body parsing | Parse the raw payload once inside the security class. Treat parse failures as "not a known event" and pass through; do not raise from a parse error. |
| Error causes on metrics | Distinguish auth failure (`*_token_invalid`, `*_signature_verification_failed`) from routine filter rejections (`*_event_filtered`) so dashboards don't merge attacks with benign filters. |
