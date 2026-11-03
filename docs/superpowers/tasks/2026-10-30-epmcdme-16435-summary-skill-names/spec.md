# EPMCDME-16435: slash-invoked plugin skills classify as "Pure chat"

## Problem

For a session with an `agent.session.summary`, the classifier names SQL `CLASSIFIER_NAMES_SQL`
(`src/codemie/repository/cli_analytics/postgres/session_derived.py:35-52`) reads names only from the
summary `skills` and `agents` keys. The CLI fills `skills` only from model `Skill` tool_use blocks.
A slash-invoked plugin skill (`/stlc-factory:suite-health`) lands in the summary `commands`, so
`skills={}` and `classify_delivery_framework` returns "Pure chat". Summary-wins writing also drops the
kind-2 hook-dispatch names from `session_dims.skills`. Sessions without a summary already use a
wider union and classify correctly. Confirmed on a scratch DB with real ingest and rollup: for summary
sessions with `skills={}` the stored `delivery_framework` is NULL and the old names SQL returns NULL.

## Scope and phasing

The task is treated as L: shared SQL, person view and the A5 aggregate, each with its own data source,
wiring and tests. The plan should be split into independently committable phases:
1. R1 shared SQL (plus its tests and the R3 guide line). Delivers the session list, detail and stored label.
2. R2 person view (gated by its own id check).
3. R5 A5 aggregate (gated by its own identifier check).
4. Remaining tests and docs.
R2 and R5 each have a gating verification and an ES-only fallback, so R1 ships even if they do not.

## Requirements

### R1. Unconditional name union (read-time, one edit point)
`CLASSIFIER_NAMES_SQL` returns the union, with or without a summary, of:
- keys of `sd.skills` and `sd.agents` (existing `jsonb_typeof` guards kept);
- `session_skills` rows (OTel `skill_activated`);
- `invocations_hourly` rows with `kind = 2` for the session (carries `agent.skill.dispatch` names;
  confirmed present for summary sessions);
- string elements of `sd.commands`, only when `jsonb_typeof(sd.commands) = 'array'` and the element
  is a JSON string (same guard as `reader.py:672`).

Only empty and non-string elements are dropped. No name denylist: `reload-plugins`, `plan` and similar
stay and match no rule. Result stays de-duplicated, ordered `COLLATE "C"`, NULL when empty. The `CASE`
on `summary_ts` is removed. The classifier (`update_session_derived`) and
`reader.get_skill_names_by_session` (session list and detail) both import this constant and stay
identical. No migration, no `hook_events` join. Expected result for the repro: names
`['reload-plugins','stlc-factory:suite-health']` give "STLC Factory (QA factory)".

### R2. Person view recent-sessions label
Facts: `_person_recent_sessions` (`usage_attribution/handler.py:943`) calls
`classify_delivery_framework(info.get("skills") or [])`. `info["skills"]` comes only from Elasticsearch via
`queries.sessions_extra_body` (`queries.py:550-564`), a terms aggregation on
`attributes.skills_project_names.keyword` (`constants.py:51`). That field is written by the CLI
(codemie-code `metrics-api-client.ts:419`) and holds skills INSTALLED under `{cwd}/.claude/skills/*/SKILL.md`
(disk scan at session start), not used skills, and not derived from `Skill` tool_use or `commands`.
Plugin skills (`stlc-factory:*`) live in the plugin cache and are generally absent. So `commands` cannot be
merged into the ES keys; the label for plugin sessions must come from Postgres.

Requirement: for the session ids of the recent-sessions list, fetch names with
`get_skill_names_by_session` (one batched call per page) and classify those names. Postgres names win:
the ES keys are installed skills, and classification is first-match-wins, so merging them lets an installed
SDLC skill override a used STLC one (amended after MR review). If Postgres has no names for a session id,
or the lookup fails, the label uses the ES keys alone (previous behavior). The usage_attribution service has no Postgres reader today; the reader is obtained
the same way `CliAnalyticsHandler` gets it (`repository/cli_analytics/factory.py:55`), injected into the
handler so it can be faked in tests.

First step, before any wiring: verify that ES `attributes.session_id` equals Postgres
`session_dims.session_id` (only `test_live_dispatch_gate.py:599` treats `trace_id` as `session_id`) and
record how much the two sources overlap, in the task directory. Fallback if the ids differ: the person
view stays ES-only, R2 wiring is not shipped, and the gap is a documented follow-up; R1 still ships.

### R3. Docs
Update the classifier-input sentence at `.ai-run/guides/integration/cli-analytics-storage.md:298`
to describe the unconditional union above. Add a short note on the A5 source rule (R5) where the
guide describes `delivery_framework`.

