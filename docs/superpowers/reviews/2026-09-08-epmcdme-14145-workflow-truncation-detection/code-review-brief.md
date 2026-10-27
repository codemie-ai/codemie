# Code review — 2026-09-08-epmcdme-14145-workflow-truncation-detection (2026-09-08)

**request-changes** · confidence: high · 3 blocking · 3 deferred · 2 filtered as noise
Coverage: edge-case ✓ · acceptance ✓ · standards ✓ · blind — n/a (balanced profile) · verification-gap — n/a (balanced profile)  (3/3 applicable lenses ran)

## Resolution (2026-09-08)

- **fixed**: CR-001 (`langgraph_agent.py:628` now carries an inline comment documenting why no lock is needed — `init_agent_node`'s two call sites never pass a persistent `assistant=`, so `generate_execution_context` always builds a fresh instance; a regression test, `test_tc_imr_019_send_reachable_node_never_caches_persistent_assistant` in `tests/codemie/workflows/test_iteration_map_reduce.py`, now asserts a Send-reachable node's `AgentNode.assistant` stays `None` and that repeated `generate_execution_context()` calls on the same node instance return distinct assistant objects, so a future regression of the invariant the removed lock guarded fails a test instead of silently corrupting a response)
- **fixed** (2026-09-09): CR-002 — added `else` branch in `_detect_terminal_truncation` (`langgraph_agent.py:1274-1277`) that resets `_last_finish_reason=None`/`_last_truncated=False` when no truncation indicator is found, satisfying spec.md:52's per-supervisor-turn reset requirement; the pre-existing `xfail` regression test `test_detect_terminal_truncation_resets_flags_for_later_non_truncated_turn` now passes and its marker was removed
- **fixed** (2026-09-09): CR-004 — added `is_finish_reason_truncated` guard inside the `is_valid_ai_message` branch of both `parse_message_type` and `parse_supervisor_message_type` (`langgraph_event_adapter.py`), so non-streaming terminal messages carrying both non-empty content and a truncated finish_reason now trigger `_detect_terminal_truncation` regardless of the content-routing branch taken

## Look here first

- `src/codemie/agents/langgraph_agent.py:1250` — [other: correctness] `_detect_terminal_truncation` sets `_last_truncated=True` on a match but never resets it on a non-match; a truncated intermediate/subagent turn can leak into a later, genuinely-complete final answer within the same call — CR-002
- `src/codemie/agents/langgraph_event_adapter.py:146` — [other: correctness] the new truncation check is gated behind `is_valid_ai_message` (non-empty content); non-streaming LLM invocations yield one chunk with both full content and truncated metadata, so detection never runs for them — CR-004
- `src/codemie/agents/langgraph_agent.py:628` — [other: concurrency] a commit removed the `threading.Lock` an earlier review round (`8ace3e510`) had added to guard `_last_finish_reason`/`_last_truncated`, with no rationale given and no replacement guard. Re-verified independently: no path today invokes the same `LangGraphAgent` instance concurrently (pool checkout is exclusive; the `Send`-based parallel-iteration fan-out always builds a fresh, unshared instance per branch; the only persistent-instance path routes one speaker at a time, never concurrently) — so not a live bug, but the removal leaves the invariant unenforced against a future regression of the same corruption class. Downgraded critical → major — CR-001

## Checked and clean

commit-format ✓ · code-quality ✓ · security ✓ · 3 deferred → code-review-deferred.md (incl. a confirmed but pre-existing Vertex AI/Gemini gap in the unmodified `_get_truncation_indicator` allow-list — `aws_bedrock` and `azure_openai` were verified fully covered)
