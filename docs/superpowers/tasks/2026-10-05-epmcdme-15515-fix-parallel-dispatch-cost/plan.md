# EPMCDME-15515 Fix Parallel-Dispatch Cost and Token Duplication — Implementation Plan

> Recorded after implementation (sdlc-light shape). Every task below is done; the plan documents what was
> built, in which order, and how each step was verified.

## Clarification assumptions

1. **Attribution must be exact, not estimated.** AC1 asks for "the cost and tokens of that subagent only", and AC2
   for different values when the work differs. An even split of the parent total (first attempt, commit `d09b06bb0`)
   and per-subagent time windows (second attempt, `6bf71fc66`) both fail this for parallel siblings, so the final
   design is an ID chain.
2. **The ID chain.** Agent tool call hook event (`agent.tool.end`, `tool_response.agentId`) → the subagent's
   `llm_request` spans (`attrs.agent_id`) → their `parent_span_id`, which groups the subagent's `api_request` log
   events. Those events are summed per agent. Verified on real sessions in the local analytics DB.
3. **Fallback (owner decision, check round).** Only a dispatch with no agentId at all (a skill, or an agent from
   telemetry without it) falls back to its parent span's `api_request` events, counted from the first dispatch under
   that parent on, as before the fix. An agent whose agentId is known but has no `api_request` group gets `NULL`
   cost and tokens (shown as "—"), never the main thread's cost.
4. **No response-shape change for cost.** `cost_usd` and the token fields were already nullable; the handler's
   `_token_dict` maps `NULL`/`0` to `null`.

---

**Goal:** Each agent row in `dispatches[]` reports only its own subagent's cost and tokens, so parallel subagents
show different, non-duplicated values that sum to no more than the session total.

**Architecture:** In `get_session_detail_dispatches`, new CTEs `agent_end` / `launched` read the agentId from each
Agent call's hook event; `agent_groups` / `agent_usage` sum the `api_request` events under each agentId's
`llm_request` group. The old `lr` CTE becomes `parent_usage` (same `first_start` boundary) and is joined only when
the dispatch has no agentId. Final columns use `coalesce(au.<x>, pu.<x>)`.

**Tech Stack:** Python 3.12, asyncpg raw SQL, pytest, live Postgres tests (skipped when the DB is unreachable).

**Spec:** EPMCDME-15515 ticket (actual result and acceptance criteria) and `technical-analysis.md` in this folder.

## Global Constraints

- Missing numbers are `null`, never `0` (cli-analytics-events contract).
- Skill and slash-command rows keep their pre-existing cost semantics (parent span from the first dispatch on).
- `session_id` stays a bound parameter; only int enum constants are interpolated into the SQL.
- Commit messages start with `EPMCDME-15515:`.

## Review Focus

1. Two parallel subagents get their own exact totals, not an even split and not a duplicate. → Task 3.
2. A dispatch with a known agentId but no api_request group gets `NULL`, not the main thread's cost. → Task 5.
3. A dispatch with no agentId keeps the parent-span fallback, counted from the first dispatch on. → Task 5.
4. Agent rows never sum to more than the session total. → Task 6.

---

### Task 1: First attempt — even split by sibling count (superseded)

**Commit:** `d09b06bb0`

- [x] `dispatch_counts` CTE counted dispatches per `parent_span_id`; `lr` divided the parent's cost and tokens by it.
- [x] 4 SQL-shape regression tests in `test_reader.py`.

**Outcome:** removed the duplicate, but two siblings that did different work still showed equal values (AC2 not
met). Superseded by Task 2 and Task 3.

### Task 2: Second attempt — per-subagent time windows (superseded)

**Commit:** `6bf71fc66`

- [x] Attributed `api_request` events to a dispatch by time window; added `test_reader_live_dispatches.py`.

**Outcome:** parallel windows overlap, so requests inside the overlap cannot be attributed by time. Superseded by
Task 3.