### R4. Tests (R1 and R2)
- Rewrite the exact-SQL-text assertions at `tests/codemie/repository/cli_analytics/postgres/test_reader.py:105-153`
  (the "summary branch has no `session_skills`" check is now wrong).
- Live-Postgres test: summary session with `skills={}` and `commands` containing a stlc-factory entry,
  plus malformed `commands` (non-string, empty, null and nested elements inside an array). Non-array
  `commands` is never stored by ingest (NULL), so it is covered as NULL.
- Live-Postgres test: a summary session picks up kind-2 hook-dispatch and `session_skills` names.
- New unit tests for the person-view path, which has none today (`_person_recent_sessions`,
  `_person_session_extras`, `sessions_extra_body`): Postgres-only names yield the STLC label; ES-only keys
  still classify; Postgres miss falls back to ES keys; Postgres error falls back to ES keys.
- Run tests from the worktree with its own poetry venv. `codemie` is installed editable from the main
  checkout in the main venv, so confirm tests import the worktree `src` (check `codemie.__file__`).

### R5. A5 aggregate (`queries.sessions_body`)
Facts: the only call site is `worker.py:259` (`_evidence_specs`, via `fetch_inputs`). The aggregation is
`terms(F_USER_ID)` then `terms(F_REPOSITORY)` with sub-aggregations `sessions`, `mcp_any`, `mcp_work`
and `framework`; `framework` is a regexp filter on `skills_project_names` built by
`handler.framework_regex()`. Only `framework` changes; `sessions`, `mcp_any`, `mcp_work` stay on ES.
Rows are `[uid, repo, sessions, mcp_any, mcp_work, framework]` (`parsing.parse_sessions`), merged in
`pipeline._build_sess`, and consumed by `classifier._how`, which uses `framework > 0` as a boolean
"any framework" signal (`how.framework`, weight in `settings.py:369`) and `sessions > 0`.

Design:
- Source: Postgres `session_dims.delivery_framework`. A session counts as framework when the label is
  NOT NULL and differs from the configured fallback label ("Pure chat"). New repository reader method
  (port in `ports.py`), modelled on `reader.get_repository_sessions` (`reader.py:832-846`) and
  `_sessions_cte` (`reader.py:323-366`), returning `(user_email, repository, sessions, framework_sessions)`
  for a window, using `_STARTED_IN_WINDOW` with end-1ms to match the ES `[gte, lt)` semantics.
  Repository value follows the reader convention `coalesce(nullif(repo_remote,''), nullif(repository,''), '')`.
  The repository layer must not import the service layer (`test_layering.py`).
- Join to ES rows: map ES `user_id` (app `users.id`) to `lower(email)` through the app `users` table
  (`app_db.load_people`), and match on the (user, repository) pair the pipeline already uses.
- Execution model (recommend option b). The worker child is synchronous (`worker.fetch_inputs`,
  multiprocessing child) while the CLI Analytics reader is async on a lazily created asyncpg pool.
  (a) Run the query inside the child with `asyncio.run` on a fresh engine: no change to dispatch, but a
  second engine per child, loop handling in a sync worker, and the factory singleton rebuilt per process.
  (b) Run the query in the API process before dispatching the job and pass the plain rows (picklable
  tuples) in the job dict: the child stays synchronous, the reader runs on the loop it was built for, and
  failures are handled before dispatch. Recommended: (b). The query runs only when the job will actually
  compute (not on a cache hit), and the rows become part of the job inputs.
- Merge rule per (user, repo) pair, when Postgres is available: `framework = min(sessions, max(es_framework, pg_framework))`.
  The `max` keeps old sessions (classified only in ES, or with NULL stored label) counted; the clamp keeps
  the invariant `framework <= sessions` when the windows or sources differ. When Postgres is unavailable
  the ES regexp value is used unchanged.
- Fallback to the existing ES path when: `features:cliAnalytics` is off (default, `customer-config.yaml:119-121`),
  `CliAnalyticsStorageConfigError` (as `cli_analytics_jobs.py:92-97`), analytics Postgres is unreachable,
  or the query returns no rows for the window. A failure never fails the evidence job.
- Cache: the evidence blob is cached per hour under a key containing `eff.fetch_hash`, which includes
  `INPUTS_VERSION` (`constants.py:27`, currently "3"). Bump `INPUTS_VERSION` because the source of
  `sess.framework` changes; otherwise stale ES-based evidence is served.
- Old sessions: only new sessions matter; there is no backfill. Old rows keep a NULL or "Pure chat" stored
  label, so A5 for windows that predate the change can under-count on the Postgres side; the `max` rule
  limits this to the ES value. The evidence window can also exceed Postgres raw retention (90 days,
  `factory.py:37`); not measured, covered by the same `max` fallback.

