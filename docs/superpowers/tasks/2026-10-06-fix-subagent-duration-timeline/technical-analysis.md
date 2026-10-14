# Technical Research

**Task**: cli analytics session timeline subagent dispatch duration
**Generated**: 2026-10-06
**Research path**: filesystem (codegraph MCP not available; `mcp__codegraph__search` returned tool-not-found)

---

## 1. Original Context

task_context (verbatim ticket EPMCDME-15512, "[CLI Analytics][Session details] Timeline shows subagent duration in milliseconds (3–6 ms) instead of the real run time (20–63 s)"):

Expected result: On the session details page, Timeline shows one row per subagent (dispatches[] with kind "agent"). start_offset_ms is correct, but duration_ms is wrong: it shows 3–6 ms instead of the actual 20–65 s of subagent work. Parallel subagents therefore appear as dots and their overlap is invisible.

Acceptance criteria:
1. For each subagent, duration_ms ≈ actual run time (±~1 s).
2. For each subagent, start_offset_ms + duration_ms <= session duration.
3. Two parallel subagents render as overlapping bars; two sequential subagents do not overlap.
4. Detail panel on bar click shows the same duration as the API.

Reproduction: session ad5d81b2-eba9-4288-a9fd-e0a524461060 (two parallel Explore subagents; API durations 6 ms and 3 ms, expected ~64500 ms and ~34200 ms) and 9c4725f2-954e-4463-bc88-5a97608ece42 (sequential; 5 ms vs ~20600 ms). Subagent end = SubagentHandback of the same subagent.

Repos: backend `codemie` (branch EPMCDME-15512_fix-subagent-duration-timeline, this checkout; prior commits 6bf71fc66 "track by time window each subagent", dfa13ea0e "use id as a chain betweeen data", 4716946f1 "code review and additional fixes" — read them to understand what was already tried), UI `codemie-ui` if present locally, and CLI `codemie-code` plugin/OTel emission if relevant (analytics events: CLI analytics storage / dispatches computation).

Run-level inputs: feature_area `cli analytics session timeline subagent dispatch duration`.

---

## 2. Codebase Findings

Sibling repos present: `C:/Users/OlehPrusak/codemie-dev/codemie-ui`, `C:/Users/OlehPrusak/codemie-dev/codemie-code`. The UI and CLI repos were not researched in depth in this pass (see Risk Indicators).

### Existing Implementations

- `src/codemie/repository/cli_analytics/postgres/reader.py` — `CliAnalyticsReader.get_session_detail_dispatches` (lines ~626–715). **Primary duration computation for dispatches.**
  - CTE `t` (lines ~640–644): Agent/skill tool spans (`subagent_type <> ''`).
  - CTE `ex` (lines ~645–648): TOOL_EXECUTION span duration keyed by `tool_use_id`.
  - CTE `ia` (lines ~649–652): INTERACTION span.
  - CTE `launched` (lines ~653–659): `agentId` parsed from the `agent.tool.end` hook event's `tool_response`.
  - CTEs `agent_usage` / `parent_usage` (lines ~660–688): cost and tokens.
  - Line ~691: `duration_ms = ex.duration_ns / 1e6` (coalesced to 0). **This is the suspected root cause for agent rows.**
  - Lines ~693–694: `real_duration_ms` from the INTERACTION span. Used only for skills and commands.
- `src/codemie/service/analytics/handlers/cli_analytics_handler.py` — `LocalAnalyticsHandler`.
  - `get_session_detail` calls the reader (line ~620) and `_build_dispatches` (line ~668).
  - `_build_dispatches` (lines ~715–770): prepends a synthetic "session" row (offset 0, duration = session duration). Per row: `start_offset_ms = span_start - session_start` (line ~752); `duration_ms = real_duration_ms if kind in (skill, command) else duration_ms` (lines ~758–760). **Agent rows therefore keep the tool-execution duration.**
  - `_dispatch_label_and_kind` (lines ~137–153): kind `agent` when `subagent_type` is set; label `agent · X` or `agent · X #n`.
