# EPMCDME-11348: Workflows global order by Total Runs

Written without superpowers:writing-plans (direct write, user-approved).
Repo: codemie (branch EPMCDME-11348-workflows-global-order, base main).
Commit per task using the repository's existing convention. Do not commit or push. Do not touch codemie-ui.

## Acceptance criteria

- The workflows terms aggregation orders buckets by `unique_executions` desc, then `_key` asc, so every page slice follows one global order.
- The workflows request body contains `order: [{"unique_executions": "desc"}, {"_key": "asc"}]` and no longer contains `{"_count": "desc"}`.
- All other workflows sub-aggregations are unchanged.
- `get_top_workflow_usage` still orders by `{"1-bucket": "desc"}`.
- `AggregationBuilder.build_terms_agg` accepts a dict or a list of dicts for `order`; runtime behaviour is unchanged and existing dict callers are unaffected.
- New unit tests pass; `make ruff` is clean; the relevant pytest subset passes.
- No changes under codemie-ui; nothing committed or pushed.

## Tasks

### Task 1: Widen the `order` annotation in AggregationBuilder

- File: `codemie/src/codemie/service/analytics/aggregation_builder.py:33` — change `order: dict[str, str]` to `order: dict[str, str] | list[dict[str, str]]`. Update the docstring at line 42 to mention the list form. No body change (line 68 passes `order` through).
- Note: the brief cites 34 call sites across 11 handler files; research counted 28 `build_terms_agg` callers. Either count is valid because the change is type-only.
- Add `tests/codemie/service/analytics/test_aggregation_builder.py`: characterization test that a list `order` passes through `build_terms_agg` unchanged in `terms.order`.

Test-first: no — pure annotation change; the characterization test pins pass-through behaviour and passes on both sides of the edit.

### Task 2: Use two-key order for the workflows aggregation

- Depends on Task 1.
- File: `codemie/src/codemie/service/analytics/handlers/workflow_handler.py:128` — replace `order={"_count": "desc"}` with `order=[{"unique_executions": "desc"}, {"_key": "asc"}]`. Leave all sub-aggregations (lines 89-122) and `_parse_workflows_result` untouched. Do not change `get_top_workflow_usage` or `_build_top_workflow_usage_aggregation` (line 298).
- Add `tests/codemie/service/analytics/handlers/test_workflow_handler.py` (pattern: `test_budget_handler.py`, calling the private builder and inspecting the body):
  - asserts the workflows request body `paginated_results.terms.order` equals the two-key list;
  - asserts `get_top_workflow_usage` still uses `{"1-bucket": "desc"}` (guard test; passes before and after).

Test-first: yes — `test_workflows_request_uses_unique_executions_then_key_order` fails on the current `{"_count": "desc"}` and passes after the edit.

## Negative-constraint pass

Source: requirements list items 1-7 (no formal Non-goals section).
- "Keep every other sub-aggregation unchanged" — honoured by Task 2 (only line 128 changes); verified by leaving lines 89-122 untouched.
- "Do not change get_top_workflow_usage" — honoured by Task 2 (guard test asserts `{"1-bucket": "desc"}`); no task edits lines 214-306.
- "Runtime behaviour unchanged" for AggregationBuilder — honoured by Task 1 (annotation and docstring only).
- "Do not commit or push" — no task includes a commit or push step.
- "Do not touch codemie-ui" — no task references that workspace.
- Manual ES check (seeded index `codemie_metrics_logs_seed`, expect wf-light-10 with 39 runs first) is verification, not a task.
- Assumption (recorded, not a task): the Kibana reference metric is the unique execution_id count, i.e. the MR's "Total Runs".

Quality gates (`make ruff`, pytest subset) are run by the calling flow, not planned as tasks.

negative-constraints: checked (4 explicit constraints, all honoured by tasks 1-2)
