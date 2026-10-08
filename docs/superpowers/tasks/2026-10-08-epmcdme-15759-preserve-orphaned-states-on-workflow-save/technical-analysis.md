# Technical Research

**Task**: workflow yaml_config orphaned_states meta_states
**Generated**: 2026-10-08
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15759 — Backend defect (not a UI or test bug). On workflow create (src/codemie/rest_api/routers/workflow.py ~line 597) and update (~line 646), the helper _rewrite_yaml_config_from_objects() (added by commit 6d40ebfa1, EPMCDME-14349 "Project Level Model Availability") rebuilds yaml_config from only assistants, custom_nodes, tools and states. It silently drops `orphaned_states` and `meta_states`. Result: the UI sends an unconnected node (e.g. Tool_1 with a Code Executor tool) in the PUT body as `orphaned_states: [tool_1]` plus its position in `meta_states`; the save returns 200, but the stored YAML has `tools: [tool_1]` and no `orphaned_states`/`meta_states`. On reload the node is not rendered. Any unconnected node or saved node layout is lost on save. Expected fix: the helper must preserve `orphaned_states` and `meta_states` (and ideally any other top-level yaml_config keys it does not own) on create and update. Also check whether other code paths rewrite yaml_config the same way (e.g. import/clone/copy, other routers/services). Identify existing tests for this helper and for workflow create/update so the plan can add a failing-first test.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/routers/workflow.py:128-147` `_rewrite_yaml_config_from_objects(workflow_config) -> None`: builds a fresh dict with exactly these keys: `messages_limit_before_summarization`, `tokens_limit_before_summarization`, `enable_summarization_node`, `assistants`, `custom_nodes`, `tools`, `states` (list items via `model_dump(exclude_none=True)`), drops `None` values, then `workflow_config.yaml_config = yaml.safe_dump(..., sort_keys=False)`. The original YAML is discarded, so every other top-level key is lost.
- Only two callers (grep over src and tests): `create_workflow` (line 597, inside the `try/except/else` after `parse_execution_config()` succeeds, preceded by `_normalize_workflow_model_ids`) and `update_workflow` (line 646, inside a `try` that maps any exception to HTTP 400 `WORKFLOW_CONFIGURATION_ERROR`). Both run `parse_execution_config()` first, so the original YAML text is still available in `workflow_config.yaml_config` at the time of the rewrite.
- Introduced in `6d40ebfa1` (only commit that added it; it also split create into `_persist_and_finalize_new_workflow`).
- `src/codemie/core/workflow_models/workflow_config.py:239-275` `parse_execution_config()`: parses `yaml_config` and populates only typed fields (assistants, custom_nodes, tools, states, retry_policy, summarization limits, type, enable_summarization_node, recursion_limit, max_concurrency, verbose, max_iteration_key_output_limit, pool_config, max_nesting_level). `orphaned_states` / `meta_states` have no model field anywhere (grep: zero occurrences in models), so they exist only inside the YAML text. Rewriting from objects therefore cannot recover them.
- Other top-level keys also dropped by the rewrite (same root cause, wider than the ticket): `retry_policy`, `type`, `recursion_limit`, `max_concurrency`, `verbose`, `max_iteration_key_output_limit`, `pool_config`, `max_nesting_level`. All are allowed by `src/codemie/workflows/execution_config_schema.yaml`. Because `workflow_service.update_workflow` calls `stored_config.parse_execution_config()` on the (rewritten) yaml_config, these fall back to defaults on save (e.g. `type` -> `'generic'`, `retry_policy` -> default). Also, `enable_summarization_node` is parsed with default `True` when absent and then written explicitly into the rewritten YAML.
- `orphaned_states` is read by `src/codemie/workflows/validation/schema.py:249-275` (`_find_state_using_assistant/tool/node` search `states` and `orphaned_states` for error reporting), confirming it is a recognised yaml key. `meta_states` is referenced nowhere in `src`.
- `src/codemie/workflows/execution_config_schema.yaml`: top-level object schema lists known properties and has no `additionalProperties: false` (observed: only `properties:` and `$defs:` at top level), so extra keys such as `orphaned_states`/`meta_states` pass JSON-schema validation (`config_yaml_validation.py:256-275`). Cross-reference validation (`_validate_workflow_execution_config_cross_references`) was not read in detail.

### Architecture and Layers Affected
- API/router layer: `routers/workflow.py` (`create_workflow` line 576, `update_workflow` line 618, helper at 128).
- Model layer: `WorkflowConfig` / `WorkflowConfigBase` (`yaml_config: Optional[str]` text column).
- Service layer (downstream, not changed by the defect): `WorkflowService.update_workflow` (`src/codemie/service/workflow_service.py` ~785-820) assigns `yaml_config` from the request config, re-parses, and uses `_yaml_content_changed` (line 75; compares `yaml.safe_load` of old/new) to decide whether to append `yaml_config_history`. If rewrite output differs semantically from the old stored YAML (e.g. dropped keys), a history entry is created; once keys are preserved, no-op saves should compare equal.

### Integration Points
- UI/SDK send `yaml_config` as text in POST/PUT bodies (`CreateWorkflowRequest`, `UpdateWorkflowRequest`); UI stores graph layout and unconnected nodes in `orphaned_states` / `meta_states` inside that YAML.
- Other places that write `yaml_config` (checked; none is the same defect):
  - `src/codemie/service/llm_retirement_service.py:210-235` `_update_yaml_config_field`: `yaml.safe_load` -> in-place model replace -> `yaml.dump` of the whole parsed dict; preserves unknown keys. This is the model to imitate.
  - `src/codemie/core/workflow_models/workflow_config.py:190-225` `WorkflowConfigBase.from_yaml` and `:365-420` `WorkflowConfigTemplate.from_yaml`: `yaml_config=yaml.safe_dump(execution_config)` where `execution_config` is the raw dict from the YAML -> preserves keys (used for templates/prebuilt workflows; not import/clone endpoints).
  - `src/codemie/workflows/workflow_generator/nodes/validation.py:284-298`: generator builds `yaml_config` for AI-generated workflows (new content, no UI layout to lose).
  - `src/codemie/service/workflow_service.py:807` only restores `old_yaml` when unchanged.
  - `src/external/deployment_scripts/preconfigured_workflows.py:56`: string `.replace` (preserves).
  - Alembic migrations operate on text via regex/JSON (one-off).
  - No workflow clone/copy/import router or service function found (grep for clone/copy_workflow/import_workflow/duplicate in `routers/workflow*.py` and `workflow_service.py` returned nothing). `_normalize_workflow_model_ids` is also only called from create/update.
  - Conclusion: `_rewrite_yaml_config_from_objects` is the only destructive rebuild-from-objects path found.

### Patterns and Conventions
- Router-local private helpers prefixed `_`; mutation in place returning `None`.
- Helper docstring states intent: persist normalized deployment_name model ids into the YAML text the UI panel renders.
- `yaml.safe_dump(..., sort_keys=False)` is used to keep key order. Note existing `_LiteralBlockDumper` in `llm_retirement_service.py` keeps multiline strings as block scalars; the router helper does not use it (formatting is already lossy there: comments and block scalars in the original YAML are not preserved).
- Guides: no guide covers this specific helper; `.ai-run/guides/api/rest-api-patterns.md`, `architecture/service-layer-patterns.md` apply.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` present. Relevant: `testing/testing-patterns.md` (location, narrowest-scope runs, "Seam Tests for Policy Helpers": test the helper AND each callsite observing the boundary value), `testing/testing-api-patterns.md`, `quality-gates.md` (`make ruff`, `make test`). Not read in depth beyond testing-patterns head.
- Historic planning docs for 6d40ebfa1 exist under `docs/superpowers/` (plans/tasks for EPMCDME-14349/14452); not read.

