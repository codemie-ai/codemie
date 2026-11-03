# Fix 3: second MR review (EPMCDME-16435, MR !4696)

Worktree: /Users/vponkratina/work/ai/codemie/.claude/worktrees/EPMCDME-16435-stlc-summary-names (HEAD 20d57e075). Work only there. No git add/commit (the controller commits). English only. Re-verify each line reference before editing.
Run `make ruff` and `poetry run pytest tests/codemie/repository/cli_analytics tests/codemie/service/usage_attribution -q -k "not live"` with `env -u CLI_ANALYTICS_PG_URL`. Do not run the live suite (the controller does). Write `fix_mr_review3.CHECKPOINT.md` next to this file (per item: what changed at file:line, tests).

Facts (verified): the CLI sends an already-parsed repository id to ES (`owner/repo`, or a longer subgroup path such as `epm-cdme/platform/backend/codemie`, lower-cased) and the same value as `repo_remote` in hook events, so Postgres `session_dims.repo_remote` is that parsed id, not a raw URL. `session_dims.repository` is the raw cwd; the CLI's fallback for the ES attribute is `truncateProjectPath(cwd)` = last two path segments, case kept; an empty/whitespace/root value reports the literal `unknown`.

## Items

### R1 (+R10, R11). `normalize_repository` must not re-parse parsed ids (framework_sessions.py)
Today `epm-cdme/platform/backend/codemie` becomes `backend/codemie`. New behaviour:
- `repo_remote` that looks like a URL (`scheme://`) or scp form (`user@host:path`): parse it as now (keep the existing logic for these).
- Any other non-empty `repo_remote` (a parsed id, any number of segments): return it stripped and lower-cased, all segments kept.
- Empty `repo_remote`: fall back to the cwd the way the CLI does (`truncateProjectPath`: last two segments for an absolute path, lower-cased); an empty/whitespace/root cwd and an empty remote both give `unknown` (matches the ES `unknown` bucket) instead of None. Check the CLI source (read-only /Users/vponkratina/work/ai/codemie-code, `truncateProjectPath` in metrics-post-processor.ts, `extractRepository` in utils/paths.ts) and follow it; report any difference.
- Regenerate/extend `tests/codemie/service/usage_attribution/fixtures/repository_normalization_vectors.json`: add parsed-id vectors (2, 3, 4 segments, mixed case, Azure DevOps style `org/project/_git/repo` if the CLI can produce it, subgroup paths), empty and `unknown` cases, with `source` set accordingly. Keep all existing vectors passing; if an existing vector's expectation must change because the contract changed, change it and list it in the CHECKPOINT.
- Update the docstring: why URL parsing is kept (older/other writers), and that parsed ids pass through.

### R2. `merge_framework` double counting (framework_sessions.py ~165-184)
The full Postgres count is applied to every ES row sharing the normalized key `(email.lower(), repo.lower())`. Make the per-key Postgres count a budget shared by the rows of that key: total = min(sum of the rows' sessions, max(sum of the rows' ES framework, pg)); give each row its own ES framework as floor, then distribute the remainder over the rows by remaining capacity (sessions - framework). Tests: two rows with the same email/repo, case-different repos, duplicate emails, a single row (unchanged result).

### R3. A failed Postgres read must not be cached as the final answer (postgres_sources.py, analysis.py, worker/framework_merge)
`pg_framework_rows` returns `[]` for no reader, timeout and error alike. Make failure distinguishable (e.g. return None for timeout/error/unavailable-by-failure, `[]` only for a real empty result; a disabled feature or unconfigured storage is not a failure). When it failed, the ES-only result must not be reused for the rest of the hour: skip storing the evidence and inputs blobs (or store them with a short TTL constant, your call, pick the smaller change) so the next request retries. Tests for both branches, including that a success is cached as before.

### R5. Person-view Postgres timeout (postgres_sources.py `skill_names_by_session`)
Wrap the call in `asyncio.wait_for` with a new constant in constants.py (5 s); on timeout log and return `{}` like on error (ES keys remain).

### R6. Capacity before the Postgres read (analysis.py `_compute` / `_run_job`)
The Postgres read must not run when the request is refused as busy. Move it inside the slot (after the capacity decision), e.g. pass an async callable/lazy value into the job or perform it right after the slot is acquired, whichever is the smaller change. Test: busy path performs no Postgres read.

### R9. Labels (postgres_sources.py ~60, delivery_framework helper)
Exclude the fallback label from the rule labels even when a rule shares it (`[l for l in labels[:-1] if l != fallback]`, dedupe), and when the list is empty skip the query and return an empty result (success, not failure). Tests.

### R12. People loaded once (framework_merge.py ~41, worker.py fetch_inputs)
`merge_postgres_framework` and `fetch_inputs` both call `load_people`; `db_queries` gets two `people` entries. Load once: reuse the single load (reorder, or pass the email map in), keep the enrichment behaviour of `fetch_inputs`. Test: one `people` entry.

### R13. Shared session identity expression (reader.py ~862 and `_sessions_cte` ~353)
Extract the user-email expression into one shared module constant used by both (the A5 query adds `lower(...)` around it). Existing SQL tests must still pass.

### R14. Safe cleanups only (framework_sessions.py ~156-182)
Remove only what an earlier layer certainly guarantees (re-lowering of the Postgres-side email that `aggregate_pg_rows` already lowers). Keep the ES-side lowering and anything protecting public callers; list in the CHECKPOINT what you removed and what you kept and why.

### R15. Docstrings
reader.py ~681 `get_skill_names_by_session`: describe the real sources (session_skills, hook-dispatch names, `skills`/`agents` keys, `commands` elements) and the returned rows. `get_framework_sessions` docstring: drop the reference to `_NOT_WINDOWED` (a test-only constant); say plainly that the query is global per person/repository.

### R4. Remove the hour bound on the kind-2 arm (session_derived.py)
Delete `_HOUR_FROM`/`_HOUR_TO` and the `i.hour` predicates (the bound uses columns filled only by dimension hooks, so a dispatch outside that window is lost; the measured gain was 0.1-0.5 ms). Restore the comment accordingly (keep the unconditional-union description). Update/remove tests that asserted the bound (test_session_derived.py, test_live_summary_skill_names.py), and add a live test: a kind-2 dispatch more than 2 hours after the last dimension hook is still classified.

### Out of scope
R7 (measured separately), R8 (rollout note, controller), `.codemie/`, `doctor.json`.
