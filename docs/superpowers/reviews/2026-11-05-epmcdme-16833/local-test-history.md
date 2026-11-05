# EPMCDME-16833 - superseded local pytest run (history, not the current verification)

Bound to the pre-rebase head `47a963027` (rebased: `43fb535cc`), base `0c4e0ea44`, Windows host, Postgres 17 container. It is separate from the manual test-harness run in the MR's `## Test harness` section and does not say the local tests passed.

Targeted set (new `test_live_session_summary_totals.py`, `test_live_session_detail_consistency`, `test_live_summary_counters`, `test_live_session_page`, `test_reader`, analytics handler tests, API model tests and `test_live_dispatch_gate`): **1345 passed, 7 failed**.

The 7 failures are all in `test_live_dispatch_gate.py`:

- 5 x `get_session_detail_dispatches() missing 'scope'` and 1 x `LocalAnalyticsFilter() missing 'scope'`: the tests were written before the `scope` argument existed (stale test signatures, not touched by this change);
- 1 x Windows `OSError` from `datetime.fromtimestamp` in `otlp.py` (host-specific, file not in the diff).

Provenance of "also fails on the base": the author's notes record that the failures were reproduced on a detached worktree at `0c4e0ea44`, later removed. The raw baseline log of `test_live_dispatch_gate` was not retained, so that statement rests on the notes. What exists as raw output: the run after the change (7 failed, same causes) and a separate baseline log of the new test file only. No broad baseline replay was done to settle it.

Other local findings, not caused by this change as far as inspected: `test_live_summary_skill_names.py` 12 failed (`get_skill_names_by_session() missing keyword-only 'scope'`, stale tests); `test_dirty.py` `edge_of_the_calendar` Windows `OSError` in `otlp.py` (not rerun on the base). The full `cli_analytics/postgres` directory run was cut by a timeout and not repeated.

After `0a8f2b181` (assertions only) the 20 tests of `test_live_session_summary_totals.py` pass.

Not verified here: `tests/codemie/rest_api/router_guards/test_openapi_snapshot.py` (enterprise-marked, needs `codemie-enterprise`). The scoped probe in `openapi-probe-result*.json` replaces it for the list and detail endpoints only.
