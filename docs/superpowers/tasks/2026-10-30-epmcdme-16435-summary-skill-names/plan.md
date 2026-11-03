# EPMCDME-16435 Summary-session skill names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Slash-invoked plugin skills stop classifying as "Pure chat" in the session list, detail, stored label, person view and A5 aggregate.

**Architecture:** Phase 1 makes `CLASSIFIER_NAMES_SQL` an unconditional union (one edit point shared by classifier and reader). Phases 2 and 3 are each gated by an ES-vs-Postgres identifier check with an ES-only fallback, so Phase 1 ships alone. Phase 4 is docs.

**Tech Stack:** Python, SQLAlchemy async/asyncpg, Elasticsearch, pytest.

**Spec:** `docs/superpowers/tasks/2026-10-30-epmcdme-16435-summary-skill-names/spec.md` (analysis: `technical-analysis.md` same dir).

Commit per task using the repository's existing convention (no AI trailers, never push).

## Global Constraints

- Code, comments, tests in English. Repository layer must not import service (`tests/codemie/repository/cli_analytics/test_layering.py`).
- No migration, no `hook_events` join, no name denylist, no CLI change, no change to classifier rules/order or customer config, no backfill.
- Only empty and non-string `commands` elements are dropped; result de-duplicated, `ORDER BY name COLLATE "C"`, NULL when empty.
- Run tests from the worktree: `cd /Users/vponkratina/work/ai/codemie/.claude/worktrees/EPMCDME-16435-stlc-summary-names && poetry run pytest <path> -q`. First confirm `poetry run python -c "import codemie;print(codemie.__file__)"` prints a path under the worktree `src/`; if not, stop and fix the venv (`poetry install`).
- Live tests: URL from `TEST_LOCAL_PG_URL` (default `postgresql://postgres:password@localhost:5432/postgres`), skip when unreachable, session ids `test-epmcdme-16435-<slug>`. Docker stack (Postgres :5432, ES :9200) is up; never stop containers or delete volumes.

## Review Focus

- `commands` array holding null, number, nested array, empty string: never raises, never in names (Task 1.2).
- Summary session with `skills={}` plus dispatch-hook and `session_skills` names (Task 1.2).
- Person view: Postgres lookup raises or has no row: ES keys alone (Task 2.2).
- A5: Postgres reports more framework sessions than `sessions`: clamp keeps `framework <= sessions` (Task 3.3).
- A5: cliAnalytics off / storage config error / Postgres down: evidence job still succeeds on ES path (Task 3.3).

---

## Phase 1: R1 shared names SQL

### Task 1.1: Unconditional union in CLASSIFIER_NAMES_SQL

**Files:** Modify `src/codemie/repository/cli_analytics/postgres/session_derived.py:35-52`; Test `tests/codemie/repository/cli_analytics/postgres/test_reader.py:105-153`.

**Interfaces:** Produces unchanged constant `CLASSIFIER_NAMES_SQL` (used by `_READ` and `reader.get_skill_names_by_session`).

Test-first: yes — rewritten `test_reader.py` assertions require the SQL to contain `session_skills`, `invocations_hourly`, `kind = 2`, `jsonb_array_elements_text(sd.commands)` and `jsonb_typeof(sd.commands) = 'array'` and NOT `CASE`/`summary_ts`; fails on current code.

- [ ] Replace the two-branch text assertions at `test_reader.py:105-153` (including the "summary branch has no `session_skills`" check) with the assertions above; run `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -q`, expect FAIL.
- [ ] Remove the `CASE` on `summary_ts`: make the former ELSE union unconditional and add (i) string elements of `sd.commands` guarded by `jsonb_typeof(sd.commands)='array'` and `jsonb_typeof(e)='string'` (same guard as `reader.py:672`), (ii) `invocations_hourly` rows with `i.session_id = sd.session_id AND i.kind = 2`. Keep the existing `jsonb_typeof` guards, empty-name filter, `array_agg ... COLLATE "C"`.
- [ ] Run `test_reader.py` and `test_session_derived.py`; expect PASS.

### Task 1.2: Live-DB test for summary sessions

**Files:** Create `tests/codemie/repository/cli_analytics/postgres/test_live_summary_skill_names.py` (model on `test_live_session_subagent_cost.py` and `test_live_dispatch_gate.py:79-105`).

Test-first: yes — tests fail against the old SQL, pass after 1.1.

- [ ] Tests (each asserts `get_skill_names_by_session` names and `classify_delivery_framework(names)`):
  - `test_summary_commands_classify_stlc`: `skills={}`, `commands=["reload-plugins","stlc-factory:suite-health"]` gives `['reload-plugins','stlc-factory:suite-health']` and "STLC Factory (QA factory)" (parametrize the four STLC first-prompt skills from `tests/codemie/service/analytics/test_delivery_framework.py:60-91`).
  - `test_malformed_commands_elements_dropped`: `["", null, 5, ["x"], "ok"]` gives `['ok']`; `commands` NULL gives `[]`.
  - `test_summary_session_unions_dispatch_and_session_skills`: kind-2 `invocations_hourly` row plus `session_skills` row both appear.
  - `test_list_names_equal_classifier_input`: reader result equals the names `update_session_derived` hands to `classify`.
- [ ] Run `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_live_summary_skill_names.py -q`; expect PASS (or SKIP only if Postgres unreachable, which is a failure of the environment: report it).

## Phase 2: R2 person view

### Task 2.1: Gating check ES session_id vs Postgres session_id

**Files:** Create `docs/superpowers/tasks/2026-10-30-epmcdme-16435-summary-skill-names/r2-verification.md`.

