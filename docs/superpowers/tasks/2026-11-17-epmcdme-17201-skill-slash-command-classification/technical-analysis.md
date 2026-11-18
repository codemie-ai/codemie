# Technical Research

**Task**: cli analytics, session detail, dispatch kind, tools and models, slash_commands skills_invoked
**Generated**: 2026-11-17
**Research path**: filesystem

---

## 1. Original Context

User request: "https://jiraeu.epam.com/browse/EPMCDME-17201 And move it inprogress btw"

Resolved ticket EPMCDME-17201 (Bug): [CLI Analytics][Session Detail / Tools & Models] A skill run as a slash command is shown as a "command" and listed under both Skills and Slash Commands

Steps to reproduce / actual result:
- Session 7d61abd7: skill `sdlc-factory:sdlc-standard` recorded as started via slash command `/sdlc-factory:sdlc-standard`, no Skill tool call. Session Detail: Skills = 1, but the timeline shows "command · sdlc-factory:sdlc-standard" with kind `command` and no skill bar.
- Tools & Models (GET /v1/analytics/cli-analytics/tools): `sdlc-factory:sdlc-standard` appears in `skills_invoked` (1) AND `slash_commands` (1).
- Same-plugin skills started via the Skill tool show as "skill · <name>" — classification depends on how the skill was started, not what was run.

Root cause per ticket (verify against current source):
- `cli_analytics_handler.py` ~lines 755-772, `_dispatch_label_and_kind()`: a row with `skill_name` and `is_slash_command` returns ("command · {skill}", "command"). Introduced in fa1bfc246 (EPMCDME-14100).
- `reader.py` ~line 3191 ff.: dispatch query models these rows as "slash-command skills".
- `reader.py` ~lines 1855-1863 and handler ~line 1863: Tools & Models takes skills from the hourly invocation rollup and slash commands from the session's commands without excluding commands that are skills, so a slash-started skill is counted in both.

Acceptance criteria:
1. A skill started with a slash command is shown with kind `skill` in Session Detail.
2. Session Detail Skills count and timeline skill bars agree for the same session.
3. Tools & Models lists a slash-started skill under Skills only (or under both if product decides, with rule documented/labelled in UI).
4. Plain slash commands that are not skills stay classified as commands.
5. Cost, token, duration attribution of slash-started skills (EPMCDME-16392, EPMCDME-15968) is unchanged.

Regression tests requested: backend unit `_dispatch_label_and_kind()` (slash-command skill row -> kind `skill`; plain command stays `command`; agent rows unchanged); backend integration (session with slash-started skill + Skill-tool skill, both appear as skills, `skill_count` matches); backend integration /tools (slash-started skill not duplicated under `slash_commands`); UI timeline skill bar (UI is out of this backend repo — note whether anything backend-side drives it).

Related: EPMCDME-16392, EPMCDME-15968, EPMCDME-14100.

Repo is the CodeMie backend (Python/FastAPI) at C:\Users\OlehPrusak\codemie-dev\codemie. Start with the guides in .ai-run/guides/ (see AGENTS.md); relevant docs include .ai-run/guides/integration/cli-analytics-events.md and cli-analytics-storage.md.

---

## 2. Codebase Findings

Note: line numbers in the ticket are stale. All line numbers below come from current source on branch `EPMCDME-17201_skill-slash-command-classification`.

### Existing Implementations

Handler (service layer) — `src/codemie/service/analytics/handlers/cli_analytics_handler.py`
- `:452-468` `_dispatch_label_and_kind(row, type_totals, counters)` — Session Detail bug site. Agent branch (keyed on `subagent_type`) runs first and returns `agent · X` / `agent · X #n`, kind `agent`. Then:
  ```python
  skill = _s(row.get("skill_name"))
  if skill:
      if row.get("is_slash_command"):
          return f"command · {skill}", "command"
      return f"skill · {skill}", "skill"
  ```
