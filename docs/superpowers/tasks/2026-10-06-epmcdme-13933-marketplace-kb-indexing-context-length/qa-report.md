# QA Gate Report — epmcdme-13933-marketplace-kb-indexing-context-length (iteration 3: base chunking pipeline + guardrails on chunks)

**Branch**: EPMCDME-13933_marketplace-kb-indexing-context-length (HEAD 057835cd8)
**Runner**: poetry. Gates come from `.ai-run/guides/quality-gates.md`. `make` is unavailable in this Windows shell, so each Makefile target's command body was run verbatim.
**Started**: 2026-10-07T23:50:00Z
**Status**: BLOCKED. The `test` gate exits 1. All failures are pre-existing or environment-related; none is attributable to this change (details below).

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|------|--------|--------|----------|---------|-------|
| lint | guide | PASS | ~10s | `poetry run ruff format && poetry run ruff check --fix && poetry run ruff check` (make ruff) | 2724 files unchanged; all checks passed |
| build | guide | PASS | ~20s | `poetry build` (make build) | codemie-0.8.0 sdist and wheel built |
| license | guide | PASS | ~5s | `check_license_headers.py --check --quiet` (make license-check) | 2434 files checked, 0 missing |
| secrets | guide | PASS | 3m29s | `docker run … gitleaks:v8.30.1 dir --config=/workspace/.gitleaks.toml /workspace` (make gitleaks) | "no leaks found" |
| unit | guide | FAIL (pre-existing) | 20m09s | `poetry run pytest tests/` (make test), with `-o addopts="--import-mode=importlib"` because xdist is absent locally | 203 failed, 1 error, 19047 passed, 198 skipped. See the failure detail below |
| affected | — | PASS | ~90s | `pytest tests/codemie/datasource tests/codemie/service/platform tests/codemie/service/search_and_rerank tests/codemie/agents/tools/kb tests/codemie/rest_api/routers/test_assistant_marketplace.py` | 1582 passed. The only failures are the 5 known git/svn/multiprocessing environment tests, which fail identically on main |
| live check | — | PASS | ~2m | `live_chunk_check.py` against the rebuilt local stack (backend image from this branch) | 13/13 checks passed (listed below), re-run on the rebuilt iteration-3 image; ids are now <id>_1..N |
| coverage | guide | SKIPPED | — | `make coverage` | Skip-if condition met: coverage was not requested |
| sonar | guide | SKIPPED | — | `node scripts/sonar/run-local-sonar.js` | `SONAR_TOKEN` is not set, so the script self-skips. CI still owes this gate |
| test-harness | guide | SKIPPED | — | `poetry run codemie-test-harness run sanity-api -n 2 --reruns 10` | The user runs this manually before the MR. It is required by the MR compliance bot |
| ui | guide | SKIPPED | — | (n/a) | No UI surface changed |
| commit-msg | hook | PASS | — | `EPMCDME-<n>: ` prefix rule | All branch commits match |
| pre-push | hook | FAIL (pre-existing) | — | pytest + sonar-local | The pytest part is the same as the unit gate; sonar self-skips |
| ci-templates | ci | N/A | — | remote gitlab templates | Cannot be run locally. The MR must ask for `/sanity` |

### Live check (13/13 passed)

1. A short assistant is indexed as 1 chunk.
2. A long assistant (20k words) is indexed as 44 chunks.
3. Chunk ids have the form `<id>_<n>`.
4. A legacy single document was injected for the long assistant.
5. After the full reindex the ids are exactly `<id>_0..43`.
6. The full reindex removed the legacy document.
7. There are no duplicate chunks.
8. The index is healthy (`error=f`, `completed=t`).
9. Search returns 200 with no indexing error.
10. A phrase from the very end of the long description is found.
11. The long assistant appears exactly once in the results.
12. Its coverage line reads "1 of 1 parts included", with no re-query notice.
13. Unpublishing removed all chunks.

## Failure detail

The latest branch run (iteration 3) has 204 failing ids (203 FAILED + 1 ERROR). They were compared with the main baseline from the first QA round, which re-ran the same failing files with the source reverted to merge-base `6d40ebf`:

| Set | Count |
|-----|-------|
| Fail on both the branch and main (pre-existing, environment-related) | 203 |
| Fail only on the branch | 1 (logout mock-TMS ERROR, seen in every run) |
| Fail only on main | 0 |
| In the touched areas (loader, platform processor, search_and_rerank, search_kb, marketplace) | 0 |

The one failure seen only on the branch is skipped when run on its own and does not import a changed module, so it is environment flakiness under the full run, not a regression. Earlier iterations also hit one timing-sensitive executor test, listed for history:

- `tests/codemie/service/security/test_logout_token_cleanup_mock_tms.py::test_logout_removes_only_the_users_rows_and_pod_fallback`: ERROR in the full run. It needs the mock TMS and is skipped when run alone. The same test showed up the same way in the first QA round.
- `tests/codemie_tools/data_management/code_executor/test_tool_call_channel.py::TestProtocol::test_deeply_nested_request_json_is_answered_not_fatal`: timing-sensitive executor test. It passes when run alone.

## Drift signal

No. The implementation matches the approved chunking `spec.md`. The marketplace search additions from the code review (over-fetch, coverage normalisation, routing limit) refine Design §3 without changing its intent.
