# Technical Research

**Task**: cli analytics session timeline parallel subagent dispatch cost and tokens
**Generated**: 2026-10-05 (updated 2026-10-06 after the check round)
**Research path**: filesystem plus the local analytics PostgreSQL (`codemie_analytics` schema in the `postgres` docker container)

---

## 1. Original Context

task_context (ticket EPMCDME-15515, Bug, "[CLI Analytics][Session details] Parallel subagents show identical cost and tokens in the Timeline"):

In the session details Timeline, each subagent row (`dispatches[]` with `kind: "agent"`) shows cost and tokens. When two subagents run in parallel, both rows show exactly the same cost and tokens, although the subagents did different tasks and made a different number of API requests.

Reproduction: session `ad5d81b2-eba9-4288-a9fd-e0a524461060` (two parallel Explore subagents launched in one message). Both rows: `cost_usd 0.2499958`, `input_tokens 7526`, `output_tokens 578`, `cache_read_tokens 182059`, `cache_creation_tokens 21490`.

Acceptance criteria:
1. Each subagent dispatch shows the cost and tokens of that subagent only.
2. Two parallel subagents with different tasks show different values.
3. The details panel (click on a bar) shows the same values as the API.

Related ticket on the same code path: EPMCDME-15512 (subagent bar duration), built on top of this branch.

---

## 2. Codebase Findings

### Existing Implementations

- `PostgresCliAnalyticsReader.get_session_detail_dispatches` (`src/codemie/repository/cli_analytics/postgres/reader.py`) builds one row per Agent/Skill tool span (`t` CTE, `span_kind = TOOL`) plus slash-command skills from hook events.
- Before the fix, cost and tokens came from the `lr` CTE: the sum of `api_request` log events (`event_kind = API_REQUEST`) whose `span_id` is the dispatch's `parent_span_id`, counted from the first dispatch under that parent on (`first_start` CTE, `FILTER (WHERE after)`).
- **Root cause**: parallel siblings share one `parent_span_id` (the main-thread span that issued both Agent calls), so every sibling joined the same `lr` row and showed the parent's whole total. Verified on session `52d226aa`: both Agent tool spans have parent `eb2faca7…`.
- `LocalAnalyticsHandler._build_dispatches` (`src/codemie/service/analytics/handlers/cli_analytics_handler.py`) maps rows to the response; `_token_dict` turns `0`/`None` into `null`. No handler change is needed for cost.

### Data model facts (verified in the local analytics DB)

| Link | Source | Key |
|---|---|---|
| Agent call → agentId | `hook_events`, `event_type = 'agent.tool.end'`, `tool_name = 'Agent'` | `attrs->>'tool_response'` contains `"agentId":"<id>"` |
| agentId → subagent LLM requests | `spans`, `span_kind = LLM_REQUEST (4)` | `attrs->>'agent_id'`; main-thread requests have no `agent_id` |
| LLM requests → api_request events | `log_events`, `event_kind = API_REQUEST (1)` | `log_events.span_id = llm_request.parent_span_id` (one grouping span per subagent) |

Session `52d226aa`: agent `a824b34b…` → group `9d8a1c85…` (6 llm_request, 6 api_request, $0.1317482); agent `a981168…` → group `ce337fd7…` (4 / 4, $0.0631483); main-thread groups carry no `agent_id`. The sum of all 19 api_request events equals the session total ($0.4136593).

### Architecture and Layers Affected

- Repository: reader SQL only (`get_session_detail_dispatches`).
- Service and REST model: unchanged for this ticket (EPMCDME-15512 changes them for duration).

### Integration Points

- `GET /v1/analytics/cli-analytics/sessions/{trace_id}` (`src/codemie/rest_api/routers/cli_analytics.py`) → handler → reader.
- UI `codemie-ui` renders `dispatches[]` directly; no UI change is needed for cost.

### Patterns and Conventions

- Raw SQL in f-strings with int constants interpolated (`_API_REQUEST`, `_LLM_REQUEST`) and the session id bound as `$sid`.
- Missing numbers are `null`, never `0` (`.ai-run/guides/integration/cli-analytics-events.md`).

---

## 3. Documentation Findings

- `.ai-run/guides/integration/cli-analytics-events.md`: `agent_id` is "the subagent that made the request; `""` in the main thread" on API request records. This is the field the ID chain relies on.
- `.ai-run/guides/integration/cli-analytics-storage.md`: `subagent_invocations` / `session_usage.agent_id` exist but are recomputed aggregates; the session-detail path reads raw `spans`/`log_events`/`hook_events`, so the chain is built there.

---

## 4. Testing Landscape

- SQL-shape tests: `tests/codemie/repository/cli_analytics/postgres/test_reader.py` (records the SQL with `_SqlRecorder`).
- Behavioural tests: `tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py` against a real analytics Postgres, skipped when unreachable.
- Handler tests: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`.

---

## 5. Configuration and Environment

- `TEST_LOCAL_PG_URL` points the live tests at a real analytics Postgres (default `postgresql://postgres:password@localhost:5432/postgres`).
- On this workstation a host PostgreSQL also listens on `5432`, so the default URL fails authentication and the live tests skip silently. Workaround used: a temporary `alpine/socat` container on `codemie-dev_codemie-network` forwarding `localhost:55432` to `postgres:5432`.

---

## 6. Risk Indicators

- **An even split is wrong by construction**: the first fix (commit `d09b06bb0`) divided the parent total by the sibling count. Siblings with different work still showed equal values, so AC2 was not met. Replaced.
- **Time windows are ambiguous**: the second attempt (`6bf71fc66`) attributed requests by time window per subagent; overlapping parallel windows cannot be separated by time. Replaced by the ID chain (`dfa13ea0e`).
- **Fallback to the parent span** (`coalesce(agent_usage, parent_usage)`) leaks main-thread cost onto a subagent when its agentId is known but it has no api_request group (review finding CR-001).
- **Dropped `first_start` filter**: the rewrite removed the "from the first dispatch on" boundary, which also changed skill rows (CR-002).
- **Live tests can pass by accident**: the orphan test seeded a hook event without `tool_name = 'Agent'` and an empty parent span, so it never exercised the chain-without-events case.

---

## 7. Summary for Complexity Assessment

One SQL query in one repository file, plus SQL-shape and live tests. The novel part is the agentId ID chain across three tables (hook event → llm_request span → api_request group); everything else follows the existing CTE style. Main risks are the attribution fallback rules and live tests that skip without a reachable database.
