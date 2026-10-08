# EPMCDME-15759 (revised) Persist workflow yaml_config as sent; only model ids may change

**Goal:** `_rewrite_yaml_config_from_objects` stops re-serializing typed objects. The user's YAML is stored exactly as sent (orphaned_states, meta_states, comments, key order, no pydantic defaults, no injected `enable_summarization_node`); the only permitted change is the `model` value of assistants/custom_nodes that `_normalize_workflow_model_ids` rewrote (base_name -> deployment_name).

**Architecture:** Inside the helper, parse the original YAML and compare each typed assistant/custom_node `model` with the `model` of the original dict entry with the same `id`. No difference: return without touching `yaml_config` (byte-for-byte). Difference: patch only those `model` values in the parsed dict and `yaml.safe_dump(..., sort_keys=False)` it. Helper signature and the two callers (`create_workflow` ~603, `update_workflow` ~652, which keeps its exception -> HTTP 400 mapping) are unchanged. Builds on e83a74052 and 63a1b61e5 with new commits on top; no history rewrite.

**Format-preservation decision:** `ruamel.yaml` is not a project dependency (only an optional extra of another package in `poetry.lock`), so it is not used. A text-level `model:` substitution is rejected: matching a scalar to its owning `id` inside raw text is fragile (quotes, trailing comments, flow style). PyYAML dump is used only on the changed-model path; comments and formatting are lost there, as they were before this work.

Commit per task using the repository's existing convention. Run narrow tests only: `poetry run pytest tests/codemie/rest_api/routers/test_workflow.py -k <name>`, then `make ruff`.

## Acceptance criteria

- A minimal YAML (assistants id/model/system_prompt, `tools: []`, `custom_nodes: []`, states id/assistant_id/next.state_id/resolve_dynamic_values_in_prompt) posted or put is persisted byte-for-byte when no model id changes: no defaults, no `enable_summarization_node`, comments and multiline prompts intact.
- When a `base_name` model is normalized, the persisted YAML differs from the sent YAML only in those `model` values; orphaned_states, meta_states, retry_policy, type, pool_config and all other keys are unchanged.
- Empty or non-mapping YAML does not raise in the helper and is left untouched.
- Create and update behave as before otherwise; an exception in the rewrite on update still yields HTTP 400.
- No schema, model, config or migration change; harness and UI repos untouched.

## Downstream audit (done while planning; no code change needed)

- `WorkflowService._update_workflow_values` (`workflow_service.py:783-819`) re-parses via `parse_execution_config()` (defaults come from the pydantic models, not the stored text) and `_yaml_content_changed` compares parsed dicts, so as-sent YAML yields no spurious history on no-op saves. A first save of a previously expanded workflow with a minimal YAML adds one history entry, which is correct.
- `get_workflow` and `parse_execution_config` (`workflow_config.py:239-275`) default every absent key (`enable_summarization_node` True, `skill_ids` [], retry_policy, etc.), so execution, validation (`WorkflowExecutor.validate_workflow` validates the sent text against the JSON schema) and the UI do not need defaults persisted.
- MCP strip/validation (`workflow.py:205`, `:525`) mutates typed objects after the rewrite; the old rewrite had already dumped before the strip, so behaviour is identical.
- `llm_retirement_service._update_yaml_config_field` (parse, replace, dump) and `preconfigured_workflows.py` string replace operate on whatever text is stored; they do not depend on expanded keys.
- Existing tests that depend on the old contract are listed in the tasks below (all in `tests/codemie/rest_api/routers/test_workflow.py`).

---

### Task 1: Leave YAML untouched; patch only changed model ids

**Files:** `src/codemie/rest_api/routers/workflow.py:128-153` (helper), `tests/codemie/rest_api/routers/test_workflow.py:349-481` (helper tests from 63a1b61e5)

**Test-first:** yes — helper test on a minimal YAML (with a comment and a `|` multiline prompt) calls `parse_execution_config()` then `_rewrite_yaml_config_from_objects` and asserts `config.yaml_config == original_text`; fails today because the output carries every pydantic default plus `enable_summarization_node: true`.

- [ ] Add a `minimal_yaml_config` fixture string mirroring the harness payload (see acceptance criteria) with a comment and a multiline prompt. Adapt the existing helper tests in place; do not duplicate them:
  - `test_rewrite_yaml_config_preserves_orphaned_and_meta_states` and `test_rewrite_yaml_config_preserves_other_top_level_keys`: merge into one test over `yaml_config_with_foreign_keys` asserting `config.yaml_config == yaml_config_with_foreign_keys` (byte-for-byte, covers orphaned/meta and the other keys).
  - Add (a): the minimal-YAML byte-for-byte test, plus `"enable_summarization_node" not in yaml.safe_load(...)` and no `limit_tool_output_tokens` / `retry_policy` default keys.
  - `test_rewrite_yaml_config_reflects_normalized_models_and_keeps_foreign_keys` (b): keep the `config.assistants[0].model = "gpt-4o-2024-11-20"` setup; assert `loaded` equals `yaml.safe_load(yaml_config_with_foreign_keys)` with only `assistants[0]["model"]` replaced (this proves orphaned_states, meta_states, retry_policy, type, pool_config, tools, states carry no injected defaults). Add a custom_nodes variant only if the fixture gains a custom node.
  - `test_rewrite_yaml_config_tolerates_missing_or_non_mapping_yaml` (c): keep the parametrization over `None`, `""`, `"- a"`; change the assertion from the owned-keys dict to `config.yaml_config == raw_yaml` and no exception.
  - `test_rewrite_yaml_config_keeps_summarization_flag_and_drops_none_values`: delete; its intent (explicit `enable_summarization_node: false` survives, no summarization limits invented) is covered by the byte-for-byte test on `yaml_config_with_foreign_keys`.
