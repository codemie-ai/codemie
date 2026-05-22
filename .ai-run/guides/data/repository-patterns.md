# Repository Patterns

## Repository Boundary

Repositories own data access and storage-specific behavior.

| Avoid | Prefer |
|---|---|
| Direct storage access from services when a repository exists | Extend the matching repository |
| Returning provider-specific raw responses to services | Convert to project entities or models |

Evidence: repository modules live under `src/codemie/repository/`; base Elasticsearch repository converts hits through `to_entity` at `src/codemie/repository/base_elastic_repository.py:76`.

## Storage Factories

Use existing repository factories when storage provider varies by config.

| Avoid | Prefer |
|---|---|
| `if provider == ...` branches scattered across services | Central factory/provider selection |
| Adding a cloud repository without matching tests | Follow existing AWS/Azure/GCP repository patterns |

Evidence: file repositories include AWS, Azure, GCP, and filesystem implementations under `src/codemie/repository/`.
