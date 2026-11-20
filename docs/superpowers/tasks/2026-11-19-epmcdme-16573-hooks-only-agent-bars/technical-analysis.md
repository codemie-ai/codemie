# Technical Research

**Task**: cli-analytics session-detail-dispatches hooks-only agent-timeline
**Generated**: 2026-11-19
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-16573: Session Detail Timeline shows no agent bars for hooks-only sessions (GitHub Copilot CLI,
and Claude Code sessions ingested without OTel) even though the AGENTS tile and Tools & Models →
Agent subtypes correctly report the same subagent runs.

Root cause (already located in source, re-verify against current HEAD before any design decision):
`get_session_detail_dispatches` in src/codemie/repository/cli_analytics/postgres/reader.py builds every
agent/skill dispatch row from a `t` CTE that only reads `spans` WHERE span_kind = TOOL AND
(subagent_type <> '' OR skill_name <> ''), i.e. it requires an OTel tool span. The query already has one
hook-only fallback branch (around reader.py:3716-3733): a UNION ALL that builds SKILL rows straight from
`agent.subagent.start` hook events when no span/dispatch names that fork (matched against `dispatch_fork`
and `skill_fork`/`t`). There is no equivalent branch for plain AGENT forks (hook_events.agent_type <> '',
skill_name = ''). hook_events already has a typed `agent_type` column (vocabulary.py HOOK_TEXT_FIELDS),
and a `first_subagent_start` dedup helper exists and is reused by the skill branch. The `stopped` CTE
(per-agent_id end time, idle-gap corrected) and `agent_usage` CTE (per-agent_id cost/tokens, from
span_agent_usage UNION paired_agent_usage) are both keyed by agent_id and already computed earlier in the
query — reusable as-is for a new agent branch.

cli_analytics_handler.py `_dispatch_label_and_kind` (~line 801) and `_count_dispatch_subtypes` (~line 780)
are data-source agnostic: they only read `subagent_type`/`skill_name` off each row dict, regardless of
whether it came from a span or a hook event — so no handler/API/frontend change should be needed, but
confirm this rather than assume it.

Suggested fix shape: add a third UNION ALL branch in `get_session_detail_dispatches` mirroring the existing
hook-only skill branch, selecting `hook_events` WHERE event_type='agent.subagent.start' AND agent_type<>''
AND skill_name='', deduped via `first_subagent_start`, joined to `stopped` (end time) and `agent_usage`
(cost/tokens, NULL when none) by agent_id. Must be gated with a NOT EXISTS against `t`/`launched`
(tool-span-based agent rows) so sessions that already have OTel Agent tool spans never get a duplicate row
for the same agent_id — a Claude Code session with OTel Agent tool spans plus the same subagent hooks must
still get exactly one row per subagent, no duplicate from the hook path.