- `:1171-1223` `_build_dispatches` — emits synthetic row 0 (`kind: session`), calls `_count_dispatch_subtypes` (`:431`), `_dispatch_label_and_kind` (`:1203`), `_bar_position(row, kind, ...)` (`:1207`).
- `:117-146` `_duration_for_kind` / `_bar_position` — `kind in ("skill","command")` both use `real_duration_ms`; `agent` uses `subagent_duration_ms`. Re-kinding slash-skill rows to `skill` leaves duration and clamping unchanged (protects AC5).
- `:1110-1146` session-detail dict; `:1137` `"skill_count": _opt_int(scalars.get("skill_count"))`.
- `:1245-1303` `get_tools` — `invocation_rows = await self._repo.get_invocations(f)`; builds `skills_invoked` / `agent_subtypes` / `slash_commands` via `_top_invocations(rows, "skill"|"agent"|"command", ...)`. `_top_invocations` (`:1291-1303`) only filters on `r["kind"] == kind` and sorts `(-count, name)`; no cross-kind exclusion. Also reads `get_class_lists(f, "invocation")` keyed `(kind, name)`.

Reader (repository layer) — `src/codemie/repository/cli_analytics/postgres/reader.py`
- `:1122-1150` `get_invocations` — Tools & Models bug site. `CASE kind WHEN 2 THEN 'skill' WHEN 3 THEN 'agent' ELSE 'command' END` over a 3-arm UNION ALL: kind 2/3 from `invocations_hourly`; kind 4 from `invocations_hourly` when `_no_summary(...)` and `name <> ALL($builtin_commands)`; kind 4 from `session_dims.commands` (jsonb array) when `summary_ts IS NOT NULL`. Nothing removes a kind-4 name that is also a kind-2 skill.
- `:773-805` `get_class_lists(f, by)` `"invocation"` variant (`:787-800`) — repeats the same kind 2/3/4 UNION and CASE; any dedupe in `get_invocations` must be mirrored here.
- `:333-346` comment + `_no_summary()` — one slash-command source per session.
- `:1826-2148` `get_session_detail_dispatches` — dispatch query (docstring `:1830-1857`). CTE `t` (`:1873-1881`) Agent/Skill TOOL spans; `dispatch_fork` CTE (`:1928-1949`) from `agent.skill.dispatch` hooks. Final UNION ALL arms: `:2062` Agent/Skill tool spans `0 AS is_slash_command`; `:2100-2117` skill-tagged `agent.subagent.start` fork without initiator, literal `1`; `:2122-2140` `dispatch_fork` slash dispatch, literal `1`. `ORDER BY dispatch_start, is_slash_command, subagent_type COLLATE "C", skill_name COLLATE "C"` at `:2143`.
- `:1664-1728` `get_session_detail_scalars`; `skill_count` at `:1718-1725` already adds `agent.skill.dispatch` hooks not in kind-2 names. This is why Skills = 1 but no skill bar.
- `:1152-1168` `get_skill_names_by_session` (uses `CLASSIFIER_NAMES_SQL`).

Rollups — `src/codemie/repository/cli_analytics/postgres/rollups.py`
- `:1781` `_IS_DISPATCH`; `:1812-1815` `_COUNTED_SKILL_HOOK` counts a dispatch as a skill invocation.
- `:1909-1915` `tool_facts_daily.skill_count` (hook-fallback arm).
- `:1978-2055` `invocations_hourly` INSERT: kind 1 tool, 2 skill, 3 agent, 4 slash command. Kind 2 via spans (`:2020`) + hook arm (`:2027-2030`); kind 4 (`:2046-2053`) from `log_events.command_name` excluding `BUILTIN_COMMAND_NAMES`.
- `:2058-2063` `session_skills` from OTel `skill_activated`.

