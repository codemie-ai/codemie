---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 1fb0123fec9c6130992319b428404eb4efbb6fd4
---

# Code review — 2026-11-12-epmcdme-15516

**Decision:** approve (confidence high).
**Base:** `266837d80a8fd05a3d6fb5032c3747f37db7db57` (origin/main) · **Reviewed head:** `1fb0123fec9c6130992319b428404eb4efbb6fd4` · **Ticket:** EPMCDME-15516 · **MR:** !4400

The commit that adds this directory comes after the reviewed head and changes no code.

## Lint follow-up after `3bebfe775` (style-only, not a new review round)

The `changed-checks` CI task (strict ruff on changed files) flagged two new `UP038` issues in `history_projection_service.py`: `isinstance(contexts, (list, tuple))` in `_collect_context_tool_names` and `isinstance(skill_ids, (list, tuple, set))` in `_has_skill_tool`. Both now use the `X | Y` form; `isinstance` treats a union and a tuple of the same types identically, so behavior is unchanged. `ruff check`, `ruff format --check` and `pytest tests/codemie/rest_api/handlers tests/codemie/service/conversation` (404 passed) were re-run. `decision` and `reviewed_head` above stay as the review set them.

## Scope

Branch commits (merge commits from main excluded):

- `90e8130d3` EPMCDME-15516: Replay datasource tool calls natively in history projection
- `338b7b8eb` EPMCDME-15516: Move replay tool availability into ConversationHistoryProjectionService

Diff against the base: 3 files, +158/-50.

- `src/codemie/rest_api/handlers/assistant_handlers.py`: the replay tool-name helpers are removed; the handler calls `ConversationHistoryProjectionService.get_available_tool_names(self.assistant)`.
- `src/codemie/service/conversation/history_projection_service.py`: `get_available_tool_names` plus the moved helpers and the new `_collect_context_tool_names`.
- `tests/codemie/service/conversation/test_history_projection_service.py`: tests for the datasource-bound names.

The two merges of main (`729aff84f`, `1fb0123fe`) had one conflict, in `assistant_handlers.py`: main added `_build_agent` and `_execution_llm_model` next to the helpers this branch removes. Resolution: keep the main methods, drop the helpers. Main changed neither the helpers nor their callers.

## Acceptance

| Item | Status | Evidence |
|---|---|---|
| Datasource tool calls of the current assistant replay natively in `NATIVE_TOOLS_MODE` | pass | `available_tool_names` now contains the runtime names of KB, provider and code tools, so `_should_render_native_tool_replay` matches the name stored in the tool thought |
| Text ledger kept for another assistant's tools and for tools no longer available | pass | projection logic unchanged; names absent from the set still fall back to the ledger |
| Review comment (move the logic into `ConversationHistoryProjectionService`) | pass | `338b7b8eb`; the handler keeps one call |

## Runtime name check

Each name built by `_collect_context_tool_names` was compared with the place that binds the tool at runtime:

| Context type | Built here | Runtime binder | Match |
|---|---|---|---|
| Knowledge base | `adapt_tool_name("search_kb_{}", context.name)` | `SearchKBTool.__init__`: `adapt_tool_name(name_template, index_info.repo_name)`, template `search_kb_{}` | yes |
| Provider | `f"{sanitize_datasource_name(context.name)}_{tool}"` for configured tool names | `ProviderToolFactory._tool_name`: `f"{sanitize_datasource_name(repo_name)}_{tool_config.name}"`, no hashing on either side | yes (superset) |
| Code | `adapt_tool_name(f"{tool}_{{}}", context.name)` for configured names plus `search_code_repo`, `get_repository_file_tree` | `CodeToolkit._tool_name`: `adapt_tool_name(tool.name + "_{}", repo_name)`; `_add_code_tools` binds tree and search even when not configured | yes (superset) |

`context.name` is the datasource `repo_name`: `ToolkitService` resolves the index with `repo_name=context.name`. The `_v2` code tools (`search_code_repo_v2`, `get_repository_file_tree_v2`) are bound only when configured in toolkits, so their names are already in the configured set. Names over the length limit are hashed by `adapt_tool_name` on both sides.

## Standards

- Commit format: pass, both subjects follow `EPMCDME-####: Description`.
- Code quality: pass. Small typed static helpers, no new module. The new imports in the projection service (`codemie.agents.tools.*`, `codemie.agents.utils`, `ContextType`) create no import cycle: the unit suite imports the service directly.
- Security: pass. No logging of tool arguments or credentials; the change only widens a name set used for replay formatting.

## Findings

None blocking. Note, no change requested: provider and code names are a superset of the tools actually bound (every configured toolkit tool name is prefixed per datasource). An extra name only lets a stored call with that exact name replay natively; it never adds a tool to the agent.

## Verification

- `ruff check`, `ruff format --check` on the changed files: exit 0.
- `pytest tests/codemie/rest_api/handlers tests/codemie/service/conversation` at the reviewed head: 404 passed.
- `test_history_projection_service.py`: 21 passed.
- CI on `729aff84f`: ci-pipeline (build, sonar, docker-build-verify, gitleaks) passed, ai-review "no blockers found", SonarQube quality gate passed.
- `make test-harness` (from the MR description, pre-merge head): 197 passed, 4 skipped. Not repeated after the main merges.
