# Fix: MR !4696 review findings (EPMCDME-16435)

Worktree: /Users/vponkratina/work/ai/codemie/.claude/worktrees/EPMCDME-16435-stlc-summary-names (branch EPMCDME-16435_stlc-summary-names). Work only there.
Do NOT run git add/commit (hook forbids it); the controller commits. Code, comments, tests: English only.
Run `make ruff` and the narrow tests (`tests/codemie/repository/cli_analytics`, `tests/codemie/service/usage_attribution`) with the worktree venv. For live tests use `make test-cli-analytics-live` (docker postgres is up; never stop containers or delete volumes).
Write `fix_mr_review.CHECKPOINT.md` next to this file: per item, what changed (file:line) and test result.

Re-verify each line reference before editing; they come from a scout pass and may have drifted.

## Items

### F2. Skip the Postgres framework read when inputs or evidence are cached (analysis.py ~773)
`_load_cached_inputs` returns `(inputs_blob, None)` when inputs are cached, else `(None, evidence_blob)`. The worker reuses the cached blob and never calls `fetch_inputs`, so `pg_framework_rows` is consumed only when neither blob is cached.
Guard: `[] if (inputs_blob or evidence_blob) else await <pg read>`.
Tests (`tests/codemie/service/usage_attribution/test_framework_wiring.py`): flip `test_cached_inputs_without_evidence_still_read_postgres` (~270-281) to expect no read; add evidence-only cached and nothing-cached cases.

### F3 + F9. Compare against the current rule labels, drop the dead `sessions` count
`reader.get_framework_sessions` (reader.py ~846-879, ports.py ~215-217) compares `delivery_framework <> $fallback_label`. After a rename of the fallback label, old "Pure chat" rows would count. Change the parameter to the list of current non-fallback labels (`get_framework_labels()[:-1]`, verify the helper) and use `delivery_framework = ANY($labels::text[])`. Update caller (analysis.py ~748), the fake in test_framework_wiring.py (~49), test_reader.py (~374-385) and the live test `test_live_framework_sessions.py`.
F9: the per-pair `sessions` value computed in `framework_sessions.py` (~171) is ignored; remove it from the query/normalizer if nothing reads it, or use it. Keep `merge_framework` semantics (`framework = min(sessions, max(es, pg))`, where `sessions` is the ES count).

### F1. One shared reader helper with the feature-flag check
Person view `_resolve_reader` (handler.py ~298-307) lacks the `features:cliAnalytics` enabled check that analysis.py ~733-748 performs. Create one helper `reader_or_none()` (disabled flag, bad config or unreachable storage -> None) in a new sibling module under `src/codemie/service/usage_attribution/` and use it in both places. Add tests for the flag-off path in both callers.

### F4. Person view: Postgres names win
handler.py `_with_postgres_skill_names` (~978): when Postgres returned names for the session, classify them alone; use the ES keys only when Postgres has none or failed. Spec R2 is already amended. Update `test_person_recent_sessions.py` (a case where ES holds an installed SDLC skill and Postgres holds STLC names must label STLC).

### F7. Parallel fetches in the person view
handler.py ~931-932: the two awaits are serial; run them concurrently with the existing `_gather` helper (~1064-1065).

### F5. Bound the A5 Postgres read
analysis.py `_pg_framework_rows` (~729-752): wrap the read in `asyncio.wait_for` with a constant timeout (put the constant in constants.py) and run `aggregate_pg_rows` through `asyncio.to_thread`. On timeout or error: log and fall back to `[]` (ES path), like the existing error fallback. Add a timeout-path test.

### F13. Size rule (`.ai-run/guides/standards/code-quality.md:43`)
analysis.py (914 lines), handler.py (1065) and worker.py (548) are over ~500 lines. Move the new helpers out instead of growing them: `_pg_framework_rows`, the person-view Postgres helper (`_with_postgres_skill_names` / `_resolve_reader`), `_merge_postgres_framework` go to sibling modules (the F1 module may host the reader helper and the A5 read; keep the worker merge next to `framework_sessions.py`). Callers keep one-line call sites. Update imports and test patches.

### F8. Stale comment
session_derived.py lines ~28-31 describe the old conditional behaviour; rewrite to describe the unconditional union.

### F6. Hour bound on the kind-2 arm of `CLASSIFIER_NAMES_SQL`
The `invocations_hourly` kind 2 arm (session_derived.py ~42) has no `hour` predicate, so no partition pruning. Add an hour bound derived from `session_dims.started_at` and `last_event_at` of the same row (truncate to hour; allow one hour margin each side). Do NOT gate on `summary_ts` (spec R1 keeps the union unconditional). Verify with `make test-cli-analytics-live`, and EXPLAIN on a scratch session if cheap; report what you could not verify.

### F10. Document the A5 scope
`get_framework_sessions` docstring: it is deliberately global per person/repository (no project scoping and no `_NOT_WINDOWED` handling), matching the ES side. Add one test assertion that the SQL carries no project predicate.

### F12. Strengthen the weak assertion
test_reader.py ~150 `"coalesce(" in recorder.statements[0]`: assert the exact fragment the names SQL relies on (the commands arm `jsonb_typeof(sd.commands)` guard and the union of all sources).

### Out of scope
F11 (golden vectors shared with the CLI repo) is a follow-up. Do not touch `.codemie/`, `doctor.json`.
