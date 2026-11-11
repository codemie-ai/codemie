# LLM Providers

## Provider Configuration

Use model provider YAML files and central LLM services.

| Avoid | Prefer |
|---|---|
| Hardcoding model names in feature code | Read configured model/service values |
| Assuming one provider | Keep AWS, Azure, GCP, Anthropic, LiteLLM paths pluggable |

Evidence: provider configuration files live under `config/llms/`. README documents `MODELS_ENV` at `README.md:61`.

## Enterprise LiteLLM

Treat LiteLLM proxy behavior as provider-backed enterprise functionality.

| Avoid | Prefer |
|---|---|
| Importing enterprise implementation everywhere | Use service/provider registry boundaries |
| Assuming LiteLLM is always enabled | Gate behavior with `is_litellm_enabled` and `config` |

Evidence: app startup registers LiteLLM providers conditionally at `src/codemie/rest_api/main.py:265`.

### Output Token Limit (`max_tokens`)

The LiteLLM factory sends `max_tokens` only for a Claude model (`LLMModel.is_claude`: name, or `claude_family`) with a positive `max_output_tokens`, and only when `LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS` is on. Without it the proxy default (4096) cuts replies. A declared auto-router has no provider metadata of its own. When the catalog is stored, a router without its own limit gets the minimum `max_output_tokens` of its distinct tier models. If every tier is Claude, the router is also marked `claude_family`, so the limit is sent whatever the router's alias.

| Avoid | Prefer |
|---|---|
| Guessing a router limit from the tiers that resolved | Leave it unset when any tier is missing or has no limit. A limit above a tier's own makes the provider reject the request. The `router_output_limit ... source=unresolved_tiers` warning is logged. |
| Writing your own `isinstance(..., int) and > 0` checks | `is_positive_token_limit` (rejects `bool`) |
| Matching `"claude"` in the model name | `LLMModel.is_claude()` |
| A new value derived from router tiers with its own resolution | `LLMService._router_tier_models` (any unresolved tier gives `None`) |

Evidence: `src/codemie/service/llm_service/llm_service.py:185` (`_apply_router_output_limits`, called from `initialize_default_litellm_models` at `:791`); `src/codemie/enterprise/litellm/llm_factory.py:393`; `src/codemie/configs/llm_config.py:229`, `:272`.

## Outbound Request Tagging

Direct provider calls and LiteLLM-proxied calls tag traffic through different mechanisms.
Never mix them. When you touch outbound LLM requests, route header changes through the
shared header-building helper. Do not add them at individual call sites.

| Avoid | Prefer |
|---|---|
| Adding `X-CodeMie-*` headers on the LiteLLM path | LiteLLM tags via `x-litellm-tags`. Direct provider factories tag via headers. |
| Assuming one header mechanism fits every provider | Each provider client takes headers through its own argument. Some, for example Bedrock, accept no custom HTTP headers at all. |
| Letting injected defaults override `client_headers` from the model configuration | Seed defaults first. The explicit value wins on collision. |
| Tagging with user-identifying information | Carry only non-personal context (app version, project) |

Evidence: `_build_codemie_tagging_headers()` and the per-provider factories in `src/codemie/core/dependecies.py`. Header constants are in `src/codemie/core/constants.py`.

## Speech-to-Text Models

The model configured through the `STT_API_*` variables stays the default and is always listed first. When LiteLLM is on, LiteLLM models with `model_info.mode: audio_transcription` are added after it. A deployment with only LiteLLM models lists only those. `GET /v1/audio_transcription_models` returns the merged list. If a LiteLLM model has the same name as the env model, the LiteLLM model wins.

| Avoid | Prefer |
|---|---|
| Building an STT client at the call site | `SttService` in `src/codemie/service/stt_service.py` |
| Sending the audio or transcript to a tracer by hand | `SttService` opens a Langfuse/Phoenix generation through `ObservabilityProvider.update_current_generation` |

A LiteLLM STT model can carry `stt_config` in its `model_info` in the LiteLLM proxy configuration. `default_language` is the language for that model when the request has none. The model from the `STT_API_*` variables has no `stt_config`, so it gets a language only from the request. `forward_request_headers` lists incoming request headers to send to the gateway. `response_headers` and `response_body_fields` list the provider response headers and body fields to return to the caller and record in observability. A trailing `*` matches a prefix. LiteLLM relays an upstream header as `llm_provider-<name>`. CodeMie removes that prefix before it matches a name. The env model has no such metadata.

The optional `conversation_id` form field of `POST /v1/speech-recognition` becomes the `session_id` of the `Audio Transcription` trace. The trace then appears with the other traces of that conversation in Langfuse and Phoenix.

`request_timeout_seconds` in `stt_config` sets the client timeout for that model. The default is 300 seconds, which is above the usual server limit for long audio. CodeMie lists LiteLLM STT models only when `LLM_PROXY_MODE` is `lite_llm`, because only that mode can call them. If the LiteLLM client is not available at call time, the endpoint returns 503, not a client error. STT reuses the existing header lists and has none of its own. A request header is never forwarded if it is in `FORWARDED_HEADERS_BLOCKLIST` (for example `authorization`, `cookie` and `x-api-key`) or in `PROXY_HOP_BY_HOP_HEADERS` of the LiteLLM proxy router (for example `host`, `content-length` and the LiteLLM customer ID header). A provider response header is dropped if it is in either of those or in `PROXY_RESPONSE_HOP_BY_HOP_HEADERS`, and CodeMie also drops values that HTTP headers cannot carry. A model forwards only the headers that its `stt_config` lists, so `x-userinfo` is not sent unless a model lists it. To block more names in every deployment, add them to `FORWARDED_HEADERS_BLOCKLIST`.

If no STT model is configured, or the `STT_API_*` settings are incomplete, `POST /v1/speech-recognition` returns 503 "Speech recognition is not configured". Only an unknown or not allowed model name returns 400 (`UnknownSttModelError`). Other errors are not reported as client errors. A model name needs an authenticated user.