### Task 3: ID chain from agentId to the subagent's api_request group

**Commits:** `dfa13ea0e`, `4716946f1`

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py` (`get_session_detail_dispatches`)
- Modify: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`
- Modify: `tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py`

- [x] `agent_end` (`DISTINCT ON (tool_use_id)`, latest `agent.tool.end` of `tool_name = 'Agent'`) and `launched`
      (`substring(tool_response from 'agentId[^a-zA-Z0-9_-]+([a-zA-Z0-9_-]+)')`).
- [x] `agent_groups` (`span_kind = LLM_REQUEST`, `attrs ? 'agent_id'`) and `agent_usage` (sum of `api_request`
      events where `l.span_id = g.parent_span_id`).
- [x] SQL-shape tests `test_agent_id_is_read_from_the_agent_tool_call_hook_event`,
      `test_agent_cost_is_summed_from_the_api_requests_of_its_llm_request_group`.
- [x] Live test `test_two_parallel_agents_get_their_own_exact_totals_not_an_even_split` (0.11 vs 0.17).
- [x] Round-1 code review (`code-review-final.json`): `request-changes`, one open decision CR-001.

### Task 4: Duration timeline (EPMCDME-15512, same query)

**Commit:** `a4bcf3c77` — tracked in `docs/superpowers/tasks/2026-10-06-fix-subagent-duration-timeline/`.
Cost CTEs were left untouched by that change.

### Task 5: Check round — CR-001 decision and CR-002 regression

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py`
- Modify: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`
- Modify: `tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py`

Test-first: yes — both live tests below were run red before the SQL change.

- [x] **CR-001.** Rewrote `test_a_dispatch_without_an_llm_group_has_no_cost_rather_than_a_guess` so it really
      exercises the case: a real Agent hook event (`tool_name = 'Agent'`) under a main-thread span that has its own
      `api_request`. Red: the orphan showed the main thread's 0.5.
- [x] Fix: `LEFT JOIN parent_usage pu ON pu.span_id = t.parent_span_id AND la.agent_id IS NULL`.
- [x] **CR-002.** New live test `test_a_dispatch_with_no_agent_id_falls_back_to_its_parent_span_from_its_start_on`.
      Red: 0.37 (whole parent span) instead of 0.07.
- [x] Fix: restored `first_start` and the `FILTER (WHERE after)` sums in `parent_usage`; docstring updated.
- [x] SQL-shape test renamed to `test_an_agent_dispatch_falls_back_to_its_parent_span_only_when_it_has_no_agent_id`
      and asserts the conditional join and the restored filter.
- [x] Removed the unused `_HOOK_EVENTS_SQL` test constant.

### Task 6: Verification on real data

- [x] Ran the new query over all 8 real sessions in the local analytics DB: 35 agent dispatches, 0 with `NULL`
      cost, so the CR-001 rule does not strip cost from real agents.
- [x] Session `52d226aa` via the API: Explore #1 $0.1317482 (1390 / 2378 / 137616 / 31066), Explore #2
      $0.0631483 (219 / 670 / 82339 / 15817), each equal to its own api_request group; session $0.4136593 equals
      the sum of all 19 api_request events; agents together $0.195 < session.

## Summary of changed surfaces

| File | Change |
|---|---|
| `src/codemie/repository/cli_analytics/postgres/reader.py` | agentId chain CTEs, conditional parent-span fallback, restored `first_start` |
| `tests/codemie/repository/cli_analytics/postgres/test_reader.py` | SQL-shape tests for the chain and the fallback rule |
| `tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py` | live tests: parallel exact totals, orphan `NULL`, no-chain fallback |

## Known limitations

- Dispatches without an agentId that share one parent span each show that parent's total from the first dispatch
  on (pre-existing, accepted by the owner).
- `codemie-ui` `DispatchRow` types are unchanged; `null` cost renders as "—".
