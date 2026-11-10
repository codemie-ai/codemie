# Technical Research

**Task**: workflow context reset output key template validation
**Generated**: 2026-10-29
**Research path**: codegraph

---

## 1. Original Context

Jira EPMCDME-16499, Story "Workflow Node Contract Validation — 1. Bug Fix: Full Reset Plus Output Key and Template Corpus Harness"; the full ticket text is also in `<run_dir>/work-item.md`. THIS RUN IMPLEMENTS ONLY THE `bug-fix-codemie` SUB-TASK (repository: codemie backend, this repo). The `bug-fix-codemie-docs` sub-task (codemie-docs repo) is out of scope for this run.

Context (verbatim from ticket):
- A full context reset combined with an explicit output key on the same transition crashes the node today (OQ-7). The spec makes fixing it part of this delivery.
- The iteration key depends on the shape of the output (spec v6 §2.8, corrected to match EPMCDME-11725). A list output is iterated whole and the key is ignored, which built-in templates rely on with placeholder keys such as `_`. For a dictionary output, a missing key fails the run with a non-retryable error.
- Nodes can already declare what they produce (output contract), but nothing declares what a node needs. Saving checks structure, cross-references and resources layer by layer and stops at the first failing layer. Existing workflows keep saving and running as they do today.

Acceptance Criteria for this sub-task (spec FR-20261006-001 v6.0):
1. Given a transition with a full context reset and an explicit output key, when the node runs, then it does not fail and the context afterwards holds only the output key [AC-40 runtime] → bug-fix-codemie.
2. Given the 64 built-in workflow templates, when an offline test saves each through today's validation pipeline, then none produces an error, and the test fails if one does [AC-43 harness, D2] → bug-fix-codemie.

Sub-task: bug-fix-codemie — reset + output key fix, offline template corpus harness — codemie, M (16), sdlc-standard.

