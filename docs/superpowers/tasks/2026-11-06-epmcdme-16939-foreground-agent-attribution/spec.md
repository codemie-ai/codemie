# EPMCDME-16939: Foreground Agent attribution in the CLI Analytics Timeline

Repos: API (codemie, main change), CLI (codemie-code, small change), UI (codemie-ui, no change). One fix, one plan.

## Problem

A foreground Agent call with a long prompt returns a result whose `agentId` sits after the echoed prompt. The CLI cuts the result to 300 characters, so the id is lost (`OtlpAgentAdapter.ts:60-69`). Only `Skill` has a dedicated field that survives the cut (`resolveSkillForkFields`, `tool-fields.ts:68-74`). The stored `agent.tool.end` then has no id, but `agent.subagent.stop` carries the same `tool_use_id`, the `agent_id` and `duration_ms`. The Timeline reads the id from the end only, so the row falls back to the parent's API requests, which start at the dispatch and are not bounded by the call's end (`reader.py:1630,1644-1692`). The row shows the parent's later request (wrong model, tokens, cost), and its duration is null because the stop is joined by agent id (`reader.py:1689`). Session totals are correct. A call that ended in `agent.tool.error` gets the same wrong row, because the `launched` row is built from `agent.tool.end` only (`reader.py:1489-1496`).

The result shape is taken from the ticket and a transcript. The hook-level record is unverified until the plan's first task captures it (see plan notes).

## Approach

### The naming rule

One rule answers "which agent did this call start" and "which call named this started agent". It is defined on hook rows, so it holds on days with no tool spans and at ingest. The Timeline joins a TOOL span to it by `tool_use_id`.

An **Agent call** is the Claude Code `Agent` hook rows with one `(session, tool_use_id)`, non-empty. Its id resolves from the first source that answers:
1. `fork_agent_id` on its `agent.tool.end` (new, from the CLI).
2. The `agent_id` of the Claude Code `agent.subagent.stop` rows with the same session and `tool_use_id`. Several distinct ids make the call **ambiguous**: it names no agent and blocks source 3.
3. The complete `agentId` in the `tool_response` text, only when 1 and 2 give nothing.

A started agent is named by the call whose resolved id equals its `agent_id`. If several calls resolve to one id (a resumed agent, a quoted id), the highest-ranked source wins, ties go to the earliest call, and the other calls are treated as unresolved. One agent's usage never lands on two rows, and no agent is counted twice.

The call's **end** is its latest end or error row (`_ended_call`, `fork_attribution.py:197-207`).

The rule is one shared SQL fragment in `fork_attribution.py` with a forward form (call to id) and a reverse form (agent to call), built from one inner text. Every reader joins it: the Timeline reader, agent counts, rollups, and `_named_by_end` for slash-command pairing. Once the id resolves, the existing id-keyed joins give the subagent its own model, tokens, cost and duration.

### Ingest marking

One SQL statement, keyed on `(session, tool_use_id)` and run after the insert, queues the day of every start whose `agent_id` any row of the call carries. The Python mirror keeps only the Skill branch. The Agent branch is retired, and the SQL covers all three sources.

### Fallback

A parent-usage fallback applies only to a call with a recorded end or error, with the window `[start, least(next sibling start, call end))`. A **Claude Code Agent call** with no recorded end or error and no resolved id takes no parent requests, so its row shows no subagent data. Rows outside that scope (the older `Task` tool, an empty `tool_use_id`, other clients) and Skill rows keep today's estimate from the parent window. The scope is one `takes_parent` value in the shared fragment, read by the final join.

### CLI

The resolver (renamed) also returns `fork_agent_id` for `Agent` calls. It reads `agentId` as a top-level key of the uncut `tool_response` object (background and foreground results alike); a result delivered as JSON text is not read. `Task` is not stamped.

### Docs

