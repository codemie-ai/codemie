# Fix 4: third MR review (EPMCDME-16435, MR !4696)

Worktree: /Users/vponkratina/work/ai/codemie/.claude/worktrees/EPMCDME-16435-stlc-summary-names (HEAD d99eeaa18). Work only there. No git add/commit. English only. Re-verify each line reference before editing (they drift).
Run `make ruff` and `env -u CLI_ANALYTICS_PG_URL poetry run pytest tests/codemie/repository/cli_analytics tests/codemie/service/usage_attribution -q -k "not live"`. Do not run the live suite (the controller does). Write `fix_mr_review4.CHECKPOINT.md` next to this file (per item: what changed at file:line, tests).

Decisions already taken (do not revisit): the person view stays in this MR. Its Postgres lookup matches only when the ES proxy session id equals `session_dims.session_id` (not the case for Claude Code, whose ES id is the CLI-generated CODEMIE_SESSION_ID); that is documented, not fixed here.

## Items

### R3 (+R6). A failed Postgres read must make the next identical request retry (analysis.py ~701-727, 759-770)
`_compute` nulls only `inputs_blob`/`evidence_blob` on a failed read, but `_store_computed_result` still writes `{prefix}:meta` and the sections with the full TTL, so `get_analysis` serves the ES-only answer as a hit for `analysisCacheMinutes`. Make a failed-read result short-lived: store meta/sections with a short TTL constant (add to constants.py, e.g. 60 s) or do not store meta when the Postgres read failed; pick the smaller change that makes the SAME range retry. Keep the last-good copy semantics unchanged. Rewrite `test_a_failed_read_is_not_kept_for_the_hour` (test_framework_wiring.py ~183-194) so the second call uses the SAME range and asserts the Postgres read ran again; add the success case (cached as before). Fix the comment at analysis.py ~769 so it states what is true.

### R9. A failed merge must be treated like a failed read (framework_merge.py ~35-39, worker.py)
`merge_postgres_framework` swallows exceptions and the ES-only evidence is cached for the hour. Carry the failure back to the parent (e.g. in the worker result/evidence meta flag) so the same short-lived path as R3 applies and the evidence blob is not stored for the hour; log the failure at warning with the exception type. Test both outcomes.

### R7. Worker must not start after shutdown (analysis.py ~861-868)
`await before_start(job)` sits between the `_closing` check and the spawn. Re-check `self._closing` after `before_start` (and release the admission/slot properly if closing) before spawning. Test: shutdown set while before_start is awaiting -> no process started.

### R2. Lower bound on the kind-2 arm (session_derived.py ~43-47)
Add only a lower bound to the `invocations_hourly` kind-2 probe: `AND i.hour >= coalesce(date_trunc('hour', sd.started_at) - interval '1 hour', '-infinity'::timestamptz)`. No upper bound (late dispatches stay). NULL `started_at` must not drop rows (the coalesce). Measured: partitions before the session start are skipped at run time. Update the comment above the SQL (state why there is no upper bound), update `test_session_derived.py` and the live test: a dispatch more than 2 hours after the last dimension hook is still classified; a session with NULL started_at keeps its kind-2 names; a dispatch 30 minutes before started_at is kept (one hour margin).

### R8. Slash commands without a summary (session_derived.py ~44)
Include kind 4 (slash-command user prompts written by rollups.py ~1942-1950) in the kind arm: `i.kind IN (2, 4)`. Add a live test: a session with no summary and no dispatch row, only a kind-4 `stlc-factory:x` row, classifies as STLC. Keep the lower bound of R2 on this arm.

### R4. Known session that used nothing (postgres_sources.py ~84-85)
`skill_names_by_session` drops empty name lists, so "known session, used nothing" falls through to the ES installed-skill keys and a plain chat can be labelled as a factory. Return an entry (empty list) for every session id the reader returned a row for, and make `postgres_names_win` (or its caller in handler.py ~902) prefer the Postgres entry whenever the session has a row, even with an empty list; ES keys stay the fallback only when there is NO row or the lookup failed. Check that get_skill_names_by_session returns a row for every existing session_dims row (coalesce to empty array) and add tests: row with empty names -> label from Postgres only (Pure chat), no row -> ES keys, failure -> ES keys.

### R10. Config failures and logging (postgres_sources.py ~53-59, 76; delivery_framework helper)
Move the lazy imports, `reader_or_none()` and `get_rule_framework_labels()` inside the try/except so a config error cannot become a 503; log a broken storage config at warning (type of the exception only, no secrets), not debug. Tests.

### R12. Redundant steps (framework_sessions.py ~187-209)
Remove only what an earlier layer certainly guarantees: the per-key re-sum that `aggregate_pg_rows` already did, copying every row when only matched rows change, and dead branches in the distribution loop that cannot trigger. Keep the clamps that protect against over-count. Prove with tests that results are unchanged (existing tests plus a randomised equivalence check against the previous implementation kept in the test file, or a table of cases). List removed/kept with reasons in the CHECKPOINT.

### R13. Merge key through `identity.canonical_value` (framework_sessions.py ~196)
Use `identity.canonical_value` (src/codemie/service/usage_attribution/identity.py:74) for the repository part of the merge key on BOTH sides (ES value and the normalised Postgres value), so `Acme//Api/` matches `acme/api`. Email keeps `.strip().lower()`. Do not change `normalize_repository`'s own contract or the golden vectors; add merge tests for `Acme//Api/` vs `acme/api` and for a trailing slash.

### R14. Types (postgres_sources.py ~31, 70; framework_merge.py ~25)
`reader_or_none() -> CliAnalyticsReader | None` and `skill_names_by_session(reader: CliAnalyticsReader, ...)` (Protocol in repository/cli_analytics/ports.py), no bare `Any`; type `sess_spec` and the `list[list]` rows precisely. Keep the fakes in tests compatible (they may satisfy the Protocol structurally). `make ruff` must stay green.

### R15. Stale docs
- framework_sessions.py module docstring (~17-20): `repo_remote` is the id the CLI already parsed (older or other writers may store a raw URL; both are handled), `repository` is the earliest raw cwd.
- `classify_delivery_framework` docstring (service/analytics/delivery_framework.py ~76-85): the input is the session's skill names, subagent types, hook-dispatch names and slash-command names (`commands`), with a correct example.
- `.ai-run/guides/integration/cli-analytics-events.md` ~413: only if the `repo_remote` example plainly contradicts the CLI (a parsed id such as `org/repo`); if you change it, say so in the CHECKPOINT.
- postgres_sources.py: add a short comment on `skill_names_by_session` stating that it matches only when the ES proxy session id equals `session_dims.session_id` (true e.g. for pinned `--session-id` sessions, not for Claude Code), and why a miss silently falls back to the ES keys.

### Out of scope
R1 (no join key exists for Claude Code; documented in the MR), R5 (measured: no index needed), R11 (no used-skills field in ES; Postgres stays the source). `.codemie/`, `doctor.json`.
