# Technical Research

**Task**: cli analytics slash commands postgres
**Generated**: 2026-10-06T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Bug fix: CLI Analytics 'Slash commands' chart includes non-slash-command telemetry (Claude Code built-ins). The CLI Analytics Tools & Models chart labelled 'Slash commands' includes built-in lifecycle/configuration commands such as model and quit/exit. Reproduction used /compact twice, /model once, and /exit; the chart showed compact=2, model=1, exit=1. Expected: count only actual slash-command usage; compact=2 and model/exit/quit must not appear. Raw OTel telemetry reports these through claude_code.skill_activated / command_name, while backend analytics currently counts every user_prompt event with non-empty command_name. Relevant code is under src/codemie/repository/cli_analytics/postgres, especially reader.py and rollups.py; relevant tests include tests/codemie/repository/cli_analytics/postgres/test_reader.py. The UI currently only renders the returned slash_commands data and should be changed only if research proves it necessary.

---

## 2. Codebase Findings

### Existing Implementations

- `/Users/Anton_Rubanovskii/Projects/EPM-SDAI/CDME/codemie/src/codemie/repository/cli_analytics/postgres/reader.py` — `PostgresCliAnalyticsReader`: all fact queries. The `get_invocations` method (lines 418–441) is the primary query powering the Slash commands chart. It returns rows of `(kind, name, count)` where kind 4 = slash commands.
- `/Users/Anton_Rubanovskii/Projects/EPM-SDAI/CDME/codemie/src/codemie/repository/cli_analytics/postgres/rollups.py` — `RollupRefresher`: maintains `invocations_hourly`. Lines 1536–1593 write kind 4 rows by selecting `log_events` rows where `event_kind = EventKind.USER_PROMPT (2)` and `command_name <> ''`.
- `/Users/Anton_Rubanovskii/Projects/EPM-SDAI/CDME/codemie/src/codemie/repository/cli_analytics/vocabulary.py` — canonical event/span kinds; `SENTINEL_EXACT` tuple (lines 139–140) already enumerates built-in command names: `('/clear', '/resume', '/compact', '/exit', '/quit')` — used only for first-prompt filtering today.
- `/Users/Anton_Rubanovskii/Projects/EPM-SDAI/CDME/codemie/src/codemie/repository/cli_analytics/ports.py` — `CliAnalyticsReader` Protocol; `get_invocations` is declared there.
- `/Users/Anton_Rubanovskii/Projects/EPM-SDAI/CDME/codemie/src/codemie/repository/cli_analytics/postgres/otlp.py` — OTel log-record decoder; `command_name` is lifted from the `"command_name"` attribute (line 676). The `event_kind` is derived from `event_name` using `EVENT_KIND.get(event_name, EventKind.OTHER)` (line 668).

### The Bug — Precise Location

The rollup INSERT into `invocations_hourly` for kind 4 (lines 1590–1593 of `rollups.py`) is:

```sql
SELECT date_trunc('hour', l.ts, 'UTC'), k.session_id, 4, l.command_name, count(*), 0
FROM log_events l ...
WHERE k.kinds & {_LOG} <> 0 AND l.event_kind = {USER_PROMPT} AND l.command_name <> ''
```

Claude Code emits OTel log records with `event_type = claude_code.skill_activated` (mapped to `EventKind.SKILL_ACTIVATED = 3`) **and separately** `event_type = user_prompt` (mapped to `EventKind.USER_PROMPT = 2`). Built-in lifecycle commands (`/model`, `/exit`, `/quit`) generate `user_prompt` records with a non-empty `command_name`; the rollup picks these up indiscriminately alongside real plugin slash commands.

The `agent.session.summary` path for sessions **with** a summary stores `commands` from `session_dims.commands`. The contract (`cli-analytics-events.md`, convention 9) states that command names carry no leading slash. The `SENTINEL_EXACT` tuple in `vocabulary.py` stores them **with** a leading slash (e.g. `'/exit'`) for the first-prompt filter, but the built-in names that land in `invocations_hourly` are stored without slash by the OTel sender (matching convention 9): `exit`, `model`, `quit`, `compact`.

### Architecture and Layers Affected

- **Rollup layer** (`rollups.py`): the INSERT that writes kind-4 rows to `invocations_hourly` is the data source for the bug. This is where filtering must be applied.
- **Reader layer** (`reader.py`): `get_invocations` reads `invocations_hourly` kind 4 for sessions without a summary, and `session_dims.commands` for sessions with a summary. If the rollup is fixed, new rollup slices will be clean; historical rows remain dirty until backfilled.
- **Vocabulary layer** (`vocabulary.py`): `SENTINEL_EXACT` already names `/compact`, `/exit`, `/quit`, `/clear`, `/resume` with leading slashes. A companion constant without leading slashes (matching what OTel stores) would be the natural home for the built-in name list consumed by the rollup.
- **Summary / `session_dims.commands`**: for sessions with a summary, the commands come from `session_dims.commands`. If the client sends `/model`, `/exit`, `/quit` in the `commands` array of `agent.session.summary`, those values would also appear. The contract spec says `commands` is "slash commands"; whether the client filters built-ins before building that array is an open question that research cannot verify from server code alone.

