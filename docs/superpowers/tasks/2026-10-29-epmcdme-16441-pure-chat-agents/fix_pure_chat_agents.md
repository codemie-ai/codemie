# Fix: factory sessions that only dispatch factory subagents are labelled "Pure chat"

Jira EPMCDME-16441, verification finding 4 (Dzmitry Lopukh, 2026-10-29). Branch
EPMCDME-16441-backend-session-end-subagent-cost, MR !4652. Commit prefix `EPMCDME-16441:` (the architect
commits; you do not touch git). Follow `.ai-run/guides/testing/testing-patterns.md`; run only the narrow suites under
Gates. English only.

## Problem

A Claude Code session that runs `Agent` with `subagent_type` `sdlc-factory:complexity-assessor` and invokes no factory
skill gets `delivery_framework = "Pure chat"`, in `claude -p` and interactive mode. With a factory skill it is
"CodeMie AI Factory". Factory runs that only dispatch factory agents are not counted as factory sessions.

Cause: `classify_delivery_framework(skill_names)` (`src/codemie/service/analytics/delivery_framework.py`) matches the
customer-config regexes (`^sdlc-factory:` ...) against skill names only. `SKILL_NAMES_SQL`
(`src/codemie/repository/cli_analytics/postgres/session_derived.py:37-45`) reads `session_skills` and the keys of
`session_dims.skills`. A subagent start without `skill_name` lands only in `invocations_hourly` kind 3 and therefore in
`session_dims.agents` (`rollups.py:1083-1085`, `1215`), never in skills. `_framework()` (`session_derived.py:78-91`) also
returns None for an empty list, so the stored column is not written for an agents-only session.

## Decision (owner to confirm)

Feed the session's subagent types to the existing classifier as additional names, so the existing regexes match them.
No config change, no new table, no new signal type. Rejected: a separate agent-rule list in customer config (second
config surface for the same labels), and reading `subagent_invocations` (not rollup-retained the same way, includes
forks, and skips empty `agent_id`).

## Change

1. `session_derived.py`: add `_AGENTS_OBJECT = "CASE WHEN jsonb_typeof(sd.agents) = 'object' THEN sd.agents END"`
   (same guard as `_SKILLS_OBJECT`, so no row can raise). In `SKILL_NAMES_SQL`, union the keys of `sd.agents` into BOTH
   branches (summary branch and the session_skills/hooks branch), dropping empty names, keeping the
   `ORDER BY ... COLLATE "C"` determinism and NULL when there are none. Rename the constant to
   `CLASSIFIER_NAMES_SQL` and update its importers (`reader.py:46`, `session_derived.py:_READ`). Keep
   `get_skill_names_by_session` (port `ports.py:186`, handler calls at `cli_analytics_handler.py:816,875`) and its
   `skill_names` output key unchanged, so the handler and the framework filter need no edit; update its docstring.
2. `_framework()` guard (`if not skill_names`) needs no change once the array includes agents; keep it.
3. `delivery_framework.py`: the docstring of `classify_delivery_framework` says the input is skill names; say it is
   skill names and subagent types. Do not change the function signature.
4. Not in scope: the ES usage_attribution path (`usage_attribution/handler.py:146`, `constants.py:79`), `--now`
   backfill (leaves NULL by design), the Python-paged framework filter.

Known effect to document, not to fix: a namespaced agent such as `superpowers:code-reviewer`, `bmad-*`, `opsx:*` or
`speckit.*` now also labels the session by its rule. Priority stays positional, so `sdlc-factory:` wins. Built-in types
(`Explore`, `general-purpose`, `Plan`) match no rule. Existing stored labels update only on the next recompute.

## Tests

- `tests/codemie/repository/cli_analytics/postgres/test_session_derived.py`: update
  `test_without_a_summary_the_classifier_reads_the_skills_of_otel_and_of_hooks` (~144, pins the SQL text) and add a
  case that the classifier input includes the keys of `sd.agents` in both branches and ignores a non-object `agents`.
- `tests/codemie/repository/cli_analytics/postgres/test_reader.py`:
  `test_skill_names_read_session_dims_with_the_sql_of_the_classifier` (~117) follows the renamed constant.
- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`: a list and a detail case where the reader
  returns only `sdlc-factory:complexity-assessor` as a name and the label is "CodeMie AI Factory"
  (mirror ~276 and ~1363).
- `tests/codemie/service/analytics/test_delivery_framework.py`: an agent type name classifies by the same rule as a
  skill name; a built-in type stays Pure chat.
- Live test (skips without `TEST_LOCAL_PG_URL`; say so in the CHECKPOINT): based on
  `test_live_dispatch_gate.py:524` (`agent.subagent.start`, `agent_type=sdlc-factory:complexity-assessor`,
  `skill_name` NULL, plus one api_request log). Run the refresher WITH a classifier (`RollupFamily`/`RollupRefresher`
  `classify=...`, see `_refresh_rollups` at ~229 which passes none) and assert
  `session_dims.delivery_framework == 'CodeMie AI Factory'` via `_session_dims(engine, sid, 'delivery_framework')`;
  also assert the list and detail labels agree.

## Docs

`.ai-run/guides/integration/cli-analytics-storage.md` ~298 (delivery_framework derivation) and ~154-159: the
classifier reads skill names and subagent types (`session_dims.agents`). Mention the rule-collision effect in one line.

## Gates

Narrow suites only: the four unit files above, then `make ruff` per `.ai-run/guides/quality-gates.md`. Report exactly
what ran. Write the report to
`docs/superpowers/tasks/2026-10-29-epmcdme-16441-pure-chat-agents/fix_pure_chat_agents.CHECKPOINT.md`.
