# EPMCDME-16499 Full Reset + Output Key Fix and Template Corpus Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A transition with `clear_context_store: true` plus `output_key` no longer crashes and leaves only the output key in the context; an offline test proves all built-in templates pass `WorkflowExecutor.validate_workflow`.

**Architecture:** Runtime fix reuses the existing `CONTEXT_STORE_KEEP_NEW_ONLY_FLAG` sentinel so the unchanged reducer replaces the prior store. The harness is a new parametrized test that builds a `WorkflowConfig` per template file and runs the real validation pipeline with DB/resource and executor creation patched.

**Tech Stack:** Python, pytest, PyYAML, LangGraph reducer in `codemie/workflows/models.py`.

## Global Constraints

- Backend repo only. Branch work from `origin/main`.
- No migration, no YAML schema change, no template change (unless Task 2 exposes a failing template; then fix it and report it separately).
- Do not change the reducer `add_or_replace_context_store`, `"keep_current"` semantics, `checkpoint_saver`, `get_prebuilt_workflows()`, or anything in workflow versioning.
- New Python files need the Apache header (copy from `tests/codemie/workflows/test_placeholder_no_rejection.py:1-13`).
- Commit per task using the repository's existing convention.

---

### Task 1: Full reset keeps only the output key

**Files:**
- Modify: `src/codemie/workflows/nodes/base_node.py:680-683` (`_prepare_resolved_context_store`) and `:597-603` (`_add_output_key`)
- Create: `tests/codemie/workflows/test_reset_with_output_key.py`

**Interfaces:**
- Consumes: `CONTEXT_STORE_KEEP_NEW_ONLY_FLAG` (already imported in `base_node.py`), `add_or_replace_context_store` from `codemie.workflows.models`.
- Produces: no new symbols; behavior change only.

Test-first: yes — a node with `clear_context_store=True` and `output_key="k"` currently raises `TypeError: 'NoneType' object does not support item assignment`; the new tests fail on that error.

- [ ] **Step 1: Write failing tests.** In the new module, define a `MockNode(BaseNode)` and the fixtures exactly as in `tests/codemie/workflows/test_flag_interactions.py:44-75`, and build the node and call `node(state_schema)` as in `:341-358` (prior `context_store` `{"old1": "v1"}`, execute result `{"new": "data"}`). Cases:
  1. `clear_context_store=True, output_key="k"`: no exception; `add_or_replace_context_store(prior_store, result[CONTEXT_STORE_VARIABLE])` has exactly the key `"k"` and equals `result["k"]`.
  2. Same plus `append_to_context=True`: reduced store equals `{"k": [result_value]}`, where `result_value` is the value the node would write (use `processed_output`; derive it from a run with `append_to_context=False`).
  3. Same plus `store_in_context=True` and `reset_keys_in_context_store=["old1"]`: store still only `"k"` (reset wins).
  4. `clear_context_store=True` with no `output_key`: `result[CONTEXT_STORE_VARIABLE] is None` and reducer result is `{}`.
  5. `clear_context_store="keep_current"` with `output_key`: still keeps parsed keys (`"new"`) plus `"k"`.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/workflows/test_reset_with_output_key.py -v`. Expected: cases 1-3 FAIL with `TypeError`; 4-5 pass.
- [ ] **Step 3: Implement.** In `_prepare_resolved_context_store`, for a full reset return `{CONTEXT_STORE_KEEP_NEW_ONLY_FLAG: True}` when `state_next.output_key` is set, else keep returning `None`. In `_add_output_key`, when `append_to_context` is true and `clear_context_store is True`, write `[actual_value]` to `final_state[CONTEXT_STORE_VARIABLE][output_key]` instead of the append-marker dict (the reducer does not resolve markers on the keep-new-only path). Other branches stay as they are. Update the docstring of `_prepare_resolved_context_store` return type note.
- [ ] **Step 4: Run** the new module plus `tests/codemie/workflows/test_flag_interactions.py tests/codemie/workflows/test_context_store_management.py tests/codemie/workflows/test_base_node_lifecycle.py`. Expected: all PASS (TC_FI_006/010 unchanged).
- [ ] **Step 5: Commit** using the repository's convention.

---

### Task 2: Offline template corpus harness

**Files:**
- Create: `tests/codemie/workflows/test_workflow_templates_validation.py`

**Interfaces:**
- Consumes: `WORKFLOW_TEMPLATES_DIR` from `codemie.configs.config` (`config.py:113`), `WorkflowConfig` (`core/workflow_models/workflow_config.py`), `WorkflowExecutor.validate_workflow(config, user, error_format="string")`.
- Produces: module-level `TEMPLATE_FILES = sorted(Path(WORKFLOW_TEMPLATES_DIR).glob("*_template.yaml"))` used by both tests.

Test-first: no — the harness is itself the test; it must pass against current code. If a template fails, that is the finding: fix the template (not the validator) and report it separately.

- [ ] **Step 1: Write the module** with an Apache header and three parts, following the patch pattern of `tests/codemie/workflows/test_placeholder_no_rejection.py:53-62`:
  - `test_template_corpus_is_complete`: asserts `len(TEMPLATE_FILES) >= 64`, and for each file that `yaml.safe_load` returns a mapping with an `execution_config` mapping (collect offenders and fail with their names). Prevents a vacuous pass.
  - `test_template_saves_without_error`, parametrized over `TEMPLATE_FILES` with `ids=[p.name ...]`: load the file, build `WorkflowConfig(name=data["name"], description=data.get("description", ""), mode=data["mode"], yaml_config=yaml.safe_dump(data["execution_config"]))` (keeps `${input:...}` as literal text), use `user = MagicMock()` with `user.project_names = ["app"]`, patch `codemie.workflows.workflow.validate_workflow_config_resources_availability` and `WorkflowExecutor.create_executor` (MagicMock returning an object whose `_init_workflow` returns None), then call `WorkflowExecutor.validate_workflow(...)`. Any raised exception fails the case; do not catch it.
  - Do not use `WorkflowService.get_prebuilt_workflows()` and do not compile graphs.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/workflows/test_workflow_templates_validation.py -v`. Expected: all PASS, 64+ cases. If a case fails, read the error, fix only that template YAML under `config/templates/workflow/`, rerun, and note it in the task report.
- [ ] **Step 3: Prove the gate bites.** Temporarily break one template (for example delete a required key), confirm its case fails by name, then revert. Do not commit the break.
- [ ] **Step 4: Commit** using the repository's convention.

---

## Self-review notes

- Spec coverage: AC 1-3 map to Task 1 cases 1-5; AC 4-5 to Task 2; AC 6 to the Global Constraints.
- negative-constraints: honored. No reducer change (Task 1 edits only `base_node.py`), `"keep_current"` pinned by case 5, `get_prebuilt_workflows()` and graph compilation avoided in Task 2, no checkpoint_saver, schema, migration or versioning changes, and no Story 2 linker work.