### Integration Points

- `invocations_hourly` — rollup table; read by `get_invocations` and `get_tool_usage`; written by `rollups.py`.
- `log_events` — raw OTel log records; `event_kind` and `command_name` columns are the source.
- `session_dims.commands` — summary path; separate branch in `get_invocations`.
- `vocabulary.py` `SENTINEL_EXACT` — existing sentinel list for first-prompt filtering; overlaps conceptually with the set of built-in command names.
- Backfill (`postgres/backfill.py`) — already supports replaying rollup families for a date range; historical kind-4 rows would need a backfill run to become clean after the rollup fix.

### Patterns and Conventions

- Rollup statements are plain SQL strings in a list (`RECOMPUTE`) in `rollups.py`; the kind-4 INSERT is one element of that list, built at module import time using f-string interpolation of vocabulary constants.
- Sentinel/exclusion lists live in `vocabulary.py` as module-level tuples; the rollup `_PROMPT_OK` predicate uses `_text_array(SENTINEL_EXACT)` which converts the tuple to a SQL `ARRAY[...]` literal. The same pattern is available for a built-in command filter.
- No filter is applied at the reader level — filtering is always baked into rollup time, not query time.
- The `_no_summary` helper in `reader.py` (line 60) and the three-branch UNION in `get_invocations` (lines 428–440) show the design: one authoritative source per session, chosen by whether `session_dims.summary_ts IS NOT NULL`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `/Users/Anton_Rubanovskii/Projects/EPM-SDAI/CDME/codemie/.ai-run/guides/integration/cli-analytics-events.md` — event contract; names the `commands` field of `agent.session.summary` and `agent.subagent.usage`; convention 9 states command names carry no leading slash.
- `/Users/Anton_Rubanovskii/Projects/EPM-SDAI/CDME/codemie/.ai-run/guides/integration/cli-analytics-storage.md` — storage layout, window semantics, rollup family table, environment variables.

### Architectural Decisions

- Design decision (storage guide): "A session with a summary counts its slash commands from `commands` alone." The OTel branch of `get_invocations` (kind 4) is the fallback for sessions that never sent a summary.
- `cli-analytics-events.md` convention 9: "Command names (`commands`, `primary_command`, `command_name`) carry no leading slash." This means both paths (summary `commands` and OTel `command_name`) store names like `model`, `exit`, not `/model`, `/exit`.
- The comment at `rollups.py` line 1536: "Hourly invocations: kinds 1–3 come from spans (hooks on a fallback pair), kind 4 (slash commands) from user prompts." The intent was slash commands only; the WHERE clause does not enforce that intent.

### Derived Conventions

- Exclusion lists for known sentinel values are kept as tuples in `vocabulary.py` and converted to SQL array literals via a helper (`_text_array`) inside `rollups.py`. Adding a built-in command list there follows the established pattern.
- Rollup SQL is constructed at module import from vocabulary constants; there is no runtime configuration path for this filtering.

---

## 4. Testing Landscape

### Existing Coverage

- `/Users/Anton_Rubanovskii/Projects/EPM-SDAI/CDME/codemie/tests/codemie/repository/cli_analytics/postgres/test_reader.py` — covers `get_invocations` structural tests: three branches are scoped to `sel`, branch 2 excludes sessions with a summary, branch 3 (summary) only counts non-empty string elements. Tests inspect the SQL text via `_SqlRecorder`.
- `/Users/Anton_Rubanovskii/Projects/EPM-SDAI/CDME/codemie/tests/codemie/repository/cli_analytics/postgres/test_rollups.py` — covers the rollup RECOMPUTE list, summary-only storage of `commands`, and control-flow; no test verifies that built-in command names are excluded from kind-4 rows.
- Parametrized test `test_every_fact_a_windowed_query_reads_is_scoped_to_the_selected_sessions` covers all `_WINDOWED` methods including `get_invocations` for scope correctness.

### Testing Framework and Patterns

- `pytest` + `pytest-asyncio`; tests are in `tests/codemie/repository/cli_analytics/postgres/`.
- SQL recorder pattern: `_SqlRecorder` / `_ArgRecorder` capture raw SQL strings without a DB connection; assertions inspect the SQL text.
- `_invocation_branches()` helper in `test_reader.py` (lines 524–538) extracts each UNION ALL branch of `get_invocations` as a string for targeted assertions.
- Rollup SQL is tested as static strings via assertions on `rollups.RECOMPUTE` list elements.

### Coverage Gaps

