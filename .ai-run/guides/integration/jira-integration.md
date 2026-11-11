# Jira Integration

## Work Item Integrations

Keep Jira and related work-item behavior in datasource/tool adapters.

| Avoid | Prefer |
|---|---|
| Calling Jira APIs directly from routers | Use datasource processors or `codemie_tools` project-management adapters |
| Mixing Jira ticket conventions into unrelated code | Keep workflow conventions in `.ai-run/guides/standards/git-workflow.md` |

Evidence: Jira datasource package exists under `src/codemie/datasource/jira/`; project-management tools live under `src/codemie_tools/core/project_management/`.

## Generic Tool Write Keys

`GenericJiraIssueTool.written_refs` reports the issue keys that a call created or changed. The truncation status report uses them (contract: `.ai-run/guides/agents/custom-tool-creation.md`). It normalises `relative_url` first: it adds a leading slash and drops the query string, and the path patterns accept `latest` as the version.

| Call | Result |
|---|---|
| GET, or POST to `/rest/api/<v>/search` | `()` |
| POST `/rest/api/<v>/issue` | `key` from the response body. `None` if the body cannot be read. |
| POST `/rest/api/<v>/issue/bulk` | Every `key` in `issues` |
| Any other write | Issue keys in the URL. A POST with no key in its URL gives `None`. |

When a new POST endpoint returns the created key only in its body (not in the URL), add a pattern for it. Otherwise it gives `None` and the report falls back to scanning the output text.

Evidence: `src/codemie_tools/core/project_management/jira/tools.py:144`; `tests/codemie_tools/core/project_management/jira/test_written_refs.py`.
