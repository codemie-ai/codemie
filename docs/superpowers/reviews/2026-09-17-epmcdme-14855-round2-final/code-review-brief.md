# Code review — 2026-09-17-epmcdme-14855-round2-final (2026-09-17)

**request-changes** · confidence: low · 11 blocking · 0 deferred · 2 filtered as noise
Coverage: blind ✓ · edge-case ✓ · verification-gap ✓ · acceptance — n/a (no spec)  (3/4 lenses ran)

## Look here first

- `src/codemie/rest_api/handlers/assistant_handlers.py:1064` — [security] `_build_timeout_result` skips the `HIDE_AGENT_STREAMING_EXCEPTIONS` redaction `_send_error_to_thread` applies; a queue-poll timeout still ships a raw stacktrace — CR-010
- `src/codemie/agents/langgraph_agent.py:866` — [security] unclassified exceptions (category INTERNAL) never hit the new redaction gate; `InternalError.details.traceback` still leaks the full trace to the client — CR-003
- `src/codemie/rest_api/handlers/assistant_handlers.py:1277` — [infra] `A2AAssistantHandler.stream_generator` still calls `queue.get()` with no timeout, unlike the two sibling paths this diff fixed — can hang forever — CR-011
- `src/codemie/agents/langgraph_agent.py:1519` — [other: concurrency] `_on_tool_error`'s abort counter doesn't distinguish parallel tool-call batches from real consecutive retries — CR-005
- `src/codemie/agents/langgraph_event_adapter.py:228` — [other: state] echo-dedup guard drops an entire mixed update, not just the subagent echo key — CR-006

## Also flagged

- `commit c20340587 / 962c19748` — [other: git-workflow] two commit subjects use a branch-slug prefix instead of `EPMCDME-####:` — CR-001
- `src/codemie/agents/callbacks/agent_streaming_callback.py:405` — [other: concurrency] `_inflight_run_ids` discard only checks the current callback's author bucket — CR-002
- `src/codemie/agents/langgraph_agent.py:1166` — [other: robustness] `_check_truncation_in_chunk` assumes a 3-tuple chunk whenever `self.subagents` is set — CR-004
- `src/codemie/rest_api/handlers/assistant_handlers.py:533` — [infra] `_get_queue_poll_timeout_seconds` has no floor/clamp, unlike its sibling — CR-007
- `src/codemie/rest_api/handlers/assistant_handlers.py:763` — [infra] queue-poll timeout re-resolved from config on every streamed chunk, no cache — CR-008
- `src/codemie/rest_api/handlers/assistant_handlers.py:1053` — [infra] queue-poll timeout doesn't cancel the still-running agent, only stops reporting it — CR-009

## Checked and clean

code-quality ✓ · security ✓ (secret-handling/untrusted-input scope only — see CR-003/CR-010 above for the stacktrace-leak gaps this narrower audit didn't cover)