Other
- `src/codemie/repository/cli_analytics/vocabulary.py:97` `SKILL_DISPATCH_EVENT = "agent.skill.dispatch"`; `:218` `BUILTIN_COMMAND_NAMES = ("model","exit","quit")`.
- `.../postgres/session_derived.py:31-85` classifier names SQL; comment at `:34` says a slash-invoked plugin skill lands in summary `commands`, not `skills`.
- `.../postgres/inline_skill_attribution.py` — inline (no-fork) slash-skill cost attribution (EPMCDME-16392). Do not touch.
- `.../postgres/fork_attribution.py` — fork pairing helpers.
- `.../postgres/otlp.py:119,709` sets `command_name`.
- `.../ports.py:339` declares `get_invocations` on the `CliAnalyticsReader` Protocol.
- `src/codemie/rest_api/routers/cli_analytics.py:649-666` `GET /tools` — thin; calls `_handler().get_tools(f)`.
- `src/codemie/rest_api/models/cli_analytics.py:358-367` `LocalAnalyticsDispatchRow.kind: str` (free string; description `'session' | 'agent' | 'skill' | 'command'`); `:374` `skill_count`; `:495-505` `LocalAnalyticsInvocationRow` / `LocalAnalyticsToolsData`.

Root-cause verification
- Session Detail claim: confirmed (handler `:465-466`), ticket line numbers wrong. `is_slash_command` is hard-coded `1` in reader arms 2 and 3, so every `is_slash_command=1` row is by construction a skill (from `agent.skill.dispatch` or a skill-tagged `agent.subagent.start`). Plain slash commands (e.g. `/compact`) never reach the dispatch query; they exist only in invocations kind 4 and summary `commands`. Therefore returning `skill · X` / `skill` for any row with `skill_name` satisfies AC1 and AC4 with one handler change.
- Count/bars mismatch: confirmed. `skill_count` already counts dispatch hooks; timeline rows are kind `command`.
- Tools & Models double count: confirmed at `reader.py:1122-1150` (+ `get_class_lists`), not at the ticket's lines. Kind 2 includes `agent.skill.dispatch`; kind 4 is OTel `command_name` (no-summary sessions) or summary `commands` (summary sessions), which holds slash-started skills.
- Introducing commit is `fa1bfc2466` (EPMCDME-14100), confirmed by `git log -S"command · "`.

### Architecture and Layers Affected
- Router (`rest_api/routers/cli_analytics.py`) -> Service/handler (`LocalAnalyticsHandler`, `service/analytics/handlers/cli_analytics_handler.py`) -> Repository port (`CliAnalyticsReader`) -> `PostgresCliAnalyticsReader` (`postgres/reader.py`) -> tables maintained by `postgres/rollups.py`. Response schemas in `rest_api/models/cli_analytics.py`.
- Handler is pure post-processing of reader rows; all cost/token/duration attribution is computed in SQL.
- Fix touches: Service (handler `_dispatch_label_and_kind`), Repository (reader `get_invocations`, `get_class_lists`), optionally Rollups. No DB-persistence schema change.

### Integration Points
- Flow is one-way router -> handler -> reader -> tables; no import cycle risk.
- Reader depends on `fork_attribution`, `inline_skill_attribution`, `session_derived`, `vocabulary`.
- Frontend UI (out of repo) consumes `dispatches[].kind` and `label` (Session Detail), `skill_count`, and `skills_invoked` / `slash_commands` (Tools & Models). No new backend field is required for the UI; the label prefix `command · ` becomes `skill · `. The UI may render bar colour by kind, so a UI note is needed.
- No `CLI_ANALYTICS_*` flag gates this logic.

### Patterns and Conventions
- Kind-to-name mapping is duplicated in SQL (`get_invocations` and `get_class_lists`); keep them in sync.
- Handler must not import adapter modules; new reader query goes into the port and PostgreSQL reader with a test (storage guide line 21).
- Keep `kind` values within the existing set (`session|agent|skill|command`) to avoid an OpenAPI change.
- Ordering tie-break `is_slash_command` in dispatch `ORDER BY` (design D11) must be preserved; do not change SQL flag semantics unless the tie-break is kept.
- Cost columns of the final UNION arm must stay byte-identical (AC5; EPMCDME-16392 froze the dispatch SQL bound parameters).

