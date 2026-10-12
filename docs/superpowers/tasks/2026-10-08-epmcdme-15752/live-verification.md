# Live verification, EPMCDME-15752 (branch EPMCDME-15752_script-step-tool-map)

Date 2026-10-08. All values below are test values created for this run (tokens are fake labels such as TOK-ALICE-A; no real secret appears). No file under src/ or tests/ was edited and nothing was committed.

## Environment

- Stopped the `codemie` container (it was crash-looping: `alembic: Can't locate revision 'y1z2a3b4c5d6'`, i.e. the old DB belonged to another branch). Removed `postgres` and `elasticsearch` containers and deleted volumes `codemie_postgres_data`, `codemie_elasticsearch_data` (authorised: DB from scratch).
- Started from this checkout with compose plus an override (kubeconfig mount `.kube-local` -> `/kube`, `CODE_EXECUTOR_KUBECONFIG_PATH=/kube/kubeconfig-internal.yaml`, `CODE_EXECUTOR_DOCKER_IMAGE=codemie/codemie-python:2.52.0`, `FEATURE_WORKSPACE_SCRIPT_BRIDGE=true`): `docker compose -f docker-compose.yml -f <override> up -d codemie`. `src/` is bind-mounted, so the container serves the branch code. Migrations ran on the empty DB (alembic head `f88ad134467d`), "Application startup complete".
- Branch code confirmed live: `/openapi.json` contains schema `ScriptToolAccess`; `WorkflowPublishValidationResponse.warnings` exists; `/v1/workflows` accepted and stored `script_tool_access`.
- Sandbox: existing `k3s` container (pre-existing, kept as found), SandboxMode jobs, real script runs.
- Tool under test: `generic_jira_tool` pointed at a local fake Jira (`http://host.docker.internal:18099`, python http.server) that logs the Authorization token of every request and answers `/rest/api/2/myself` with `displayName = who:<token>`. This shows exactly which integration a call used, and a refused call leaves NO hit in the fake's log (positive control: allowed calls leave one hit).
- Users: alice (workflow creator), bob (runner), admin (superadmin from .env). Project `scn15752` (alice, bob members). Integrations (type Jira, project scn15752): alice `jira-a`=TOK-ALICE-A, `jira-b`=TOK-ALICE-B, `jira-alice-only`; bob `jira-a`=TOK-BOB-A, `jira-b`=TOK-BOB-B, `jira-bob`; alice `conf-a` (Confluence type); alice `jira-otherproj` in alice's personal project.
- Global state is produced by `POST /v1/workflows/{id}/marketplace/publish` (`categories:["architecture"]`).
- LLM (gpt-4.1 via the configured proxy) was used only for the assistant and chat scenarios, test data only.

## Scenarios (script step probe calls `call_tool("generic_jira_tool", ..., integration_alias=...)`, results printed as JSON)

