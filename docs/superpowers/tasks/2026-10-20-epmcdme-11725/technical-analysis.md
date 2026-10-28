# Technical Research

**Task**: workflow iterator langgraph
**Generated**: 2026-10-20
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-11725 (Bug): Workflow iterator stops execution and marks success when input list is missing.
Users should receive correct workflow execution status and clear failure details when an iterator is configured with an invalid or missing input list. Currently, if the iterator cannot find the list in input (wrong key, or input without list), workflow execution stops and is marked as SUCCEEDED. Step ResultFinalizerNode also is not triggered (toggle is on). Expected: workflow execution fails; execution step details give clear failure reason; not marked successful.
Affected areas: workflow execution engine, iterator input validation logic, workflow status/result handling, execution step error reporting.
Acceptance criteria:
- When iterator cannot find the configured list in input, workflow execution is marked as failed.
- Execution details show a clear and actionable error message explaining that the iterator input list could not be found.
- Workflow is not marked as successful when iterator configuration is invalid due to missing list input.
- Users can identify the iterator failure reason from execution step details without additional investigation.
- Regression testing confirms correct behavior for valid list input, empty list input, and missing list input scenarios.
Note: repo guides in .ai-run/guides/ (workflows/langgraph-workflows.md is the P0 guide). Do not touch Dockerfile or Makefile.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/workflows/workflow.py` `WorkflowExecutor.continue_iteration` (~L731-791): the conditional-edge router for any transition with `next.iter_key`. Returns `list[Send]`, one per item.
  - If `iter_key in state_schema and not finish_iteration`: `items_to_process = [state_schema[iter_key]]`.
  - Else parses `state_schema[ITER_SOURCE]` via `parse_from_string_representation` (`workflows/utils/utils.py:111`, returns the raw string if no JSON found), then:
    - `iter_key` starting with `/` -> `get_iterable_items_by_pointer` (L696; `UnwrappingJsonPointerEvaluator` in `workflows/utils/json_utils.py`; wraps a non-list result as `[result]`; appears to raise ValueError on missing path, not verified in full).
    - otherwise -> `get_iterable_items_by_key` (L705): list source -> itself; dict -> `source.get(iter_key, [])` (**silent `[]` when key is missing**); anything else (string, no JSON) -> `[source]`.
  - L755 `isinstance(items_to_process, list)` check is effectively dead for the key path (always a list, unless dict value is a non-list).
- Root-cause path: a dict without the key yields `[]`, so `continue_iteration` returns `[]`. LangGraph treats an empty Send list as no next node: the graph ends silently, `_run_workflow_execution` returns normally, `_execute_workflow_stream` (L1021-1033) sets `workflow_succeeded=True` and calls `workflow_execution_service.finish()` (SUCCEEDED). The ResultFinalizerNode is only reached via edges (`init_workflow_edges` L798, `RESULT_FINALIZER_NODE -> END`), so it is skipped too.
- Failure reporting already exists: any exception raised in the router propagates to `_execute_workflow_stream` except-branch -> `_handle_task_exception` (L1248): creates a state "AI/Run Thoughts", sends a Thought, `finish_state(... FAILED, output=error_message)`, `workflow_execution_service.fail(error_class, error_message)`. So raising a descriptive exception in `continue_iteration` is enough to satisfy status + step-detail ACs.
- Ambiguity to resolve in spec: a legitimately empty list (`{"items": []}`) must remain non-failing (existing test TC_IMR_016 asserts `[]` Send list). Missing key (`.get` default) and empty list are currently indistinguishable; also a nested/inner iteration (`ITERATION_NODE_NUMBER_KEY` set) can legitimately see empty collections. Non-JSON source string currently becomes a single-item list `[source]` (arguably "input without list").
- `src/codemie/core/workflow_models/workflow_models.py`: `WorkflowNextState.iter_key` (L192), `include_in_iterator_context` (L262), validator L330 (iter_key vs state_ids); `WorkflowState.finish_iteration` (L381).
- `src/codemie/workflows/exceptions.py`: home of workflow-scoped exceptions (FeatureDisabledError, etc.); no iterator exception exists.

### Architecture and Layers Affected
Workflow engine only: `WorkflowExecutor` routing (`workflow.py`), status/error handling already in executor + `WorkflowExecutionService`. No API, DB or config layer is involved by the root cause.

### Integration Points
`continue_iteration` is wired in `_handle_single_state` (L815-824) via `add_conditional_edges`; `workflow.py:944` also references `state.next.iter_key`. `ITER_SOURCE` set in `nodes/base_node.py:561` and `nodes/transform_node.py:728`. Only `workflow.py` calls `get_iterable_items_by_key/pointer`.

### Patterns and Conventions
Guide `langgraph-workflows.md`: extend `WorkflowExecutor`/nodes, no separate execution path. Errors are raised as exceptions and handled centrally by `_handle_task_exception`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/workflows/langgraph-workflows.md` (23 lines, generic; nothing about iterators or failure handling). Also `development/error-handling.md` (typed exceptions) is relevant.

