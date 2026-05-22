# Jira Integration

## Work Item Integrations

Keep Jira and related work-item behavior in datasource/tool adapters.

| Avoid | Prefer |
|---|---|
| Calling Jira APIs directly from routers | Use datasource processors or `codemie_tools` project-management adapters |
| Mixing Jira ticket conventions into unrelated code | Keep workflow conventions in `.ai-run/guides/standards/git-workflow.md` |

Evidence: Jira datasource package exists under `src/codemie/datasource/jira/`; project-management tools live under `src/codemie_tools/core/project_management/`.
