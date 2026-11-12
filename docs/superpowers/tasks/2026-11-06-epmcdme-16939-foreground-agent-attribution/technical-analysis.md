# Technical Research

**Task**: cli-analytics subagent attribution tool_response agent_id timeline (EPMCDME-16939)
**Generated**: 2026-11-06
**Research path**: filesystem (backend `codemie` primary; CLI `codemie-code`; UI `codemie-ui`)

---

## 1. Original Context

Ticket EPMCDME-16939 (https://jiraeu.epam.com/browse/EPMCDME-16939), Bug, In Progress.

Summary: [CLI Analytics][Foreground Agents] Subagent Timeline misattributes parent API usage and fails to report actual execution duration

Environment: Production codemie.lab.epam.com/analytics (project epm-cdme-cp); CodeMie CLI 0.16.6 (codemie-claude --profile codemie-prod), Claude Code with Production OTLP capture through the local proxy daemon; Backend API 2.64.0; UI 2.64.0. Session executed 2026-11-06 03:34-03:35 UTC.

Steps to Reproduce:
1. Start Claude Code with CodeMie CLI v0.16.6 and Production OTLP capture.
2. Execute a foreground Agent using sdlc-factory:tech-analyst-filesystem with a sufficiently long prompt (longer than ~260 characters).
3. Allow the agent to finish and telemetry to be ingested.
4. Open CLI Analytics -> Sessions -> Session Details -> Timeline.
5. Compare the dispatch cost, tokens and duration with the actual SubagentStart/SubagentStop events and model API requests.

Actual vs Expected (Timeline dispatch row):
- Model: expected Haiku 5.5 (claude-haiku-5-5, 3 API calls); actual: parent Sonnet request attributed (claude-sonnet-5 request at 03:35:12.238Z, after the agent returned)
- Tokens: expected in 6 / out 1,152 / cache read 25,917 / cache create 18,651 = 45,726; actual in 2 / out 549 / cache read 44,437 / cache create 843 = 45,831
- Cost: expected $0.005023; actual $0.019529 (3.9x overstated)
- Duration: expected 21,236 ms (SubagentStop duration_ms); actual null

Evidence: Claude/trace session 896a557d-c090-4d45-88f2-c4aacc13a83b (CodeMie session 7242e5e5-8b4e-4dbb-b4c7-8dddab6d2064); subagent sdlc-factory:tech-analyst-filesystem, agentId ad01cb9e34b94b630, tool_use_id toolu_bdrk_01XCFiafMzK3xf5qVKeCmvmj. The agent.subagent.stop hook event carries agent_id=ad01cb9e34b94b630, the same tool_use_id and duration_ms=21236. The agent.tool.end hook event: tool_response is cut to 300 characters and contains no agentId (a foreground Agent result begins with the echoed prompt). Session Detail API dispatch row: {"kind":"agent","duration_ms":null,"cost_usd":0.01952874,"input_tokens":2,"output_tokens":549,"cache_read_tokens":44437,"cache_creation_tokens":843} - identical to the parent's api_request event at 03:35:12.238Z. Session totals (cost $0.238210, tokens, API calls) are correct; only per-dispatch attribution is wrong.

Root Cause (as reported in the ticket, to be checked against the code):
- CLI: codemie-code/src/agents/core/OtlpAgentAdapter.ts:60-66 truncates tool_response to 300 chars (MAX_TOOL_FIELD_CHARS), removing agentId from a foreground Agent result. An equivalent rescue exists only for Skill (claude-code-otlp/event-fields/tool-fields.ts:68-74 resolveSkillForkFields, wired at claude-code-otlp.plugin.ts:543).
- Backend: codemie/src/codemie/repository/cli_analytics/postgres/reader.py:1237-1243 (agent_end) reads the agent id only from the truncated tool_response text (fork_attribution.py:29-32), so launched.agent_id is NULL. reader.py:1409 then falls back to parent_usage - the parent's window starting at the dispatch - which picks up the parent's subsequent API request. The agent.subagent.stop hook, which carries both tool_use_id and agent_id, is not used for correlation.
- Duration: needs the resolved agent id (stopped joined on la.agent_id; cli_analytics_handler.py:103-105 returns null).

Impact: incorrect per-agent cost, wrong model and token attribution, missing Timeline duration for foreground subagent dispatches with long prompts - practically every SDLC Factory dispatch. Session totals remain correct.

Required Fix (from ticket):
1. Preserve agentId as a dedicated CLI telemetry field for Agent tool results (mirror resolveSkillForkFields), independent of the 300-char tool_response cut.
2. Correlate dispatches with SubagentStart/SubagentStop using agent_id and tool_use_id (e.g. coalesce(dedicated field, agent_end agentId, agent.subagent.stop agent_id)), so existing data is also corrected.
3. Exclude parent API calls from subagent attribution; use the parent-window fallback only when no agent id can be resolved, and bound it by the Agent call's end.
4. Add foreground-agent regression tests with long responses (>300 chars, no agentId in cut text) asserting subagent model, tokens, cost and duration, and that a later parent API request is excluded.

Acceptance Criteria (developer-owned automated tests):
Unit (CLI & Backend): preserve agentId and tool_use_id when Agent responses exceed the 300-char truncation; correctly correlate foreground subagents with lifecycle events and API requests; exclude parent API requests from subagent token/cost attribution including requests immediately after agent completion; compute subagent duration, tokens and cost from actual lifecycle timestamps and model pricing; handle missing identifiers and incomplete telemetry without wrongly attributing parent usage.
Integration (CLI -> Proxy -> OTLP -> Backend): foreground-agent telemetry ingestion and correct attribution for long Agent responses; sequential and parallel subagents, different models, concurrent parent sessions, no cross-agent attribution or double counting; failed, interrupted and cancelled agents keep correct status and attributable partial usage; Session Detail API and Timeline metrics match source telemetry (model, tokens, cost, duration +-1 s); parent requests excluded from subagent metrics while session-level totals stay accurate.

Related: EPMCDME-15576 (background agent dispatch attribution; fix commit 2b75e017b deployed since backend 2.62.0; does not cover a foreground result whose agentId is cut off) and EPMCDME-15512 (subagent duration in the Timeline).

Scope notes (from caller): cross-repo; backend primary, CLI same branch name, check UI need. Backend runs in Docker only. Local data has only `async_launched` Agent results. Report prior art (2b75e017b, CLI 1da8b85 #685) and the rollups/ingestor readers of the Agent end's agentId. Leave uncommitted files alone (`config/customer/customer-config.yaml` is local-dev); never commit.

---

## 2. Codebase Findings

### Existing Implementations

Paths below are in `codemie\src\codemie\repository\cli_analytics\postgres\` (BE) unless prefixed `CLI:` (`...\codemie-code\src\agents\`) or `UI:`.

**Purpose A: name the subagent an Agent tool call started (agentId), so its cost and run can be joined**
- CLI cut: `CLI:core/OtlpAgentAdapter.ts:60-69` `TEXT_FIELD_LIMITS` cuts `tool_response` (and `tool_input`, `error`, `description`) to `MAX_TOOL_FIELD_CHARS = 300` after masking (`limitPayload`, :307-331; object values are `JSON.stringify`-ed then sliced). Identifier keys never masked: `UNMASKED_FIELD_KEYS` :72-76 (includes `fork_agent_id`, `agent_id`, `tool_use_id`).
- CLI rescue for Skill only: `CLI:plugins/claude-code-otlp/event-fields/tool-fields.ts:68-74` `resolveSkillForkFields` returns `{fork_agent_id}` for `toolName === 'Skill'` only, from the uncut result (object or JSON string via `parseJsonObject` :145), id sliced to 300; wired in `onPostToolUse` `claude-code-otlp.plugin.ts:539-548` (`PostToolUse` only; `onPostToolUseFailure` :550 stamps nothing). `Agent`/`Task` are handled by `resolveToolContextFields` :17-26 (`subagent_type`, `description`) and `AGENT_DISPATCH_TOOLS` :138; no agent-id field exists for them.
- BE ingest: `agent.tool.end` attrs go to `hook_events.attrs` (jsonb); `tool_use_id`, `tool_name`, `agent_id` are typed columns (`otlp.py:149,155,184,219`). `fork_agent_id` and `tool_response` stay in `attrs`.
- BE regex (cut id is no key): `fork_attribution.py:29-35` `tool_response_agent_id` / `_AGENT_ID` accept only `"agentId":"…"` with closing quote. Skill: `skill_end_agent_id` :126-131 = `coalesce(nullif(attrs->>'fork_agent_id',''), regex)`. Python mirror `_named_agent_id` :45-56 reads `fork_agent_id` only when `tool_name == 'Skill'`; test pins that an Agent end ignores `fork_agent_id` (`tests/.../test_fork_attribution.py:52`).
- BE timeline reader (the bug site): `reader.py:1489-1496` CTE `agent_end` = latest `agent.tool.end` of `Agent` per `tool_use_id`, `agent_id` = `tool_response_agent_id(attrs->>'tool_response')` only. `reader.py:1510-1517` `launched`: `launch_ts = coalesce(forked.ts, agent_start.ts, e.ts)`, `forked` joined by `e.agent_id` (:1504-1509, :1517). `reader.py:1518-1526` `skill_fork` (Skill ends via `skill_end_agent_id`).
- No Python/SQL reader looks at `agent.subagent.stop.tool_use_id` or `agent.subagent.start.tool_use_id`; `stopped` (:1568-1575), `idle` (:1549-1567), `forked` (:1504) are all keyed by the typed `agent_id` alone.

**Purpose B: attribute cost, tokens, model to the dispatch**
- `reader.py:1576-1593` `agent_groups`/`span_agent_usage`: sums `log_events` api_requests whose `span_id` is the parent span of `llm_request` spans carrying `attrs.agent_id` = the subagent. `reader.py:1594-1624` `paired_agent_usage` for forks with no tagged request; `agent_usage` = union. Per-model sums `_model_sums(...)` feed `models`.
- `reader.py:1625-1659` fallback: `fallback_start`/`sibling_window`/`parent_usage` give a dispatch with no agent id (`takes_parent = la.agent_id IS NULL AND sf.agent_id IS NULL` :1630) the parent span's api_requests from the dispatch start to the next later sibling start under the same parent, unbounded for the last sibling, split among `ties`. Start used = `coalesce(launch_ts, span_start)` (:1629), i.e. the Agent `agent.tool.start` when `forked` is unjoined. No bound by the Agent call's end (`agent_end.ts` is not in `sibling_window`).
- Final select `reader.py:1676-1692`: `coalesce(au.*, pu.*)`; `agent_usage au` joins on `coalesce(la.agent_id, sf.agent_id)` (:1690); `parent_usage pu` joins only when both ids are NULL (:1692).
- Pricing: `LocalAnalyticsHandler.get_session_detail` `cli_analytics_handler.py:1057` re-prices each row's `models` with `dispatch_money` (`session_money.py:87-91`, divides by `ties`). The row served to the UI carries no model field; `_build_dispatches` :1141-1193 emits only `label, kind, start_offset_ms, duration_ms, cost_usd, token fields`.

**Purpose C: report the subagent's run duration**
- `reader.py:1674-1675` `subagent_duration_ms = stop.ended_at - launch_ts` (timestamps; the stop event's `attrs.duration_ms` is not read anywhere in the dispatches query). `stopped.ended_at = max(stop.ts) - idle` (:1568-1575) joined at :1689 on `coalesce(la.agent_id, sf.agent_id)`; NULL agent id gives NULL duration. Handler `_duration_for_kind` `cli_analytics_handler.py:111-120` (agent: `subagent_duration_ms`, None passes through), `_bar_position` :123-140 clamps to session end.
- CLI stop event: `claude-code-otlp.plugin.ts:422-483` builds `agent.subagent.stop` with `resolveSubagentStopFields` (`event-fields/subagent-fields.ts:22-37`): `tool_use_id` always sent (`''` when unknown; from the hook payload, undeclared on the SDK type, else the `.meta.json` sidecar, :447-449), `duration_ms`, `started_at` only when the usage pass found them. `agent.subagent.start` (:212) sends no `tool_use_id` (spool and DB: none).

**Purpose D: keep counts and rollups consistent with the same "which start does an Agent end name" rule (must stay in step with the reader)**
- `ingestor.py:574` `named_subagent_ids(stored.hooks)` -> `_mark_subagent_days` re-queues the subagent's own day (`FORK_DAYS_SQL` `fork_attribution.py:77-83`) because an end can arrive after the subagent's day. Python mirror reads end's `fork_agent_id` (Skill) else full `agentId` in `tool_response`.
- `rollups.py:2145-2150` `_NAMED_SUBAGENT_ID` (+ `_MARK_RANGE` branch :2189-2194): same rule in SQL, for the dirty-day marking.
- `fork_attribution.py:134-155` `_named_by_end(fork, 'Agent'|'Skill')` inside `dispatch_fork_candidate`: a `agent.subagent.start` is a candidate fork of a slash command unless a Skill end names it, and (for a start without `skill_name`) unless an Agent end names it by complete `agentId`. Users: `fork_of_dispatch` :171-194, `reader.py:1311-1312` (agent_count correction), `reader.py:1539` (`dispatch_fork` CTE), `rollups.py` counting (`_IS_SKILL_FORK` :1741 uses `fork_of_counted_call`, Skill only).
- Net: an Agent end whose `agentId` is cut is treated, in all of these, as naming no subagent.

**Prior art (how Skill/background was solved)**
- BE commit `2b75e017b` (EPMCDME-15557 "Exclude denied Agent calls, attribute agent runs, count a forked skill once (EPMCDME-15576) (EPMCDME-15968)", 2026-10-23, 22 files): added `fork_attribution.py` (shared SQL fragments + Python mirror so counts and reader "read one rule"), `agent_end`/`launched`/`forked`/`stopped`/`skill_fork`/`dispatch_fork` CTEs, `dirty.py`/`ingestor.py` re-queueing, 2.8k-line `test_live_dispatch_gate.py`. Agent id source: complete `"agentId"` in `tool_response` only (a cut id explicitly rejected, :30-31); the dedicated field is consumed for Skill only.
- CLI commit `1da8b85` (#685, "skip api-request-blob lines, attribute skills, add fork_agent_id"; docs name EPMCDME-16316), 2026-10-23: `resolveSkillForkFields`, `fork_agent_id` in `UNMASKED_FIELD_KEYS`, tests `tool-fields.test.ts:146-175`, `claude-code-otlp.plugin.test.ts:700-728`, `OtlpAgentAdapter.test.ts:715`. Documented in `docs/ARCHITECTURE-OTLP-PLUGIN.md:198` ("`fork_agent_id` exists only for a `Skill`").
- Same cut-off already handled elsewhere in the CLI: `transcript/fork-skill.ts:135-145` reads `toolUseResult.agentId` (typed) else the text.

**Other touched code**: `UI: src/pages/analytics/components/cli-analytics/views/SessionTimeline.tsx:112` (`rawWidth = duration_ms / durationSafe`, min 3%), `UI: .../format.ts:27` `formatDuration(null) -> '—'`, `UI: src/types/cliAnalytics.ts:88-98` `DispatchRow.duration_ms: number` (handler can send null). No UI field carries a per-dispatch model.

### Decisions Already in the Code

- Cut id is no key: only a complete quoted `agentId` is accepted (`fork_attribution.py:29-32`); reason recorded in the docstring (CLI cuts at 300). Alternative: partial match; rejected.
- Dedicated CLI field instead of widening the cut: `fork_agent_id`, Skill only, id sliced to 300, derived before the base-class cut (`tool-fields.ts:61-74`). The convention for "a field the cut would otherwise lose" is a resolver in `event-fields/tool-fields.ts` + a key in `UNMASKED_FIELD_KEYS` + a spread in `onPostToolUse`. Agent ends are explicitly insulated from `fork_attribution` reading `fork_agent_id` (`test_fork_attribution.py:52`).
- Agent id with no resolvable value takes the parent window: `reader.py:1692` (decision recorded in the `get_session_detail_dispatches` docstring :1447-1453 "A dispatch with no agentId at all ... falls back to its own slice of the parent span's API requests"); a known agentId without requests gets NULL (not the parent's).
- Agent run time = stop ts - start; start = SubagentStart, else call start, else ack (`reader.py:1511-1514`, docstring :1436-1440). Stop correlation is by `agent_id` only; `tool_use_id` is the key between call, span and end.
- Shared SQL fragments in `fork_attribution.py` serve both rollups and reader ("both twins read one rule", module docstring :15-16); Python mirror `named_subagent_ids` exists for ingest-time dirty marking and has a unit test pinning it to the SQL.
- Direction of storage: agent_id <-> tool_use_id is stored on three different events: Agent end (tool_use_id typed; agent_id only inside cut text), `agent.subagent.stop` (agent_id and tool_use_id typed columns, `duration_ms`/`started_at` in attrs), `agent.subagent.start` (agent_id typed, no tool_use_id). The task needs tool_use_id -> agent_id; the stop event is the only stored row holding both typed.

### Task Statements Checked Against the Code

- CLI truncation at `OtlpAgentAdapter.ts:60-66` removes agentId from long results: line range holds (actual :60-69); that the foreground result puts `agentId` after the echoed prompt is **unverified** (see below).
- Rescue exists only for Skill (`tool-fields.ts:68-74`, wired `plugin.ts:543`): **holds**.
- `reader.py:1237-1243` agent_end reads agentId only from truncated text: logic **holds**; lines are stale, now `reader.py:1489-1496` (current branch). Likewise the `reader.py:1409` fallback is now `:1692`/`:1644-1659`, handler `:103-105` is `:111-120`.
- `launched.agent_id` NULL -> `parent_usage` picks the parent's later request: **holds** (`reader.py:1630,1692`; window start `coalesce(launch_ts, span_start)` with `launch_ts` falling to `agent_start.ts` when `forked` is unjoined; `next_start` NULL for the last sibling so the window is open-ended).
- `agent.subagent.stop` (agent_id + tool_use_id) not used for correlation: **holds** (no CTE reads stop `tool_use_id`).
- Duration needs resolved agent id (`stopped` joined on `la.agent_id`; handler returns null): **holds** (`reader.py:1689`, `cli_analytics_handler.py:117-119`).
- A foreground Agent result is cut to 300 chars with no agentId, and begins with the echoed prompt: **unverified**. Local sources contain only `async_launched` results (161 transcript `toolUseResult` records with agentId, all `async_launched`; 20/20 DB Agent ends carry an id in their first 300 chars). The existing live test fixture invents a short foreground shape with an early id (`test_reader_live_dispatches.py:126-128`). What would settle it: one foreground `PostToolUse`/`toolUseResult` record from a long-prompt Agent call.
- Fix item 2, "so existing data is also corrected", via the stop event: **depends on stored data**. Local DB: 141 `agent.subagent.stop` rows, 5 with `tool_use_id` (all 2026-10-14 or later); 136 NULL (CLI sends `''`, DB shows NULL, up to 2026-10-13). Whether prod rows for the ticket's period carry it is unverified; the ticket's evidence says that session's stop does.
- Fix item 2 "coalesce(dedicated field, agent_end agentId, stop agent_id)": **keeps mechanisms of Purpose D in place** if only the reader changes: `ingestor.named_subagent_ids`, `rollups._NAMED_SUBAGENT_ID`, `_named_by_end(…, 'Agent')` would keep reading `tool_response` only (consumers listed below).
- "Model: expected Haiku" as a row value: **contradicted for the API shape**: the dispatch row has no model; model enters only through `models` for pricing (`cli_analytics_handler.py:1057,1179-1191`).
- Expected duration 21,236 ms = SubagentStop `duration_ms`: the reader never reads `attrs.duration_ms`; it computes `stop.ts - launch_ts` (`reader.py:1674-1675`), so equality with the event field depends on `launch_ts`; the AC allows +-1 s.
- "2b75e017b deployed since backend 2.62.0", Prod versions: **unverified** (outside the repository; commit exists and is in this history).
- CLI 0.16.6: **holds** (`c23a0f8 chore: bump version to 0.16.6`, CLI HEAD `d001dc1`).

### Consumers of Changed Contracts

Contract 1: CLI `agent.tool.end` for `Agent` gaining a dedicated agent-id field (name not yet chosen).
- `OtlpAgentAdapter.ts:72-76` `UNMASKED_FIELD_KEYS` (a new key not listed there is masked like free text); `claude-code-otlp.event-id.ts:70-80` native events hash payload+timestamp, so a new field changes only new events' `event_id`.
- BE readers of the Agent end (all read `tool_response` text only): `reader.py:1492` (timeline), `fork_attribution.py:45-56,138` (`named_subagent_ids`, `_named_by_end`), `rollups.py:2147-2150,2192`.
- Tests that pin current Agent behaviour: `test_fork_attribution.py:44-91` (Agent end ignores `fork_agent_id`), `test_reader.py:1371-1393` (exact `agent_end` SQL text), `test_reader.py:1411-1431`, `:1435-1477` (SQL-text assertions on `launched`, `forked`, `parent_usage`, `takes_parent`), `test_ingestor.py` (named forks), `test_dirty.py`.

Contract 2: how a timeline row resolves its agent id / window (`agent_end`, `launched`, `sibling_window`, `parent_usage`).
- `get_session_detail_dispatches` single caller: `cli_analytics_handler.py:1023`; row keys consumed in `_build_dispatches`, `_duration_for_kind`, `dispatch_money` (`models`, `ties`, `subagent_duration_ms`, `real_duration_ms`, `dispatch_start`); port `ports.py` (declares the method).
- Skill/command rows share `launched`-style CTEs (`skill_fork`, `dispatch_fork`, `inline_usage`) and `agent_usage`/`stopped`; the fourth UNION branch (`reader.py:1699-1716`) excludes forks via `dispatch_fork`/`skill_fork`.
- Session scalars `agent_count` (`reader.py:1298-1325`) and the rollup agent counts use `fork_of_dispatch` / `dispatch_fork_candidate`, which consult `_named_by_end`.
- Doc contracts: `.ai-run/guides/integration/cli-analytics-events.md` (agent.skill.dispatch/agent.tool.end sections, ~:430-445, which describe the fork key as the Skill end's id or `prompt_id`) and `cli-analytics-storage.md`; CLI `docs/ARCHITECTURE-OTLP-PLUGIN.md:198`.

Contract 3: UI `DispatchRow` (`UI: types/cliAnalytics.ts:88-98`): `duration_ms` typed `number`; handler emits `None` today (the bug's own symptom) and the UI renders it (`formatDuration` null -> '—', bar min width 3%). No other UI field or code path depends on agent ids.

### Architecture and Layers Affected

- CLI hook adapter layer (`core/OtlpAgentAdapter` base + `plugins/claude-code-otlp` event-fields/plugin) -> spool/forwarder (unchanged, transports events as is).
- Backend: ingest decode (`otlp.py`, `ingestor.py` dirty marking) -> storage (`hook_events`, `spans`, `log_events`) -> rollup dirty marking (`rollups.py`, `dirty.py`) -> reader SQL (`reader.py`, `fork_attribution.py`) -> handler (`cli_analytics_handler.py`, `session_money.py`) -> API.
- UI: display only.

### Integration Points

- CLI -> local proxy daemon spool -> forwarder -> backend OTLP endpoint (`hooks.ndjson` lines `{agentName, hookEvent(JSON string), evidence_v}`).
- Claude Code hook payloads (`PostToolUse.tool_response: unknown` per `claude-agent-sdk-hooks.d.ts:429`; `SubagentStop` type declares no `tool_use_id`).
- Dedup: backend ledger by `day + hash(type, session, event_id)`.

### Patterns and Conventions

- Shared SQL fragment functions return SQL strings; reader and rollups import them; a Python mirror is unit-tested against them.
- Reader tests are two-tier: SQL-text assertions in `test_reader.py` (via `_SqlRecorder`) and live-Postgres behaviour tests (`test_reader_live_dispatches.py`, `test_live_dispatch_gate.py`) that insert rows into a real DB and call the unmocked reader.
- CLI: resolvers are pure functions in `event-fields/`, no I/O, never mocked; Vitest unit tests next to them.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- BE: `.ai-run/guides/integration/cli-analytics-events.md` (event contracts, fork key rules), `cli-analytics-storage.md` (rollup/fallback rules), `.ai-run/guides/testing/local-verification.md` (live API checks before an MR), `.ai-run/guides/quality-gates.md`.
- CLI: `docs/ARCHITECTURE-OTLP-PLUGIN.md` (event-fields, dispatch pattern), `.ai-run/guides/architecture/architecture.md`.

### Architectural Decisions
- `fork_attribution.py` module docstring: counts and reader must read one rule. `reader.py:1425-1456` docstring is the recorded behaviour of dispatch attribution (exact ID chain; parent fallback only with no agent id).
- `OtlpAgentAdapter.ts` header: event built at hook time because the forwarder's env is frozen; fields derived from uncut input before truncation.

### Derived Conventions
- A stored-contract change is documented in the events guide in the same change (2b75e017b touched both guides).

---

## 4. Testing Landscape

### Existing Coverage
- BE: `test_reader.py:1371-1488` (SQL text of agent chain, run time, fallback, sibling window); `test_reader_live_dispatches.py` (live: parallel/sequential agents :188-231, :465-503, no-agent-id fallback :302, run times :366, foreground :423); `test_fork_attribution.py`; `test_ingestor.py`, `test_dirty.py`, `test_rollups.py`, `test_live_dispatch_gate.py`, `test_live_session_subagent_cost.py`.
- CLI: `tool-fields.test.ts:146-175`, `claude-code-otlp.plugin.test.ts:700-728`, `OtlpAgentAdapter.test.ts:685-720`, `tests/integration/analytics/` and `analytics*.test.ts` (CLI-level; not checked for a CLI->backend path).

### Testing Framework and Patterns
- BE: pytest + pytest-asyncio; `make test-cli-analytics-live` runs `-k live` on throwaway Postgres 14 and 17; live files skip without `TEST_LOCAL_PG_URL` (default `postgresql://postgres:password@localhost:5432/postgres`; the `postgres` container is up locally and holds the `codemie_analytics` schema).
- CLI: Vitest 4 (`--project unit|cli|agent`), per `package.json:44`.

### Coverage Gaps
- No test of an Agent end whose `tool_response` is cut before `agentId` (existing foreground fixtures put `agentId` first, `test_reader_live_dispatches.py:126-128`).
- No test of `agent.subagent.stop.tool_use_id` as a join key; no test that excludes a parent api_request logged after the Agent end.
- No CLI test that an Agent PostToolUse stamps an id field; none for Agent in `resolveSkillForkFields` except that it returns `{}` (`tool-fields.test.ts:162`, asserts today's behaviour).
- No repo-local CLI -> proxy -> backend end-to-end harness found; the ACs name one.

---

## 5. Configuration and Environment

### Environment Variables
- BE tests: `TEST_LOCAL_PG_URL` (live tests). No runtime env vars found governing dispatch attribution.

### Configuration Files
- `pytest.ini` forces `PG_URL` to a dead port for the suite (per comment in `test_reader_live_dispatches.py:39`); `Makefile:33-48` live target.
- Local-dev only, do not touch: `codemie\config\customer\customer-config.yaml` (modified, unstaged); CLI `.codemie/codemie-cli.config.json` (modified).

### Feature Flags and Deployment Concerns
- None found for this path. Backend change applies at read time to stored rows; CLI change reaches users only with a CLI release (prod CLI 0.16.6).
- Local data: spool `the local OTLP spool` (4 sessions, 346 hook lines): Agent `tool.end` has `resp_len=300` with the id in the first 300 chars (async acks begin with `"isAsync":true,"status":"async_launched","agentId"`); `agent.subagent.stop` carries `agent_id`, `tool_use_id`, `duration_ms` (219601, 225795); `agent.subagent.start` carries no `tool_use_id`. **Not confirmed locally:** any foreground Agent with a cut-off id.

---

## 6. Risk Indicators

- Three readers of the Agent end's `agentId` (reader `agent_end`, `named_subagent_ids`/`_NAMED_SUBAGENT_ID`, `_named_by_end(…,'Agent')`) must agree; changing one leaves the others treating the start as unnamed (see Purpose D). Consumers listed under Contract 1.
- Task statement "keeps mechanism in place": fix item 2 as worded (coalesce in the reader) leaves `fork_attribution.dispatch_fork_candidate` / `fork_of_dispatch` and `rollups._NAMED_SUBAGENT_ID` unchanged (pointer: Task Statements, third-from-last bullets).
- `fork_attribution.py` is shared by rollups and reader and hit by a 2.8k-line live test file; SQL-text assertions in `test_reader.py` pin exact CTE text, so any CTE edit breaks them mechanically.
- Existing-data correction depends on stop rows having `tool_use_id`; local DB shows 136/141 without (older CLI). Speculative: sessions from CLIs that did not send it stay on the parent-window fallback.
- Sibling-window fallback is unbounded for the last dispatch under a parent (`reader.py:1639-1641`) and is the path that produced the ticket's wrong numbers; it also serves skills and unresolved agents.
- Foreground result shape is unverified locally; the exact position of `agentId` in the PostToolUse `tool_response` (object vs string, key order) decides whether the cut drops it, and a CLI resolver written against the async shape could miss it.
- Duration semantics: reader derives from timestamps (`stop.ts - launch_ts`), the ticket expects the event's `duration_ms`; SendMessage continuation (`idle` CTE) adjusts end time. Foreground stop precedes the Agent end (`test_reader_live_dispatches.py:423`).
- Prod CLI is 0.16.6: a CLI-only fix does not reach already-installed clients or stored data. Speculative: the backend change is the only path covering existing data.
- Hook-event dedup: new field alters `event_id` content hash for new events only (`claude-code-otlp.event-id.ts`); no resend risk found.
- Cross-repo: UI typing `duration_ms: number` vs null from the API; display already tolerates null. No UI file depends on agent ids.
- Tests/AC scope: the integration AC (CLI -> proxy -> OTLP -> backend) has no existing harness in either repo; cancelled/failed/interrupted agents: `agent.tool.error`/denied paths exist in `_ended_call`/`executed_tool_span` (`fork_attribution.py:197-232`), but no agent-status field is read by the timeline reader (status is not a dispatch column).

---

## 7. Summary for Complexity Assessment

The defect sits across two repositories and four layers. The CLI hook adapter truncates `tool_response` to 300 chars (`OtlpAgentAdapter.ts:60-69`) and only Skill results get a dedicated id field (`resolveSkillForkFields`, `tool-fields.ts:68-74`). On the backend the Agent end's id is read from that text by three independent pieces: the timeline reader's `agent_end` CTE (`reader.py:1489-1496`), the ingest/rollup dirty-day marking (`named_subagent_ids`, `_NAMED_SUBAGENT_ID`), and the fork-candidate rule (`_named_by_end`) behind agent counts and slash-command pairing. With no id, the dispatch takes the parent-span window (`reader.py:1644-1692`, open-ended for the last sibling) and duration stays NULL because `stopped` is joined by agent id (`:1689`). The stored `agent.subagent.stop` row is the only event with `agent_id` and `tool_use_id` both typed. The UI needs no change (null duration already renders as an em dash); only its `DispatchRow.duration_ms` type is narrower than the API.

Files that read or produce the Agent end's id today: CLI `OtlpAgentAdapter.ts`, `tool-fields.ts`, `claude-code-otlp.plugin.ts`; backend `reader.py`, `fork_attribution.py`, `rollups.py`, `ingestor.py`, plus the handler and two guides as documentation. The SQL is long, shared between rollups and reader, and pinned by text-level tests; the prior art (2b75e017b, 22 files) shows the same area changing at that scale. The ids already live in typed columns or `attrs` (`hook_events` schema checked in the local DB). Speculative: whether the change needs a new column or migration is for spec/plan; none is implied by this survey.

Test posture: good unit and live-PG coverage for background agents and fallback, none for a cut-off foreground result, and none for stop-by-`tool_use_id`. No CLI->backend integration harness exists locally. Key unknowns: the real foreground `tool_response` shape (unconfirmed locally; only `async_launched` data exists), how many stored stop rows carry `tool_use_id` in prod, and consistency of the three Agent-end readers.

---

## 8. External References

None named by the task as source-of-truth paths. Items the caller named, resolved:
- Backend repo `codemie` (resolved; branch `EPMCDME-16939_foreground-agent-attribution`, only `config/customer/customer-config.yaml` modified).
- CLI repo `codemie-code` (resolved; same branch name, HEAD `d001dc1`; `.codemie/codemie-cli.config.json` modified).
- UI repo `codemie-ui` (resolved; on `main`, clean).
- Commit `2b75e017b` in codemie and `1da8b85` in CLI (both resolved via `git show --stat`).
- Local data (resolved): OTLP spool `the local OTLP spool` (4 sessions); Claude jsonl under `the local Claude transcripts` (161 Agent `toolUseResult`s with agentId, all `async_launched`); Docker `postgres` container, schema `codemie_analytics` (20 Agent `tool.end`, all with a complete id; 141 stops, 5 with `tool_use_id`). Jira ticket URL not fetched (text given verbatim).