Update the `agent.tool.end` section of `.ai-run/guides/integration/cli-analytics-events.md` and `docs/ARCHITECTURE-OTLP-PLUGIN.md:198` in the CLI repo.

## Acceptance criteria

**A. CLI** (fixture: a sanitized real foreground Agent result record)
- A1: A result whose `agentId` sits after a long prompt produces `fork_agent_id` on `agent.tool.end`.
- A2: No id is stamped when the result has none (failure, denial, malformed). Skill behaviour is unchanged.

**B. Attribution** (live-Postgres tests)
- B1: A foreground Agent with a cut result gets its own model, tokens and cost from each of the three sources. The stop wins over a conflicting text id.
- B2: A parent request logged after the Agent returned is excluded.
- B3: Parallel and sequential agents are not cross-attributed or double counted. A `tool_use_id` reused in another session or on a non-Claude event never matches. A resumed agent, and a prompt quoting an earlier `agentId`, put the usage on one row only.
- B4: An errored, cancelled or interrupted call shows its own data when its stop names it, otherwise it follows the fallback rule.
- B5: Session totals are unchanged.

**C. Duration** (fixture: a separate full run with hook rows, start and stop events, and API requests)
- C1: The duration the Session Detail row carries (through `dispatch_money`, `_duration_for_kind`, `_bar_position`) is the stop's `duration_ms` when present, otherwise stop ts minus start (older CLI versions, continued agents). On the captured full run it is within +-1 s of the stop's `duration_ms`. Reopened after the real capture: Claude Code waits for the SubagentStop hooks before the Agent call returns, so the stop row timestamp lags the subagent's end by seconds. The ticket's expected figure (SubagentStop `duration_ms`) is what the Timeline now shows.

**D. Consistency and fallback**
- D1: When the only name for a started agent is its stop (a day with no tool spans, a stop in a later batch or the reverse, two conflicting stops), the Timeline, agent and rollup counts, slash-command pairing and ingest marking agree. This applies to sessions ingested after the deploy.
- D2: A call with a recorded end or error never takes parent requests after it. A Claude Code Agent call with no end or error and no resolved id takes none, including a never-ended last sibling and one tied with an ended sibling.
- D3: Rows outside the Claude Code Agent scope (`Task`, empty `tool_use_id`, other clients) and Skill rows keep today's parent-window estimate.
- D4: The tests that pin the old behaviour are re-pinned: `test_fork_attribution.py:52`, the SQL-text assertions in `test_reader.py` (including `:1371-1393,1435,1458`), `test_reader_live_dispatches.py:302` and `tool-fields.test.ts:162`.

## Non-goals

- No UI change and no change to the dispatch row shape.
- No backfill, migration or new code for past days, and no edit to `cli-analytics-storage.md`.
- Duration = stop `duration_ms`, else stop ts minus start (see C1); no other duration source.
- No change to Skill attribution, and no `Task` stamping or reading.
- No end-to-end harness. The ticket's Integration scenarios go to a follow-up ticket, so this spec alone does not close the ticket. Its "Model: Haiku" and 21,236 ms figures are checked through priced cost and C1.
- No change to session totals or pricing.

## Decisions