### Architectural Decisions
None recorded for iterator semantics.

### Derived Conventions
Typed exceptions in `workflows/exceptions.py` (message in `__init__`); errors surface as FAILED via `_handle_task_exception`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/workflows/test_iteration_map_reduce.py` (TC_IMR_001..018): calls `executor.continue_iteration(state_schema, workflow_state)` directly; includes JSON pointer (004), `finish_iteration` (010), empty inner list (016, expects `[]`).
- `tests/codemie/workflows/test_workflow_state_transitions.py` (TC_WST_005-007): iter_key start.

### Testing Framework and Patterns
pytest; fixtures `mock_user`, `mock_thought_queue`, `basic_workflow_config`; `@patch('codemie.workflows.workflow.WorkflowExecutor' ... 'WorkflowExecutionService')`; state_schema dicts with `ITER_SOURCE` JSON strings.

### Coverage Gaps
No test for missing key, non-dict/no-list source, or missing JSON pointer; no test that a router exception yields FAILED status via `_execute_workflow_stream`.

---

## 5. Configuration and Environment

### Environment Variables
None relevant.

### Configuration Files
`enable_summarization_node` (default True in `workflow_config.py:110`) controls ResultFinalizerNode edges; `execution_config_schema.yaml` governs YAML schema (no change evidenced).

### Feature Flags and Deployment Concerns
None. Dockerfile and Makefile are modified in the working tree and must not be touched.

---

## 6. Risk Indicators

- Speculative: raising on missing key must not break TC_IMR_016 (empty list) or nested iterations where `ITER_SOURCE` of an inner node legitimately lacks the key; the distinction missing vs empty needs a sentinel (e.g. `key in source`) rather than `.get(..., [])`.
- Speculative: the non-JSON-string fallback `[source]` and wrapping of non-list pointer results are deliberate lenient behaviours; changing them risks regressions for existing workflows. Scope decision for spec.
- Risk: pointer-missing path behaviour not fully verified; confirm `UnwrappingJsonPointerEvaluator.get_node_by_pointer` raises (and with what message).
- Risk: error text must name the iter_key and state id to satisfy "actionable"; `_handle_task_exception` records it on a generic "AI/Run Thoughts" state, not the iterating state (acceptable per ACs but note).
- No guide coverage of iterator semantics.

---

## 7. Summary for Complexity Assessment

The bug is localized to `WorkflowExecutor.continue_iteration` / `get_iterable_items_by_key` in `src/codemie/workflows/workflow.py`. A missing key silently yields `[]`, so the LangGraph router emits no Sends, the graph terminates without reaching ResultFinalizerNode, and the executor treats clean termination as SUCCEEDED. Existing exception handling (`_handle_task_exception`) already marks the execution and a step FAILED with the error message, so a raised, descriptive exception at the router is the natural integration point; change surface is about one source file (optionally one exception class in `workflows/exceptions.py`) plus tests.

Technical novelty is low; the main subtlety is distinguishing missing-key from legitimately empty lists and nested iteration cases. Test posture is good: a direct-call test file for `continue_iteration` exists with clear patterns to extend (valid, empty, missing). Gaps: no missing-key or end-to-end FAILED-status tests.

---

## 8. External References

None named by the task
