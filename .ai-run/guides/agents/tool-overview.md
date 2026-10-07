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

## Static Tool Facts

`src/codemie_tools/base/tool_registry.py` is the single source of static tool facts (which tool classes exist under which name, and their `script_callable` flag). Every concrete `CodeMieTool` subclass registers itself on definition.

| Avoid | Prefer |
|---|---|
| A new scan, catalog or name list of tool classes | Read `tool_registry.lookup(name)` |
| Hard-coding a new per-class fact elsewhere | Extend `ToolRecord` in `tool_registry.py` |

Evidence: `CodeMieTool.__pydantic_init_subclass__` calls `tool_registry.register`; `ProjectScope.resolve` uses it to skip building excluded tools.
