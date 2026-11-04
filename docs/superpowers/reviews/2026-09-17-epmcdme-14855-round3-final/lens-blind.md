# Lens: blind (author/event continuity) — EPMCDME-14855 round-3 final

Range: `1824ed8c7..168e3ad06` (8 commits). Frozen diff: `code-review.local.diff`.

## Focus
Hunt for author-continuity blind spots — paths where an author value could go
`str -> None -> str` (or change identity) between related events, leaving
per-author keyed state (`_inflight_run_ids`, `_consecutive_tool_error_count`,
`_inflight_tool_run_ids`) stranded or double-counted.

## What the diff changes (state-keyed surfaces)
1. `agent_streaming_callback.py` `on_tool_end` / `on_tool_error`: discard run_id
   from **every** author bucket instead of only the event's own author (R3-CR-002).
2. `langgraph_agent.py` `_on_tool_start/_end/_error`: new `_inflight_tool_run_ids`
   mirrors the callback's inflight bookkeeping; all-bucket discard on end/error;
   streak advances only when the author's own inflight set empties (R3-CR-005).
3. `langgraph_event_adapter.py` `parse_supervisor_update_type`: mixed update ticks
   now drop only echoed subagent keys instead of the whole update (R3-CR-006).
4. `langgraph_agent.py` `_check_truncation_in_chunk`: namespace strip keyed on
   chunk shape (`len(chunk) > 2`), not the `subagents` flag (R3-CR-004).
5. `core/errors.py` + handlers: shared `redact_for_streaming()` under
   `HIDE_AGENT_STREAMING_EXCEPTIONS`, applied in `generate()` error path and
   `_build_timeout_result`; A2A `stream_generator` gets a bounded queue poll.

## Author-discontinuity paths checked
- **Resume replay**: `_replay_tool_start_on_resume` starts tools under
  `author=None`; the real end/error arrives under the adapter-resolved author.
  The all-bucket discard in both the callback and the agent covers this exactly.
  Tests `test_replayed_start_under_none_is_cleared_by_end_under_author` /
  `..._error_under_author` pin it.
- **Supervisor echo suppression**: previously a mixed update tick
  (`{subagent: …, supervisor: …}`) was dropped wholesale; now only subagent keys
  are filtered, so supervisor-owned messages in the same tick are still parsed.
  `test_parse_supervisor_update_type_processes_non_subagent_keys_alongside_echo`
  pins it; the echo-only early return is preserved by its companion test.
- **`author=None` inflight bucket growth**: the None bucket is created on
  replay-start and always emptied by the all-bucket discard on end/error; no
  permanent leak path remains.

## Residual notes (non-blocking)
- R3-CR-002 remains a patch over a deeper design smell: the replay path can
  start a tool under a *different author identity* than the real event. If a
  future change introduces a third author identity for the same run_id, the
  all-bucket discard still removes it (idempotent by run_id string), so the
  failure mode is contained.
- `_check_truncation_in_chunk` strips the namespace but does not restore it;
  the strip is local to the check, and the same shape logic governs the rest of
  the pipeline (`_prepare_chunk_for_processing`), so no divergence.

## Verdict
No author-discontinuity defect found. Lens clean.