Gating first step, before any code: verify on real or seeded data (the local docker stack is available;
never stop containers or delete volumes) (1) the mapping `users.id` to `lower(email)` against
`session_dims.user_email`, (2) equality of ES `attributes.repository` (lower-cased, `identity.py:17`) with
the Postgres repository string, and (3) equality of ES and Postgres session ids. Record results in the task
directory. Fallback if verification fails: A5 stays ES-only, the gap is a documented follow-up, and R1
and R2 still ship.

Tests (none exist today for `sessions_body`, `parse_sessions` or the `sess` merge):
- Unit tests: `sessions_body` shape, `parse_sessions`, `pipeline._build_sess` / `key_for` with the Postgres
  rows, the merge rule (including clamp and ES-only fallback), worker flag-off and error fallback, and
  `INPUTS_VERSION` in the fetch hash (extend `test_settings.py:278-285`).
- Live test on docker-compose Postgres for the new reader method (pattern:
  `test_live_session_subagent_cost.py`, session ids `test-epmcdme-16435-<slug>`, `live_rollup_lock`,
  skip on connection failure; pass the URL explicitly where a file defaults to "").

## Acceptance criteria
1. A summary session with `skills={}` and `commands=[...stlc...]` classifies as "STLC Factory (QA factory)" for each of the four STLC first-prompt skills.
2. sdlc, superpowers and no-factory labels are unchanged; first-match-wins and customer-config rules unchanged.
3. Kind-2 hook-dispatch names and `session_skills` contribute for summary sessions.
4. Non-string, empty, null and nested array elements never raise and never appear in the names.
5. Session list and detail `skill_names` equal the classifier's input names.
6. A person-view recent session whose names exist only in Postgres (summary `commands` or dispatch) is labelled "STLC Factory (QA factory)".
7. The person view falls back to ES keys when Postgres has no row for the session id or errors.
8. The ES/Postgres session id mapping and coverage check is recorded before R2 wiring; if it fails, R2 is deferred as a documented follow-up.
9. New unit tests cover the changed person-view label path.
10. A session with `session_dims.delivery_framework = "STLC Factory (QA factory)"` and no installed skills in ES makes A5 `framework > 0` for its (user, repo) pair.
11. A5 `sessions`, `mcp_any` and `mcp_work` are unchanged; `framework <= sessions` holds for every pair.
12. A5 `framework` falls back to the ES regexp path when cliAnalytics is off, storage config is invalid, Postgres is unreachable or returns no rows; the evidence job does not fail.
13. `INPUTS_VERSION` is bumped and the fetch hash test is updated.
14. The user-id, repository-string and session-id verification for R5 is recorded before wiring; if it fails, A5 stays ES-only and the gap is documented.
15. New unit tests cover `sessions_body`, `parse_sessions`, the pipeline merge and the new reader method, plus a live test on docker-compose Postgres.
16. Session cost, tokens and skills counts are unchanged.
17. `make ruff` and the touched test modules pass, run from the worktree against the worktree `src`.

## Non-goals
- No CLI (codemie-code) change; no change to what the CLI puts in `skills`, `commands` or `skills_project_names`.
- No migration, new column or `hook_events` join.
- No change to classifier rule order, rules, or customer config; no Preview/prod ConfigMap work.
- No name denylist (builtin or non-skill commands stay in `skill_names`).
- No refreshing of stored `session_dims.delivery_framework` and no backfill of old sessions; only new sessions matter.
- No change to A5 `sessions`, `mcp_any` or `mcp_work`; they stay on ES.

## Open risks
- Unverified identifier mappings between ES and Postgres: session id (R2, R5), `users.id` to email and repository string (R5). Each is gated by a first step with an ES-only fallback.
- Window semantics differ: ES `@timestamp` of session-total records, `[gte, lt)`, versus Postgres session `started_at`, inclusive. A session can fall in one window and not the other; the clamp keeps `framework <= sessions` but counts can differ at the edges.
- The A5 evidence window can exceed Postgres raw retention (90 days); not measured. Old windows may under-count on the Postgres side (mitigated by `max` with ES).
- Running the Postgres query before dispatch (option b) adds latency to job start and a new dependency on the analytics DB in the usage_attribution flow; failures are swallowed to the ES path.
- Measured names-SQL cost (synthetic data, page of 50 sessions): old 0.17 ms / 9 buffers; new 0.82 ms / 314 buffers. `invocations_hourly` is partitioned by `hour` and the names query has no `hour` predicate, so there is no partition pruning: one index probe per partition per session, growing with retained monthly partitions. Accepted for now; re-measure on real volume before merge. Possible future options, not part of this task: a bounded `hour` range or pre-aggregation.
- Stored labels of already-classified sessions stay as they are (accepted by the owner).
