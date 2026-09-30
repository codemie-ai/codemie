DONE_WITH_CONCERNS
commit: 7e709885e
paths: src/codemie/enterprise/mcp_auth/_callback_pages.py, src/codemie/enterprise/mcp_auth/_diagnostics.py, tests/enterprise/mcp_auth/test_oauth2_callback_bridge.py
test_command: poetry run pytest tests/enterprise/mcp_auth/test_oauth2_callback_bridge.py tests/enterprise/mcp_auth/test_mcp_auth_status_bridge.py  (68 passed; RED before impl: 3 failed as expected)

files:
- _callback_pages.py: no-opener success branch reports, then closes tab unless keep-open, with fallback.
- _diagnostics.py: client diagnostics log line now includes phase (one-line addition).
- test_oauth2_callback_bridge.py: three script-branch tests plus one phase-diagnostics WARNING test.

renames: none
reused helpers: _CALLBACK_FALLBACK_DELAY_MS, CALLBACK_KEEP_TAB_OPEN gate, sendDiagnostics, _sanitize_log_value, _build_enabled_client
standards applied: TDD RED seen (JS branch lacked close/gate; log lacked phase) then GREEN; doubled braces kept in f-string; ruff format and check clean; Apache header untouched; git-workflow commit format; context7 MDN check of Window.close() and Window.closed.

Ruling: _diagnostics.py was edited though not named in Files. spec B3 assumed phase is already logged for result=error, but only the timeout message carried it. The required test could not pass otherwise. Change is one appended `phase=` field; no model or status change.
Ruling: no-opener fallback and keep-open text reuse CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE (existing no-opener text); no new constants, CLOSE_MESSAGE stays opener-only.
Ruling: opener path left byte-identical; the close/fallback block is duplicated in both branches (two copies, left apart per DRY rule).
Ruling: discovered-flow /status authenticated test already exists (test_build_discovered_auth_status_response_uses_flow_store_and_tms, store_token=True), so none added.
Ruling: script tests are text-based (matches file convention); "true does not close" is asserted as close reachable only after the KEEP_TAB_OPEN gate, message in its else branch. Opener-branch test is a regression guard and passed before the change.
Concern: with the frontend cutting the opener, every success beacon has opener_present=false, so the diagnostics endpoint logs WARNING for each success (existing rule in _diagnostics.py). Log noise; not changed.
Concern: window.close() may still be blocked by browsers for non-script-opened tabs (MDN: script-closable only); the 300 ms fallback message covers that.