Required test coverage:
- Integration (disposable analytics Postgres, real ingest → rollups → get_session_detail_dispatches):
  a hooks-only session (no TOOL spans) with agent.subagent.start/stop pairs for two agents (e.g. explore,
  file-auditor) plus three overlapping subagents (a /fleet-shaped parallel run), with usage rows (agent
  scope) for only one of the five, must produce one agent row per subagent run (5), each labelled by its
  agent_type, each bar from its start hook to its matching stop (by agent_id), overlaps preserved, the one
  with usage carrying cost/tokens and the rest NULL/empty (never the main thread's), and the "session" row
  unchanged.
- Integration (same harness): a Claude Code session with OTel Agent tool spans and the same subagent hooks
  must still produce exactly one row per subagent (no duplicate from the new hook-based branch), same
  timing/pricing as today.
- Unit-level coverage for any pure-Python helper logic touched (the ticket itself says SQL-only logic needs
  no unit test, but this run requires unit tests wherever there is testable Python logic, not just SQL).
- Follow the existing live-Postgres integration-test harness pattern used by sibling files
  tests/codemie/repository/cli_analytics/postgres/test_live_foreground_agent_run.py and
  test_live_skill_tool_attribution.py (NOT a file named test_reader_live_dispatches.py — that file does not
  exist in this repo; the ticket's reference to it is stale).

Explicitly OUT OF SCOPE — do not touch, do not investigate for changes, just note if relevant:
- The "Active time is empty for hooks-only sessions" defect (active_ms / Active tile derivation) —
  a related but separate defect (different field, same OTel-only root-cause category).
- The Copilot skills defect EPMCDME-16571 (skill bars not yet reported by Copilot) — a dependency for
  skill bars, not for agent bars; this fix is agent-bars only.

Constraint for the eventual plan/implementation: keep the change as minimal as possible — this should be
a single new UNION ALL branch plus its guard condition in one SQL function, not a refactor of the query or
of agent_call_attribution.py.

---

## 2. Codebase Findings

### Existing Implementations

**Purpose: render one Timeline bar per agent/skill dispatch for Session Detail.**
`get_session_detail_dispatches` (`src/codemie/repository/cli_analytics/postgres/reader.py:3340-3764`) is
the sole producer. Verified against current HEAD: the root cause holds exactly as described.
- `t` CTE (`reader.py:3408-3420`): reads `spans` WHERE `span_kind = TOOL AND (subagent_type <> '' OR
  skill_name <> '')` — the OTel-only source for agent and inline-skill rows. First `SELECT` of the final
  `UNION ALL` (`reader.py:3660-3709`) emits rows from `t`, joined to `launched`, `skill_fork`, `stopped`,
  `agent_usage`, `parent_usage`, `skill_usage`.
- Second `SELECT` of the `UNION ALL` (`reader.py:3710-3733`, comment "A forked skill... Its run time and
  cost come by its typed agent_id, as an agent's do") is the existing hook-only **skill** fallback: `FROM
  hook_events h WHERE h.event_type = 'agent.subagent.start' AND h.skill_name <> '' AND is_claude_code(h)`,
  deduped with `first_subagent_start(h)` (`fork_attribution.py:98-108`), excluded when `dispatch_fork` or
  `t`+`skill_fork` already covers the same `agent_id`. It joins `stopped` and `agent_usage` by `h.agent_id`
  exactly as the task's suggested new branch would.
- Third `SELECT` (`reader.py:3734-3756`) handles slash-command dispatches from `dispatch_fork`.
- **No branch reads `hook_events.agent_type`** for a plain agent fork (agent_type <> '', skill_name = '')
  — confirmed by grep: `agent_type` appears in the query only inside the unrelated `agent_count` correction
  at `reader.py:3193-3218` (session scalars, not dispatches), never inside
  `get_session_detail_dispatches`. This is the gap the ticket names.

**Purpose: report agent-run counts without requiring an OTel tool span (the AGENTS tile / Tools & Models
the ticket says already works).**
`get_session_detail_scalars` (`reader.py:3169-3221`) already has a working hooks-only agent correction: a
`hook_events` subquery at `reader.py:3202-3217` counts `agent.subagent.start` rows with `agent_type <> ''`,
deduped by `first_subagent_start`, excluding skill forks (`fork_of_counted_call`, `fork_of_dispatch`),
agent_types already seen in `spans`/`invocations_hourly`, restricted to a UTC day with no TOOL/EXEC/
INTERACTION span at all. `rollups.py:2098-2101` has the equivalent rollup-side hook branch for
`invocations_hourly` (kind 3, Agent subtypes), gated the same way (`_NO_SPAN_OF_KIND`). These are the two
mechanisms the ticket says "correctly report the same subagent runs" today, and they are the closest
working precedent for a Timeline-side branch: same source table, same event_type, same `agent_type`
column, same `first_subagent_start` dedup, same per-day span-exclusion idea (though the Timeline fix is
per-agent_id, not per-day, since `dispatch_fork`/`t`/`skill_fork` NOT EXISTS checks are already per-row by
design in the hook-only skill branch it must mirror).

**Purpose: anchor a hooks-only bar's end time and run duration by agent_id.**
`stopped` CTE (`reader.py:3521-3539`): per-`agent_id` `ended_at` (idle-gap corrected) and
`stop_duration_ms`, built once from `agent.subagent.stop` hook events, already keyed by `agent_id` alone
(not by tool_use_id or span), so it already serves both existing hook-only paths and would serve a third
with no change.

**Purpose: anchor a hooks-only bar's cost/tokens by agent_id.**
`agent_usage` CTE (`reader.py:3583-3588`) = `span_agent_usage UNION ALL paired_agent_usage`, both keyed by
`agent_id`. `span_agent_usage` (`reader.py:3546-3557`) requires an `agent_groups` span (LLM request spans
with an `agent_id` attr) — present whenever OTel captured the subagent's requests, absent for a pure-hook
session unless `paired_agent_usage` (`reader.py:3558-3582`, from `paired_requests`,
`inline_skill_attribution.py`) matches transcript-reported usage to API requests by agent_id. Either way,
the join is `au.agent_id = <some agent_id>` — no change needed for a new branch, matching the ticket's
claim.

**Purpose: Timeline label/kind/position from a dispatch row, independent of its SQL source.**
`cli_analytics_handler.py`: `_count_dispatch_subtypes` (`:780-786`) and `_dispatch_label_and_kind`
(`:801-816`) read only `row.get("subagent_type")` / `row.get("skill_name")`; `_duration_for_kind` (`:165-
174`) reads `subagent_duration_ms` for kind `"agent"`; `_bar_position` (`:177-194`) reads
`row.get("dispatch_start")`. None inspects a source discriminator. `_build_dispatches` (`:1816-`) iterates
`dispatch_rows` uniformly. **Confirmed**: the task's claim that the handler/API/frontend need no change
holds, because the existing hook-only skill branch already proves the same row shape (`subagent_type`,
`skill_name`, `is_slash_command`, `dispatch_start`, `duration_ms`, `real_duration_ms`,
`subagent_duration_ms`, `cost_usd`, token columns, `models`) reaches this code and renders correctly for
skills today.

### Decisions Already in the Code

- **One row per subagent run, keyed by agent_id, is the established contract** (docstring,
  `reader.py:3341-3391`): "One row per skill invocation... An agent's bar starts at its SubagentStart hook
  event". The existing hook-only skill branch enforces no-duplicate with two `NOT EXISTS` guards
  (`reader.py:3731-3733`) against `dispatch_fork` and against `t JOIN skill_fork`. A new agent branch must
  use the equivalent guard against `t`/`launched` (agent rows), not invent a new mechanism.
- **`first_subagent_start` is the one dedup rule for hook-sourced forks** (`fork_attribution.py:98-108`):
  ties by `(ts, ingest_seq)`, already reused by both the scalars correction and the skill branch — the
  established convention a new agent branch must follow rather than re-deriving dedup logic.
  `is_claude_code()` (`hook_row_sql.py:28`) gates Claude Code-only hook shapes; GitHub Copilot's hook events
  carry `agent_type` but may not satisfy `is_claude_code` — this determines whether the new branch should
  gate on it or not (a design question for the spec, not resolved here).
  Speculative: **Section 6 only** — Copilot's `is_claude_code` compatibility is unverified until a Copilot
  capture is read; the scalar-correction counterpart (`reader.py:3205`) explicitly allows a non-Claude-Code
  skill-tagged start through via `NOT (coalesce(h.skill_name,'')<>'' AND is_claude_code(h))`, suggesting the
  existing fallback mechanisms are already Copilot-aware for some shapes.
- **`agent_usage`/`stopped` are keyed by `agent_id` only, never by `tool_use_id` or span** — this is already
  the right direction for a hook-events-only row; no data is stored in a form the task would need to
  transform.

### Task Statements Checked Against the Code

- "`get_session_detail_dispatches` builds every agent/skill dispatch row from a `t` CTE that only reads
  spans WHERE span_kind = TOOL..." — **holds**, verbatim at `reader.py:3408-3420`.
- "The query already has one hook-only fallback branch (around reader.py:3716-3733)" — **holds**, this is
  the second `SELECT` of the `UNION ALL`, lines 3710-3733 (minor line-number drift inside the stated range,
  same branch).
- "There is no equivalent branch for plain AGENT forks" — **holds**, confirmed by grep: no `agent_type`
  reference anywhere inside `get_session_detail_dispatches`.
- "hook_events already has a typed `agent_type` column (vocabulary.py HOOK_TEXT_FIELDS)" — **holds**,
  `src/codemie/repository/cli_analytics/vocabulary.py:142`.
- "a `first_subagent_start` dedup helper exists and is reused by the skill branch" — **holds**,
  `fork_attribution.py:98-108`, used at `reader.py:3730`.
- "The `stopped` CTE... and `agent_usage` CTE... are both keyed by agent_id and already computed earlier in
  the query — reusable as-is" — **holds**.
- "`_dispatch_label_and_kind`... and `_count_dispatch_subtypes`... are data-source agnostic... so no
  handler/API/frontend change should be needed" — **holds**, confirmed above; this statement explicitly
  asked to be confirmed rather than assumed, and is confirmed by direct read of both functions and
  `_build_dispatches`.
- "a file named test_reader_live_dispatches.py — that file does not exist in this repo" — **holds**, no
  such file in `tests/codemie/repository/cli_analytics/postgres/` (directory listing captured below).
- The AGENTS tile / Tools & Models "correctly report the same subagent runs" for hooks-only sessions —
  **holds, and keeps an existing mechanism in place**: `get_session_detail_scalars`'s `agent_count`
  correction (`reader.py:3193-3218`) and the rollup's kind-3 hook branch (`rollups.py:2098-2101`) already
  do this independently of `get_session_detail_dispatches`; this fix adds a third, separate mechanism for
  the Timeline rather than reusing those two (they count, this one must render start/stop/cost per row) —
  see Risk Indicators.

### Consumers of Changed Contracts

The row shape `get_session_detail_dispatches` returns (`subagent_type`, `skill_name`, `is_slash_command`,
`dispatch_start`, `duration_ms`, `real_duration_ms`, `subagent_duration_ms`, `cost_usd`, `input_tokens`,
`output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `models`) is a shared contract. Searched for
every caller of `get_session_detail_dispatches`:
- `cli_analytics_handler.py:1693` — the only production caller, inside `get_session_detail`, immediately
  wrapped by `dispatch_money` (`session_money.py:115`) and fed to `_build_dispatches`. A new branch
  emitting the same column set with `subagent_type` populated and `skill_name = ''` needs no change here.
- Test callers: `test_live_skill_tool_attribution.py:182,253` and `test_fork_attribution.py` (helper-level,
  not the query) call it directly and assert on `(subagent_type, skill_name, is_slash_command)` keys and
  per-field values — a new UNION branch must not perturb these existing rows (the guard against
  double-counting tool-span-based agent rows protects this, but the new branch also must not match rows
  these tests already cover via skill_fork/dispatch_fork, which it won't since it is gated to
  `skill_name = ''`).
- No other reader method, rollup, or API route reads this specific function's output; `get_session_detail`
  is the only consumer searched and found.

### Architecture and Layers Affected

- **Repository layer** (PostgreSQL): `src/codemie/repository/cli_analytics/postgres/reader.py` —
  `get_session_detail_dispatches`, the single SQL statement to change (one new `UNION ALL` branch + guard).
  Helper SQL-builder modules it already imports and would reuse unchanged: `fork_attribution.py`
  (`first_subagent_start`, `is_claude_code` via `hook_row_sql.py`), `agent_call_attribution.py`
  (`agent_call_ids_cte`, not directly needed by the new branch but defines `launched`/`t`'s shape).
- **Service/handler layer**: `src/codemie/service/analytics/handlers/cli_analytics_handler.py` — read-only
  consumer, confirmed to need no change for this ticket.
- **No API or frontend layer files were found to need a change** — the dispatch row shape and its JSON
  rendering are already source-agnostic.

### Integration Points

- Internal: `reader.py` CTEs `t`, `launched`, `skill_fork`, `dispatch_fork`, `stopped`, `agent_usage` are
  all already computed in the same statement; the new branch is a consumer of `stopped`/`agent_usage`
  joined by `agent_id`, and a producer of a `NOT EXISTS` predicate the `t`/`launched`-sourced rows must
  stay unaffected by.
- No external service, HTTP client, or third-party SDK is involved; this is a pure PostgreSQL query change
  over data already ingested from OTel spans and CLI hook events (`hook_events`, `spans`, `log_events`
  tables).

### Patterns and Conventions

- SQL-builder functions as composable fragments (`fork_attribution.py`, `agent_call_attribution.py`,
  `hook_row_sql.py`) injected into f-string `WITH` CTEs — the established way to share a predicate across
  branches; a new branch should call `first_subagent_start("h")` and the existing `NOT EXISTS` idiom rather
  than writing new SQL for the same checks.
- Each `UNION ALL` branch in this function already matches the full output column list positionally, with
  explicit `NULL` or literal `0`/`''` placeholders for columns the branch's source doesn't have (e.g. the
  skill branch's `''` for `subagent_type`, hard-coded `0` for `is_slash_command`'s duration_ms slot) — a new
  branch must match this same positional column order exactly.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/cli-analytics-events.md` — documents the hook event contract: `agent_id`,
  `agent_type` as typed hook columns (lines ~133, ~178, ~211, ~259), `agents: {agent_type: int}` summary
  field (~347), and the `agent.skill.dispatch` fork-matching rule (~408) that the existing `dispatch_fork`
  CTE implements. Directly relevant: confirms `agent_type` is a stable, documented column, not an
  incidental one.
- No guide file specifically documents `get_session_detail_dispatches` or the Timeline bar algorithm beyond
  what is captured in the function's own docstring (`reader.py:3341-3391`), which is the primary source of
  truth for this change.

### Architectural Decisions

- No ADR file found for this specific query. The docstring itself records the design decisions inline
  (agent_id as the join key, idle-gap correction, stop-duration anchoring) — effectively the recorded
  decision log for this function.

### Derived Conventions

- Mirror an existing sibling `UNION ALL` branch rather than refactor: confirmed as the established pattern
  by comparing the skill hook-fallback branch's structure to the tool-span branch — same CTEs joined by
  `agent_id`/`tool_use_id`, different source table.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/repository/cli_analytics/postgres/test_live_skill_tool_attribution.py` — live-Postgres
  integration test exercising `get_session_detail_dispatches` directly (`:182`, `:253`), asserting on
  `(subagent_type, skill_name, is_slash_command)` and token fields; the closest existing harness for a new
  agent-branch test, covering the **skill** hook fallback but not an **agent** one.
  Also: `_tool_call`/`_agent` helpers in this file (`:97-148`) already construct `agent.subagent.start`
  hook rows with `agent_type`/`agent_id` for its own scenarios — reusable for the new test's fixtures.
- `tests/codemie/repository/cli_analytics/postgres/test_reader_live_copilot_detail.py` — "Live-Postgres
  checks for EPMCDME-16457: Session Detail of a hook-only (GitHub Copilot CLI) session" — exercises a
  hooks-only session end-to-end (scalars, tool counts, aggregates, cross-midnight, model normalization) but
  **never calls `get_session_detail_dispatches` or asserts on `dispatches`** — confirmed by grep across its
  ~840 lines: no `dispatches` or `get_session_detail_dispatches` reference. This is the gap the ticket's
  symptom describes and the closest existing fixture pattern (Copilot hook-only inserts) for the new
  integration test.
- `tests/codemie/repository/cli_analytics/postgres/test_live_foreground_agent_run.py` — named by the task
  as a harness pattern to follow; replays a sanitized real capture (`support/foreground-agent-run.json`)
  through real ingest → rollups → `LocalAnalyticsHandler.get_session_detail`, with pricing mocked via
  `coding_agent_pricing.lookup_cost_config`. This is a capture-replay pattern, distinct from
  `test_live_skill_tool_attribution.py`'s direct-insert pattern; either is a valid template.
- `test_live_session_subagent_cost.py`, `test_live_migration_subagent_listing.py` — adjacent subagent-cost
  and subagent-listing live tests not yet read in full; likely relevant for confirming `agent_usage`/
  `stopped` keying assumptions if the new branch's usage-matching needs closer verification.

### Testing Framework and Patterns

- pytest, `pytest.mark.skipif(not os.environ.get("TEST_LOCAL_PG_URL"), ...)` gates every live-Postgres test
  — this repo's standard pattern for integration tests needing a disposable analytics Postgres, exactly
  what the ticket's required test coverage calls for.
- Fixtures: direct SQL inserts into `hook_events`/`spans`/`log_events` (as in
  `test_live_skill_tool_attribution.py`) or JSON capture replay plus `RollupRefresher` (as in
  `test_live_foreground_agent_run.py`) are the two established fixture styles.

### Coverage Gaps

- No test exercises `get_session_detail_dispatches` for a hooks-only **agent** fork at all today — this is
  exactly the gap the ticket's "Required test coverage" section describes and the task explicitly asks for.
- No test exercises the overlapping-subagents ("/fleet-shaped parallel run") scenario against
  `get_session_detail_dispatches` for either spans or hooks.
- No existing test proves "exactly one row, no duplicate" for an agent that has both an OTel Agent tool
  span and subagent hooks — the closest analog is the skill branch's `NOT EXISTS` guards, untested from the
  agent side.

---

## 5. Configuration and Environment

### Environment Variables

- `TEST_LOCAL_PG_URL` — gates every live-Postgres integration test in this directory (skip when unset); the
  required integration tests for this ticket depend on it being set in the test-running environment.

### Configuration Files

- None specific to this feature area beyond the general analytics Postgres engine config
  (`src/codemie/repository/cli_analytics/postgres/engine.py`, not read in depth — not needed for a query-
  only change).

### Feature Flags and Deployment Concerns

- None found gating this code path; `get_session_detail_dispatches` runs unconditionally for every Session
  Detail request within `detail_gate(scope)`.

---

## 6. Risk Indicators

- The new branch's `NOT EXISTS` guard against `t`/`launched` must match the exact semantics of the existing
  skill branch's guards (`reader.py:3731-3733`) or a Claude Code session with both an OTel Agent tool span
  and the matching subagent hooks will double-render a bar — this is the one most failure-prone part of a
  "minimal" change, since the agent case's guard target (`t` rows with `subagent_type <> ''`, joined via
  `launched`) is structurally different from the skill case's guard target (`t` rows with `skill_name <>
  ''`, joined via `skill_fork`).
- `is_claude_code()` gating is unverified for GitHub Copilot's hook shape specifically for a plain agent
  fork; the scalar-count correction at `reader.py:3205` treats Claude-Code-ness conditionally rather than
  as a blanket filter, suggesting a naive copy of the skill branch's `AND is_claude_code(h)` could silently
  exclude Copilot sessions — the primary named symptom.
- Three independent mechanisms now compute "agent count/bars for hooks-only sessions" with similar but not
  identical guard logic (`get_session_detail_scalars`'s `agent_count` correction, `rollups.py`'s kind-3
  hook branch, and the new Timeline branch) — see Decisions Already in the Code / Task Statements; keeping
  all three in sync is a maintenance risk the task does not ask to resolve, only to avoid breaking.
- No existing test covers overlapping concurrent subagents through `get_session_detail_dispatches` for
  either spans or hooks — the required 5-row overlapping-subagent test is genuinely new ground, not an
  extension of an existing assertion shape.
- `paired_agent_usage`'s matching (transcript-reported usage paired to API requests by agent_id) is the
  usage path most likely exercised by a hooks-only session with no `agent_groups` span; its correctness for
  the new branch rests on `agent_usage`'s existing UNION, not on anything the new branch adds — low risk,
  but worth confirming with the one-of-five-has-usage integration scenario.

---

## 7. Summary for Complexity Assessment

This is a single-layer change confined to one PostgreSQL query function,
`get_session_detail_dispatches` in `src/codemie/repository/cli_analytics/postgres/reader.py` (3340-3764):
add one new `UNION ALL` branch reading `hook_events` for plain agent forks
(`agent_type <> '', skill_name = ''`), mirroring the existing hook-only skill fallback branch
(3710-3733) structurally and reusing CTEs (`stopped`, `agent_usage`, `first_subagent_start`) that are
already computed in the same statement for the same purpose. No handler, API, or frontend code needs to
change — confirmed by reading `_dispatch_label_and_kind`, `_count_dispatch_subtypes`, and
`_build_dispatches` in `cli_analytics_handler.py`, all of which already render the existing hook-only skill
rows through the identical path. Two sibling mechanisms (`get_session_detail_scalars`'s agent_count
hook-fallback and the rollup's kind-3 hook branch) already solve the analogous counting problem for the
AGENTS tile, giving a close, working precedent for the dedup and Claude-Code-gating approach, though not an
identical guard shape since this branch must additionally avoid duplicating per-row agent bars rather than
per-day counts.

The main technical risk is correctness of the no-duplicate guard against tool-span-based agent rows (`t`/
`launched`) for a session that has both OTel spans and hook events for the same agent_id, and verifying
whether `is_claude_code()` gating (copied uncritically from the skill branch) would wrongly exclude GitHub
Copilot CLI sessions, the primary symptom named in the ticket. Test coverage for this exact path is
currently zero: `test_reader_live_copilot_detail.py` exercises hooks-only sessions end-to-end but never
calls `get_session_detail_dispatches` or asserts on `dispatches`, and `test_live_skill_tool_attribution.py`
covers the analogous skill fallback but not the agent one. The two required integration tests (hooks-only
multi-agent with overlaps, and span+hook duplicate-prevention) are new scenarios, not extensions of
existing assertions, but they have two directly reusable harness templates (direct-insert in
`test_live_skill_tool_attribution.py`, capture-replay in `test_live_foreground_agent_run.py`) and existing
fixture helpers (`_tool_call`/`_agent` in the former) that already build `agent.subagent.start` hook rows
with `agent_type`/`agent_id`.

---

## 8. External References

None named by the task. Every file, line range, and helper function the task's root-cause analysis and
suggested fix shape name are inside this repository (`src/codemie/repository/cli_analytics/postgres/
reader.py`, `cli_analytics_handler.py`, `vocabulary.py`, and the two sibling test files); all were located,
read, and verified against current HEAD in Section 2 above, not treated as an unread external source.