- [ ] Run, confirm red (minimal-YAML and model-change tests fail on the current helper).
- [ ] Rewrite the helper body. Keep `original = yaml.safe_load(workflow_config.yaml_config or "")` and return early when it is not a dict. Add one new private function, then call it for `assistants` and `custom_nodes`; update the helper docstring to say only model ids are rewritten and everything else is stored as sent:

```python
def _patch_model_ids_in_yaml(config: dict[str, Any], section: str, items: list | None) -> bool:
    """Copy normalized item.model values into the parsed YAML entry with the same id."""
    entries = {e["id"]: e for e in config.get(section) or [] if isinstance(e, dict) and "id" in e}
    changed = False
    for item in items or []:
        entry = entries.get(item.id)
        if entry is not None and "model" in entry and item.model and entry["model"] != item.model:
            entry["model"] = item.model
            changed = True
    return changed
```

  In the helper: evaluate both sections (do not short-circuit), and only if either returned True set `workflow_config.yaml_config = yaml.safe_dump(original, sort_keys=False)`. Remove the now-unused `owned_config` building; no `exclude_none` dumps remain.
- [ ] Run the helper tests (`-k rewrite_yaml_config`), then `make ruff`.

### Task 2: POST and PUT persistence tests on the literal stored YAML

**Files:** `tests/codemie/rest_api/routers/test_workflow.py` — `test_create_workflow` (:318-346), `test_create_workflow_persists_orphaned_and_meta_states` (:522), `test_update_workflow_persists_orphaned_and_meta_states` (:689); new minimal-YAML and model-change tests beside them

**Test-first:** yes — POST `/v1/workflows` and PUT `/v1/workflows/{id}` with `minimal_yaml_config`; `create_workflow.call_args.args[0].yaml_config` / `update_workflow.call_args.args[1].yaml_config` must equal the sent text exactly (red against the current helper, which expands defaults; confirm by running before Task 1 lands, or with `git stash push src/codemie/rest_api/routers/workflow.py` then restore).

- [ ] Adapt the two existing `*_persists_orphaned_and_meta_states` tests: add `assert <captured yaml_config> == orphaned_meta_yaml_config` (literal, byte-for-byte); keep the loaded-dict assertions. Both already patch the same stack as the placeholder tests, so reuse it.
- [ ] Add POST and PUT tests (a) with `minimal_yaml_config`: captured yaml equals the sent text, `enable_summarization_node` absent from the loaded dict. Copy the patch set from the neighbouring tests; do not call the helper to build expectations.
- [ ] Add POST and PUT tests (b): patch `codemie.rest_api.routers.workflow.llm_service.get_allowed_chat_models` to return one `LLMModel` whose `base_name` is the sent model and `deployment_name` a different id (and `_get_project_for_workflow` to `None`), send `orphaned_meta_yaml_config` with the base_name model, and assert the captured YAML loads to the sent dict with only that assistant's `model` replaced (orphaned_states and meta_states included). Check how existing tests construct `LLMModel` before writing the stub.
- [ ] `test_create_workflow`: keep the mirror (it still builds the expectation with both helpers and stays valid, since the helper is deterministic), but add a literal assertion that `workflow_executor.call_args.kwargs["workflow_config"].yaml_config == create_workflow_request.yaml_config`. If the unpatched `llm_service` normalizes that fixture's model, drop the literal assertion and keep only the mirror; the byte-for-byte coverage lives in the new tests.
- [ ] Run `-k "create_workflow or update_workflow"`, confirm the full file is green, then `make ruff`.

---

## Self-review

- Coverage: AC1 -> T1(a), T2(a); AC2 -> T1(b), T2(b); AC3 -> T1(c); AC4 -> callers and 400 mapping untouched (helper signature unchanged; existing update tests stay in the T2 run); tests to update are named in T1 and T2.
- negative-constraints:
  - Do not rewrite history: new commits only; no task amends e83a74052 or 63a1b61e5.
  - Do not reuse v1 titles: titles are new.
  - No injected defaults, no `enable_summarization_node` or summarization limits: honored by T1 (no typed dump) and asserted by T1(a)/T2(a).
  - No ruamel unless already a dependency: it is not, so not used (see decision above).
  - No schema/model/config/migration change: only `workflow.py` and the test file are touched.
  - Out of scope, recovery of workflows saved since 6d40ebfa1: no task.
  - Harness and UI repos: untouched.
  - Tests must not derive expectations from the helper: T1/T2 assert literal text; the retained `test_create_workflow` mirror is supplemented by a literal assertion.
