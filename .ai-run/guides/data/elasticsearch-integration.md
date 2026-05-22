# Elasticsearch Integration

## Repository Access

Keep Elasticsearch details inside repository or service classes that already own search behavior.

| Avoid | Prefer |
|---|---|
| Building ES query bodies in routers | Build queries in repositories/services |
| Returning raw ES hits through API layers | Convert hits to project entities |

Evidence: `BaseElasticRepository` wraps `get`, `search`, `index`, and `update` at `src/codemie/repository/base_elastic_repository.py:25`.

## Index Setup

Index creation belongs in startup or dedicated services, not scattered feature code.

| Avoid | Prefer |
|---|---|
| Creating indexes opportunistically inside unrelated handlers | Use explicit startup/service setup |
| Ignoring index creation failures silently | Log actionable context |

Evidence: conversation analytics index setup is triggered during app initialization at `src/codemie/rest_api/main.py:357`.
