# EPMCDME-16939 Foreground Agent attribution - Implementation Plan

Spec: `spec.md` (approved; its decisions are not reopened). Two repos, branch `EPMCDME-16939_foreground-agent-attribution` in both:
- **API** = repo `codemie`, source in `src/codemie/repository/cli_analytics/postgres/` (`pg/`), tests in `tests/codemie/repository/cli_analytics/postgres/` (`pgtests/`).
- **CLI** = repo `codemie-code`. UI: no change.

Rules for every task:
- **Never commit, stage or push.** Leave edits unstaged; do not touch `config/customer/customer-config.yaml` or `.codemie/codemie-cli.config.json`.
- API has no local Python. Run tests only in Docker: scoped pytest through the repo's Docker setup, `make test-cli-analytics-live` for live-Postgres tests, `make ruff` (see `.ai-run/guides/quality-gates.md`, `testing/local-verification.md`). CLI: `npx vitest run --project unit <file>`, `npm run lint -- --max-warnings=0`, `npx tsc --noEmit`.
- New live tests go in `pgtests/test_live_foreground_agent_attribution.py` (name must contain `live`), reusing the seed helpers of `test_reader_live_dispatches.py`.
- Order: Tasks 1-9 API, 10-11 CLI, 12 API (needs the Task 10 fixture), 13-14 docs.
- Decision AR-12 (user): a Claude Code Agent call that loses its agent id to another call, or has no end/error and no id, takes NO parent requests (empty row).
- Negative constraints honored: no UI/row-shape change (T4-T6 touch SQL only); no backfill, migration or edit to `cli-analytics-storage.md` (T14 edits the events guide only); duration = stop `duration_ms`, else stop ts minus start (T12, C1 reopened: the stop row ts lags the subagent's end by seconds); Skill rows and `Task`/empty-`tool_use_id`/other-client rows keep today's window (T6); `Task` is never stamped (T11); totals and pricing untouched.

## Task 1 - API: baseline measurements and stored-data checks
Test-first: no - measurement only, no code.
- Against the local docker `postgres` (schema `codemie_analytics`) pick the session with most dispatches x starts and the one with most Agent calls. Run `EXPLAIN (ANALYZE, BUFFERS)` for the Timeline (`reader.py:1470`), `agent_count` (`reader.py:1298-1325`), the `dispatch_fork` CTE (`reader.py:1527`), the fork rollup and `_MARK_RANGE` (`rollups.py:2151`). Write the timings (no session ids) in the final handoff notes.
- **Bar (AR-13):** after Task 8 each statement is at most 2x its baseline and under half its configured timeout; the batch marking statement finishes under 1 s on a replay of the Agent-heaviest session as one batch. **On a miss:** Timeline/`agent_count` use a session-scoped CTE that resolves call-to-id once; the marking falls back to a superset statement queuing every day with an `agent.subagent.start` row of the session (it must never fail a batch for good).
- Answer from stored data and record: count of non-`Agent` dispatch spans and spans with empty `tool_use_id`; which `tool_use_id` a SendMessage continuation's stop carries; whether a resume returns the same `agentId`; whether a subagent's api_requests share the parent's `span_id`; whether a long-prompt Agent shares a prompt with a slash command.
- Verify: numbers recorded; if the span-sharing or resume answer contradicts the spec, stop and report.

## Task 2 - API: agent-call constant and shared naming fragment
Test-first: yes - live test: an Agent call whose cut end has no id resolves from its stop's `agent_id` (same session, same `tool_use_id`); `fork_agent_id` beats the stop, the stop beats a conflicting text id; two stops with different ids make the call ambiguous (no id, text blocked); a `tool_use_id` reused in another session, an empty one, or a non-Claude event never matches; when two calls resolve to one id the highest source wins, ties go to the earliest call.
- `pg/fork_attribution.py`: add one `AGENT_CALL_TOOLS` constant (Agent only; comment: `Task` is deliberately out) and the rule as one inner SQL text with a forward form (call -> id) and a reverse form (started agent -> call, a prefilter on rows carrying the id, then the forward resolution). Every lookup: session equality, non-empty `tool_use_id`, `is_claude_code()`.
- Split `_ended_call` (`fork_attribution.py:197-207`) into the end-row predicate and its EXISTS wrapper; add `call_end_ts` taking `max(ts)` from the same predicate.
- Verify: new live test and `test_fork_attribution.py` green (Docker); `make ruff`.

## Task 3 - API: slash-command pairing and agent counts read the fragment
Test-first: yes - live test D1: a started agent named only by its stop (day with no tool spans, stop in a later batch, or two conflicting stops) is not a slash-command fork candidate, and `agent_count` counts it once.
- `fork_attribution.py:134-155`: `_named_by_end(fork, 'Agent')` becomes the reverse form; Skill branch unchanged. Re-pin the SQL-text test at `test_fork_attribution.py:133`.
- Verify: scoped pytest on `test_fork_attribution.py`, `test_live_dispatch_gate.py` via `make test-cli-analytics-live`.

## Task 4 - API: Timeline resolves the agent id through the fragment
Test-first: yes - live tests B1/B3: a foreground Agent with a cut result gets its own model, tokens, cost and a non-null duration from each of the three sources; parallel and sequential agents are not cross-attributed; a resumed agent and a prompt quoting an earlier `agentId` put usage on one row.
- `reader.py:1489-1517`: `agent_end` keeps only `ts` (launch-ack fallback) and the `tool_response_agent_id` call at `:1492` is removed; `launched.agent_id` comes from the forward fragment keyed on `t.tool_use_id`. Replace the `'Agent'` literals at `:1479,1494,1501` with `AGENT_CALL_TOOLS`.
- `stopped` (`:1568-1575`) also selects the stop's summed `duration_ms` (unread; the one-expression switch for Task 12 at `:1674-1675`).
- Re-pin the SQL-text assertions `test_reader.py:1371-1393,1411-1431`.
- Verify: scoped pytest `test_reader.py`; `make test-cli-analytics-live`.

## Task 5 - API: bound the parent fallback at the call's end
Test-first: yes - live tests B2/D2: a parent api_request logged after an Agent call returned is excluded from its row; an errored call (`agent.tool.error`) is bounded the same way; two siblings tied on start but with different ends split correctly.
- `reader.py:1625-1659`: `fallback_start` gets `bound_ts` (the call's end via `call_end_ts`; NULL for rows outside the Claude Code Agent scope = open as today). `sibling_window` window end is `least(next_start, bound_ts)` written so a NULL bound on an in-scope call never reads as open; `ties` partitions by `(parent_span_id, start_ts, bound_ts)`; `window_requests` (`:1462`) reads that end.
- Re-pin `test_reader.py:1435-1477` (`:1458` sibling window).
- Verify: as Task 4.

## Task 6 - API: takes_parent scope (never-ended and id-losing calls take none)
Test-first: yes - live tests: a Claude Code Agent call with no end/error and no id takes no parent requests, including a never-ended last sibling and one tied with an ended sibling; a call that lost its id to another call (B3 resumed agent) shows an empty row; `Task`, empty `tool_use_id`, other-client and Skill rows keep today's parent-window estimate (D3); an errored/cancelled call with a stop naming it shows its own data, else follows the fallback (B4); session totals unchanged (B5).
- Add one `takes_parent` value to the shared fragment (false for in-scope never-ended and loser calls, true otherwise); `fallback_start` (`reader.py:1630`) and the final join (`:1692`) read it instead of `la.agent_id IS NULL AND sf.agent_id IS NULL`.
- Re-pin `test_reader_live_dispatches.py:302` (an unresolved call now needs an end to take the parent window); update the docstring at `reader.py:1447-1453`.
- Verify: `make test-cli-analytics-live` (PG 17 and 14, none skipped).

## Task 7 - API: ingest marking by (session, tool_use_id)
Test-first: yes - tests: an Agent end, error or stop in a batch queues the day of every start whose `agent_id` any row of the call carries (field, stop, text), including a stop in a later batch or the reverse; a live case seeds many calls and runs the statement under `SET LOCAL statement_timeout = 1000`.
- `fork_attribution.py`: one marking SQL keyed on unnest(session, tool_use_id) built from the fragment; helper returning the batch's Agent-call keys. `named_subagent_ids`/`_named_agent_id` (`:45-74`) keep the Skill branch only; re-pin `test_fork_attribution.py:44-91` (line 52: Agent end now ignored by the mirror).
- `ingestor.py:351-358,574-576`: run the statement after the insert, inside the transaction. `dirty.py:157` uses `AGENT_CALL_TOOLS`. Update `test_ingestor.py:451-479` and `test_dirty.py`.
- Verify: scoped pytest on the three files; `make test-cli-analytics-live`.

## Task 8 - API: range marker `_MARK_RANGE` uses the fragment
Test-first: yes - live test D1: for a stop-only-named start on the day after a backfilled range, the batch statement (Task 7) and `_MARK_RANGE` queue the same days.
- `rollups.py:2145-2194`: `_NAMED_SUBAGENT_ID` becomes `CASE Skill -> skill_end_agent_id ELSE <fragment reverse prefilter>`; `_MARK_RANGE` joins it. Replace the `'Agent'` literals at `rollups.py:1854,1975,2128,2194` with `AGENT_CALL_TOOLS`. Update `test_rollups.py` text pins.
- Verify: scoped pytest `test_rollups.py`; `make test-cli-analytics-live`.

## Task 9 - API: re-measure against the Task 1 bar
Test-first: no - measurement; apply the Task 1 fallback only if the bar is missed.
- Repeat the Task 1 `EXPLAIN (ANALYZE, BUFFERS)` runs; record before/after and the Timeline's connection hold time (pool of 8, 30 s read timeout).
- Verify: `make ruff`, `make test-cli-analytics-live`.

## Task 10 - CLI: capture sanitized foreground-Agent fixtures
Test-first: no - fixture capture. Repo: CLI (codemie-code).
- Run a headless `claude -p` session with the CodeMie CLI hooks that makes one foreground Agent call with a prompt over 700 chars. Capture the uncut PostToolUse stdin with a throwaway wrapper kept outside the repo; read the stored hook lines from the local OTLP spool of the CodeMie proxy (one hooks file per session, each line has a JSON string field `hookEvent`); read the span and api_request rows from the local analytics Postgres after ingest.
- Write two sanitized files under `src/agents/plugins/claude-code-otlp/event-fields/__tests__/fixtures/`: `foreground-agent-result.json` (the PostToolUse record, A1) and `foreground-agent-run.json` (hook rows, start/stop events, spans, API requests, A1/C1). Replace session ids, emails, paths and machine names with fixed placeholders; keep ids, timestamps, durations and token numbers.
- Record the result shape (object or JSON text, where `agentId` sits). If it is an object, drop the JSON-text tolerance in Task 11.
- Verify: search both files for the real session id, `@`, local user and machine names and absolute paths: no match.

## Task 11 - CLI: stamp fork_agent_id on Agent results
Test-first: yes - Vitest: with `foreground-agent-result.json` (agentId after a long prompt) the `Agent` `agent.tool.end` carries `fork_agent_id` while its `tool_response` is cut to 300 chars without the id (A1); no id is stamped for a failure, denial or malformed result, `Task` is not stamped, Skill is unchanged (A2).
- `event-fields/tool-fields.ts:61-74`: rename `resolveSkillForkFields` and return the id for `Skill` and for the tools in a new `AGENT_RESULT_ID_TOOLS = {Agent}` set beside `AGENT_DISPATCH_TOOLS` (`:138`) with a comment giving the `Task` decision. Agent reads top-level `agentId` of the uncut object. Update the import and call in `claude-code-otlp.plugin.ts:65,541-548`.
- Re-pin `tool-fields.test.ts:146-175` (`:162` now stamps) and add the plugin case beside `claude-code-otlp.plugin.test.ts:700-728`; `fork_agent_id` is already in `UNMASKED_FIELD_KEYS` (`OtlpAgentAdapter.ts:72-76`).
- Verify: `npx vitest run --project unit` on the three test files, `npm run lint -- --max-warnings=0`, `npx tsc --noEmit`.

## Task 12 - API: C1 duration and full-run test on the captured fixture
Test-first: no - characterization of Tasks 4-6 on real data. Repo: API (codemie).
- Copy the sanitized `foreground-agent-run.json` content from Task 10 into `tests/codemie/repository/cli_analytics/support/`. A live test loads it and calls `LocalAnalyticsHandler.get_session_detail` (`cli_analytics_handler.py:1023`); assert the dispatch row's `duration_ms` (through `dispatch_money`, `_duration_for_kind` `:111-120`, `_bar_position` `:123-140`) is within +-1 s of the stop's `duration_ms`, and its tokens, cost and model sums equal the fixture's subagent requests (the parent request excluded).
- C1 was reopened by the real capture (stop ts minus start 11.6 s vs stop `duration_ms` 7.4 s): the Timeline duration is now the stop's `duration_ms` when present, else stop ts minus start. Make that one-expression switch at the Task 4 `duration_ms` column (`subagent_duration_ms` CASE in `reader.py`), keep the fallback, test both branches.
- Verify: `make test-cli-analytics-live`.

## Task 13 - CLI: document the Agent fork_agent_id
Test-first: no - docs. Repo: CLI (codemie-code).
- `docs/ARCHITECTURE-OTLP-PLUGIN.md:198` (and the resolver name at `:95,:238`): `fork_agent_id` is stamped for `Skill` and `Agent`, not `Task`, read from the uncut result.
- Verify: re-read the three lines; no stale `resolveSkillForkFields`.

## Task 14 - API: document the naming rule in the events guide
Test-first: no - docs. Repo: API (codemie).
- `.ai-run/guides/integration/cli-analytics-events.md`, `agent.tool.end` section (~:430-445): the three id sources and their order, the ambiguous and loser rules, the bounded fallback and the no-parent-requests scope. Do not edit `cli-analytics-storage.md`.
- Verify: re-read the section against Tasks 2, 5 and 6.
