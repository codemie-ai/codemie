---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: ac12a9b35274eada06a5605f432e1001bd3a415f
---

# Code review — 2026-10-22-supervisor-token-limit-loop

**Decision:** approve (scoped final round, confidence final low · check high · scoped-final low)  
**Reviewed head:** `ac12a9b35` · **Rounds:** final → check → final → final → check → final (scoped)

**Closed:** 9 fixed · 0 moot · 2 decided

Addressed two review comments on the first approved version (abort signal not propagated to sibling
sub-agents; text-only truncation wrongly treated as an error), then iterated through the findings each
fix surfaced in turn: CR-001/CR-002 (the abort flag was per-instance and invisible across the separate
`LangGraphAgent` each sub-agent gets, and `invoke()` ignored `success=False` on an aborted stream) are
resolved by moving the flag onto the shared `ThreadedGenerator` (`mark_consumer_aborted`/
`is_consumer_aborted`, the same object `is_closed()` already uses cross-instance) and adding the same
success check `generate()`/`invoke_task()`/`invoke_with_a2a_output()` already have. Re-verifying that
fix surfaced three more: a false ELK alert from routing a benign abort through `handle_agent_exception`
(fixed, static fallback message), `generate()`'s success-path duration metric reusing a pre-persistence
timestamp (fixed, recomputed after persistence/token-counting), and the abort `except Exception`'s
scope (**decided, not changed**: narrowing it to `StreamAbortedException` only would exclude
`TokenLimitExceededException` - this ticket's own headline scenario - and `GeneratorExit` cannot reach
that clause since `_stream_graph` is not itself a generator; documented in a comment). The next round
found the abort-path `except` block never called `stream.close()` (fixed, mirrors the `should_stop`
branch) and that the new `invalid_tool_calls` gate has no matching check in the shared truncation
detector (**decided, not changed**: the same assumption already backs
`LangGraphEventAdapter._finalize_truncated_text` from EPMCDME-15787, unrelated to and predating this
ticket; extending the shared detector is a separate, riskier change out of scope here). A check round
verified both against current source and found nothing new. Running the `sanity-api` test harness
end-to-end then surfaced what no diff-scoped review could: `thread_generator` is typed as
`ThreadedGenerator` but workflow execution actually substitutes a `ThoughtQueue` or `DualQueue` -
neither had the new abort-flag methods, so every workflow run failed with
`AttributeError: 'ThoughtQueue' object has no attribute 'mark_consumer_aborted'`. Fixed by adding
`mark_consumer_aborted`/`is_consumer_aborted` to both (mirroring each's existing `is_closed` pattern)
and declaring both on the `MessageQueue` Protocol; a scoped final round reviewing only that fix (not
the rest of the branch) found 0 blocking findings and independently confirmed no further
`MessageQueue`-shaped class was missed. The harness re-run after the fix: 196 passed, 4 skipped, 4
rerun, 0 failed.

## Open — 0

## Coverage

Graded at the final round; later rounds re-verify specific findings, not these rows - Open and Closed
above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| Supervisor node: no further LLM call after its own last tool-call response was truncated | pass | Hook wired via `_compose_pre_model_hooks` in `_build_supervisor_agent`; test asserts `supervisor_model.call_count==1`. |
| Sub-agent node: same stop, scoped to that sub-agent's own execution | pass | Hook wired in `_build_single_agent`; test shows `child_model.call_count==1`, supervisor only does the handoff call. |
| Disconnect/hedging-cancel: consumer-aborted run stops the node's next model call | pass | Hook checks `is_closed()`/`is_consumer_aborted()` before the model call; test asserts `call_count==0`. |
| Negative case: normal non-truncated tool-call response, open non-cancelled stream, unaffected | pass | Real compiled-graph test: tool-call then final answer, `call_count==2`, result unchanged. |
| Verification: a real, unmocked `create_supervisor().compile()` graph asserts no LLM call after truncation | pass | `test_langgraph_supervisor_truncation_stops_loop.py` builds an unmocked graph with a recording fake model. |
| New `pre_model_hook` composed in both `_build_supervisor_agent` and `_build_single_agent` via existing wiring | pass | `pre_model_hooks.py` adds `_build_truncation_abort_pre_model_hook`; wired at `langgraph_agent.py:323,367`. |
| Truncation check reused from the existing instance-bound detector, not a third detector | pass | Hook calls `agent._safe_check_for_truncation`, wrapping the pre-existing `_check_for_truncated_response` (unmodified). |
| New `StreamAbortedException`, distinct from `TokenLimitExceededException`, for disconnect/hedging-cancel | pass | `core/exceptions.py` adds `StreamAbortedException(Exception)`; confirmed not a `TokenLimitExceededException`/`ValueError`. |
| `StreamAbortedException` swallowed silently near `.stream()`; no error sent to a listener-less thread | pass | `stream()` logs info and skips `_send_error_to_thread`; test asserts it was not called. |
| A raise from the hook propagates unmodified out of the node through the compiled graph | pass | Real-graph tests assert the raw exception surfaces from `app.invoke()`. |
| Non-goal: `langgraph_event_adapter.py`'s existing truncation path left untouched | pass | File not present in the diff. |
| Non-goal: `get_truncation_indicator`/`_check_for_truncated_response` dedup out of scope | pass | No changes to those functions; hook only reuses the instance method - see the `invalid_tool_calls` decision above. |
| Non-goal: no graceful degradation, stop remains a hard failure with no new recovery behavior | pass | Exceptions propagate out of the whole graph invocation in real-graph tests; no new catch/continue added. |
| Non-goal: no change to the API-boundary `agent_token_limit` error formatting | pass | `proxy_router.py` untouched; `TokenLimitExceededException` still reaches the pre-existing generic error path. |

Standards: commit-format pass · code-quality pass · security pass