- No test verifies that built-in commands (`model`, `exit`, `quit`, `compact`, `clear`, `resume`) are excluded from the kind-4 INSERT in `rollups.py`.
- No test verifies that kind-4 rows in `invocations_hourly` written from `log_events` exclude names that appear in a built-in command list.
- No test for the summary path (`session_dims.commands`) verifying that built-in names are absent there (this may be a client-side concern, but the server reader currently has no filter on that branch either).

---

## 5. Configuration and Environment

### Environment Variables

- `CLI_ANALYTICS_PG_URL` — analytics PostgreSQL connection; empty = application DB.
- `CLI_ANALYTICS_PG_SCHEMA` — table schema (`codemie_analytics`).
- `CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS` — freshness interval (default 30 s).
- `CLI_ANALYTICS_RAW_RETENTION_DAYS` — raw log_events retention (default 90 days). Historical kind-4 rows older than retention cannot be backfilled.

### Configuration Files

- No feature flags or toggles govern slash-command filtering; the rollup SQL is static.

### Feature Flags and Deployment Concerns

- Fixing the rollup INSERT cleans only future rollup slices. Historical `invocations_hourly` rows for kind 4 that already include built-in names will persist until a backfill is run. The backfill command (`postgres/backfill.py`) supports replaying rollup families over a date range and is the standard mechanism for this.
- Raw `log_events` beyond `CLI_ANALYTICS_RAW_RETENTION_DAYS` cannot be backfilled.

---

## 6. Risk Indicators

- **Historical data drift**: fixing the rollup INSERT does not retroactively clean existing `invocations_hourly` kind-4 rows. A backfill run is required for historical accuracy. The scope of raw retention (`CLI_ANALYTICS_RAW_RETENTION_DAYS`, default 90 days) limits how far back a backfill can reach.
- **Summary path unverified**: for sessions with `session_dims.summary_ts IS NOT NULL`, slash commands come from `session_dims.commands`. Whether the Claude Code client sends lifecycle/built-in commands in that array is not verifiable from server code alone. If it does, the reader's summary branch would also need filtering, but this cannot be confirmed without client-side telemetry samples.
- **Vocabulary constant alignment**: `SENTINEL_EXACT` stores names with a leading slash (`/exit`, `/quit`, etc.) for first-prompt use; the rollup must filter names without leading slash (`exit`, `quit`), since convention 9 guarantees no leading slash in `command_name`. Mixing these up would either over-filter or fail to filter.
- **`_text_array` helper scope**: the helper converting a Python tuple to a SQL `ARRAY[...]` literal lives inside `rollups.py`, not `vocabulary.py`. Adding a new tuple to `vocabulary.py` and consuming it through `_text_array` in the rollup follows the pattern already set by `SENTINEL_EXACT` and `_PROMPT_OK`, but the helper is module-private; this is not a blocker, just a convention to follow exactly.
- **No live DB integration tests** for this rollup family: the test suite verifies SQL text structure (recorder pattern) and static rollup statement membership, not actual query results against a schema. A regression could pass tests while silently introducing a SQL error.
- **Speculative**: if `/model` is not in `SENTINEL_EXACT` (it is not — that constant only has `/clear`, `/resume`, `/compact`, `/exit`, `/quit`), a separate or extended built-in list must be defined. The task's bug report confirms `model` appears in the chart, so `model` must be added to any filter list.

---

## 7. Summary for Complexity Assessment

The bug is narrow and well-localised. The rollup INSERT for `invocations_hourly` kind 4 in `rollups.py` (line 1590–1593) selects all `user_prompt` log events with a non-empty `command_name`, without excluding built-in lifecycle command names. The fix is a single SQL predicate addition using a vocabulary constant — the same pattern already used by `_PROMPT_OK` and `SENTINEL_EXACT` for first-prompt filtering. One new tuple of built-in names belongs in `vocabulary.py` (without leading slashes, per convention 9); the rollup statement references it through `_text_array`. No reader-layer change is needed for the OTel branch, as the reader reads what the rollup writes.

Test coverage exists for the structural shape of `get_invocations` (three scoped UNION ALL branches, correct session-scope, no own window) but not for the absence of built-in names in kind-4 rows. Two test files are in scope for new tests: `test_rollups.py` (assert the built-in exclusion predicate is present in the INSERT statement) and `test_reader.py` (assert via the `_invocation_branches` helper that the kind-4 OTel branch excludes the built-in list). The testing patterns (SQL text assertions, `_invocation_branches` helper, vocabulary constant inspection) are already established and straightforward to extend.

The main residual risk is historical data: fixing the rollup does not clean already-written kind-4 rows. A backfill run is the documented mechanism, bounded by raw retention (default 90 days). The summary path (`session_dims.commands`) is a second potential source if the client includes lifecycle command names there; this is not verifiable from server code and is a low-probability secondary risk noted for the spec to decide.

---

## 8. External References

None named by the task.