Decisions: D2 — Story 1 adds the offline harness (every built-in template passes today's pipeline with no errors); Story 2 (later) runs the linker over it and adds the warning ceiling.

Out of scope: nested/constraint validation, sub-workflow contracts, type coercion, canvas affordances, cycle analysis, re-validation on every canvas edit, output repair loop, CLI execution command.

Constraints: branch backend work from origin/main (the local branch epmcdme-16499 is currently 1 commit behind origin/main). The workflow-versioning feature (yaml_config_history, /versions routes) exists in nearby files — do not duplicate. New columns/tables would need Alembic migrations; this sub-task should need none.

Research focus: where the transition context-reset (full reset) and output-key handling are implemented in the workflow runtime, the exact crash path when both are set, existing tests around it, and where the 64 built-in workflow templates live and how workflow configs are saved/validated today (the "validation pipeline"), so an offline pytest harness can save every template without DB/network and fail on any error.

---

## 2. Codebase Findings

### Existing Implementations

Reset and output key (runtime):
- `src/codemie/core/workflow_models/workflow_models.py:190` `WorkflowNextState` — fields `output_key: Optional[str]` (l.198), `clear_context_store: bool | Literal["keep_current"] = False` (l.231; True = full reset, "keep_current" = keep only this node's new values), `reset_keys_in_context_store`, `store_in_context`, `append_to_context`.
- `src/codemie/workflows/nodes/base_node.py` `BaseNode`:
  - `_prepare_resolved_context_store` (l.663-703): `clear_context_store is True` returns `None` (l.681-683) before anything else, including `store_in_context` and the new values. "keep_current" adds sentinel `CONTEXT_STORE_KEEP_NEW_ONLY_FLAG` (l.696).
  - `_add_output_key` (l.580-603): runs after the context store is prepared (called at l.739 from `finalize_and_update_state`, l.705). With an `output_key` it does `final_state[CONTEXT_STORE_VARIABLE][output_key] = ...` (l.600 for `append_to_context`, l.603 otherwise).
  - `_build_base_final_state` (l.523) copies the prepared store into `final_state[CONTEXT_STORE_VARIABLE]`, so after a full reset that value is `None`.
- `src/codemie/workflows/models.py:39` reducer `add_or_replace_context_store(left, right)`: `right is None` returns `{}`; `right` carrying `CONTEXT_STORE_KEEP_NEW_ONLY_FLAG` replaces `left` with `right` (flag popped); otherwise merges (append-marker values accumulate, `CONTEXT_STORE_DELETE_MARKER` keys dropped). Constants: `src/codemie/workflows/constants.py:78-79`.
- `src/codemie/workflows/checkpoint_saver.py:111` `update_last_checkpoint(..., output_key)` also writes `context_store[output_key]` (interrupt/resume path); l.145-154 guards on missing store.

Exact crash path (read from source, not executed): `clear_context_store: true` and `output_key: X` on the same `next` → `_prepare_resolved_context_store` returns `None` → `final_state[context_store] = None` → `_add_output_key` evaluates `None[output_key] = value` → `TypeError: 'NoneType' object does not support item assignment` inside the node. Applies to every `BaseNode` subclass that goes through `finalize_and_update_state` (agent, tool, custom, sub-workflow nodes).

Note on target semantics: AC says the context afterwards holds only the output key. The reducer only replaces `left` for `None` (-> `{}`) or when the keep-new-only flag is present; a plain `{output_key: v}` would be merged into the old store and keep old keys. `"keep_current"` (not full reset) keeps all parsed output keys plus the output key, so it is a different behaviour from the AC.

Template and validation side:
- `config/templates/workflow/*_template.yaml` — the built-in templates; 64 files counted (glob). `src/codemie/configs/config.py:113` `WORKFLOW_TEMPLATES_DIR = <repo>/config/templates/workflow`.
- `src/codemie/service/workflow_service.py:626` `WorkflowService.get_prebuilt_workflows()` — lists the dir, reads files ending `_template.yaml`, builds each with `WorkflowConfigTemplate.from_yaml`, silently skips (log only) any that fail; caches in `_cached_prebuilt_workflows`, sorted by name. `materialize_template` (l.668) substitutes `${input:var}` placeholders via `src/codemie/workflows/placeholders.py` (`materialize_template_source`, `extract_placeholder_names`).
- `src/codemie/core/workflow_models/workflow_config.py`: `WorkflowConfigBase.from_yaml` (l.191, returns `None` on pydantic/YAML error), `parse_execution_config` (l.240, builds assistants/tools/custom_nodes/states from `yaml_config`), `WorkflowConfigTemplate` (l.370; `from_yaml` falls back to `model_construct` when placeholders break typed fields).
- Validation pipeline = `src/codemie/workflows/workflow.py:224` `WorkflowExecutor.validate_workflow(workflow_config, user, error_format)`, called on create (`rest_api/routers/workflow.py:527` in `_persist_and_finalize_new_workflow`), on update (`workflow_service.py:182` `validate_for_update`), by `/validate` routes (`routers/workflow.py:792, 862`) and by the generator. In order:
  1. `validate_workflow_execution_config_yaml(yaml_config)` — JSON schema (`src/codemie/workflows/execution_config_schema.yaml`) then cross-references; implemented in `src/codemie/workflows/validation/schema.py:727` (imported via `codemie.workflows.validation`). Stops at the first failing layer. Legacy duplicate `src/codemie/workflows/config_yaml_validation.py` is not used by `workflow.py` but is still unit-tested.
  2. `workflow_config.parse_execution_config()` — pydantic `ValidationError` mapped by `PydanticErrorTransformer`.
  3. Non-autonomous mode with no states -> `STATE_MISSING_ERR`.
  4. `validate_workflow_config_resources_availability(workflow_config, user)` (`validation/resources.py`; older copy `config_resources_validation.py`) — hits DB/ES/toolkits: `Assistant.get_by_ids`, `ToolsService.find_toolkit_for_tool`, `MCPToolkitService.get_mcp_server_tools`, `IndexInfo.get_by_id`.
  5. `WorkflowExecutor.create_executor(...)._init_workflow()` — builds `WorkflowExecutionService` (constructor calls `_refresh_workflow_execution`, DB) and compiles the LangGraph (`_compile_graph`, l.397; `_inject_user_context`, l.415, which instantiates AgentNode/ToolNode/custom/sub-workflow nodes).

### Architecture and Layers Affected
- Workflow runtime node layer: `codemie/workflows/nodes/base_node.py` (state finalization), `codemie/workflows/models.py` (context-store reducer).
- Workflow config/validation layer (read-only for the harness): `workflows/workflow.py`, `workflows/validation/*`, `core/workflow_models/*`.
- Static config: `config/templates/workflow/*.yaml`.
- Tests only for the harness (new test module; no production code needed to "save" templates).

### Integration Points
- `finalize_and_update_state` is called by node `__call__` paths; the LangGraph reducer on `AgentMessages.context_store` (`models.py:76`) decides replace vs merge.
- Pipeline step 4 and step 5 reach DB/ES/MCP; an offline harness must patch them (see 4).

### Patterns and Conventions
- Context-store mutation is expressed through sentinels consumed by the reducer (`CONTEXT_STORE_KEEP_NEW_ONLY_FLAG`, `CONTEXT_STORE_DELETE_MARKER`, `CONTEXT_STORE_APPEND_MARKER`), not by direct state edits.
- Prebuilt templates carry `slug`, top-level `name/description/mode`, and an `execution_config:` block; 10 of them contain `${input:...}` placeholders (30 occurrences) which persisted workflows treat as literal text.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/workflows/langgraph-workflows.md` (workflow patterns, P0 for this area), `.ai-run/guides/testing/testing-patterns.md` (tests beside behavior area, mock provider boundaries, narrowest scope), `.ai-run/guides/quality-gates.md` (`make ruff`, `make license-check`; Apache header required on new Python files). Not read in full beyond testing-patterns/quality-gates.

### Architectural Decisions
- Test docstrings in `tests/codemie/workflows/test_flag_interactions.py` and `test_context_store_management.py` record the intended flag semantics (clear before store; True clears everything, "keep_current" keeps new values only). No ADR found.

### Derived Conventions
- Per-flag behaviour documented in `WorkflowNextState` field docstrings (`workflow_models.py:200-260`).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/workflows/test_flag_interactions.py` — TC_FI_001..010; TC_FI_006 (l.327) and TC_FI_010 assert `clear_context_store=True` yields `context_store is None or == {}` (no `output_key` set). Uses a `MockNode(BaseNode)` plus `WorkflowState`/`WorkflowNextState`, calling `node(state_schema)`.
- `tests/codemie/workflows/test_context_store_management.py` — TC_CSM_003 (clear flag), TC_CSM_011/012 ("keep_current" vs True).
- `tests/codemie/workflows/test_base_node_lifecycle.py` — `test_state_finalization_with_output_key_configuration` (l.445), `test_append_to_context_output_key_wraps_with_sentinel` (l.1325), `test_append_to_context_false_output_key_sets_direct_key` (l.1370).
- `tests/codemie/workflows/test_checkpoint_saver.py` — `output_key` update path.
- No existing test combines `clear_context_store=True` with `output_key`.
- Validation: `tests/codemie/workflows/test_placeholder_no_rejection.py` is the precedent for running `WorkflowExecutor.validate_workflow` offline: it patches `codemie.workflows.workflow.validate_workflow_config_resources_availability` and `WorkflowExecutor.create_executor` (returns a MagicMock whose `_init_workflow` returns None), with `user = MagicMock()`. Also `test_config_resources_validation.py`, `test_config_yaml_validation.py`.
- Templates: `tests/codemie/service/test_workflow_service.py` (~l.443-495, prebuilt loading with `tmp_path`/monkeypatched `WORKFLOW_TEMPLATES_DIR`); `tests/codemie/rest_api/routers/test_workflow.py`. No test iterates the real 64 templates.

### Testing Framework and Patterns
- pytest with `pytest-env` and `pytest-mock` (checked by an autouse session fixture); `tests/conftest.py` session-autouse `mock_database_engine` patches `PostgresClient.get_engine`, so DB is mocked by default. Run via `poetry run pytest`.

### Coverage Gaps
- Reset + output key combination (runtime); real-template corpus through `validate_workflow`; `_init_workflow` compilation of real templates is not exercised anywhere offline.

---

## 5. Configuration and Environment

### Environment Variables
- `pytest.ini` `env =` block sets `ENV`, `REPOS_LOCAL_DIR`, `PG_URL` for tests. `WORKFLOW_TEMPLATES_DIR` is a `Path` config attribute, not an env var.

### Configuration Files
- `src/codemie/workflows/execution_config_schema.yaml` — JSON schema for `execution_config`; defines `output_key` (l.176), `clear_context_store` (oneOf boolean | "keep_current", l.184).
- `config/templates/workflow/` — corpus. No built-in template uses `clear_context_store` (grep: none), so the runtime fix does not touch template behaviour.

### Feature Flags and Deployment Concerns
- No flag governs this sub-task; the ticket's two platform flags belong to later stories. No migration needed (no model/column change in what was read).

---

## 6. Risk Indicators

- Existing tests TC_FI_006/TC_FI_010 accept `None` or `{}` for a full reset without an output key; a fix that changes the no-`output_key` result would break the reducer contract (`None` -> `{}`). Keeping the no-key branch as is is the lower-risk reading.
- Reducer mutates `right` (`pop` of the flag) and, with the keep-new-only flag, assigns `right` wholesale without resolving `CONTEXT_STORE_APPEND_MARKER` values, so full reset + `append_to_context` + output key would leave a raw marker dict in the store (existing behaviour of "keep_current" + append; unverified at runtime).
- Speculative: the fix must also decide what `store_in_context=false` and `reset_keys_in_context_store` mean next to a full reset with an output key; no spec text read for those combinations.
- `get_prebuilt_workflows()` swallows load failures (log only) and caches; a harness built on it alone would pass vacuously with fewer than 64 templates. The count of 64 and the file list need asserting.
- 10 templates have `${input:...}` placeholders; `WorkflowConfigTemplate.from_yaml` may return a `model_construct` object (unvalidated states) for those, so "saving" them via the template object differs from saving `template.yaml_config` as a `WorkflowConfig`.
- `workflow_autonomous_template.yaml` (`mode: Autonomous`, no `states:`): by reading, base `_compile_graph` (`workflow.py:399`) does `workflow_config.states[0]` and `SupervisorWorkflowExecutor` does not override `_init_workflow`/`_compile_graph`, so the real step 5 may raise `IndexError` for it. Not executed; confirm before deciding how the harness treats the compile step.
- Pipeline steps 4 and 5 touch DB/ES/MCP/LLM-dependent code; harness fidelity vs offline-ness is a design choice (patching `create_executor` as in `test_placeholder_no_rejection.py` skips graph compilation and node construction).
- Duplicate validation modules (`config_yaml_validation.py` vs `validation/schema.py`, `config_resources_validation.py` vs `validation/resources.py`): `workflow.py` uses the `validation` package; patch targets must be on `codemie.workflows.workflow.*`.
- Versioning feature (`yaml_config_history`, `WorkflowConfig.update`) lives in `workflow_config.py`/`workflow_service.py`; not touched by this sub-task.

---

## 7. Summary for Complexity Assessment

The runtime fix sits in one class: `BaseNode._prepare_resolved_context_store` / `_add_output_key` / `finalize_and_update_state` in `src/codemie/workflows/nodes/base_node.py`, with the context-store reducer in `src/codemie/workflows/models.py` deciding replace vs merge. Today a full reset (`clear_context_store: true`) returns `None` for the store and `_add_output_key` then indexes into `None`, so the node raises `TypeError`. No schema, model or migration appears to be involved; the YAML schema already accepts both keys. The change surface is 1-2 source files plus tests; semantics to settle are how "only the output key survives" is expressed through the reducer and how it interacts with `append_to_context`.

The harness is test-only: 64 templates in `config/templates/workflow/`, loaded via `WorkflowService.get_prebuilt_workflows()` or directly from `WORKFLOW_TEMPLATES_DIR`, pushed through `WorkflowExecutor.validate_workflow` with the resource check (and possibly executor creation) patched, following the pattern in `tests/codemie/workflows/test_placeholder_no_rejection.py`. Unknowns that drive effort: whether all 64 currently pass (not run), the placeholder templates, and the autonomous template with no states, plus whether the compile step is in or out of scope for "today's pipeline".

Test posture: strong existing unit coverage of context-store flags and `output_key` separately (TC_FI, TC_CSM, base_node_lifecycle), none for their combination, none over the real template corpus. Key risks are listed in section 6; the largest are vacuous-pass of the harness if load failures are swallowed and the autonomous template's compile path.

---

## 8. External References

None named by the task (the ticket text is in `<run_dir>/work-item.md`, read; spec FR-20261006-001 v6.0 is cited by id only and no path was given).
