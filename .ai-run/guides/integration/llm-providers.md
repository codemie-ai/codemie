# LLM Providers

## Provider Configuration

Use model provider YAML files and central LLM services.

| Avoid | Prefer |
|---|---|
| Hardcoding model names in feature code | Read configured model/service values |
| Assuming one provider | Keep AWS, Azure, GCP, Anthropic, LiteLLM paths pluggable |

Evidence: provider config files live under `config/llms/`; README documents `MODELS_ENV` at `README.md:61`.

## Enterprise LiteLLM

Treat LiteLLM proxy behavior as provider-backed enterprise functionality.

| Avoid | Prefer |
|---|---|
| Importing enterprise implementation everywhere | Use service/provider registry boundaries |
| Assuming LiteLLM is always enabled | Gate behavior with `is_litellm_enabled` and config |

Evidence: app startup registers LiteLLM providers conditionally at `src/codemie/rest_api/main.py:265`.

## Outbound Request Tagging

Direct provider calls and LiteLLM-proxied calls tag traffic through different mechanisms;
never mix them. When touching outbound LLM requests, route header changes through the
shared header-building helper rather than adding them at individual call sites.

| Avoid | Prefer |
|---|---|
| Adding `X-CodeMie-*` headers on the LiteLLM path | LiteLLM tags via `x-litellm-tags`; direct provider factories tag via headers |
| Assuming one header mechanism fits every provider | Each provider client takes headers through its own argument; some (e.g. Bedrock) accept no custom HTTP headers at all |
| Letting injected defaults override model-config `client_headers` | Seed defaults first; explicit model config wins on collision |
| Tagging with user-identifying information | Carry only non-personal context (app version, project) |

Evidence: `_build_codemie_tagging_headers()` and the per-provider factories in `src/codemie/core/dependecies.py`; header constants in `src/codemie/core/constants.py`.
