# Custom Tool Creation

## Choose The Right Layer

Use `codemie_tools` for reusable external capabilities and `codemie/agents/tools` for platform-facing adapters.

| Avoid | Prefer |
|---|---|
| One-off tool logic in agent prompts | Tool class with explicit inputs and outputs |
| Duplicating an existing provider toolkit | Extend the nearest toolkit package |

Evidence: provider toolkits exist under `src/codemie_tools/git/`, `src/codemie_tools/cloud/`, `src/codemie_tools/qa/`, and `src/codemie_tools/data_management/`.

## Tests

Add or update focused tests when tool schema, serialization, or provider behavior changes.

| Avoid | Prefer |
|---|---|
| Only manual testing for schema changes | Unit tests around schema and toolkit behavior |
| Broad integration tests for simple transformations | Narrow tests near the tool package |

Evidence: existing toolkit tests live under `tests/codemie_tools/` and agent tool tests under `tests/codemie/agents/tools/`.
