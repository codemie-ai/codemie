# Lens: verification-gap — EPMCDME-14855 round-3 final

Range: `1824ed8c7..168e3ad06`. Question asked of every claim: *what proof backs
it, and does the proof actually cover the live path?*

## Standards-grade claims — code-verified

| Claim | Verification |
|---|---|
| Stacktrace/traceback cannot reach the client under `HIDE_AGENT_STREAMING_EXCEPTIONS` | Redaction is a single in-place method (`ErrorResponse.redact_for_streaming`) applied at both producers: `generate()` error path (langgraph_agent.py:871) and `_build_timeout_result` (assistant_handlers.py:556). `get_error()` reads the live mutated object — the detached-copy leak class from F2/R2-CR-010 is structurally closed |
| A2A stream cannot block forever | `await asyncio.to_thread(generator_queue.queue.get, timeout=queue_poll_timeout)` + `queue.Empty` → `_build_timeout_result` + `break`. Same bounded pattern as the Standard (line 818) and Hedged (hedged_handler.py:394) paths |
| One parallel batch = one failed round; cap still trips on repeated batches | Batch-tail gate + 4 dedicated unit tests incl. the cap-trips companion |
| Shape-mismatch chunk degrades instead of crashing | `len(chunk) > 2` strip; both mismatch directions tested |
| Echo filtering keeps non-echo keys | Filter builds a residual dict; early return only when empty; both directions tested |
| Inflight buckets never leak | All-bucket discard on end *and* error, in both the agent and the callback; `test_failed_batch_leaves_no_inflight_run_ids_behind` |

## Weak claims / coverage gaps (none blocking)

1. **`DynamicConfigService.get_typed_value_safe` contract is documented, not
   source-tested.** The clamp test monkeypatches the service, so the real
   method's return types (e.g. a string sneaking past `int` coercion) are
   unverified. `max(1, …)` would raise `TypeError` on `str`. Pre-existing
   coupling across the file; not introduced by this range.
2. **`__parse_supervisor_update_type` tests mock the caller boundary**
   (`_parse_tool_message` MagicMock), so the real message-parsing path under a
   mixed tick is covered only indirectly. The real-graph suite
   (`test_langgraph_tool_error_cap_real_graph.py`) covers the error-cap side but
   not mixed-tick parsing end-to-end.
3. **Real-graph suite initializes `_inflight_tool_run_ids = {}` manually in
   `_make_supervisor_agent()`** — evidence the attribute is now a required init
   invariant but no test fails if a future constructor refactor drops it (the
   mock bypasses `__init__`). A `__init__` presence assertion would close this.
4. **Deferred by decision, not by omission**: R2-CR-008 (cache the poll timeout)
   and R2-CR-009 (cancel the still-running producer on timeout) are documented
   WONTFIX/defer in `_TaskMY/EPMCDME-14855/findings.md` with rationale (measured
   ~0.36 ms/100 lookups; architectural scope). The A2A timeout path inherits the
   "producer keeps running" behavior — known, accepted for this round.

## Verdict
No unverified blocking claim remains. Lens clean, with the 4 documented
coverage notes above for the next review cycle.
