# Spec: EPMCDME-16496 — acceptance criteria from the Jira ticket and its three comments

Source: https://jiraeu.epam.com/browse/EPMCDME-16496 (Bug, In Progress). Carried over verbatim from the
prior review round in this same conversation (fetched read-only via the codemie-jira-assistant adapter).

Context: the reporter verified two earlier iterations on BE MR !4659 (with `_IS_LISTED` and
`session_dims.is_subagent`). Comment 3 shows the regression is already on backend main
(`get_session_cost_facts(include_costless=...)`, commit a80c1922, EPMCDME-16441). This branch is a
re-implementation from main and does not contain !4659. The criteria below describe behaviour.

## Acceptance criteria

- AC1 (description): a session with hook events but no token usage (for example Copilot Chat Local, or
  non-Local Chat with no session.shutdown) is listed in Sessions and its Session Detail opens (no 404 "Session
  not found"). It is counted in Overview total_sessions and shows its prompts, turns and tool calls. Tokens and
  cost are empty or "n/a".
- AC2 (comments 1 and 2): a session with no prompt, no API call and no tool call is not listed or counted, or at
  least is counted in dead_sessions rather than as a regular session. Examples: only agent.session.start and
  agent.session.env from VS Code restarts, Copilot desktop helper sessions, and `copilot` then /exit (backend
  receives only agent.session.end).
- AC3 (comment 1; comment 2 marks it fixed on !4659): Copilot subagent fragments are not shown as separate
  sessions. A fragment's child session id carries the subagent's prompt and tool hooks, and that id is the
  agent_id of the parent's agent.subagent.start. Fragments belong to the parent. Comment 1 allows either
  EPMCDME-16572 or the backend to fix it ("the backend should not list a session that is only a subagent's
  agent id"). Comment 2 verified "no subagent id among them (session_dims.is_subagent, or named as agent_id by a
  parent's agent.subagent.start)" as fixed.
- AC4 (comment 2): a session is listed and counted for a period only when it has activity (a prompt, an API call
  or a tool call) or cost rows in that period. Otherwise it is not listed, or at least it is counted as a dead
  session. Reporter's repro: `claude --resume` of a 2026-10-30 session, then /exit with nothing typed, viewed as
  **Last hour**; the only events in the period are session.start/env/end/git.snapshot. Also: Last 7 days, a
  session with activity on 2026-10-23 and only session.end/git.snapshot on 10-29 and 11-01. Applies to Claude
  Code too.
- AC5 (comment 3): on main, a background Copilot `sessionEnd` that produces a session consisting of one
  agent.session.end (no session start, no activity, no session-state folder) does not create a listed session
  row. The Sessions list and Overview total_sessions agree (comment 3 saw them differ by 18: 309 vs 291). Fixing
  it in the backend listing or in the CLI hook are both acceptable.
- AC6 (comment 1): sessions with usage are unchanged. Tokens, API calls and turns of Copilot CLI and Claude Code
  sessions with activity stay the same.
