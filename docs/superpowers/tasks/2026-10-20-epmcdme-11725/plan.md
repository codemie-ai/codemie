# EPMCDME-11725: Iterator fails when its input list is missing

**Goal:** A missing iterator list raises a descriptive error from the router, so the existing `_handle_task_exception` marks the run and a step FAILED. Commit per task using the repository's existing convention. Do not touch Dockerfile or Makefile.

**Facts verified:** `get_iterable_items_by_key` (`src/codemie/workflows/workflow.py:705-712`) returns `source.get(iter_key, [])`, a silent `[]` for a missing key. `UnwrappingJsonPointerEvaluator.get_node_by_pointer` (`workflows/utils/json_utils.py:60,66`) already raises `KeyError`/`ValueError` on a missing path, but the message names neither the state id nor the iter_key as an iterator problem.

## Task 1: Typed exception and missing-key detection

**Files:** `src/codemie/workflows/exceptions.py`, `src/codemie/workflows/workflow.py:705-712` and `:743-746`, `tests/codemie/workflows/test_iteration_map_reduce.py`

- [ ] Add `IteratorInputNotFoundError(Exception)` in `exceptions.py`, next to `FeatureDisabledError` and following its style. `__init__(self, iter_key, state_id, detail="")` builds a message like `Iterator input list '<iter_key>' was not found in the input of state '<state_id>'. Check iter_key and the previous step output. <detail>`.
- [ ] In `get_iterable_items_by_key`, replace `source.get(iter_key, [])` with `source[iter_key]`, so a missing key raises `KeyError`. An existing empty list still returns `[]`. The list and `[source]` fallbacks stay unchanged.
- [ ] In `continue_iteration`, wrap only the two `get_iterable_items_by_*` calls (L743-746) in `except (KeyError, ValueError) as e`. Re-raise as `IteratorInputNotFoundError(iter_key, send_to_node, str(e)) from e`. This covers pointer and key paths in one place. Leave the `iter_key in state_schema` branch and the L755 check untouched.
- Test-first: yes — in `test_iteration_map_reduce.py` (direct-call pattern of TC_IMR_001..018) add:
  - missing key: `ITER_SOURCE='{"other": [1]}'`, `iter_key="items"`; expect `IteratorInputNotFoundError`, message contains `items` and the next state id.
  - missing pointer: `iter_key="/data/items"`, same expectation.
  - valid list returns N Sends.
  - empty list `{"items": []}` returns `[]` (keep TC_IMR_016 passing).
  - non-JSON string still returns `[source]`.

## Task 2: Execution marked FAILED end to end

**Files:** `tests/codemie/workflows/test_iteration_map_reduce.py`; production code only if the test fails

- [ ] Add one test that a router `IteratorInputNotFoundError` surfaces as a failed execution. Reuse the executor and `WorkflowExecutionService` patching from the file. Drive `_execute_workflow_stream`, or `_handle_task_exception` directly if the stream is too heavy to mock. Assert `workflow_execution_service.fail(...)` is called with a message containing the `iter_key` and the state id, and that `finish()` is not called.
- Test-first: yes — fails before Task 1 because no exception is raised and `finish()` is called (SUCCEEDED).

## Final check (not a task)

Existing `test_iteration_map_reduce.py` and `test_workflow_state_transitions.py` stay green, including nested iterations. The calling flow runs the full quality gates.

negative-constraints: honored. Empty list stays `[]` (Task 1 test). Lenient fallbacks stay unchanged. Nested iteration code is untouched. Dockerfile and Makefile are not in any task.
