# Technical Research

**Task**: cli-analytics delivery_framework skill names session_dims summary
**Generated**: 2026-10-30
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-16435 — STLC Factory sessions show "Pure chat" in CLI Analytics. Root cause (confirmed on a real Claude Code transcript): for sessions that have an `agent.session.summary` hook event (any Stop, Turns>=1), SKILL_NAMES_SQL (src/codemie/repository/cli_analytics/postgres/session_derived.py:37-46) takes skill names ONLY from the keys of session_dims.skills, which is the summary payload's `skills` object. The CLI (codemie-code) builds `skills` only from model `Skill` tool_use blocks; slash-invoked plugin skills such as /stlc-factory:suite-health are recorded in the summary's `commands` (and `primary_command`), never in `skills`. So skills={} and classify_delivery_framework returns "Pure chat". Sessions without a summary (Turns 0) take a union branch (session_skills ∪ sd.skills fallback from invocations_hourly kind 2 which includes agent.skill.dispatch hook names) and are classified correctly.
Required behaviour (owner decision): the classifier input is the UNION of (a) summary `skills` keys, (b) agent.skill.dispatch hook skill_name, (c) session_skills (OTel skill_activated), (d) summary `commands` entries (slash command names without leading `/`, may include builtins like `reload-plugins` and duplicates), regardless of whether a summary exists. The same SKILL_NAMES_SQL is shared by the classifier (`_READ`, session_derived.py) and `reader.get_skill_names_by_session` (reader.py ~605-618) used by the session list (cli_analytics_handler.py ~698/737) and detail (~797/895), and the usage_attribution person view (handler.py ~884/943) — all must stay consistent. First-match-wins rule order and customer-config rules unchanged. No CLI change.
OPEN IMPLEMENTATION QUESTION to resolve in research: (1) read-time — extend SKILL_NAMES_SQL with a join/subquery over hook_events (agent.skill.dispatch skill_name, and the latest agent.session.summary attrs->'commands') — cost on the session LIST path (page of sessions) and available indexes/partitioning of hook_events; versus (2) persist at rollup — add a column (e.g. session_dims.commands jsonb/text[] or a merged names column) written in the SESSION_DIMS recompute (rollups.py ~1061-1089, 1177, 1228, 1248; update_session_derived), requiring an alembic migration under the cli_analytics postgres migrations and a recompute/backfill path (python -m codemie.repository.cli_analytics.postgres.backfill) for existing sessions on Preview/prod, plus how session_dims.delivery_framework (written in rollups.py ~2165/2226, session_derived.py:60-64) gets refreshed. Recommend one with the cost/risk trade-off.
Also research: tests that cover SKILL_NAMES_SQL / get_skill_names_by_session / classify_delivery_framework / the rollup session_dims write (tests/codemie/repository/cli_analytics/postgres/, tests/codemie/service/analytics/); whether the summary `commands` attr shape is documented (.ai-run/guides/integration/cli-analytics-events.md ~224, 236, 299) and its exact JSON type (array of strings?); how `commands` is validated/stored on ingest (otlp.py hook row, attrs jsonb); the guides that apply (.ai-run/guides/integration/cli-analytics-storage.md, cli-analytics-events.md, data/database-patterns.md, standards/code-quality.md, testing/testing-patterns.md). Evidence of the repro: docs/local/work/sessions/2026-10-30-epmcdme-16435-repro/ in the main checkout /Users/vponkratina/work/ai/codemie (task3-raw, task4-raw; read-only).

---

## 2. Codebase Findings