- `src/codemie/repository/cli_analytics/postgres/rollups.py` — span kinds `_TOOL`, `_EXEC`, `_INTERACTION` (line ~154); `agent.tool.end` / `agent.tool.start` referenced at lines ~859, ~1438, ~1692–1693. `postgres/dirty.py` lines ~49–50 list the same event types.
- `src/codemie/repository/cli_analytics/postgres/otlp.py` — `_duration_ns(start, end)` (line ~392) clamps spans longer than 30 days to 0; line ~730 sets `duration_ns` from OTLP start/end.
- `src/codemie/repository/cli_analytics/vocabulary.py` — event names `agent.tool.start` / `agent.tool.end` (lines ~85–86); `SKILL_DISPATCH_EVENT = "agent.skill.dispatch"` (line ~96).
- `src/codemie/rest_api/models/cli_analytics.py` — dispatch response model; `start_offset_ms` field at line ~233.
- Subagent usage path (not used for dispatch timing): `agent.subagent.usage` events land in `hook_events` and feed `subagent_invocations` (see Section 3).
- No references to `SubagentHandback` anywhere under `src/codemie` (grep hit count zero). The subagent end is not read for timing.

Entry point for the bug: `GET` session detail → `LocalAnalyticsHandler.get_session_detail` → `CliAnalyticsReader.get_session_detail_dispatches` → `_build_dispatches`.

### Architecture and Layers Affected

- **Repository (raw SQL)**: `reader.py` — `get_session_detail_dispatches`. Reads `spans`, `hook_events`, `log_events`. Primary locus of the bug.
- **Service / handler**: `cli_analytics_handler.py` — `_build_dispatches`, `_dispatch_label_and_kind`. Secondary locus; agent branch does not override with the subagent run time.
- **DB persistence / rollups**: `rollups.py`, `dirty.py`, `subagent_invocations` table. Potential alternative source of truth for subagent run time (`duration_ms`, `started_at`, `ended_at`).
- **Ingest**: `otlp.py` (span duration), hook-event ingest path (`agent.tool.end`, `agent.subagent.usage`).
- **REST**: `dispatches[]` response (`label`, `kind`, `start_offset_ms`, `duration_ms`, token/cost dict).
- **UI / CLI**: not inspected; `codemie-ui` renders the timeline bars and detail panel; `codemie-code` emits hook events.

### Integration Points

- Internal: reader ↔ handler ↔ REST model; reader SQL joins `spans` (TOOL, TOOL_EXECUTION, INTERACTION, LLM_REQUEST, API_REQUEST), `hook_events` (`agent.tool.end`), `log_events` (cost/tokens) by `tool_use_id` and `agentId`.
- External: none at the dispatch computation layer. Postgres (dedicated analytics DB, `CLI_ANALYTICS_PG_URL`). OTLP ingest from the CLI plugin.
- Hook contract: `agent.subagent.usage` carries `started_at`, `ended_at`, `duration_ms = ended_at - started_at`, keyed by `tool_use_id` and `agent_id`. `ended_at` is the last transcript line, which may or may not equal the SubagentHandback timestamp.

### Patterns and Conventions

