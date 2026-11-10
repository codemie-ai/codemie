# EPMCDME-16499 / bug-fix-codemie: full reset + output key, template corpus harness

Scope: codemie backend only. Spec FR-20261006-001 v6.0, AC-40 (runtime) and AC-43 (harness, D2).

## Problem

1. A transition with `clear_context_store: true` and an `output_key` crashes the node.
   `BaseNode._prepare_resolved_context_store` returns `None` for a full reset
   (`src/codemie/workflows/nodes/base_node.py:680-683`). `finalize_and_update_state` puts that `None` into
   `final_state[context_store]`, and `_add_output_key` (l.580-603) then assigns into it and raises `TypeError`.
   This hits every node that goes through `finalize_and_update_state`.
2. Nothing proves the 64 built-in templates (`config/templates/workflow/*_template.yaml`) pass the save pipeline.
   `WorkflowService.get_prebuilt_workflows()` skips templates that fail to load and only logs, so it cannot serve as a gate.

## Design

### A. Runtime fix (AC-40)

- When `clear_context_store is True` and an `output_key` is set, `_prepare_resolved_context_store` returns a store
  holding only the existing keep-new-only sentinel (`CONTEXT_STORE_KEEP_NEW_ONLY_FLAG`) instead of `None`.
  `_add_output_key` then adds the output key. The reducer `add_or_replace_context_store`
  (`src/codemie/workflows/models.py:39-71`) already replaces the prior store when it sees that flag.
  After the node, the context holds exactly `{output_key: value}`.
- Full reset without `output_key` is unchanged: it still returns `None`, which the reducer turns into `{}`.
  TC_FI_006 and TC_FI_010 pin this.
- A full reset wins over `store_in_context`, parsed output values and `reset_keys_in_context_store`. Only the output key
  survives. This matches the existing precedence, where the clear is checked first.
- With `append_to_context`, a full reset writes `[value]` (a one-element list) for the output key directly,
  not the append marker. The keep-new-only branch of the reducer does not resolve markers, so a marker would be stored
  as a raw dict. The reducer is not changed, so `"keep_current"` behaviour is untouched.
- `"keep_current"`, the sentinel flow and the reducer are otherwise not modified.

### B. Offline template corpus harness (AC-43, D2)

New test module under `tests/codemie/workflows/` (Apache header required).

- Discover templates from `WORKFLOW_TEMPLATES_DIR` by globbing `*_template.yaml`. Do not use `get_prebuilt_workflows()`.
- Parse each file. Build a `WorkflowConfig` from the top-level `name`, `description` and `mode`, with
  `yaml_config = yaml.safe_dump(execution_config)`. This mirrors what `WorkflowConfigTemplate.from_yaml` produces, and it
  keeps `${input:...}` as literal text, as `test_placeholder_no_rejection.py` shows persisted workflows do.
- Run each through the real `WorkflowExecutor.validate_workflow(config, user, error_format="string")`, with
  `validate_workflow_config_resources_availability` and `WorkflowExecutor.create_executor` patched on
  `codemie.workflows.workflow`, as in `test_placeholder_no_rejection.py`.
  The JSON-schema layer, the cross-reference layer, `parse_execution_config` and the missing-states check run for real.
- Parametrize per template so a failure names the template and the error. Any raised error fails that case.
- Guard against a vacuous pass: a separate test fails if fewer than 64 templates are discovered or any file cannot
  be parsed as a YAML mapping with an `execution_config` mapping.

## Acceptance criteria

1. A node with `clear_context_store: true` and an `output_key` completes without raising. The resulting context store
   equals `{output_key: value}`, with prior keys and parsed output keys gone.
2. The same holds with `append_to_context: true`: the store is `{output_key: [value]}`.
3. Full reset without `output_key` still yields `None` (reducer result `{}`). `"keep_current"` and the other
   flag-interaction tests (TC_FI, TC_CSM, base_node_lifecycle) pass unchanged.
4. The harness runs offline, with no DB, network or LLM.
5. All discovered templates (at least 64) save through `validate_workflow` with no error. The test fails if any template
   errors, and fails if fewer than 64 are found.
6. No migration, no change to the YAML schema, no change to existing templates (unless AC 5 exposes a failing one, see Risks).

## Non-goals

- The `bug-fix-codemie-docs` sub-task (codemie-docs repo).
- The linker, the warning ceiling and any Story 2 work.
- Node input contracts, nested or constraint validation, sub-workflow contracts, type coercion, canvas affordances,
  cycle analysis, re-validation on canvas edits, the output repair loop, and a CLI execution command.
- Changing the reducer, `"keep_current"` semantics or the iteration-key rules.
- Fixing the resume path in `checkpoint_saver.update_last_checkpoint`, graph compilation in the harness, resource
  availability checks, or `get_prebuilt_workflows()` behaviour.
- Anything in the workflow-versioning feature (`yaml_config_history`, `/versions` routes).

## Risks

- The 64 templates have not been run through the pipeline. If one fails today, the harness exposes it. Then the
  template is fixed (D2 says every template passes today), and that fix is reported separately.
- Patching `create_executor` skips graph compilation, so compile-time defects are not covered. This includes
  `workflow_autonomous_template.yaml`, which has no states.
- Branch backend work from `origin/main`; the local branch is one commit behind.