### Existing Implementations
Names in the task differ from the code. There is no `SKILL_NAMES_SQL`; it is `CLASSIFIER_NAMES_SQL` (since commit 7578a3e36), and it already also unions the keys of `agents`.
- `src/codemie/repository/cli_analytics/postgres/session_derived.py:35-52` — `_SKILLS_OBJECT`, `_AGENTS_OBJECT`, `CLASSIFIER_NAMES_SQL`. Branch `sd.summary_ts IS NOT NULL`: keys(sd.skills) ∪ keys(sd.agents). ELSE branch: `session_skills` rows ∪ keys(sd.skills) ∪ keys(sd.agents). Empty names are dropped; result is `array_agg ... ORDER BY name COLLATE "C"`, NULL when none.
- `session_derived.py:53-58` `_READ` (uses CLASSIFIER_NAMES_SQL), `:85` `_framework` (no names -> None, column not written), `:100` `update_session_derived` (reads names, calls the injected `classify`, `_WRITE_DERIVED` only changes a row when the value differs).
- `reader.py:678` `get_skill_names_by_session` — `SELECT sd.session_id, coalesce(CLASSIFIER_NAMES_SQL, ARRAY[]::text[]) AS skill_names FROM session_dims sd WHERE sd.session_id = ANY(...)`. Imports the constant (reader.py:46), so reader and classifier cannot drift as long as the constant is the single edit point.
- `src/codemie/service/analytics/delivery_framework.py:68` `classify_delivery_framework(list[str])` — customer-config regex rules (`cliAnalytics.deliveryFramework`), first match wins, fallback label "Pure chat". No change needed.
- Callers: `cli_analytics_handler.py:736` (list, via `skills_by_session`), `:816/:875/:978` (detail), `cli_analytics_jobs.py:101` (`runtime.set_classifier`), `rollups.py:2165/2226-2228` (refresher calls `update_session_derived` last in its transaction).
- Source columns already present on `session_dims`: `commands` jsonb (rollups.py:1219, `_summary_or(_json_typed(_SUMMARY_ATTRS,"commands","array"), "NULL::jsonb")` — summary-only, NULL without a summary, written as a set with other summary columns), `primary_command`, `skills`, `agents`, `summary_ts`. The fallback `skills` (rollups.py:1087-1089) is `jsonb_object_agg` over `invocations_hourly` kind 2 for the session; kind 2 includes names derived from `agent.skill.dispatch` hooks (rollups.py:1649, 1852).
- `session_skills` is filled (rollups.py:1920) from `log_events` `skill_activated` (OTel) only.
- `reader.get_invocations` (reader.py:649-675) already reads `session_dims.commands` with `jsonb_typeof(e.cmd)='string' AND <> ''` and excludes `BUILTIN_COMMAND_NAMES` (`vocabulary.py:169`: model, exit, quit) — a precedent for how to unnest and filter commands.

### Architecture and Layers Affected
Repository (postgres `session_derived.py`, `reader.py`), service (`cli_analytics_handler.py` consumers, unchanged signatures), docs (guides). No router, no Alembic change on the read-time path.

### Integration Points
- Person view `usage_attribution/handler.py:884-943` does NOT use the SQL: it builds `skills` from aggregation buckets (`parsing.bucket_list(b, "skills")`, line ~911) and calls `classify_delivery_framework(info.get("skills"))`. It is a separate (non-session_dims) source, so changing the SQL will not make it consistent; verify its data source in the spec stage before promising consistency.
- `ports.py:186` declares `get_skill_names_by_session`.

