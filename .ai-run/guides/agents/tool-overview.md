# Tool Overview

## Toolkit Layers

The repo has two tool layers: platform agent tools and reusable external toolkits.

| Layer | Location | Use |
|---|---|---|
| Agent platform tools | `src/codemie/agents/tools/` | Runtime tool interfaces, schema compatibility, platform adapters |
| Reusable integrations | `src/codemie_tools/` | Git, cloud, QA, file analysis, data management, notification, and other toolkits |

Evidence: both packages are included from `src` at `pyproject.toml:16`.

## Integration Boundaries

Keep external service clients behind toolkit or service boundaries.

| Avoid | Prefer |
|---|---|
| Calling GitLab/Jira/cloud SDKs from routers | Use a service or `codemie_tools` adapter |
| Exposing low-level provider objects to agents | Return normalized tool outputs |

Evidence: Git provider toolkits are grouped under `src/codemie_tools/git/`.