| # | Criterion | Setup | Observed | Fake Jira hits | Result |
|---|---|---|---|---|---|
| S1a | no map -> tool_unavailable | creator=runner=alice, no `script_tool_access` | jira none, jira alias=jira-a, confluence: all `tool_unavailable`, `may_have_run:false` | none | PASS |
| S1b | empty map `[]` -> tool_unavailable | same with `script_tool_access: []` | jira, confluence: `tool_unavailable` | none | PASS |
| S2 | listed tool runs; unlisted -> tool_unavailable; no aliases + alias passed -> bad_arguments | map `[{generic_jira_tool, []}]`, non-global, alice | listed: 200 `who:TOK-ALICE-A`; alias jira-a: `bad_arguments` "takes no integration_alias in this step; omit it."; confluence: `tool_unavailable` | 1 (TOK-ALICE-A) | PASS |
| S2b | same, global, bob runs: automatic lookup is the RUNNING user's | global, map `[{jira, []}]`, run by bob | listed: `who:TOK-BOB-A` (not alice's); alias: `bad_arguments` | 1 (TOK-BOB-A) | PASS |
| S3 | global, [jira-a, jira-b]: a -> creator's; c -> bad_arguments naming a,b; none -> bad_arguments | alice creates, publishes, bob runs | pass a: `who:TOK-ALICE-A`; pass b: `who:TOK-ALICE-B`; pass jira-c: `bad_arguments` "Integration alias 'jira-c' is not permitted ...; permitted aliases: jira-a, jira-b."; none: `bad_arguments` "needs an integration_alias; permitted aliases: jira-a, jira-b." | 2 (A, B only) | PASS |
| S3b | script names an alias the step does not list: creator's account not reached | same run: `jira-bob` (bob's own), `jira-otherproj` (alice's, other project) | both `bad_arguments`, not permitted | none for them | PASS |
| S4 | single alias a, none passed -> runs under a | global, [jira-a], bob runs | none: `who:TOK-ALICE-A`; pass a: `who:TOK-ALICE-A` | 2 (TOK-ALICE-A) | PASS |
| S5 | non-global, listed alias -> running user's integration; none -> clear error | non-global, [jira-a, jira-alice-only], bob runs | pass jira-a: `who:TOK-BOB-A`; pass jira-alice-only (bob has none): `bad_arguments` "Integration alias 'jira-alice-only' for tool 'generic_jira_tool' was not found in this project: Setting with alias jira-alice-only not found"; none: `bad_arguments` (needs alias) | 1 (TOK-BOB-A) | PASS |
| S5b | negative control for S3: same map but the workflow NOT global (publish had failed with 422 on empty categories, which made a useful control) | non-global [jira-a, jira-b], bob runs | a -> TOK-BOB-A, b -> TOK-BOB-B (bob's, not alice's) | 2 (bob) | PASS (global flips owner, non-global does not) |
| S6 | assistant step with alias | workflow assistant step, tools execute_workspace_script + generic_jira_tool(alias jira-a); script run by the LLM | no alias: 200 `who:TOK-ALICE-A`; alias jira-a: `bad_arguments` "integration_alias is not supported for the tools of an assistant; omit it." | 1 | PASS |
| S6b | chat assistant with alias | same tools on a standalone assistant, conversation, `POST /v1/assistants/{id}/model` | identical outcome | 1 | PASS |

### Save-time validation (`POST /v1/workflows`, creator alice; 400 with message naming step, tool, alias)

| Case | Map | HTTP | Message (details) |
|---|---|---|---|
| V0 valid (control) | jira + [jira-a] | 200 | created, warnings [] |
| V1 unknown tool | no_such_tool | 400 | Step 'run_script': Tool 'no_such_tool' does not exist |
| V2 script tool itself | execute_workspace_script | 400 | Tool 'execute_workspace_script' cannot be called from a script |
| V2b not script-callable | list_workspace_files | 400 | Tool 'list_workspace_files' cannot be called from a script |
| V3 alias missing | jira + jira-nope | 400 | Integration alias 'jira-nope' for tool 'generic_jira_tool' does not exist for the workflow creator |
| V3b alias only bob has | jira + jira-bob | 400 | same wording (alice has none) |
| V4 alias outside project | jira + jira-otherproj | 400 | same wording ("does not exist for the workflow creator"; does not say "outside the project") |
| V5 wrong credential type | jira + conf-a (Confluence) | 400 | Integration alias 'conf-a' does not fit the credential type 'Jira' of tool 'generic_jira_tool' |
| V6 no-credential tool + alias | wikipedia + jira-a | 400 | Tool 'wikipedia' takes no credentials, so integration alias 'jira-a' is not allowed |
| V6b same tool, no alias (control) | wikipedia + [] | 200 | created |
| V7 duplicate tool names | jira twice | 400 | Duplicate tool names in script_tool_access: generic_jira_tool |
| V8 map on non-script tool step | list_workspace_files step with map | 400 | script_tool_access is only allowed on 'execute_workspace_script' steps |

### Warnings (`kind: script_step_owner_integration`)

| Call | Workflow state | warnings |
|---|---|---|
| `POST .../marketplace/publish/validate` | not yet global, map jira + [jira-a] | PRESENT: step run_script, tool generic_jira_tool, aliases [jira-a], message "runs under the workflow author's integration (jira-a) for everyone who runs this workflow." |
| same, map jira + [] (control) | not global | [] |
| same, no map (control) | not global | [] |
| `POST /v1/workflows` and `/validate` on a NON-global workflow with aliases (control) | not global | [] |
| `POST .../marketplace/publish` | publishes (200) | response has no `warnings` field |
| `POST .../validate` (also with `"is_global": true` in body) on an ALREADY GLOBAL workflow with aliases | global | [] , expected a warning |
| `PUT /v1/workflows/{id}` on an ALREADY GLOBAL workflow with aliases | global (still global afterwards) | [] , expected a warning |

## Finding caused by the change (needs a decision)

The warning never fires on save or validate of an already-global workflow. `validate_workflow` and `update_workflow` pass `proposed.is_global` / `updated_config.is_global`, where the config is built from `UpdateWorkflowRequest`, which has no `is_global` field, so it is always False (`routers/workflow.py` ~lines 698-701 and 768; the unit tests mock this). Only the publish/validate preview warns. Criterion "published or saved as global" is therefore met for publish preview only. Suggested fix: use the stored workflow's `is_global` (`workflow.is_global`) for save/validate, or OR it with the request value.

Smaller observations: V4 message does not distinguish "outside the project" from "does not exist"; the state-output endpoint returns 500 when given a state NAME instead of the state row id (not related to the change).

## Not covered live

- Editor UI, the test run and generator offering only the map's tools (other repo / UI); covered by unit tests only.
- Model-written script naming an unlisted alias: covered with scripts in a run-start file only (S3b).

## Backend log

No backend exception came from the policy code. Relevant lines: `WARNING Status: 400, Error: Workflow Configuration error, Details: ... Script step tool access is invalid: Step 'run_script': Integration alias ...` (the save-time refusals). Refused script tool calls are not logged server-side by design (error returned to the script); proof of "tool not run" is the empty fake-Jira log.

## Harness (see report for counts)

- Invocation: `uvx codemie-test-harness run sanity-api -n 2 --reruns 2` (PyPI package, API domain http://localhost:8080, admin account from .env) -> `197 passed, 4 skipped, 5 rerun in 1257.27s (0:20:57)`, 0 failed. Reruns that then passed: test_create_assistant_and_prompt_with_file[test.csv], test_passing_history_to_chat, test_delete_history_by_index, test_workflow_include_in_llm_history_true_default, test_workflow_with_user_input_and_file_attachment[test.csv] (LLM variance). Skips: gif datasource indexing, three search_assistant filter tests.
- Script tests: worktree of `origin/EPMCDME-15559_script-excluded-tools-tests` run with `uvx --from <worktree>/test-harness codemie-test-harness run --marks "workspace" -n 1 --reruns 0` (18 passed, 2 failed) and `--marks "workspace and not_for_parallel_run" -n 0 --reruns 0` (4 passed, 6 failed, 39 errors, 9 xfailed). JIRA_URL/JIRA_TOKEN env pointed at the fake Jira.
  - Caused by the change (intended: AC "no map -> no tool"): every harness test whose bare script step calls a tool without a map (test_script_excluded_tools, test_workflow_assistant_step_excluded_tools fixtures time out because the control Jira call raises, the step fails and the workflow keeps retrying; test_workflow_script_tool_calls matrix; test_shared_workflow_running_user). Control: the same probe scripts with `script_tool_access: [{generic_jira_tool, []}]` pass (excluded-tools probe returns tool_unavailable/tool_blocked per tool and control 200; matrix returns me/info 200, unknown_tool tool_unavailable, script_tool tool_blocked, bad_arguments naming `methodd`). These tests need a map added to their steps.
  - Environment: test_workflow_execute_workspace_script_generates_simple_deck_presentation (`ModuleNotFoundError: simple_deck`, module absent in the local code-executor image); test_run_with_file_of_another_user_is_rejected (no second user configured for `regular_client`, upload and run use the same account); test_scripts_run_but_cannot_call_tools_when_bridge_is_switched_off (needs `PUT /v1/config/declarations/features/workspaceScriptBridge`, 400 locally).

## Re-verification after fix 41ad5b006

Same stack as above (compose + override, `src/` bind-mounted, existing DB volume with users alice/bob and test integrations, fake Jira on :18099). The codemie container was recreated and serves the new code (`routers/workflow.py` in the container uses the stored `workflow.is_global` for validate/update; `ScriptToolAccess` in `/openapi.json`). Test tokens only.

| # | Check | Observed | Result |
|---|---|---|---|
| a | Already-global workflow, script step lists [jira-a, jira-b]: `POST /validate` and `PUT /v1/workflows/{id}` | both 200 with `warnings` = one entry `kind: script_step_owner_integration`, step run_script, tool generic_jira_tool, aliases [jira-a, jira-b] (previously `[]`) | PASS |
| b | Same workflow after `marketplace/unpublish` (is_global false): `/validate`, `PUT`; and `publish/validate` preview | validate `[]`, PUT `[]` (no warning); publish/validate preview still warns | PASS |
| c | Global workflow, script step lists generic_jira_tool with no aliases; and global workflow with no map: `/validate`, `PUT` | all four `[]` | PASS |
| d | Regression: publish of the non-global workflow with aliases | preview warns (see b); publish 200, workflow becomes global; publish response carries no `warnings` field (unchanged) | PASS |
| e | Happy path: global [jira-a, jira-b], bob runs, script passes `jira-a` | execution Succeeded, fake Jira saw 1 hit with TOK-ALICE-A (runs under creator) | PASS |

Cleanup: codemie, postgres and elasticsearch containers stopped, fake Jira process killed. k3s and mermaid-server left as found.

## Additional runs: third user, admin, project-level settings

Same stack and method as above (compose + override, fake Jira on :18099, probe scripts calling `call_tool("generic_jira_tool", ..., integration_alias=...)` in a script step, workflows created by alice in project `scn15752`, `shared: true`, global state via `marketplace/publish`). Test tokens only. Map notation: `[a,b]` = `[{generic_jira_tool, [jira-a, jira-b]}]`, `[]` = `[{generic_jira_tool, []}]`.

Setup done for this section:

- Auth note: the earlier bearer tokens and the saved alice and bob passwords no longer worked (JWT signature failure after the container recreate; password login 401). Alice's and bob's passwords were therefore reset through `PUT /v1/admin/users/{id}/password` and new tokens obtained. If you log in as alice or bob in the UI, use the reset passwords (kept in the scratchpad, `pw_alice.txt`, `pw_bob.txt`).
- carol: created like the others through `POST /v1/admin/users` (superadmin), then `POST /v1/admin/users/{id}/projects` `{project_name: scn15752}`. She has NO integrations of her own. Login via `/v1/local-auth/login`.
- admin: superadmin from .env. Already a project admin member of `scn15752`. Created admin integration `jira-admin` (TOK-ADMIN-X) and later `jira-a` (TOK-ADMIN-A).
- Project-level setting: `POST /v1/settings/project` by admin (project admin) `{project_name: scn15752, alias: jira-project, credential_type: Jira}` with token TOK-PROJECT. Supported by the API, 200.

### A. Third user carol (no integrations)

| # | Setup | Result observed | Fake Jira hits | Result |
|---|---|---|---|---|
| A1 | global, `[a,b]`, carol passes `jira-a`; also passes none | `who:TOK-ALICE-A` (creator's account although carol has no integration). No alias: `bad_arguments` "needs an integration_alias; permitted aliases: jira-a, jira-b." | 1 (TOK-ALICE-A) | PASS |
| A2 | global, `[]` (no aliases), carol has no integration, no project-level setting existed yet | automatic call: `tool_unavailable` "Tool 'generic_jira_tool' is not available to this script run; use a tool from your own tool list." (the tool is not built for her because no credentials are found). Alias passed: `bad_arguments` "takes no integration_alias in this step; omit it." | none | OBSERVED, not predicted by spec. Behavior is safe (no fallback to alice's account) but the error text is generic and does not say that carol lacks an integration |
| A3 | NON-global `[a,b]` (alice's workflow shared in project), carol passes `jira-a` | `bad_arguments` "Integration alias 'jira-a' for tool 'generic_jira_tool' was not found in this project: Setting with alias jira-a not found". No alias: needs alias | none | PASS |
| A4 | carol REVOKED from `scn15752` (`DELETE /v1/admin/users/{id}/projects/scn15752`), global `[a,b]`, passes `jira-a` | run allowed, `who:TOK-ALICE-A` (a global workflow is runnable by any user, and the creator's account is used for a listed alias) | 1 (TOK-ALICE-A) | OBSERVED, consistent with spec as written (global = everyone). Flag for the user: a user outside the project can drive the creator's account, so the author warning is the only guard |

Carol was re-granted project access after A4.

### B. Admin / superadmin runner

| # | Setup | Result observed | Fake Jira hits | Result |
|---|---|---|---|---|
| B1 | global `[a,b]` created by alice, admin passes `jira-a`, `jira-b`, none | a: `who:TOK-ALICE-A`, b: `who:TOK-ALICE-B`, none: `bad_arguments` needs alias | 2 (ALICE-A, ALICE-B) | PASS |
| B2 | same, admin passes `jira-admin` (admin owns it, not in list) | `bad_arguments` "Integration alias 'jira-admin' is not permitted for tool 'generic_jira_tool'; permitted aliases: jira-a, jira-b." | none | PASS |
| B3a | NON-global `[a,b]`, admin owns no `jira-a` | `bad_arguments` "...was not found in this project: Setting with alias jira-a not found" | none | PASS |
| B3b | same after creating admin `jira-a` (TOK-ADMIN-A); also passes `jira-b` | a: `who:TOK-ADMIN-A` (admin's, not alice's). b (admin has none): `bad_arguments` not found | 1 (TOK-ADMIN-A) | PASS |
| B4 | global `[]`, admin (own integration `jira-admin` only), automatic | `who:TOK-ADMIN-X` (admin's own lookup, not alice's) | 1 (TOK-ADMIN-X) | PASS |

Superadmin rights give no extra reach: the policy treats admin like any runner.

### C. PROJECT-level settings reach

Setup: project-level Jira setting `jira-project` (TOK-PROJECT) in `scn15752`. Global workflow, `[]` (listed tool, no aliases).

| # | Runner | Observed | Fake Jira hits | Result |
|---|---|---|---|---|
| C1 | bob (has user-level jira-a, jira-b, jira-bob) | automatic: `who:TOK-BOB-A` (his own user-level integration wins over the project one). Alias passed: `bad_arguments`. Unlisted `generic_confluence_tool`: `tool_unavailable` | 1 (TOK-BOB-A) | PASS |
| C2 | carol (no user-level integration) | automatic: `who:TOK-PROJECT` (falls back to the PROJECT-level setting). Alias passed: `bad_arguments`. Unlisted confluence tool: `tool_unavailable` | 1 (TOK-PROJECT) | ACCEPTED-RISK PATH CONFIRMED: a listed tool with no aliases reaches project-level settings when the runner has no own integration. Unlisted tool stays `tool_unavailable` |
| C3 | extra: global `[jira-project]` (alice has no user-level `jira-project`, save accepted), carol passes `jira-project` and passes none | both `who:TOK-PROJECT` | 2 (TOK-PROJECT) | OBSERVED: a project-level alias can be listed and is used |

### Summary

No contradiction with the spec found. Points for the user:

1. A2: with no own integration and no project setting, the result is `tool_unavailable` with the generic text "use a tool from your own tool list", not a message about missing integration (safe, slightly misleading).
2. A4: a user who is not a project member can run a global workflow and use the creator's listed account (by design of global).
3. C2: project-level setting is reachable by any runner with no own integration whenever the tool is listed with no aliases (accepted risk, confirmed).
4. Passwords of alice and bob were reset (see above).

Evidence files in the scratchpad: ev_extra_A.json, ev_extra_A4.json, ev_extra_B.json, ev_extra_C.json. Stack left running; test users carol, integrations `jira-admin`, admin `jira-a` and project setting `jira-project` left in place.

## Re-verification on the single-alias design (2026-10-11, head after the `tool_access` rename)

The sections above describe the previous design (several aliases per tool, alias chosen by the script) and are kept as history. The field is now `tool_access`, a list of `{name, integration_alias?}` (the `WorkflowAssistantTool` shape); the script passes no alias (the SDK has no such parameter). Same method as before: compose with override, `src/` bind-mounted, a fresh database `db15752` in the existing postgres container (the default database belongs to another branch and was not touched), k3s sandbox, fake Jira that logs the token of every call. Users alice (creator), bob (runner), carol (no integrations), admin; project `scn15752`; all tokens are test labels.

### Calls from a script step (probe script, each row is a real workflow run)

| # | Setup | Observed | Fake Jira hits | Result |
|---|---|---|---|---|
| S1a / S1b | no `tool_access` / `tool_access: []`, alice | Jira and Confluence: `tool_unavailable` | none | PASS |
| S2 | non-global `[{jira}]`, alice | Jira: `who:TOK-ALICE-A`; Confluence (unlisted): `tool_unavailable`; `call_tool(..., integration_alias=...)`: `TypeError` (no such parameter) | 1 | PASS |
| S2b | global `[{jira}]`, bob runs | `who:TOK-BOB-A` (running user's automatic lookup, not the creator's) | 1 | PASS |
| S3a / S3b | global `[{jira, jira-a}]` and `[{jira, jira-b}]`, bob runs | `TOK-ALICE-A` / `TOK-ALICE-B` (creator's account); the script cannot pass an alias | 1 each | PASS |
| S5a / S5b | non-global pinned `jira-a` / `jira-b`, bob runs | `TOK-BOB-A` / `TOK-BOB-B` (bob's own integrations) | 1 each | PASS |
| S5c | non-global pinned `jira-alice-only`, bob has none | `bad_arguments` "Integration alias 'jira-alice-only' ... was not found in this project" | none | PASS |
| S7 | global `[{jira, jira-a}, {confluence}]`, bob runs | Jira `TOK-ALICE-A`; Confluence (automatic, bob has no Confluence integration): `tool_unavailable`; unlisted `wikipedia`: `tool_unavailable` | 1 | PASS |
| S7b | non-global, same list, alice runs | Jira pinned `TOK-ALICE-A`, Confluence automatic `TOK-ALICE-CONF` (two tools in one step, two kinds of entry) | 2 | PASS |

### Save-time validation (`POST /v1/workflows?error_format=json`, creator alice)

Every case is refused with 400 and the structured error (`message: "Invalid script tool access"`, `state_id`, `path`, `config_line`), except the valid ones (200, no warnings).

| Case | Path | Details |
|---|---|---|
| unknown tool | `tool_access.0.name` | Tool 'no_such_tool' does not exist |
| the script tool itself, `list_workspace_files`, `tavily_search_results_json` (unknown to the registry) | `tool_access.0.name` | cannot be called from a script |
| alias missing / only bob's / in another project | `tool_access.0.integration_alias` | Integration alias '...' does not exist for the workflow creator |
| wrong credential type | `tool_access.0.integration_alias` | does not fit the credential type 'Jira' |
| no-credential tool with an alias | `tool_access.0.integration_alias` | takes no credentials, so integration alias 'jira-a' is not allowed |
| duplicate tool | `tool_access.1.name` | listed more than once in tool_access |
| list on a non-script tool step | `tool_access` | only allowed on 'execute_workspace_script' steps |
| bad second entry | `tool_access.1.integration_alias` | row path points at the second entry |
| sent YAML with the bad tool on its 10th line | `tool_access.1.name`, `config_line: 10` | the line is the one in the YAML the client sent |
| valid: pinned alias; automatic plus two tools; no-credential tool without alias | | 200, `warnings: []` |

### Warnings and the publish review

| Check | Observed | Result |
|---|---|---|
| non-global, pinned alias: create, validate, PUT | `warnings: []` | PASS |
| publish preview, pinned alias | `warnings: []`, `inline_credentials` has `generic_jira_tool` / `jira-a` / `tool_integration_alias` | PASS |
| publish preview, listed tool without alias; no list | `[]`, no inline credentials | PASS |
| publish, then validate and PUT of the now global workflow | one `script_step_owner_integration` (step, tool, `integration_alias`, message) in each | PASS |
| after unpublish | `[]` in validate and PUT | PASS |
| global with a listed tool without alias; global with no list | `[]` | PASS |

### Third user, admin, project-level setting

| # | Setup | Observed | Result |
|---|---|---|---|
| A1 | global pinned `jira-a`, carol (no integrations) | `TOK-ALICE-A` | PASS |
| A2 | global automatic, carol has no integration and no project setting | `tool_unavailable` (generic text, no fallback to alice) | as before |
| A3 | non-global pinned `jira-a`, carol | `bad_arguments` alias not found in this project | PASS |
| A4 | global pinned, carol removed from the project | still runs under `TOK-ALICE-A` (global is runnable by anyone) | as before |
| B1 / B1b | global pinned `jira-a` / `jira-b`, admin runs | `TOK-ALICE-A` / `TOK-ALICE-B` (no extra reach for admin) | PASS |
| B3a / B3b | non-global pinned `jira-a`, admin without / with own `jira-a` | `bad_arguments` / `TOK-ADMIN-A` | PASS |
| B4 | global automatic, admin | `TOK-ADMIN-X` (admin's own) | PASS |
| C | global automatic, project-level `jira-project` exists; bob / carol | bob `TOK-BOB-A` (own wins); carol `TOK-PROJECT` (project-level reached, accepted risk) | as before |
| C3 | global pinned `jira-project`, carol | `TOK-PROJECT` | as before |

### Not run in this pass

- S6 / S6b (workflow assistant step and chat assistant with a pinned alias, script run by the LLM) and the harness sanity run: the machine had no route to the LLM proxy (connection errors, workflow stuck "In Progress"). To be repeated with the network available.
- Observation: with no network the stuck assistant workflow kept the executor busy and delayed later runs until the backend was restarted.

### Repeated after the move to the assistant assembly (2026-10-11)

The step's tools are now assembled the way an assistant's tools are (the tool of the step names the filter a listed tool must pass; `ToolkitService.get_tools`; the list is cut to the listed names). The runs above were repeated on that code, same environment and method:

- Save-time validation (15 cases) and the warning scenarios: same results. Texts changed: title `Invalid tool access`, "Tool '<name>' is not allowed in tool_access of '<step tool>'", warning kind `tool_access_owner_integration`; paths and `config_line` unchanged.
- Calls from a script (S1a, S1b, S2, S2b, S3a, S3b, S5a, S5b, S7, S7b): same results (unlisted tool `tool_unavailable`; global pinned alias runs as the creator, automatic and non-global as the running user; the script cannot pass an alias; two tools in one list each use their own integration).
- S5c (non-global, pinned alias the runner does not have): the step now fails at its start with "Setting with alias jira-alice-only not found" (no hit in the fake Jira) instead of `bad_arguments` inside the script. Expected: the integration is looked up when the step is assembled, as for a workflow assistant node.
- Third user, admin, project-level setting (A2, A4, B1, C): same results; A2 now reached the project-level setting because it had been created earlier in this database.
- Not repeated: S6 / S6b and the harness sanity run (no network to the LLM).
