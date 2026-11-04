# Lens: edge-case — EPMCDME-14855 round-3 final

Range: `1824ed8c7..168e3ad06`. Frozen diff: `code-review.local.diff`.

## Matrix walked

| # | Edge case | Result |
|---|---|---|
| 1 | Parallel batch of N tool calls, all fail → streak must advance once | Covered: batch-tail gate (`if not inflight: count += 1`); `test_parallel_tool_errors_in_one_batch_count_once` |
| 2 | N consecutive failing batches → cap still trips | Covered: `test_repeated_failing_parallel_batches_still_trip_the_cap` |
| 3 | Genuine sequential retries → streak advances each time | Covered: `test_sequential_tool_errors_still_count_each` |
| 4 | Replay start under None + end/error under real author | Covered: all-bucket discard in agent + callback, 4 dedicated tests |
| 5 | 2-tuple chunk while `subagents` truthy / 3-tuple while falsy | Covered: shape-based strip, 2 tests in `test_langgraph_truncation.py` |
| 6 | Mixed supervisor update tick (echo + supervisor key) | Covered: filtered dict, 2 tests in adapter tests |
| 7 | Queue poll timeout = 0 / negative | Covered: `max(1, …)` clamp, 2 tests |
| 8 | A2A producer never feeds queue → bounded poll | Covered: `asyncio.to_thread(get, timeout=…)` + `queue.Empty` branch; `test_a2a_stream_abandoned_producer_yields_timeout_result` |
| 9 | A2A sync path unaffected | Yes — `_handle_a2a_sync` untouched; only streaming path changed |
| 10 | `HIDE_AGENT_STREAMING_EXCEPTIONS` + INTERNAL error | Covered: `redact_for_streaming` strips `details['traceback']`; `test_redact_for_streaming…` pins both categories |
| 11 | `error_details` built when `include_tool_errors=False` | Guarded: `error.model_dump(…) if include_tool_errors and error else None` — no crash, no leak |
| 12 | `internal.details` is None | Handled: `dict(self.internal.details or {})` |
| 13 | `run_id=None` on tool events | `str(None)` key no-ops in discard; single-failure-with-no-start still counts (intended: unknown-origin error = one failed round) |
| 14 | Hedged handler inherits changed static→instance method | Verified: `HedgedAssistantHandler(StandardAssistantHandler)`; call sites at assistant_handlers.py:818 and hedged_handler.py:394 use `self._get_…` — instance method resolves |

## Source-verified reachability (not just diff-read)
- `import queue` present (assistant_handlers.py:17) → `except queue.Empty` valid.
- `A2AAssistantHandler.__init__` calls `super().__init__(assistant, …)` →
  `self.assistant.id` in `_build_timeout_result` resolves.
- `ThreadedGenerator.thoughts` exists (core/thread.py:59) → save_chat_history OK.
- `subagent_descriptions` always a dict (default `{}` at langgraph_agent.py:189)
  → `key not in self.agent.subagent_descriptions` cannot raise TypeError.
- `_handle_a2a_stream` has exactly one call site (line 1207), updated with the
  new kwargs — no stale callers of the old signature.

## Residual edge notes (non-blocking)
1. **Batch-tail gate is not thread-safe by design**: `_on_tool_error` mutates
   plain dicts/sets from LangGraph callback threads. Within one graph run the
   tool events arrive serialized through the callback bridge, so the read-modify
   window is benign — consistent with the pre-existing
   `_consecutive_tool_error_count` design. Test fixtures bypass the bridge, so
   the tests do not exercise real interleaving; acceptable for this fix.
2. **Extreme corner — parallel batch spanning two authors, errors interleaved
   per-author**: each author's tail advances its own streak once; a batch split
   across authors counts one failed round *per author* rather than per batch.
   Defensible (per-author streaks are the unit of abort), not a defect.
3. **Duplicate error event for the same run_id**: discard is idempotent, but a
   duplicated error with an already-empty inflight set advances the streak
   twice. Requires a producer bug upstream to trigger; log-visible, self-heals
   on next success.
4. **Clamp floor of 1s**: a misconfigured 0 now means "poll every second" rather
   than "error loudly". Deliberate trade-off per R3-CR-007; the config default
   remains authoritative for normal setups.

## Verdict
No production-blocking edge case found. Lens clean.