### Architectural Decisions
- No ADR on yaml_config ownership. Inline comments in `create_workflow` (lines 585-589) record that parse errors are deferred to `WorkflowExecutor.validate_workflow` so a half-parsed config is never rewritten/persisted.

### Derived Conventions
- yaml_config text is the source of truth for the UI; typed fields are derived caches (`get_workflow` re-parses on read, `workflow_service.py:88-90`).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_workflow.py` (single file, ~2400 lines):
  - `test_create_workflow` (line 318): mirrors the router by calling `workflow_router._rewrite_yaml_config_from_objects(expected_config)` (line 345) and asserts `WorkflowExecutor.validate_workflow` called with `workflow_config=expected_config`. This is the ONLY direct reference to the helper, and because it calls the helper itself to build the expectation, it would not detect dropped keys (self-referential). With the fix it keeps working as long as the helper stays deterministic.
  - `test_create_workflow_accepts_placeholder_like_text` (351) and `test_update_workflow_accepts_placeholder_like_text` (479): assert `create_workflow.call_args.args[0].yaml_config` / `update_workflow.call_args.args[1].yaml_config` contain `${input:assistant_id}` -> good template for capturing the persisted yaml_config.
  - `test_create_workflow_preserves_unavailable_assistant_models` (2341): captures `create_workflow.call_args.args[0]` and asserts assistant models.
  - `test_update_workflow` (442), `test_update_workflow_pins_id_from_path_param` (560), `test_update_workflow_categories_omitted_vs_empty` (2318), restricted-mode MCP tests (1983-2190, helper `_post_yaml_only_workflow` at 2075) all exercise create/update.
  - Fixtures/data: `test_yaml_config` (line 47; assistants + states only), `create_workflow_request`, `update_workflow_request`, `workflow_config`, `request_header`, `workflow_config_data`.
- No test asserts `orphaned_states` / `meta_states` (zero hits in `tests/`), nor any other top-level key survival.

### Testing Framework and Patterns
- pytest + pytest-asyncio; `httpx.AsyncClient(transport=ASGITransport(app=app))` against FastAPI `app`; heavy use of `unittest.mock.patch` on `WorkflowService.*`, `WorkflowExecutor.validate_workflow(_and_draw)`, `project_access_check`, `GuardrailService.get_entity_guardrail_assignments`, `Ability.can`. Persisted config is observed via `mock.call_args.args[...]`.
- Run: `poetry run pytest tests/codemie/rest_api/routers/test_workflow.py -k <name>`; `make ruff` for lint (Makefile lines 31-36).

### Coverage Gaps
- No unit test of `_rewrite_yaml_config_from_objects` in isolation; no test for key preservation (orphaned_states, meta_states, retry_policy, type, recursion_limit, pool_config, etc.) on either create or update.
- No test for the round trip through `WorkflowService.update_workflow` / `_yaml_content_changed` with these keys.

---

## 5. Configuration and Environment

### Environment Variables
- None relevant to this defect.

### Configuration Files
- `src/codemie/workflows/execution_config_schema.yaml`: JSON schema for yaml_config; no top-level `additionalProperties: false`, so no schema change is observed to be needed for extra keys.
- `config/customer/customer-config.yaml` (changed in 6d40ebfa1) holds feature toggles for model availability; unrelated to key preservation.

### Feature Flags and Deployment Concerns
- `_normalize_workflow_model_ids` / rewrite run unconditionally (no flag). Already-stored workflows that lost keys cannot be recovered by a code fix (no migration source for the lost data; `yaml_config_history` may hold prior versions - Speculative, see Section 6).

---

## 6. Risk Indicators

- Root cause is a whole-document rebuild (`workflow.py:134-147`); fixing only `orphaned_states` and `meta_states` would leave `retry_policy`, `type`, `recursion_limit`, `max_concurrency`, `verbose`, `max_iteration_key_output_limit`, `pool_config`, `max_nesting_level` dropped. Scope decision for spec: allow-list extension vs. merge-onto-original approach.
- Speculative: merging approach = `yaml.safe_load(original yaml_config)`, overlay the helper-owned keys, dump. Risk: a non-mapping/empty YAML (guard for `None`/non-dict) and key ordering changes that trigger spurious `yaml_config_history` entries via `_yaml_content_changed` (it compares parsed content, so ordering alone should not).
- Speculative: derived-key coherence - `orphaned_states` entries may reference `tool_id`/`assistant_id`/`custom_node_id`; model normalization applies to assistants/custom_nodes only, so overlaying original `orphaned_states` unchanged is consistent.
- `enable_summarization_node` default-True behavior from `parse_execution_config` gets written explicitly after rewrite even when absent from the user's YAML; possible pre-existing behavior change, out of scope unless the fix refactors the key set.
- `test_create_workflow` calls the helper to compute its expectation, so it gives false confidence; a new test must assert on the literal persisted YAML.
- Previously corrupted workflows (saved since 6d40ebfa1, 2026-10-06) have lost data; Speculative: `yaml_config_history` stores prior YAML and could be used for recovery, a separate concern.
- `update_workflow` swallows any exception in the rewrite into HTTP 400, so a helper bug on malformed YAML surfaces as a 400 rather than 500.
- No clone/import/copy path found, so low risk of additional rewrite sites; the other yaml writers preserve keys.

---

## 7. Summary for Complexity Assessment

The defect is localised to one private helper in the router layer, `_rewrite_yaml_config_from_objects` in `src/codemie/rest_api/routers/workflow.py` (lines 128-147), called from exactly two sites (`create_workflow`, `update_workflow`). It rebuilds `yaml_config` from typed objects and discards every top-level key that has no model field: `orphaned_states` and `meta_states` (the ticket) and also `retry_policy`, `type`, `recursion_limit`, `max_concurrency`, `verbose`, `max_iteration_key_output_limit`, `pool_config` and `max_nesting_level`. Those keys exist only in the YAML text, so the fix must start from the original `yaml_config`. The JSON schema allows extra top-level keys, and no other code path (service, migrations, templates, generator, retirement service) performs the same destructive rebuild; no clone/copy/import path exists.

Change surface is small (one function in one file, no schema, model, config or migration change observed as necessary), with no new third-party dependencies (PyYAML already imported). Technical novelty is low: `llm_retirement_service._update_yaml_config_field` shows the load-modify-dump pattern that preserves unknown keys.

Test posture: create/update are covered by API-level tests in `tests/codemie/rest_api/routers/test_workflow.py` that capture the persisted config through `mock.call_args`, but nothing asserts key preservation and the one direct reference to the helper (`test_create_workflow`, line 345) reuses the helper to build its expectation. A failing-first test can be added at the API level (POST and PUT with `orphaned_states`/`meta_states` in `yaml_config`, assert on `call_args` yaml) plus a direct unit test of the helper. Main risks are scope (ticket keys only vs. all non-owned keys), empty/non-dict YAML handling, and not regressing the model-id normalization the helper exists for.

---

## 8. External References

None named by the task