### Patterns and Conventions
SQL fragments are shared constants; tests assert on normalized SQL text; keys read with `jsonb_typeof` guards so malformed rows never raise; `COLLATE "C"` ordering; rollup-derived columns are recomputed each refresh.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/cli-analytics-events.md`: line 59 (rule 9: command names carry no leading slash), 183/225 (subagent usage `commands`: array of strings), 298 (`agent.session.summary.commands`: "array of strings, call order, duplicates kept, no slash"), 313 ("A session with a summary counts its slash commands from `commands` alone"). The shape is documented.
- `.ai-run/guides/integration/cli-analytics-storage.md`: line 155 (`delivery_framework` is rollup-derived and recomputed each time), 159 (a `--now` backfill leaves `delivery_framework` NULL until the refresher recomputes), line 298 (names the classifier input as: keys of `skills` with a summary, else `session_skills` + `skills` keys; plus `agents` keys) — this sentence must be updated by the change. Line 354: backfill command and queue bits.
- Also applicable: data/database-patterns.md, standards/code-quality.md, testing/testing-patterns.md (not re-read in depth).

### Architectural Decisions
Summary rule: summary columns written as a set only from a summary not older than the stored one (`_SUMMARY_WINS`, rollups.py:971); fallback columns only while `summary_ts` is NULL (`_FALLBACK_RUNS`). This is why summary sessions lose hook-derived skills today: `sd.skills` is replaced wholesale by the summary object, so kind-2 hook names are no longer in it.

### Derived Conventions
Ingest: summary arrives as a `hook_events` row; `commands` lives in attrs jsonb and is validated at rollup time by `_json_typed(..., "array")` (non-array becomes NULL), element type is NOT validated on write (readers check `jsonb_typeof = 'string'`). `otlp.py` pops `skill_name` into a column (`otlp.py:774`, `_pop_key(attrs, "skill_name")`).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/repository/cli_analytics/postgres/test_reader.py:105-153` — `get_skill_names_by_session`: asserts the exact text of the two CASE branches (with-summary branch must NOT contain `session_skills`; test at line ~131-142). This test WILL fail and must be rewritten by any change to the branch structure.
- `tests/codemie/repository/cli_analytics/postgres/test_session_derived.py` — `update_session_derived`, `feature_id` (mock connection).
- `tests/codemie/service/analytics/test_delivery_framework.py` — classifier rules incl. STLC (lines 60-91).
- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` — repo mocked (`get_skill_names_by_session` AsyncMock).
- Live-Postgres suites `test_live_session_page.py`, `test_live_dispatch_gate.py` (inserts `agent.skill.dispatch` hook rows, lines 79-105) and `test_live_*` use a real DB with `classify_delivery_framework`; `test_runtime.py:285` covers `set_classifier`.

### Testing Framework and Patterns
pytest + pytest-asyncio; `_SqlRecorder`/`_ArgRecorder` fake engines for SQL-text assertions; live tests against Postgres for behaviour.

### Coverage Gaps
No test exercises a summary session with `commands` or with kind-2 hook names through the names SQL against a real DB; the with-summary branch is only text-asserted.

---

## 5. Configuration and Environment

### Environment Variables
`CLI_ANALYTICS_ROLLUP_RETENTION_DAYS` (365; rollups and `session_skills` retention) per storage guide line 36; raw retention separate.

### Configuration Files
Customer config `cliAnalytics.deliveryFramework` (rules, fallbackLabel) read in `delivery_framework.py`. Alembic for this DB: `src/external/alembic_cli_analytics/versions/` (`a1c1a0000001` initial, `a1c1a0000006` extension).

### Feature Flags and Deployment Concerns
`hook_events` is `PARTITION BY RANGE (ts)` with indexes `hook_events_session_ts (session_id, ts)` and a BRIN on ts; no index on `event_type` or `skill_name` (initial schema lines 110-119, 280-281). `invocations_hourly` is partitioned by `hour` with `invocations_hourly_session (session_id)` (line 292) — PK is `(hour, session_id, kind, name)`. `session_skills` PK `(session_id, skill_name)`. Existing-row repair: the backfill CLI only queues keys and refresher re-runs `update_session_derived`; a plain `--now` leaves `delivery_framework` NULL (no classifier).

---

## 6. Risk Indicators

- Naming drift: the task and ticket refer to `SKILL_NAMES_SQL`; the code has `CLASSIFIER_NAMES_SQL` with agents keys. Specs must use the real name.
- `test_reader.py` asserts exact SQL text of both branches, incl. that the summary branch has no `session_skills`; it needs rewriting (brittle).
- Person view (`usage_attribution/handler.py`) derives skills from aggregation buckets, not the shared SQL; "all must stay consistent" is not achievable by editing the SQL alone. Needs a decision.
- Speculative: Option 1 (read-time over `hook_events`): no index on `event_type`/`skill_name`, table partitioned by ts; a `(session_id, ts)` index scan filtered by event_type for each of a page of sessions, with the "latest summary" lookup, is the costliest option and runs on the list path for every page. Not recommended.
- Speculative: Option 2 (new persisted column) needs an Alembic migration and a backfill of Preview/prod, and a second summary-wins write rule; more moving parts than needed because `session_dims.commands` already exists.
- Speculative: Option 3 (recommended, read-time but WITHOUT hook_events): in the summary branch add (i) `jsonb_array_elements_text(sd.commands)` guarded by `jsonb_typeof(sd.commands)='array'` (elements non-string need a `jsonb_typeof(e)='string'` guard as in reader.py:672), (ii) `SELECT i.name FROM invocations_hourly i WHERE i.session_id = sd.session_id AND i.kind = 2` (kind 2 carries dispatch-hook names, index `invocations_hourly_session`), and (iii) `session_skills` — i.e. make the ELSE union unconditional and drop the CASE. All sources are per-session indexed lookups already used in the non-summary branch, no migration, no schema change, one edit point (`CLASSIFIER_NAMES_SQL`) shared by classifier and reader. Cost: one extra indexed probe per session in the page (`invocations_hourly` scans the session's partitions' index; bounded by the 365-day rollup retention). Remaining check: that kind-2 rows really contain `agent.skill.dispatch` names for summary sessions (rollups.py:1649, 1852 suggest yes, confirm with the repro data).
- Speculative: whether to filter `BUILTIN_COMMAND_NAMES` / empty strings (`reload-plugins` is not in that tuple) in the names; harmless for classification (rules are regexes) but visible in the session list `skill_names` output.
- Speculative: refresh of existing rows: `delivery_framework` is recomputed whenever a session is recomputed (new events); for already-stored "Pure chat" sessions a `backfill --from --to` (no `--now`) queues days so the job reclassifies; `_WRITE_DERIVED` writes only changed values. Sessions past raw retention are not requeued, so old labels stay.

---

## 7. Summary for Complexity Assessment

The change is local to one SQL constant, `CLASSIFIER_NAMES_SQL` in `src/codemie/repository/cli_analytics/postgres/session_derived.py`, which feeds both the rollup classifier (`update_session_derived`) and the list/detail reader (`get_skill_names_by_session`). The needed source data is already stored: `session_dims.commands` (jsonb array, summary-only), `session_dims.skills/agents`, `session_skills`, and `invocations_hourly` kind 2 (hook dispatch names). Layers: repository only, plus docs and tests; classifier rules and customer config are untouched. Small file surface (1 source file, 2-3 test files, 1-2 guide lines).

Technical novelty is low: the union already exists for sessions without a summary. Recommendation is a read-time union without a `hook_events` join and without a migration (Option 3 above); this avoids both an unindexed scan of the partitioned `hook_events` and a new column plus backfill. Open points are the person view in `usage_attribution/handler.py` (a different, bucket-based source) and whether builtin commands are filtered.

Test posture: classifier rules are well tested; the names SQL is covered by brittle exact-text tests in `test_reader.py` that must be rewritten, and no live-DB test covers a summary session with commands/hook names — a gap to fill. Main risks: SQL text test churn, per-page query cost (low), and old sessions beyond raw retention not being reclassified.

---

## 8. External References

Repro evidence in `/Users/vponkratina/work/ai/codemie/docs/local/work/sessions/2026-10-30-epmcdme-16435-repro/` resolved (directory exists: `task3.md`, `task4.md`, `task3-raw/` with hook captures and readbacks, `task4-raw/` with a real transcript `d3112924-...jsonl`). Only the task files' scope was confirmed; the raw files were not opened (the claims in the task, such as `skills={}` with `commands` holding the stlc name, were not independently re-verified here).