Test-first: no — read-only verification, no code.

- [ ] Query ES (`localhost:9200`, metrics index used by `queries.sessions_extra_body`) for a sample of `attributes.session_id` values and check each against `session_dims.session_id` in docker Postgres (`psql postgresql://postgres:password@localhost:5432/postgres`); seed matching rows if local data lacks overlap. Record id format, sample size, overlap %, or "no data found" with where you looked. If ids differ, record the verdict "R2 deferred", skip Task 2.2, continue to Phase 3.

### Task 2.2: Person view uses Postgres names

**Files:** Modify `src/codemie/service/analytics/usage_attribution/handler.py:884-943` (`_person_recent_sessions`, `_person_session_extras`); reader obtained like `CliAnalyticsHandler` (`repository/cli_analytics/factory.py:55`), injected via the handler constructor; Test: create `tests/codemie/service/analytics/usage_attribution/test_person_recent_sessions.py`.

**Interfaces:** Consumes `get_skill_names_by_session(session_ids) -> dict[str, list[str]]` (`ports.py:186`).

Test-first: yes — fake reader returning `{"s1": ["stlc-factory:suite-health"]}` with ES skills `[]` expects label "STLC Factory (QA factory)"; fails today (ES keys only).

- [ ] Write tests: Postgres-only names give STLC label; ES-only keys still classify; Postgres has no row for the id gives ES-key label; fake reader raising gives ES-key label (no exception escapes).
- [ ] Implement: one batched call per page for the page's session ids, classify the union of Postgres names and ES keys; catch errors (and `CliAnalyticsStorageConfigError`) and fall back to ES keys; a feature-off or unconfigured reader means ES-only.
- [ ] Run the new test module; expect PASS.

## Phase 3: R5 A5 `framework` from Postgres

### Task 3.1: Gating check for user, repository and session identifiers

**Files:** Create `.../r5-verification.md` in the task dir.

Test-first: no — read-only verification.

- [ ] Verify on docker Postgres/ES (seed if needed): (1) app `users.id` maps to `lower(email)` equal to `session_dims.user_email` (`app_db.load_people`); (2) ES `attributes.repository` (lower-cased, `identity.py:17`) equals `coalesce(nullif(repo_remote,''),nullif(repository,''),'')`; (3) ES and Postgres session ids equal. Record results. If any fails: A5 stays ES-only, document the gap, skip Tasks 3.2-3.3.

### Task 3.2: Reader method for framework sessions

**Files:** Modify `src/codemie/repository/cli_analytics/postgres/reader.py` (near `get_repository_sessions`, 832-846; reuse `_sessions_cte`/`_STARTED_IN_WINDOW`, 323-366) and `ports.py`; Create `tests/codemie/repository/cli_analytics/postgres/test_live_framework_sessions.py`.

**Interfaces:** Produces `async def get_framework_sessions(self, start, end, fallback_label: str) -> list[tuple[str, str, int, int]]` returning `(user_email, repository, sessions, framework_sessions)`; framework means `delivery_framework IS NOT NULL AND <> fallback_label`; window end is `end - 1ms` to match ES `[gte, lt)`.

Test-first: yes — live test seeds sessions (STLC label, "Pure chat", NULL, one outside window) and expects counts `(2 sessions, 1 framework)` style per (email, repo); fails because the method does not exist.

- [ ] Write the live test plus a unit SQL-shape test; run, expect FAIL.
- [ ] Implement the method and port; run `test_layering.py` and the new tests; expect PASS.

### Task 3.3: Wire into A5 evidence

**Files:** Modify `src/codemie/service/analytics/usage_attribution/` modules `worker.py:259` (`_evidence_specs`/`fetch_inputs`), `pipeline._build_sess`, `queries.sessions_body`/`parsing.parse_sessions` as needed, job dispatch site, and `constants.py:27` (`INPUTS_VERSION` "3" to "4"); Tests: new unit tests beside existing usage_attribution tests, extend `test_settings.py:278-285`.

**Interfaces:** Consumes `get_framework_sessions` (Task 3.2). Job dict gains plain picklable tuples `pg_framework_rows`; query runs in the API process before dispatch, only when the job will compute (not on cache hit).

Test-first: yes — a pair with ES `framework=0`, Postgres framework 1, sessions 3 yields `sess.framework > 0`; fails before wiring.

- [ ] Write tests: `sessions_body` shape and `parse_sessions` row layout `[uid, repo, sessions, mcp_any, mcp_work, framework]` unchanged; `_build_sess`/`key_for` with Postgres rows (user id mapped to email through people lookup); merge `framework = min(sessions, max(es, pg))`; `sessions`, `mcp_any`, `mcp_work` unchanged; ES-only fallback for feature off, `CliAnalyticsStorageConfigError`, Postgres error, empty rows (job never fails); fetch hash changes with `INPUTS_VERSION`.
- [ ] Implement, bump `INPUTS_VERSION`, run the touched usage_attribution test modules; expect PASS.

## Phase 4: Docs

### Task 4.1: Guide updates

**Files:** Modify `.ai-run/guides/integration/cli-analytics-storage.md:298` and the `delivery_framework` description near line 155.

Test-first: no — documentation only.

- [ ] Replace the classifier-input sentence at line 298 with the unconditional union (summary `skills`/`agents` keys, `session_skills`, `invocations_hourly` kind 2, string `commands` elements); add a short A5 source note (Postgres label not NULL and not the fallback, merged with ES via `min(sessions, max(es, pg))`, ES fallback), noting it applies only if Phase 3 shipped. Record in the plan digest if Phase 2 or 3 was deferred.
