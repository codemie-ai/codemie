# Code review — 2026-09-16-epmcdme-14855-backend-final (2026-09-16)

**request-changes** · confidence: low · 11 blocking · 0 deferred · 5 filtered as noise
Coverage: blind ✓ · edge-case ✓ · verification-gap ✓ · acceptance — n/a (no spec)  (3/4 lenses ran)

## Look here first

- `src/codemie/agents/langgraph_agent.py:848` — [security] `_send_error_to_thread`/`stream()` sends full agent/tool error detail unconditionally, bypassing the `include_tool_errors=False` default — CR-004
- `src/codemie/agents/langgraph_event_adapter.py:229` — [other] multi-agent — new subagent-echo dedup guard compares a string key against a list of compiled graph objects, so it never matches and never filters anything — CR-007
- `src/codemie/agents/langgraph_agent.py:1075` — [other] supervisor-state — `_invoke_agent`'s fatal-error path never resets `_supervisor_coordinator`, unlike `stream()` — CR-005
- `src/codemie/rest_api/handlers/assistant_handlers.py:409` — [infra] fixed 300s queue timeout closes the queue but never cancels the background agent thread, which keeps running — CR-010
- `src/codemie/core/exceptions.py:296` — [other] error-classification — tool-error abort is deliberately misclassified as a token-limit error via string coupling — CR-008

## Also flagged

- `src/codemie/agents/langgraph_agent.py:142` — [config] no floor on the tool-error-cap dynamic-config value; 0/negative aborts on the first error — CR-003
- `src/codemie/agents/langgraph_agent.py:142` — [infra] tool-error-cap config re-read from the DB on every tool error, no caching — CR-002
- `src/codemie/agents/langgraph_agent.py:1133` — [other] standards — new `_check_truncation_in_chunk` param left unannotated (code-quality.md) — CR-006
- (commit history) — [other] git-workflow — 9/27 commit subjects don't match the documented format — CR-011
- `src/codemie/agents/callbacks/agent_streaming_callback.py:408` — [other] concurrency — stale in-flight run_id may never clear on an author mismatch (reachability unconfirmed) — CR-001
- `src/codemie/core/thread.py:72` — [other] concurrency — unsynchronized `is_closed`/`send` race can drop one late queue item — CR-009

## Checked and clean

security ✓ · 0 deferred
