# Project Structure

## Main Packages

`codemie` contains backend application logic; `codemie_tools` contains reusable tool integrations.

| Area | Use |
|---|---|
| `src/codemie/rest_api/` | FastAPI app, routers, request security |
| `src/codemie/service/` | Business services and orchestration |
| `src/codemie/repository/` | Persistence and storage abstractions |
| `src/codemie/agents/` | LangChain agent runtime |
| `src/codemie/workflows/` | LangGraph workflow execution |
| `src/codemie_tools/` | Agent-facing tools and external adapters |

Evidence: Poetry includes both packages from `src` at `pyproject.toml:16`.

## Where New Code Belongs

Choose the narrowest existing package that owns the behavior.

| Avoid | Prefer |
|---|---|
| Adding integration logic to routers | Put provider logic in services or `codemie_tools` adapters |
| Mixing workflow node behavior with generic service code | Use `src/codemie/workflows/nodes/` for node behavior |
| Creating new top-level packages without precedent | Extend the existing package boundary |

Evidence: integration-heavy tool packages exist under `src/codemie_tools/git/`, `src/codemie_tools/cloud/`, and `src/codemie_tools/qa/`.
