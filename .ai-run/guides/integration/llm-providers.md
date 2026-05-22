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