- Dispatch rows are derived from span joins, not from event pairing. Timing is taken from span durations (`duration_ns`), not from start/end event timestamps.
- Skills and commands already use the correct pattern: `real_duration_ms` from the INTERACTION span (`t.span_start` to its end). Agents do not.
- Identity chain for agents: Agent tool span `tool_use_id` → hook `agent.tool.end` `tool_response.agentId` → `llm_request` span with `agent_id` attribute → cost/tokens.
- Contract rules (from the events guide): `event_id` identity, first-version-wins re-sends (same UTC day, 14 days), deterministic `timestamp`, missing numbers are `null` never `0`.
- Storage guide: `subagent_invocations` keyed `(session_id, agent_id)`, latest `agent.subagent.usage` wins, `attribution` = `hook` or `heuristic`, `duration_ms` NULL when end < start.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/cli-analytics-events.md` — event contract. `agent.subagent.usage` section (lines ~120–201). Defines `duration_ms = ended_at − started_at` (lines ~139–141, ~188).
- `.ai-run/guides/integration/cli-analytics-storage.md` — storage and recalculation. `subagent_invocations` table semantics; subagent totals become `session_usage` rows only when no OTel `api_request` exists for the session. Known limitation: a session known only from hooks shows no tools in session detail.
- `docs/superpowers/tasks/2026-08-13-epmcdme-13580-session-deep-dive/spec-activity.md` (lines ~8–19) — earlier spec for a per-subagent-dispatch duration bar chart; notes `duration_ms: null` for a session that ended mid-dispatch. Related context only.
- `docs/superpowers/tasks/2026-10-06-fix-subagent-duration-timeline/` — contains only `.state.json` (flow sdlc-light, phase main). No spec, plan, or notes yet.

### Architectural Decisions

- No ADR or decision log for this topic found in `docs/`.
- Recorded contract rules (events and storage guides) are the only decisions: `event_id` identity, first-version-wins re-sends, deterministic `timestamp`, `null` not `0`, latest-wins for `subagent_invocations`, `attribution` hook/heuristic.
- Commit history (subjects only; diffs not readable with the tools available):
  - `6bf71fc66` "Change fix approach to track by time window each subagent" — per-subagent time-window tracking. Prior attempt.
  - `dfa13ea0e` "use id as a chain betweeen data" — link subagent records by id. Prior attempt.
  - `4716946f1` "Add code review and additional fixes" — branch head; no new commits on the EPMCDME-15512 branch yet.
  - `d09b06bb0` "EPMCDME-15515: Fix parallel-dispatch cost and token duplication" — parallel-dispatch cost/token attribution (already in base).

### Derived Conventions

- Timing for dispatch rows comes from span durations in the reader SQL; per-type branching (skill/command vs agent) lives in `_build_dispatches`.
- Subagent identity is carried by `tool_use_id` (Agent tool span) and `agentId` (hook `tool_response`).

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (lines ~278–306):
  - `test_skill_dispatch_uses_real_duration_ms` — asserts skill `duration_ms == real_duration_ms` (152845).
  - `test_agent_dispatch_uses_tool_execution_duration_ms` — asserts agent `duration_ms == row duration_ms` (53235), **not** `real_duration_ms` (130460). This test **codifies the suspected bug**.
  - Calls `LocalAnalyticsHandler._build_dispatches(rows, session_start, 160000, 1.0)`. Only unit coverage of dispatch duration logic. Offsets are not asserted for agent rows.
- `tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py` — live Postgres (skipped when `TEST_LOCAL_PG_URL` unreachable):
  - Seeds Agent tool span with `duration_ns = 6_000_000` (6 ms — matches the reported symptom).
  - `test_two_parallel_agents_get_their_own_exact_totals_not_an_even_split` — cost/token attribution only; no duration or overlap assertion.
  - `test_a_dispatch_without_an_llm_group_has_no_cost_rather_than_a_guess` — orphan dispatch; asserts cost NULL only.
- `tests/codemie/repository/cli_analytics/postgres/test_reader.py` — SQL text-shape tests; `real_duration_ms` only checked textually.
- `tests/codemie/rest_api/models/test_cli_analytics_models.py` — model serialization; `start_offset_ms` not checked.
- `tests/codemie/repository/cli_analytics/support/hook_event_builders.py` — shared hook-event builders.
- Not related (keyword hits only): `tests/unit/subagents/*`, `tests/codemie/service/subagents/test_builtin_subagents_registry.py`, `tests/codemie/agents/test_langgraph_multi_assistant_*` (platform subagent code, not CLI analytics).

### Testing Framework and Patterns

- pytest ^8.3.1, pytest-asyncio ^1.4.0, pytest-cov ^7.1.0, pytest-env ^1.1.3, pytest-mock ^3.14.0, pytest-httpx ^0.35.0, pytest-xdist ^3.6.1.
- `pytest.ini`: `testpaths=tests`, `pythonpath=src`, `addopts --import-mode=importlib -n 3`, `asyncio_default_fixture_loop_scope=function`, `ENV=local`, PG URL pointed at a dead port for the suite.
- Patterns: static unit tests call `_build_dispatches` with hand-built row dicts (`_dispatch_row` helper); repository mocked via `MagicMock` / `AsyncMock` per query name; live integration uses raw INSERTs (`_SPANS_SQL`, `_LOG_EVENTS_SQL`, `_HOOK_EVENTS_SQL`), `_seed_agent` builder, `_cleanup` in `finally`, session ids prefixed `test-epmcdme-15515-live-*`.

### Coverage Gaps

- No test asserts agent `duration_ms` equals subagent run time (dispatch start → SubagentHandback / `agent.subagent.usage` end). The existing test asserts the opposite.
- No test for `start_offset_ms` on agent rows.
- No sequential-subagent test (two non-overlapping dispatches).
- Parallel-subagent test asserts cost/tokens only; no overlap assertion on duration/offset windows.
- No test that a SubagentHandback or `agent.subagent.usage` end timestamp is used as the end time.
- No test for an unmatched dispatch's duration (handback absent or out of order); orphan test checks cost only.
- Live reader tests skip without Postgres; in CI without a DB, duration SQL has no executable coverage.

---

## 5. Configuration and Environment

### Environment Variables

- `CLI_ANALYTICS_PG_URL` — analytics DB URL; empty means app DB. Redacted in `to_safe_dict`.
- `CLI_ANALYTICS_PG_SCHEMA` (default `codemie_analytics`), `CLI_ANALYTICS_PG_POOL_SIZE` (8), `CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS` (30000), `CLI_ANALYTICS_PG_WORK_MEM` (32MB).
- `CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS` (1000 ms; exceeded returns 503 and the plugin retries), `CLI_ANALYTICS_PG_INGEST_STATEMENT_TIMEOUT_MS` (10000 ms).
- `CLI_ANALYTICS_RAW_RETENTION_DAYS` (90), `CLI_ANALYTICS_ROLLUP_RETENTION_DAYS` (365), `CLI_ANALYTICS_SESSION_RETENTION_DAYS` (0), `CLI_ANALYTICS_DEDUP_RETENTION_DAYS` (14).
- `CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS` (30), `CLI_ANALYTICS_ROLLUP_BATCH_SIZE` (5000), `CLI_ANALYTICS_PARTITION_PREMAKE_WEEKS` (4), `CLI_ANALYTICS_MAINTENANCE_INTERVAL_MINUTES` (60).
- `ANALYTICS_INGEST_MAX_BODY_BYTES` (5 MB), `ANALYTICS_DEFAULT_PAGE_SIZE` (20).
- `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_SERVICE_NAME` — general OTel, `src/codemie/configs/otel_config.py`. Not analytics-specific.

### Configuration Files

- `config/customer/customer-config.yaml` — `features:cliAnalytics` (CLI Analytics tab and OTEL proxy, ~line 119); `features:routingAnalytics` (~line 138); user-dimension widget toggle (~line 183).
- `src/codemie/configs/config.py` (lines ~189–221) — authoritative analytics defaults (not under `config/`).
- No config file governs session timeline or dispatch duration.

### Feature Flags and Deployment Concerns

- `features:cliAnalytics` gates the CLI Analytics tab and OTEL proxy (env `FEATURE_...`, parsed in `src/codemie/configs/customer_config.py` ~lines 133–138; enabled only when `'true'`).
- No flag changes event ingestion or dispatch timing. No env var makes the duration logic configurable.
- `deploy-templates/` and `grafana-provisioning/` have no dispatch/subagent/timeline references.
- Ingest is written directly by the API with a 1 s acquire timeout; 503s can drop or delay timing data under pool saturation.

---

## 6. Risk Indicators

- **Root cause (probable, not yet verified against live data)**: `reader.py` ~line 691 sets agent `duration_ms` from the Agent tool-execution span (`ex.duration_ns`). That span closes when the Agent tool returns its launch acknowledgement, which matches the 3–6 ms symptom. The subagent's real end is never read for timing.
- **Existing test codifies the bug**: `test_agent_dispatch_uses_tool_execution_duration_ms` asserts tool-execution duration for agents. Fixing the bug requires changing this test; it will fail under a correct fix.
- **Handler override missing for agents**: `_build_dispatches` (~lines 758–760) only substitutes `real_duration_ms` for skill/command. Agent rows fall through to the wrong value even if the reader were corrected in one place only.
- **Pairing key choice is open**: candidates are `tool_use_id` (Agent span ↔ `agent.subagent.usage`), `agent_id` (`agent.subagent.usage` ↔ `agentId` in `agent.tool.end`), or SubagentHandback timestamp. The contract says `agent.subagent.usage` `ended_at` is the last transcript line, which may differ from the handback time the ticket names. Needs confirmation against sessions ad5d81b2-… and 9c4725f2-….
- **Previous attempts left residue**: commits 6bf71fc66 (time-window tracking) and dfa13ea0e (id chaining) were not verifiable from diffs in this pass. Risk of partial, conflicting logic (time-window pairing plus id pairing) in the same path.
- **Sources of truth diverge**: `subagent_invocations` (latest-wins, `duration_ms` from `agent.subagent.usage`, NULL when end < start) versus dispatch rows (span-derived). Session detail and the subagents view may disagree.
- **Zero-clamping risk**: `otlp.py` `_duration_ns` clamps spans over 30 days to 0. Not the observed 3–6 ms cause, but a separate source of wrong zeros. Contract requires `null`, not `0`, for missing numbers; the reader uses `coalesce(..., 0)`.
- **Parallel-dispatch duplication history**: commit d09b06bb0 fixed cost/token duplication for parallel dispatch. Any duration fix must not reintroduce even-split or duplicated attribution.
- **Live test coverage gap**: duration behaviour is only exercised by live Postgres tests that skip without a DB; CI may not run them.
- **Sequential and unmatched cases untested**: no test for two sequential dispatches, or for a dispatch whose handback never arrives (session ended mid-dispatch; see spec-activity from 2026-08-13).
- **Requirements clarity**: the ticket states "Subagent end = SubagentHandback" but the codebase has no SubagentHandback event reference. Whether the handback is a hook event, a transcript line, or derived from `agent.subagent.usage` needs confirmation before design.
- **Not researched**: `codemie-ui` timeline rendering (overlap and bar drawing, detail-panel source) and `codemie-code` hook emission (what timestamps are sent for SubagentHandback). Acceptance criteria 3 and 4 depend on these.
- **Ticket context is short but concrete**: reproduction sessions and expected values are given; no DB access was available to confirm values.
- **Research method**: filesystem only (codegraph absent). Findings are from Explore summaries; key lines were not all re-read by the orchestrator.

---

## 7. Summary for Complexity Assessment

The fix is concentrated in the analytics read path: `CliAnalyticsReader.get_session_detail_dispatches` in `src/codemie/repository/cli_analytics/postgres/reader.py` (duration SQL, ~lines 626–715) and `LocalAnalyticsHandler._build_dispatches` in `src/codemie/service/analytics/handlers/cli_analytics_handler.py` (~lines 715–770). Likely change surface is 2 source files plus the reader's join logic, and possibly the `agent.subagent.usage` / `subagent_invocations` source if the subagent end is taken from there. Test changes touch `test_cli_analytics_handler.py` (one assertion currently encodes the bug) and `test_reader_live_dispatches.py` (new duration and overlap assertions). If the subagent end must come from a new SubagentHandback event, the CLI plugin (`codemie-code`) and ingest path also change, which raises the score materially. Layers touched: Repository (SQL), Service (handler), REST model (unchanged shape expected), Ingest (possibly).

Technical novelty is moderate. The skill/command path already uses the correct interval pattern (`real_duration_ms` from the INTERACTION span), so the agent path can follow an established precedent. The novel element is the subagent end-time source and the pairing key, which the prior two commits attempted via time-window and id chaining without a confirmed result. Conflicting prior logic in the same path is a meaningful risk.

Test posture is mixed. Unit tests cover the handler's branching but currently assert the buggy behaviour for agents. Live Postgres tests cover parallel cost attribution and orphan cost but make no duration or overlap assertions, and they skip without a database. There is no sequential-subagent test and no test for unmatched handback. Key risk factors for scoring: unresolved source of subagent end time, conflicting prior attempts, two sources of truth (dispatch rows versus `subagent_invocations`), the UI/CLI being unexamined, and reliance on skipped live tests for verification.