Candidate fix options
- Session Detail: change `_dispatch_label_and_kind` so any row with `skill_name` returns `skill · {skill}`, `skill` (handler-only; the `is_slash_command` flag stays for ordering).
- Tools & Models (in order of preference): (a) SQL anti-join in `get_invocations` dropping kind-4 names that also have a kind-2 / skill record for the same session (skill-name sources: kind-2 rows, `session_skills`, summary `skills` keys); (b) mirror in `get_class_lists("invocation")`; (c) handler-level global subtraction of skill names from `slash_commands` (simplest but global, could hide a plain command that shares a name with a skill in another session).
- Count semantics differ between sources (kind 2 counts calls; summary `commands` counts one per element).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/cli-analytics-events.md` — `agent.skill.dispatch` section (lines 405-431), rules 8/9 (lines 101-102), storage table (line 114). Key rules: "One invocation of a skill counts once, by its initiator"; timeline shows one row per invocation with the fork's run and cost; "a Skill call without a `TOOL` span has no row, so its fork is its own `command` row when it carries `skill_name`" (this sentence documents the old behaviour and will need updating). Cost rule: cost of API requests under the span named by `agent_id`, "never both". Command names carry no leading slash. The `commands` field contract (lines 271, 344) does not say whether it includes slash-started skills.
- `.ai-run/guides/integration/cli-analytics-storage.md` — "Slash commands (`get_invocations`, kind 4)" (lines 191-200), counter fallback (line 185), line 228 (`agents` kind 3), line 326 (`delivery_framework` classifier unions skill keys, `session_skills`, kind-2 rows and `commands`), rollup operations lines 428-429 (backfill required after changing how skills/agents are counted), line 21 (where code belongs).
- `.ai-run/guides/api/endpoint-conventions.md`, `rest-api-patterns.md`, `architecture/layered-architecture.md`, `standards/git-workflow.md` (branch `EPMCDME-12345_short-description`, commit `EPMCDME-####: Description`).
- `.ai-run/guides/testing/testing-patterns.md`, `testing-api-patterns.md`, `testing-service-patterns.md`, `local-verification.md`, `quality-gates.md`.

### Architectural Decisions
- EPMCDME-14100 (`fa1bfc2466`): introduced `command · skill` label in one commit; label origin is undocumented (design spec `docs/superpowers/specs/2026-07-20-epmcdme-13561-coding-agents-read-endpoints-design.md:159` declares `DispatchEvent.kind: 'agent'|'skill'|'command'|'tool'`).
- EPMCDME-15968 (`docs/superpowers/tasks/2026-10-21-epmcdme-15557-15576-15968/code-review-final.json`): slash-started skills count as skills and appear in `skills_invoked`; one invocation counts once; timeline is one row with the fork's cost. So skills in `skills_invoked` is intended; the `slash_commands` duplicate is the leftover.
- EPMCDME-16392 (`docs/superpowers/tasks/2026-10-30-epmcdme-16392/`, commit `4cdd610d51`): inline slash skills got their own cost/tokens/duration; generated dispatch SQL was frozen (hash is a snapshot, not a hard constraint, but cost columns must not change).
- Design `docs/superpowers/specs/2026-09-23-cli-analytics-postgres-storage-design.md` lines 154 and 642: D11 tie-break between slash dispatch and agent span at same timestamp.
- Existing task state: `docs/superpowers/tasks/2026-11-17-epmcdme-17201-skill-slash-command-classification/.state.json` (flow `sdlc-light`).