- Resolve the id once per call from its `tool_use_id` (on hook rows; the Timeline joins its span by that key) over a lookup at each reader's end row. Question: where the id is resolved. — user, answers AR-1
- The window end is the latest `agent.tool.end` or `agent.tool.error` over `agent.tool.end` only. Question: what ends a call. — user, answers AR-1
- Every reader joins one shared fragment over per-reader rules. Question: how far the rule reaches. — user
- Reuse `fork_agent_id` for Agent calls over a new name. Question: the field name. — user
- Keep the parent fallback, bounded at the call's end, over removing it. Question: what an unresolved dispatch shows. — user
- A Claude Code Agent call with no end or error and no resolved id takes no parent requests, over keeping the open window. Question: the never-ended call. Confirmed by the user as a narrowing of "unknown end as today", within the Claude Code Agent scope only. — user, answers AR-6, AR-8
- Rows outside that scope (`Task`, empty `tool_use_id`, other clients) and Skill rows keep today's estimate, over emptying them. Question: scope of the narrowing. — user, answers AR-8
- Duration = the stop's `duration_ms`, else stop ts minus start; reason: on a real capture Claude Code waits for the SubagentStop hooks before the Agent call returns, so the stop row timestamp lags the subagent's end by seconds. Question: the duration source (reopened by C1). — user
- No backfill of past days over a one-off re-queue. Question: past days. — user
- CLI unit tests and backend live-Postgres tests over an end-to-end harness. Question: test scope. — user
- The CLI reads the uncut result object only; a captured real run showed the result is an object, so no JSON-text tolerance. Question: the result shape. — user, answers AR-4
- The stop wins over the text id, and the text is used only when no stop exists, over text first. Question: source precedence. — user
- A stop with a `tool_use_id` names its start and marks its day. Question: cross-batch marking. — user
- Stamp `Agent` only, not `Task`. Question: tool names. — user
- Define the call on hook rows with forward and reverse forms from one SQL text, over a hand-written reverse per reader. Question: sharing the rule. (AR-5)
- An ambiguous call names no agent and blocks the text source. Question: conflicting stops. (AR-5)
- When several calls resolve to one id, the highest-ranked source wins and ties go to the earliest call, over each call keeping the id. Question: one agent on two rows. (AR-9)
- One SQL marking statement after the insert, with the Python mirror kept for Skill only, over a Python mirror of two sources. Question: ingest marking. (AR-11)
- Document the contract in the events guide and the plugin doc. Question: documentation.

## Rollout and plan notes

- Read-time change: stored sessions improve where the stop carries `tool_use_id`. Older CLI sessions show an empty bounded row. The plan checks whether a subagent's own requests share the parent's span, which decides what that row shows.
- Rollup counts already computed keep the old rule until the existing `backfill` command runs (see `cli-analytics-storage.md`). The release owner decides whether to run it.
- The plan's first task captures one whole foreground run as sanitized fixtures: the PostToolUse result, the stored cut end, the start and stop events, the span and API requests. A1 uses the result record, and C1 uses the run's own numbers. The captured result is an object, so the JSON-text tolerance was dropped.
- `least()` skips NULL, so a never-ended last sibling gets a NULL bound and reads as open. Write the bound so NULL means "no parent requests" for the in-scope case.
- Partition `ties` by `(parent_span_id, start_ts, bound_ts)` and give `fallback_start` one `bound_ts` column. Update the live test at `test_reader_live_dispatches.py:302`.
- Build the reverse form as a prefilter on rows carrying the id, then the forward resolution. Split `_ended_call` into the end-row predicate and its `EXISTS` wrapper, and take `max(ts)` from the same predicate.
- Define the agent-call tool names once (about eight `'Agent'` literals in the backend). Update the Timeline docstring at `reader.py:1447-1453`.
- Measure with `EXPLAIN (ANALYZE, BUFFERS)` before and after on the largest stored session. Each statement stays under half its timeout, and the marking statement under 1 s, because a marking over the 10 s statement timeout rolls the batch back. If it misses, use a session-scoped CTE in the reader or narrow the marking to the batch's pairs. Cover `agent_count`, the fork rollup, the marking statement, the range twin and the Timeline.
- Count stored non-`Agent` dispatch spans and spans with an empty `tool_use_id` before the plan. Check which `tool_use_id` a SendMessage continuation's stop carries, and whether a resume returns the same `agentId`.
- Add a handler-level assertion on the C1 fixture so the row the UI receives is checked.
- Unverified: whether Claude Code writes a span and a stop for an interrupted agent. If a stored interrupted session shows a span with no end, bound the row by its interaction.
- The CLI part reaches users only with a CLI release. Never commit. Backend tests run in Docker only.
