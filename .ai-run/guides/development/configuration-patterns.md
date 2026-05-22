# Configuration Patterns

## Central Config

Use `src/codemie/configs/` and existing YAML config trees for runtime settings.

| Avoid | Prefer |
|---|---|
| Reading environment variables directly in feature code | Add or reuse central config values |
| Hardcoding model provider choices | Use `MODELS_ENV` and provider config files |

Evidence: README describes `MODELS_ENV` and `config/llms/` provider files at `README.md:61`.

## Feature Flags

Gate optional behavior through config at assembly points or service boundaries.

| Avoid | Prefer |
|---|---|
| Scattered feature flag checks in unrelated modules | Centralize checks near app/router/service assembly |
| Assuming enterprise features always exist | Use enterprise loader/provider abstractions |

Evidence: user-management routers are gated by config in `src/codemie/rest_api/main.py:706`.