### Derived Conventions
- Comments documenting "command" kind for slash skills: `reader.py:1829-1836`, `:2095-2099` (says handler reads `real_duration_ms` for the row's "command" kind), `:2119-2121`; `vocabulary.py:96`; `rollups.py:1978-1992`, `:2038`; `session_derived.py:32-36`. These comments should be refreshed with the fix.
- No NOTE/TODO/HACK/DECISION markers in `reader.py`. No CHANGELOG entries; no docs describe the Tools & Models response beyond the two guides.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` — handler unit tests with mocked reader; `_dispatch_row` helper at `:1287`; `make_handler()` `:36`. There is no direct test of `_dispatch_label_and_kind` and no `get_tools` test here. Tests pinning kind `command` that will need updating: `:1357-1391` (`test_forked_skill_command_dispatch_uses_real_duration_ms`, `test_main_thread_slash_command_dispatch_stays_instant`, `test_a_slash_command_at_the_session_end_keeps_its_duration`, plus one more at `~1364-1392`). `skill_count: 0` fixture at `~1772`.
- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler_ordering.py:222` — `_handler(invocations=perm)` pattern; closest `get_tools` unit pattern.
- `tests/codemie/service/analytics/handlers/test_cli_analytics_class_lists.py:206` — class lists on `skills_invoked`.
- `tests/codemie/service/analytics/handlers/test_summary_handler.py:112,266,306` — unrelated KPI card.
- `tests/codemie/repository/cli_analytics/postgres/test_reader.py` — `_SqlRecorder` SQL-text asserts. `:1395-1470` `_invocation_branches` asserts exact SQL of the three `get_invocations` branches (`len(branches) == 3`); `:1279-1302`, `:1755`; `:1546-1616` dispatch SQL shape; `:1491` pins `skill_count` / `agent_count` SQL. A reader SQL change breaks these.
- `tests/codemie/repository/cli_analytics/postgres/test_rollups.py:725-835, ~1511` — rollup SQL text pins (only affected if rollups change).
- `tests/codemie/repository/cli_analytics/postgres/test_live_route_pricing.py:778-850` — live-DB end-to-end session detail; asserts labels `command · forkme`, `command · groupme`, `command · inlineme` and `kind == "command"` at `:838-844`; cost values pinned (guards EPMCDME-16392/15968). Helpers: `_hook`, `_fork`, `_dispatch`, `_stop`, `_ingest`, `_seed_inline_request`, `_refresh`, `_detail`.
- `test_reader_scope_guard.py:36`, `test_scope_4652_shapes.py:93` — dispatch scope tests.
- Router tests: `tests/codemie/rest_api/routers/test_cli_analytics_owner_sessions.py` (pattern: `_client(user)`, patch `_ensure_enabled`, `_handler`, `get_cli_analytics_storage`); no dedicated `/tools` router test.
- OpenAPI baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json` contains the `kind` description string.

### Testing Framework and Patterns
- pytest ^8.3.1, pytest-asyncio ^1.4.0, pytest-xdist (`-n 3`), pytest-mock, pytest-env; `pytest.ini`: `testpaths = tests`, `pythonpath = src`, `--import-mode=importlib`; only marker `enterprise`. No `tests/integration/` dir and no `integration` marker.
- "Integration" here means: (1) live-Postgres test gated by `TEST_LOCAL_PG_URL` (`pytestmark skipif`), run as `TEST_LOCAL_PG_URL=... poetry run pytest -n0 <file>`; (2) handler tests with mocked `CliAnalyticsReader`; (3) external `codemie-test-harness` (`make test-harness`). `make test-cli-analytics-live` also exists.
- Builders in `tests/codemie/repository/cli_analytics/support/` (`hook_event_builders.py` `skill_dispatch(**over)`, `otlp_builders.py`, `live_rollup_lock.py`, `bounded.py`, `scopes.py` with `DETAIL_SCOPE` / `EXCLUDE_SCOPE`, `contracts.py`).
- Test files carry the Apache license header; test docstrings name the Jira ticket in tests. Note the project preference: no ticket IDs in code comments (tests docstrings follow existing convention, but comments should state the reason only).
- Guide rules: mirror `src/` under `tests/codemie/`; run the narrowest scope; seam tests (per-callsite) for `_dispatch_label_and_kind` -> `_build_dispatches` -> session-detail `dispatches`; `local-verification.md` requires `make test-harness` and a live API call before an MR; regenerate OpenAPI baseline only for intended changes via `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`.

### Coverage Gaps
- No direct unit test of `_dispatch_label_and_kind` (slash-flagged skill row, agent row with slash flag, `#n` numbering, empty row -> None).
- No test mixing slash-started and Skill-tool skills and asserting `skill_count` vs dispatch kinds.
- No `/tools` test where the same name appears as both skill and command; reader tests pin SQL text only, no semantics.
- No real-DB test of `get_invocations` / kind 2 vs 4 overlap (extend `test_live_route_pricing.py` pattern with `PostgresCliAnalyticsReader.get_invocations` and `LocalAnalyticsHandler.get_tools`).
- SQL semantics are only executed by opt-in live tests; unit tests will not catch SQL logic errors in an anti-join.

---

## 5. Configuration and Environment

### Environment Variables
All in `src/codemie/configs/config.py` (~233-263, 567, 1169-1172, 1283); none gate this logic:
- `CLI_ANALYTICS_PG_URL`, `CLI_ANALYTICS_PG_SCHEMA` (`codemie_analytics`), `CLI_ANALYTICS_PG_POOL_SIZE` (8), `CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS` (30000), `CLI_ANALYTICS_PG_INGEST_STATEMENT_TIMEOUT_MS`, `CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS`, `CLI_ANALYTICS_PG_WORK_MEM` (32MB)
- `CLI_ANALYTICS_RAW_RETENTION_DAYS` (90), `CLI_ANALYTICS_ROLLUP_RETENTION_DAYS` (365), `CLI_ANALYTICS_SESSION_RETENTION_DAYS`, `CLI_ANALYTICS_SESSION_PURGE_BATCH_SIZE`, `CLI_ANALYTICS_DEDUP_RETENTION_DAYS`
- `CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS` (30), `CLI_ANALYTICS_ROLLUP_BATCH_SIZE`, `CLI_ANALYTICS_PARTITION_PREMAKE_WEEKS`, `CLI_ANALYTICS_MAINTENANCE_INTERVAL_MINUTES`, `CLI_ANALYTICS_LOG_HMAC_KEY`
- `CLI_ANALYTICS_PUBLICATION_CONTRACTS_ENABLED` (False; used in `rest_api/routers/cli_analytics.py:185`, `cli_analytics_ingest_route.py:157`)

### Configuration Files
- `src/codemie/configs/config.py` (validator `_check_cli_analytics_retention`).
- `src/codemie/repository/cli_analytics/postgres/migrations.py` (programmatic Alembic), `factory.py`, `src/codemie/service/analytics/cli_analytics_jobs.py` (rollup refresh/maintenance jobs).
- Alembic env `src/external/alembic_cli_analytics/` with revisions up to `a1c1a0000011`. No `is_slash_command` / `dispatch_kind` column or view exists; `is_slash_command` is a computed literal in the reader query only.

### Feature Flags and Deployment Concerns
- No on/off flag for reader/handler/skills/slash_commands logic. Per-project ingest gate is the DB column `applications.cli_analytics_enabled`.
- Deployment material: only `standalone/README.md` (~266-317) and `standalone/.env.standalone.example`; no `CLI_ANALYTICS` in `deploy-templates` / `docker`. Contract test `tests/codemie/standalone/test_standalone_compose_contract.py`.
- Migration need: reader SQL or handler fix is query-only, no migration and no backfill. A fix in `rollups.py` (e.g. excluding skill-backed slash commands from kind 4) needs no DDL but already-rolled-up rows keep old counts; the guide prescribes `python -m codemie.repository.cli_analytics.postgres.backfill --from <today-RAW_RETENTION_DAYS> --to <today>`. A persisted column would need a new Alembic revision (`a1c1a0000012`+); heavier than necessary.
- Per AGENTS.md, an MR touching security-relevant code or deps must ask a reviewer to post `/sanity`; this change likely does not touch those.

---

## 6. Risk Indicators

- Ticket line numbers are stale (handler 755-772 -> actual 452-468; reader 1855-1863/3191 -> actual 1122-1150 and 1826-2148). Planning must use the verified locations above.
- Rows with `is_slash_command=1` are all skills by construction, but this relies on the reader hard-coded literals (`reader.py:2100`, `:2122`); if a non-skill row is ever added to those arms, a handler-only fix would misclassify it. AC4 (plain commands stay `command`) currently has no dispatch-row path to exercise it; the plain-command case is only observable on `/tools` (kind 4) and via a handler unit test with a synthetic row lacking `skill_name`.
- Tools & Models dedupe is in SQL with duplicated logic (`get_invocations` and `get_class_lists`); the two must stay consistent or per-row class lists diverge from the lists.
- Name-collision risk: a handler-level global subtraction would hide a plain command that shares a name with a skill used in another session; a per-session SQL anti-join is more precise but needs live-DB verification.
- Count-semantics difference: kind 2 counts calls, summary `commands` counts one per element; after dedupe, a skill's count in `skills_invoked` should remain the kind-2 value (consistent with `skill_count`).
- Summary-based sessions record slash-started skills only in `commands`, not in summary `skills` (`session_derived.py:34`); sessions without any kind-2 record for the skill (hook missing) could still show a skill only under `slash_commands` if the anti-join keys solely on kind 2. Decide the skill-name source set deliberately.
- Classifier impact: `CLASSIFIER_NAMES_SQL` unions `commands` with skill names for `delivery_framework`; do not alter `commands` handling in a way that changes it.
- Exact-SQL string asserts in `test_reader.py` (`:1395-1470`) and possibly `test_rollups.py` will break on any SQL change; ordering of `is_slash_command` in `ORDER BY` (D11) must be preserved.
- AC5 safeguard: cost/token/duration live test (`test_live_route_pricing.py`) pins costs; only labels/kinds should change in that test. It needs `TEST_LOCAL_PG_URL`; without a live DB the SQL semantics are unverified.
- Docs drift: `cli-analytics-events.md` states a Skill-call-less fork "is its own `command` row"; needs updating, as do reader comments at `:1829-1836`, `:2095-2099`, `:2119-2121`.
- OpenAPI baseline: changing the `kind` field description in `rest_api/models/cli_analytics.py:360` would fail `test_openapi_matches_baseline`; avoid or regenerate deliberately.
- UI is out of repo; the frontend may colour or group bars by `kind` or match the `command · ` prefix; a cross-team note is required. AC3 offers "or under both if product decides" — product decision open; recommendation is Skills only.
- Requirements clarity: the ticket asks to "move it in progress" (Jira status transition), which is outside tech-analysis scope and left to the caller.
- No codegraph available in this environment (tool not found), so research used filesystem exploration only; no semantic call-graph was available.

---

## 7. Summary for Complexity Assessment

The task touches the Service layer (one function in `cli_analytics_handler.py`, `_dispatch_label_and_kind`) and the Repository layer (`PostgresCliAnalyticsReader.get_invocations` and its twin `get_class_lists("invocation")` in `reader.py`). No router, schema, migration or ingest change appears necessary, and the `kind` string set stays within the existing values, so the OpenAPI baseline should not change. Estimated change surface is small: 2 production files (handler, reader), plus comment/doc refresh in `reader.py` and `.ai-run/guides/integration/cli-analytics-events.md`, and roughly 4 test files (handler unit tests, reader SQL-shape tests, `test_live_route_pricing.py`, plus a new or extended `/tools` test). If the team decides to dedupe at rollup level instead, `rollups.py` changes and a backfill becomes necessary, which would raise complexity materially; the read-time approach avoids this.

Technical novelty is low for the Session Detail half: the fix follows the existing label/kind pattern and `_duration_for_kind` already treats `skill` and `command` identically, so duration and cost attribution stay unchanged. The Tools & Models half is moderately subtle: it requires a per-session anti-join across kind 2 and kind 4 sources in dense, string-pinned SQL, mirrored in two queries, and with count-semantics differences between sources. The SQL semantics cannot be validated by the default unit suite (it asserts SQL text only), so correctness depends on the opt-in live-Postgres tests gated by `TEST_LOCAL_PG_URL`.

Test coverage posture is mixed. The handler has solid mocked-reader unit coverage but no direct test of `_dispatch_label_and_kind` and no `/tools` dedupe test; the reader has many exact-string SQL assertions that will need deliberate updates; the only end-to-end session-detail test (`test_live_route_pricing.py`) pins `command` labels and costs and will need label/kind updates while its cost assertions guard AC5. Key risk factors for scoring are: stale ticket line numbers, SQL duplicated across two queries, SQL-text-only unit tests, a pending product decision on AC3, and the out-of-repo UI dependency. Overall this reads as a low-to-medium complexity bug fix with 1-2 layers affected and a medium-sized test update.
