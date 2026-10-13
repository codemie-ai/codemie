# EPMCDME-15566 — Script Runs and Their Tool Calls in Analytics

Story · Ready for dev · Epic EPMCDME-15364. Blocks EPMCDME-15561 (script tool calls as separate trace steps). Blocked by EPMCDME-15558 (skill scripts in workspace; this branch's parent), EPMCDME-15400 (scripts can call platform tools), EPMCDME-15559 (script from workflow step).
Repos: codemie (+ codemie-ui only if views need changes). Size M (17/36), provisional.

## Context
- Tool usage, tokens, errors per tool are emitted as metrics; skill usage recorded separately. A script run counts once as a call of the script-execution tool (`execute_workspace_script`). Tool calls a script makes through the SDK are performed by the backend but not recorded today.
- Calls go through a dedicated path that does not use the agent callbacks. Decided (architecture spec D23, v5): each call produces ONE event, emitted by the dispatcher of the channel; one listener per output turns it into the usage metric (this story), the trace step (story 4 / EPMCDME-15561) and the audit record. This story builds the event, the emission and the listener seam as its FIRST sub-task.
- Leaning: same usage metrics as a direct call, labelled as made by a script (skill name for skill scripts from the workspace seed ledger, never from path), plus a count of calls the bridge refused; shown in the existing tools-usage view, no new dashboard, no cost figures.

## Story
As a platform owner, I want script runs and the tool calls they make to show up in the existing analytics like other tool usage, so that I can see how scripts are used, by whom, and what they cost.

## Acceptance Criteria (provisional)
- Given a script makes tool calls, when the run completes, each call is counted in tool usage analytics under the tool's name, attributed to the same assistant, person and project as a direct call.
- Given a call made by a script fails, it is counted as an error for that tool.
- Given a script run, the run counts once as a call of the script tool and each call it made counts once under its own tool name; a tool that emits its own metric is not counted twice in totals that use the call event metric.
- Given a script shipped in a skill runs, a separate script-run metric for that skill reflects it; existing skill usage numbers unchanged (sub-task 4, after EPMCDME-15558).
- Given a script makes no tool calls, nothing changes compared to today.
- Given a call fails or is refused: a failure goes to tool errors with its error code; a refusal by policy goes to the separate refused-calls counter.
- Given a call finishes after its run ended, it is counted as a use with status abandoned.
- Given a script runs in a workflow, its calls are counted as in the decisions (bare tool step too).

## Out of Scope
- Changing how existing analytics numbers are computed or presented. New dashboards unless existing views cannot show script activity at all.

## Decisions (2026-10-06)
1. Which reports: tool usage in existing `codemie_tools_usage_*` metrics and existing view, with a label, no new dashboard. Skill usage: separate metric after EPMCDME-15558 (sub-task 4); existing skill metrics unchanged. Spending unchanged (a script call spends no LLM tokens). Runs/failures of scripts: already the calls and errors of `execute_workspace_script`.
2. Attribution: attributes of a direct call (assistant, user, project, model, tool type) plus `invoked_by="script"`. invoked_by is an enumeration, call event carries source as {script, test_run}; test_run is for story EPMCDME-15564. A direct call carries no invoked_by. No script path label. Assistant and model come from the run context, so sub-task 1 adds them to the context object. Skill name comes later (sub-task 4).
3. Tokens and cost: NO tokens metric for script calls (for a direct call it measures how much tool output enters model context; a script result does not). No cost figures.
4. Fields/outcomes: `invoked_by`, `error_code`, later `skill_name` (optional until sub-task 4). `tool_failed` and `bad_arguments` counted in errors metric with `error_code`. Refusals by policy (`tool_blocked`, `tool_unavailable`, `no_context`, `deadline_exceeded`) go to a separate counter `codemie_script_tool_calls_refused_total`; attributes: `tool_name` (only if resolves to a known tool else `unknown`), `error_code`, `assistant_name` (may be empty), `user_name`, `project`, `llm_model`; no `invoked_by`. Call abandoned after run ended: counted as use with `status=abandoned`. No figure for calls made at once.
5. No double counting: script run counts once as `execute_workspace_script`; each script call counts once under its own tool name. Tool's own metric (e.g. datasource usage): totals use the call event metric, tool's own metric read as before.
6. [MOVED OUT of this story by the ticket owner on 2026-10-12, to a story about the origin of a script; nothing below is built] Skill usage (criterion 4): separate metric `codemie_skill_script_run`, once per run of a script the seed ledger attributes to a skill (entry script only). Attributes: `skill_id`, `skill_name`, `skill_script` (path inside skill folder), `status` (success/failure), `assistant_name`, `user_name`, `project`, `llm_model`. Emitted at end of run in `AgentWorkspaceService.execute_workspace_script`, not by the call listener. `codemie_skill_tool_invoked` keeps meaning "model loaded the skill".

Workflows (2026-10-06): assistant node goes through the agent, so its script calls count like in chat. A bare workflow tool step calls `tool.execute` directly without callbacks, so today neither direct tool step nor script tool in a step is counted. Proposal: calls a script makes in a tool step are still counted by the call event with `invoked_by="script"`, no assistant, executing user and workflow project from project scope (verify in sub-task 2). Counting the tool step itself is out of scope. Execution id is not a metric label.
Accepted asymmetry: in a bare tool step the `execute_workspace_script` run and a direct tool call are not counted, as today, while calls made by a script through the SDK are.
Example: assistant calls `execute_workspace_script`, script calls Jira via SDK -> two records: `execute_workspace_script` without invoked_by, and Jira with `invoked_by=script` carrying assistant name, user, project, model. `invoked_by=script` only for calls via `codemie_runtime_sdk`; the script tool itself never carries it.

Sub-tasks referenced: 1 = call event + emission + listener seam (+ assistant/model into run context); 2 = metrics/workflow attribution; 4 = skill script-run metric (after EPMCDME-15558).
(Ticket text from the Jira assistant was cut off after "Open Questions: None".)

## Extra request from the user (not in the ticket)
Think about what analytics could show how many tokens were saved by calling tools via a script instead of directly from the model. NOTE: ticket decision 3 says no tokens metric for script calls — any such metric is an addition/design question to surface, not to silently add.
