# Fix 5 CHECKPOINT: Sonar cognitive complexity (EPMCDME-16435, MR !4696)

## Item 1: `_parse_git_remote_url` (framework_sessions.py) - done
Split into one helper per step of the CLI `parseGitRemoteUrl`; behaviour unchanged (golden vectors in
`repository_normalization_vectors.json` green, file untouched):
- `_RemoteParts` (NamedTuple: scheme, host, path, structured)
- `_split_remote` - scheme URL / scp detection, credential bail-out (None)
- `_path_segments` - host/path split by `structured`, decode of segments
- `_host_segments` - Azure DevOps / vs-ssh, legacy Visual Studio HTTPS (`_visualstudio_https_segments`), Bitbucket `scm/` (`_bitbucket_scm_segments`)
- `_trim_user_segment` - `~user` and lone `~`
- `_parse_git_remote_url` - orchestrates, suffix strip, host name, final join. Docstring citations keep function names only.

## Item 2: `fetch_inputs` (worker.py) - done
- `framework_merge.load_people_and_merge(engine, job, by_key, uids, fresh_evidence, db_timings)` (new, sibling module): the single app DB people read (same `app_db.load_people` call, same uid union, same `user_enrichment` default), the people filter, the Postgres merge via `merge_postgres_framework`; returns `(people, merge_failed, people_ms)`. `by_key["sess"]` is read only when merging (reused evidence has no "sess" spec), as before.
- `worker._assemble_inputs(...)`: the inputs dict, same keys and order; `meta` is built by `_fetch_meta` at the call site as before.
- `fetch_inputs` keeps the order of the app DB read, ES execution, people read, `_build_evidence`; `db_ms`/`db_queries` accounting unchanged (people_ms added to db_ms); cached blob path untouched. `worker.py` import of `merge_postgres_framework` replaced by `load_people_and_merge`; the now-unused local imports of `Session`/`app_db` in `fetch_inputs` removed.

## Complexity
No radon / cognitive-complexity tool is installed (`poetry run radon` not found; only mccabe, cyclomatic). Cognitive complexity
is estimated by hand (branches + nesting); all functions and new helpers are <= 15:
`_split_remote` ~5, `_path_segments` ~3, `_visualstudio_https_segments` ~5, `_bitbucket_scm_segments` ~6, `_host_segments` ~9,
`_trim_user_segment` ~4, `_parse_git_remote_url` ~6, `fetch_inputs` ~9 (was 18), `load_people_and_merge` ~6, `_assemble_inputs` ~6.
mccabe (cyclomatic, via `python -m mccabe --min 4`): `_split_remote` 4, nothing else of the changed functions >= 4 except the untouched `merge_framework` 7.

## Tests
Before: 1719 passed targeted run baseline not re-measured on the old code (no test changed). First run after the edit gave 4 failures
(`KeyError: 'sess'`: I had read `by_key["sess"]` eagerly for reused evidence); fixed by reading it only when merging.
After: `make ruff` clean; `env -u CLI_ANALYTICS_PG_URL poetry run pytest tests/codemie/repository/cli_analytics tests/codemie/service/usage_attribution -q -k "not live"` = 1719 passed.
No test added or edited (existing worker, framework wiring and golden vector tests cover both seams).

Full `poetry run pytest tests/` (with CLI_ANALYTICS_PG_URL unset): 25718 passed, 148 failed, 2 errors. Failures are outside my zone:
`tests/codemie/repository/cli_analytics/postgres/test_*live*` (need the Postgres URL I unset, excluded by `-k "not live"` in the directive),
`tests/enterprise/mcp_auth/*`, `tests/codemie/service/oauth/adapters`, `tests/codemie/service/mcp`, `tests/codemie/service/security/test_logout_token_cleanup_mock_tms.py`. None touch usage_attribution.

## Files touched
src/codemie/service/usage_attribution/framework_sessions.py, worker.py, framework_merge.py. `.codemie/` and doctor.json untouched.
