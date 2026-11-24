# Technical Research

**Task**: workflow validation contract
**Generated**: 2026-11-02
**Research path**: codegraph

---

## 1. Original Context

Jira sub-task EPMCDME-16503 "Save-time Contract Linker: Model, Simulation and Outcomes (codemie)" (parent story EPMCDME-16500, Workflow Node Contract Validation — 2. Save-time Validation). The full ticket text (context, story, background, ~45 acceptance criteria, out of scope, decisions, constraints) is in the ticket — read it in full and treat it as the task description. Key points: add new config keys (node input contract `input_schema`, workflow/state `validation` mode, workflow-wide `require_input_contracts`), a graph-wide save-time simulation of data availability in the shared workflow context, four outcomes (OK/ERROR/WARNING/UNVERIFIED) with provenance per requirement, derived requirements from tool bindings / transforms / routing expressions, strict/warn modes, backward compatibility with existing workflows and built-in templates, and a template-corpus warning ceiling. Constraints: read workflow-level validation / require_input_contracts from workflow YAML (not new columns); branch from origin/main; no behaviour change at runtime. Out of scope: API issue-field shape/coverage object/endpoints/flag, UI, docs, runtime checks, SDK/CLI.

(Ticket file read in full; its AC ids AC-1..AC-44, D2/D3/D4/D8 and constraints are cited below by id.)

---

## 2. Codebase Findings

### Existing Implementations

**P1 - check a workflow at save / dry-run time**
- `WorkflowExecutor.validate_workflow` `src/codemie/workflows/workflow.py:224`. Sequential stages, each raising on first failure (`ValueError` carrying a string or, with `error_format=json`, a dict):
  1. `validate_workflow_execution_config_yaml` `src/codemie/workflows/validation/schema.py:727` - YAML parse, JSON-schema check against `src/codemie/workflows/execution_config_schema.yaml` (Draft 2020-12), then id cross-references. Error types `parsing` / `schema_validation` / `cross_reference_validation` (`src/codemie/workflows/constants.py:19`).
  2. `workflow_config.parse_execution_config()` `src/codemie/core/workflow_models/workflow_config.py:240` - Pydantic parse; failures go through `PydanticErrorTransformer` (`validation/transformers.py:51`), type `workflow_schema`.
  3. Missing-states check; 4. `validate_workflow_config_resources_availability` `validation/resources.py:749` (assistants, tools, datasources, sub-workflows, tool_access; `_is_tool_available` ~`:600` is the one tool lookup, `ToolsService.find_toolkit_for_tool`, returns a toolkit not an args schema); 5. `create_executor(...)._init_workflow()` compiles the LangGraph graph.
- Callers: create `src/codemie/rest_api/routers/workflow.py:527`; update via `WorkflowService.validate_for_update` `src/codemie/service/workflow_service.py:182`; dry-run `POST /workflows/{id}/validate` `routers/workflow.py:721`; diagram `:792`, `:862`; `src/codemie/service/workflow_generator_service.py:112`; generator `workflow_generator/nodes/validation.py:364`.
- Existing non-blocking channel: `_consumer_slot_warnings(_sync)` `routers/workflow.py:212-240` returns `list[dict]`, surfaced as `warnings` in create/update responses and in `ValidateWorkflowResponse` `:493`. It is the only warning-class output of save today (sibling API ticket's area).
- Structured error shape: `WorkflowValidationErrorDetail` (`validation/models.py:53`: id, message, path, details, state_id, config_line, meta). Schema errors set `state_id` and a leaf `path` for `states[i].*` (`schema.py:131-148`).
- Orphan duplicates: `src/codemie/workflows/config_yaml_validation.py` and `config_resources_validation.py` are not imported anywhere in `src` or tests (live code and tests use `workflows/validation/*`).

**P2 - know what data a node finds in the shared context (runtime rules a simulation must mirror)**
- Initial context: `WorkflowExecutor.on_workflow_start` `workflow.py:1189` - `user_input` parsed as JSON; a dict seeds `context_store`, anything else gives `{}`; plus `file_names` and a propagated-headers key. No declaration of initial input exists.
- Writes after a node: `BaseNode.finalize_and_update_state` `workflows/nodes/base_node.py:705`.
  - `_collect_new_values` `:621`: parsed output dict keys; a list output is merged from its dict items (`_merge_list_to_dict` `:606`); on the first node of an iteration a dict task's keys are added too (so they are context keys only after that node).
  - `store_in_context: false` -> `{}` (`:685`), but `_add_output_key` `:580` still writes `output_key` (value `raw_output.result` if present, else the processed string) into `context_store`; with `append_to_context` it is written only as an append marker (list).
  - `append_to_context` wraps every new key as list (`:692`); `clear_context_store: true` -> `None` (`:681`); `"keep_current"` -> only new values (`:696`); `reset_keys_in_context_store` -> delete markers applied after new values (`:700`).
  - Reducer `add_or_replace_context_store` `workflows/models.py:39` applies None/keep-new/delete/append.
- Full reset plus `output_key`: in this checkout `_add_output_key` indexes `final_state[CONTEXT_STORE_VARIABLE]` which is `None` after a full reset (`TypeError`). The fix, its offline harness `tests/codemie/workflows/test_workflow_templates_validation.py` (loads `config/templates/workflow/*_template.yaml` via `WorkflowConfigTemplate.from_yaml` `workflow_config.py:377`, runs `validate_workflow` with resources/`create_executor` patched, guard >= 64 templates) and doc changes are described in `.ai-run/sdlc-factory/mr-description.md` (EPMCDME-16499). Neither the fix nor the harness file exists in this working tree (only a stale `.pyc`).
- Routing: `WorkflowNextState` `workflow_models.py:190` (`state_id`, `state_ids`, `condition{expression,then,otherwise}`, `switch{cases,default}`, `iter_key`, `output_key`, flags); `leads_to()` `:292`. Condition/switch expressions are evaluated against the node's own parsed output only (`_evaluate_condition` `workflows/utils/utils.py:415`; names = output keys + `keys`); evaluation errors fall to `otherwise`/`default` (`:435-476`). Syntax-checked at parse by `_validate_python_expression_syntax` `workflow_models.py:137`.
- Parallel: `next.state_ids` fan-out `_handle_multiple_states` `workflow.py:837`; convergence nodes deferred `find_convergence_nodes` `:527`.
- Iteration: `continue_iteration` `workflow.py:731`, `get_iterable_items_by_key` `:705` (list source -> iterated whole, key ignored; dict source -> `source[iter_key]`; other -> single item), pointer form when key starts with `/` (`:696`), `KeyError` -> `IteratorInputNotFoundError` (`workflows/exceptions.py:31`, non-retryable via `WorkflowRetryPolicy.custom_retry_on` `workflow_models.py:354`). First-level branch gets a copy filtered by `include_in_iterator_context` (default `["*"]`); nested branch (`iteration_node_number > 0`) reuses the parent store unfiltered (`_build_parallel_context` `:715`). Item travels as `task`; `FIRST_STATE_IN_ITERATION = iter_key not in state_schema` (`:787`). Schema forbids `iter_key` with `condition`/`switch`/`state_ids` and requires `state_id` (`execution_config_schema.yaml:227-243`). `find_map_nodes` `:941`.
- Tool bindings: `ToolNode._execute_tool_with_args` `workflows/nodes/tool_node.py:255` - `tool_args = state.tool_args or tool_config.tool_args`; when empty, every parameter name of the tool's args schema is looked up in context (`dict.fromkeys(annotations)`); `_get_tool_args` `:465` resolves via `process_values` with optional `input_key` namespace (`WorkflowTool.input_key`); first-in-iteration task dict keys are added. In `_resolve_dynamic_values` `service/tools/dynamic_value_utils.py:447` a context value whose key equals the argument name wins over the literal/template value. Args schema is only available from the instantiated tool at run time (`tool.args_schema`).
- Agent nodes: task template resolved from context only when `resolve_dynamic_values_in_prompt` (`agent_node.py:141`). `WorkflowState.output_schema` (`workflow_models.py:387`, JSON text) is consumed as structured-output schema by `assistant_service.py:771`.
- Transform node (custom node `transform_node`, config in `CustomWorkflowNode.config`): mappings with `output_field` and `type` (extract default, condition, template, constant, script, array_map), `output_schema` (`properties`/`required`, coercion string/integer/boolean/number), `on_error` fail (default) / skip (`{}`) / default (`default_output`) / partial (failing mapping dropped), `input_source`, `input_key` (`workflows/nodes/transform_node.py:38-99, 173-219, 287-324, 573-652`).
- Sub-workflow node: `workflows/nodes/sub_workflow_node.py`, gated by feature `subWorkflow` (`customer_config.is_feature_enabled`).

**P3 - cross-node data hand-off checks that already exist**
- Workflow generator `ValidationNode` `workflows/workflow_generator/nodes/validation.py`: `_check_iter_key_producers` `:39`, `_validate_output_schema` `:71`, `_validate_iter_key_schema` `:150` (walks producer `output_schema.properties`, requires array), `_validate_producer_schemas` `:210`, `_build_flow_adjacency` `:233`. Runs only on generated workflows (assistant states, string error lists), not on normal save. Second adjacency builder next to `WorkflowNextState.leads_to()` and `find_convergence_nodes`.
- No existing graph-wide availability analysis, no input contract, no outcome taxonomy.

**P4 - the three new keys**
- `input_schema`, state/workflow `validation`, `require_input_contracts`: none found in `src` (searched names across py/yaml/md). Not in `WorkflowState`, `WorkflowConfigBase` (`workflow_config.py:77`), `execution_config_schema.yaml`, or any template.

**Other touched code**: `WorkflowState` `workflow_models.py:380` (fields, `check_state_type`); `WorkflowConfig.parse_execution_config` `workflow_config.py:240-276` (reads each top-level key explicitly); `config/templates/workflow/*.yaml`.

### Decisions Already in the Code
- Unknown keys are tolerated: schema objects have no `additionalProperties: false` (`execution_config_schema.yaml`), and the Pydantic models use default extra handling (ignored). A state carrying `input_schema` or a workflow carrying `validation` saves today and the value is not read. Alternative (strict keys) not chosen; reason none found.
- `State.output_schema` is a JSON string (`Optional[str]`), not an object; transform `output_schema` is a dict inside `custom_node.config`. Two shapes for "output contract".
- Workflow-level settings live as top-level keys of the `execution_config` YAML and are read in `parse_execution_config` into columns/attributes (e.g. `recursion_limit`, `max_concurrency`, `pool_config`, `max_nesting_level`). Convention for a new workflow-level key; the ticket constraint reads it from YAML instead (no column).
- `reset_keys_in_context_store`: docstring says a reset key present in the state's own output "will be re-added" (`workflow_models.py:254-256`); code applies the delete marker after the new values (`base_node.py:700`, `_apply_deletion_markers` `:653`), so the delete wins.
- Condition/switch names resolve from the node's own output, not from `context_store` (`utils.py:415`).
- Validation order and early exit: each stage raises, later stages do not run.
- Warnings-not-errors convention for advisory results: `warnings` list in responses, outside the exception path.
- Errors reference nodes by `state_id` plus a leaf `path` (`schema.py:136`).
- Iteration-key semantics (list ignores key, dict missing key fails non-retryably): code-confirmed at `workflow.py:705-712` and `workflow_models.py:354`.

### Task Statements Checked Against the Code
- "Three keys exist nowhere today": holds (P4).
- "Almost every output contract is example-form or invalid": holds for sampled templates, e.g. `config/templates/workflow/test_cases_from_unstructured_data_template.yaml:102-129` (`output_schema: ["List of found features"]`, `[{ "document": "Document title", ...}]`). 56 template files use `output_schema` or `iter_key`.
- "Built-in templates use a placeholder iteration key such as {{_}}": holds - `iter_key: _` at `test_cases_from_unstructured_data_template.yaml:106,129` (literal `_`; the `{{_}}` spelling is not in that file).
- "List output iterated whole, key ignored; dict output missing key fails non-retryable": holds (`workflow.py:705-712`, `:748`, `workflow_models.py:354`).
- "Context value wins over a literal/template binding of the same name" (AC-15): holds (`dynamic_value_utils.py:447-452`).
- "Non-empty bindings omit a mandatory parameter -> never resolved" (AC-14): holds - with non-empty `tool_args` only listed keys are resolved (`tool_node.py:282-291`); a missing mandatory parameter fails args-schema validation / signature bind (`:293-317`).
- "Output writing disabled with explicit output key: only that key, as string" (AC-10): holds for the string value when `raw_output` has no `.result`; with `.result` the value is `raw_output.result` (`base_node.py:595`).
- "Full reset plus output key: corrected runtime keeps only the output key" (AC-40): the correction is not in this tree; current code raises `TypeError` (`base_node.py:581-603` with `None` store). Matching fix described in `.ai-run/sdlc-factory/mr-description.md`.
- "Offline template-corpus harness from bug-fix-codemie" (AC-43): unverified in this tree - `test_workflow_templates_validation.py` absent; described in the MR description.
- "Save makes no lookup beyond the one it already performs" (AC-38): holds - only `find_toolkit_for_tool` / MCP tool list for non-dynamic servers (`resources.py:~600`, `config_resources_validation.py:175-194`).
- "Today's validator stops at first failing stage": holds (`workflow.py:236-294`).
- "Parent story/spec section numbers (§2.8, §3.2, §6.1, §8.3)": unverified - spec is outside this repository.
- "Branch from origin/main, current checkout carries unrelated changes": current branch is `epmcdme-16503` with only `.ai-run/sdlc-factory/doctor.json` modified; whether it equals origin/main was not checked (no git access in this run).

### Consumers of Changed Contracts
- `WorkflowState` (`workflow_models.py:380`): 217 codegraph callers; embedded in `CreateWorkflowRequest.states` (`:441`) and `WorkflowConfigBase.states` (PydanticListType column), so any new field appears in the OpenAPI snapshot `tests/codemie/rest_api/router_guards/baselines/openapi.json` (29 state-related mentions; test `test_openapi_snapshot.py`, regenerate via `tests/codemie/rest_api/router_guards/regenerate_baselines.py`). Also `workflow_generator/nodes/validation.py:95,268` (`WorkflowStateModel(**gen_state.model_dump(...))`), `config_assembly.py`, `bedrock_flow_service.py`.
- `WorkflowNextState`: read by `base_node.py`, `workflow.py` edge builders, `utils.py` routing, `find_map_nodes`, generator validation.
- `validate_workflow` callers listed under P1; each expects a compiled graph on success and `ValueError` on failure; `workflow_generator_service.py:112` and generator `validation.py:364` consume the same errors (retry loop). Warnings are not part of its return value today.
- `execution_config_schema.yaml`: single schema for all callers; tested by `tests/codemie/workflows/test_config_yaml_validation.py` (34 refs).
- `WorkflowConfigBase.parse_execution_config`: called at validate, executor creation, router paths.
- `CustomWorkflowNode.config` is a free dict (schema allows only `documents_filter`/`documents_filtering_pattern` keys but does not forbid others); transform config keys are read only in `transform_node.py`.

### Architecture and Layers Affected
- Models: `core/workflow_models/workflow_models.py`, `workflow_config.py`.
- Validation package: `workflows/validation/` (schema, resources, models, transformers, line_lookup), `execution_config_schema.yaml`.
- Orchestration: `WorkflowExecutor.validate_workflow` (`workflows/workflow.py`).
- Reads (not changes, per ticket): node runtime rules in `workflows/nodes/*`, `workflows/models.py`, `workflows/utils/utils.py`, `service/tools/dynamic_value_utils.py`.
- Corpus: `config/templates/workflow/` (about 64+ files).

### Integration Points
- Tool metadata only via `ToolsService.find_toolkit_for_tool` and `MCPToolkitService.get_mcp_server_tools` inside resource validation; sub-workflows via `WorkflowService().get_workflow`; feature gate `customer_config.is_feature_enabled` (flags for this feature belong to the API sibling).
- Error reporting integration: `PydanticErrorTransformer`, `YamlLineFinder`/`extract_line_numbers` for `config_line`.
- `jsonschema` (Draft202012Validator) already a dependency of schema validation; `Draft202012Validator` also usable for checking contract structure.

### Patterns and Conventions
- Validators live in `workflows/validation/` and are re-exported in `validation/__init__.py`; error classes expose `to_dict()` returning `{error_type, message, errors[]}`.
- Layering per `.ai-run/guides/workflows/langgraph-workflows.md`: extend `WorkflowExecutor`/validation utilities, do not bypass config parsing.
- Models: Pydantic `BaseModel` with `model_validator`, backward-compat `mode='before'` validators (`WorkflowState.handle_interrupt_backward_compatibility`, `WorkflowNextState.handle_keep_history_backward_compatibility`).
- License header on each file; tests under `tests/codemie/workflows/` mirror module names.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/workflows/langgraph-workflows.md` (short; points to `WorkflowExecutor` and validation utilities), `.ai-run/guides/architecture/layered-architecture.md`, `.ai-run/guides/testing/testing-patterns.md`.
- `docs/workflows/*.md` (user docs; `05_context_management.md`, `04_state_transitions.md`, `02_configuration_reference.md`). New-key docs are out of scope (docs sibling).

### Architectural Decisions
- Ticket decisions D2 (corpus test split), D3 (iteration key), D4 (key names), D8 (two codemie subtasks). No ADR files found for workflow validation.

### Derived Conventions
- See Section 2 "Patterns and Conventions".

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/workflows/test_config_yaml_validation.py` - schema + cross-reference + YAML line lookup.
- `tests/codemie/workflows/test_config_resources_validation.py` - resource availability.
- `tests/codemie/workflows/validation/test_transformers.py` - Pydantic error transformation.
- `tests/codemie/workflows/test_backward_compatibility.py`, `test_workflow_state_transitions.py`, `test_context_store_management.py`, `test_context_store_reducer(s).py`, `test_flag_interactions.py`, `test_iteration_map_reduce.py`, `test_dynamic_value_resolution.py`, `test_tool_node_context.py` - runtime context semantics the simulator must agree with.
- `tests/codemie/workflows/nodes/test_transform_node*.py` - transform mappings and error strategies.
- `tests/codemie/rest_api/routers/test_workflow.py` (58 refs to validate) - router validation paths.
- `tests/codemie/workflows/workflow_generator/test_nodes.py`, `test_workflow_graph.py` - generator validation.
- `tests/codemie/rest_api/router_guards/test_openapi_snapshot.py` - OpenAPI baseline.

### Testing Framework and Patterns
- pytest (`pytest.ini`: `testpaths=tests`, `pythonpath=src`, `--import-mode=importlib -n 3`), `unittest.mock` patching of resource checks. Tests only run on explicit request (AGENTS.md).

### Coverage Gaps
- No tests for graph-wide data availability, input contracts, outcomes, or modes (nothing exists).
- `find_map_nodes` has no covering test per codegraph.
- Template-corpus harness absent in this tree (see P2).

---

## 5. Configuration and Environment

### Environment Variables
- None relate to validation. `WORKFLOW_TEMPLATES_DIR` `src/codemie/configs/config.py:113` -> `config/templates/workflow`.

### Configuration Files
- `src/codemie/workflows/execution_config_schema.yaml` (execution_config structure), `config/templates/workflow/*_template.yaml` (corpus; placeholders `${input:...}` handled by `src/codemie/workflows/placeholders.py`).

### Feature Flags and Deployment Concerns
- `customer_config` features (`features:subWorkflow` used by sub-workflow node and its availability check). Platform flags for the linker are API-sibling scope.
- Workflow YAML is stored as `yaml_config` text; no column exists for workflow-level `validation` or `require_input_contracts` (ticket constrains to YAML).

---

## 6. Risk Indicators

- Speculative: any new field on `WorkflowState` changes the OpenAPI snapshot baseline and `CreateWorkflowRequest` (`workflow_models.py:441`); the baseline test needs regeneration.
- Speculative: because unknown keys are ignored today (no `additionalProperties:false`, default Pydantic extra), the new keys' structural rule (AC-16) needs a rule that does not exist yet and must not break workflows that already carry unrelated unknown keys.
- Existing mechanism kept in place (Task Statements): stage-by-stage first-failure validation in `validate_workflow` (callers in P1 incl. generator retry loop); a linker added there either runs after stage 4 or changes the single-exception contract those callers rely on.
- Existing mechanism for the same purpose: generator `ValidationNode` producer/consumer checks (`validation.py:39-231`) overlap with iter-key/producer rules; it stays separate and has its own adjacency builder (three graph walkers now: `leads_to`, `find_convergence_nodes`, `_build_flow_adjacency`).
- Runtime rules are scattered across `base_node.py`, `models.py`, `workflow.py`, `tool_node.py`, `transform_node.py`, `utils.py`; the simulator must reproduce them without a shared model (risk of drift, esp. `store_in_context=false` + `output_key`, append, `keep_current`, delete-wins of reset keys).
- Full reset + `output_key` currently crashes at runtime in this tree; AC-40 depends on bug-fix-codemie landing (fix and harness not in working tree).
- Tool parameter schema is not available at save (only toolkit existence); derived requirements for tool params rely on a lookup the ticket forbids adding -> UNVERIFIED path will dominate.
- Template corpus: 56 files use `output_schema`/`iter_key`; ceiling test needs the absent harness; template YAML contains `${input:...}` placeholders that need `WorkflowConfigTemplate.from_yaml`.
- Transform `output_schema` is a dict in `custom_node.config`, state `output_schema` is a JSON string; example-form contracts (e.g. `["List of found features"]`) are not valid JSON schema.
- Orphan duplicate modules `config_yaml_validation.py` / `config_resources_validation.py` could be edited by mistake without effect.
- Documentation gap: no ADR or guide for workflow validation semantics; spec sections cited by the ticket are outside the repo.
- Local main is stale (memory note): create the branch from `origin/main`; unrelated doctor.json change in working tree.

---

## 7. Summary for Complexity Assessment

The change sits in the models and validation layers (`WorkflowState`/`WorkflowConfig` fields, `execution_config_schema.yaml`, a new analysis module under `workflows/validation/`, one hook in `WorkflowExecutor.validate_workflow`) and reads, without changing, runtime rules spread over about eight modules (context-store writes, reducer, iteration, routing, tool-arg resolution, transform, sub-workflow). Nothing for contracts, availability simulation or outcomes exists; the nearest mechanisms are the stage-by-stage save validator, a non-blocking `warnings` list in responses, and the workflow generator's producer/consumer checks.

Technical novelty is high: a graph simulation over sequential, conditional, switch, parallel, iterative and cyclic routing with four outcomes and provenance, plus lenient parsing of example-form and invalid contracts that dominate the 60-odd built-in templates. Test coverage for the surrounding runtime semantics is good, but there are no tests for the new behaviour, the template-corpus harness and the full-reset runtime fix are not in this working tree, and the OpenAPI snapshot guard will be touched by any new `WorkflowState` field.

Key risks: duplicated and drifting runtime rules, tool parameter schemas not available at save, the single-exception contract of `validate_workflow` and its generator retry-loop consumers, backward compatibility across the template corpus, and dependency on bug-fix-codemie.

---

## 8. External References

- the full ticket text - resolved, read in full. Facts used: 45 acceptance criteria grouped as availability, tool bindings, contract structure, validation modes, reset+output key, template ceiling; decisions D2-D4, D8; constraints: depend on bug-fix-codemie; branch from `origin/main`; read workflow-level `validation` and `require_input_contracts` from YAML not columns (spec §6.1); structural error for invalid `input_schema` carries state id, path `input_schema` and an error type the editor already links (spec §8.3); keys must not be renamed later; EPAM GitLab MR commits `EPMCDME-####: Description`.
- Spec v6 (§2.8, §3.2, §6.1, §8.3), parent `story.md`, parent `technical-analysis.md`: named by the ticket, not in this repository, not read.
- `.ai-run/sdlc-factory/mr-description.md` (in repo) - resolved; describes the EPMCDME-16499 fix and template harness.

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- `config` is not a module: no `config.yaml_config` exists in `src/codemie/configs/`. The spec's `config` is a `WorkflowConfig` parameter; `yaml_config` is a plain field `Optional[str] = None` on `WorkflowConfigBase` (`core/workflow_models/workflow_config.py:103`), so it is a DB column of the `workflows` table (`WorkflowConfig`, `table=True`, line 321). `WorkflowConfigTemplate` shares the base (`:370`).
- Holds the `execution_config` mapping as YAML text (not the whole template file). Written by `WorkflowConfigBase.from_yaml` (`:219`, `yaml.safe_dump(execution_config)`), `WorkflowConfigTemplate.from_yaml` (`:410`), the UI/API request, `_rewrite_yaml_config_from_objects` (`rest_api/routers/workflow.py:141-161`, rewrites only changed `model` ids; stashes the as-sent text in private `_source_yaml_config`, never persisted, `workflow_config.py:326`), and `workflow_service.py:786-820` (stored vs old YAML, history).
- Read at save/dry-run by: `validate_workflow_execution_config_yaml(workflow_config.yaml_config)` (`workflows/workflow.py:237` -> `validation/schema.py:727`, `yaml.safe_load`, JSON-Schema + cross-reference); `parse_execution_config()` (`workflow_config.py:240-276`, `yaml.safe_load(self.yaml_config) or {}`); `PydanticErrorTransformer` (`validation/transformers.py:81-82`, YAML + line map); `validation/resources.py:770` (`_source_yaml_config` or `yaml_config` for line lookup).
- State `output_schema` is `Optional[str]` "JSON schema" (`workflow_models.py:387`). Runtime reader: `AssistantService.load_and_validate_schema` (`service/assistant_service.py:696-704`): `json.loads` then `validate_json_schema`; returns the dict if a valid JSON Schema, `None` if not; catches **only** `json.JSONDecodeError` (returns `None` implicitly); `RecursionError` is not caught. Consumer: `_prepare_workflow_system_prompt` (`:747-787`) - a valid schema becomes structured output (`suggested_json_prompt`), anything else is pasted as an example (`json_prompt`). That is the existing "schema-form vs example-form" split.
- Transform node: `output_schema` in a transform config is applied by `_validate_output` (`workflows/nodes/transform_node.py:195-197`). `transform_node.py:263,275` and `workflows/utils/transform_node_utils.py:118` catch `JSONDecodeError` and fall back to raw strings (JSON-or-text tolerant); `workflows/utils/utils.py:107` (`_is_valid_json`: `ast.literal_eval`, then `json.loads`, `JSONDecodeError` -> `(False, None)`); `workflows/utils/json_utils.py:40` re-raises as `ValueError("Invalid JSON string encountered at ...")`.
- `RecursionError`: no occurrence anywhere under `src/codemie/workflows/` or in any validation path. Elsewhere only `repository/cli_analytics/postgres/otlp.py:484,530,872,893`, `service/conversation/history_projection_service.py:567`, `agents/callbacks/callback_utils.py:40`, `rest_api/routers/cli_analytics.py:189` (`except (ValueError, RecursionError)`). Different constant: `RECURSION_LIMIT: int = 50` (`workflows/constants.py:71`) is the LangGraph step limit, unrelated to Python recursion.
- Other `json.JSONDecodeError` handlers on the workflow REST surface: `rest_api/routers/workflow.py:309,343` (filters query), `routers/workflow_executions.py:793`. None parse state contracts.
- Save-time today: no step parses `output_schema` text at all; `validate_workflow` stages are YAML/schema/cross-ref (`schema.py`), Pydantic parse (`parse_execution_config`), empty-states check, resource availability (`validation/resources.py`), then `create_executor(...)._init_workflow()` (graph compile). Each stage raises on first failure (`workflow.py:224-294`), as the ticket Context says.
- Defined `WorkflowExecutor.validate_workflow(workflow_config, user, error_format="string")` (`workflows/workflow.py:223-294`); `validate_workflow_and_draw` (`:296-323`) wraps it. Error format `"json"` raises `ValueError(dict)`, else `ValueError(str with <br>)` via `_raise_validation_error` (`:206-221`).
- Callers (all `src/`): `rest_api/routers/workflow.py:527` (create, `_persist_and_finalize_new_workflow`), `:792` (dry-run draw), `:862`; `service/workflow_service.py:182` (`validate_for_update`); `service/workflow_generator_service.py:112`; `workflows/workflow_generator/nodes/validation.py:364` (`error_format="json"`). Router handler of the same name `validate_workflow` at `routers/workflow.py:721`.
- `service/tools/discovery/tool_discovery_service.py:58` class; `find_tool_by_name(tool_name) -> Optional[ToolInfo]` at `:61-109`. Order: (1) `ToolMetadataFinder.find_provider_tool` (`metadata_finder.py:40`: instantiates every provider toolkit from `ProviderToolkitsFactory.get_toolkits()` and calls `get_tools()`; on a hit returns `ToolInfo` with `config_schema={}` and `args_schema={}`); (2) `find_tool_metadata` (scans packages `codemie_tools` then `codemie.agents.tools`, `metadata_finder.py:36-37,67-86`); (3) `find_toolkit_for_metadata`; (4) `ToolConfigExtractor.extract_config_for_tool`; (5) `ToolSchemaExtractor.extract_args_schema` (`schema_extractor.py:36-74`). Returns `None` for unknown tools; `args_schema` is `{}` when nothing is extractable.
- Shape of `args_schema`: `{param: {'type': <python type>, 'required': bool}}` from `ToolConfigExtractor.get_config_schema` (`config_extractor.py:63-86`; `required = not optional and no usable default`). No JSON-Schema form, no descriptions. `ToolInfo` (`tool_discovery_service.py:39-47`) is a Pydantic model with `toolkit_class, config_class, config_schema, tool_metadata, config_param_name, args_schema`. A comment at `metadata_finder.py:32` says static tool facts live in `codemie_tools/base/tool_registry.py` ("extend it, do not add a scan").
- **Not on the workflow save path today.** The save's tool lookup is `ToolsService.find_toolkit_for_tool(user, name)` (`service/tools/tool_service.py:236-263`, over `_get_available_toolkits(user)`; `_`-prefix names resolve to the PLUGIN toolkit; raises `ValueError`), used by `validation/resources.py:386` (`_is_tool_available`) and `:422` (`_tool_requires_missing_integration`). Neither reads `tool_args` or parameter schemas (`resources.py` mentions `tool_args` nowhere).
- Consumers of `find_tool_by_name`: `service/tools/tool_execution_service.py:191` (`get_tool_with_direct_creds`, raises `ValueError("Tool not found")` on `None`), `:563` (`_is_file_dependent_tool`), and in-class `get_formatted_tool_schema` (`:111-141`, tool-schema API: `rest_api/routers/tool.py:85`; falls back to plugin tools only if a `user` is passed). `ToolDiscoveryService` is re-exported by `service/tools/discovery/__init__.py:21`.
- Not a function. It is a `bool = False` field on `MCPServerDetails` (`rest_api/models/assistant.py:239`), reachable on a workflow tool as `WorkflowTool.mcp_server` (`workflow_models.py:107`).
- Today on the save path: `validation/resources.py:373-375` (`_is_tool_available`): when true, the tool is reported available without any MCP call ("we cannot validate dynamic MCP servers"); when false, `MCPToolkitService.get_mcp_server_tools(...)` is called and the tool is matched by name (`:376-384`) - this is the one MCP call the save already makes. Same rule in `workflows/config_resources_validation.py:178` (dormant duplicate). Also listed in `service/mcp/access_control.py:43` (a field-name set used by MCP governance on save).
- Not to be confused with the sibling flags `WorkflowTool.resolve_dynamic_values_in_response` (`workflow_models.py:108`, read by `workflows/nodes/tool_node.py:525`) and `WorkflowState.resolve_dynamic_values_in_prompt` (`:392`, read by `workflows/nodes/agent_node.py:141`). The placeholder resolver is `service/tools/dynamic_value_utils.py` (`_resolve_dynamic_values_recursively` `:287`, `_resolve_dynamic_values` `:447`).
- Tests: `tests/codemie/workflows/test_config_resources_validation.py:133-178` (mock MCP server with the flag false/true), `tests/codemie/rest_api/models/test_assistant_model.py:141-258`.
- `WorkflowState` (`workflow_models.py:380-425`): fields `id, assistant_id, custom_node_id, task, finish_iteration, next, output_schema, retry_policy, interrupt_before, tool_id, tool_args, resolve_dynamic_values_in_prompt, result_as_human_message, workflow_id`; a `before` validator folds deprecated `wait_for_user_confirmation` into `interrupt_before`; an `after` validator requires exactly one of `assistant_id/custom_node_id/tool_id/workflow_id`. No `model_config`/`extra` setting is declared, so Pydantic's default (extra keys ignored) applies. `WorkflowTool` (`:100-113`) has `tool_args`, `input_key`, `tool_result_json_pointer`, `mcp_server`, `tool_access` etc.
- Importers: `WorkflowState` 217 callers, `WorkflowTool` 46, `WorkflowMode` 19 (codegraph blast radius); `workflow_models.py:31` imports `IteratorInputNotFoundError` from `codemie.workflows.exceptions` (the package already depends on a `workflows` leaf module). `WorkflowConfig` stores `states` as a JSONB-backed list of `WorkflowState` via `PydanticListType` (`workflow_config.py:96-98`); `PydanticListType` round-trip means a new `WorkflowState` field is persisted inside that JSON, not as a column.
- Producers (live path): `validation/schema.py:77` (PARSING), `:125` (SCHEMA_VALIDATION, `to_dict` of `WorkflowExecutionConfigSchemaValidationError`), `:605` (CROSS_REFERENCE_VALIDATION), `validation/resources.py:168` (RESOURCE_VALIDATION), `workflows/workflow.py:264` (WORKFLOW_SCHEMA, Pydantic errors via `PydanticErrorTransformer`), `:274` (MISSING_STATES). Dormant duplicates: `workflows/config_yaml_validation.py:113,227`, `workflows/config_resources_validation.py:126`.
- Error detail model: `WorkflowValidationErrorDetail` (`validation/models.py:53-65`): `id, message, path, details, state_id, config_line, meta(ToolMeta|MCPMeta)`; built in `schema.py:130-141` with `state_id` extracted from the error path and `model_dump(exclude_none=True)`.
- 64 `*_template.yaml` files (dir set by `config.WORKFLOW_TEMPLATES_DIR`, `configs/config.py:113`). All 12 named files exist. Loaded by `WorkflowService.get_prebuilt_workflows` (`service/workflow_service.py:626-656`): `os.listdir`, keep `*_template.yaml`, `WorkflowConfigTemplate.from_yaml`, cache sorted by name; files that fail to parse are skipped with a warning (no validation is run on them). Consumers: `routers/workflow.py:336,393` (prebuilt list/by slug), `src/external/deployment_scripts/preconfigured_workflows.py:89`; tests `tests/codemie/service/test_workflow_service.py`, `tests/codemie/rest_api/routers/test_workflow.py`, `tests/codemie/core/workflow_models/test_workflow_config.py`, `tests/external/deployment_scripts/test_preconfigured_workflows.py`. No test iterates the corpus through `validate_workflow` (no file matching `tests/**/*template*valid*.py`).
- **Workflow-level settings are copied from YAML in `parse_execution_config`** (`workflow_config.py:264-276`) into declared fields that are DB columns (`recursion_limit`, `max_concurrency`, `pool_config`, `max_nesting_level`, ...); alternative (read live from `yaml_config`) is not used by any current setting; reason: none found.
- **The raw YAML text is stored as the source of truth and re-parsed per use** (`yaml_config` column, `parse_execution_config`, `validate_workflow_execution_config_yaml` each call `yaml.safe_load`); `_rewrite_yaml_config_from_objects` deliberately keeps comments/key order (`routers/workflow.py:141-161`, docstring says so).
- **Contract text is a JSON string, with "valid JSON Schema" meaning schema-form**, and everything else treated as an example (`assistant_service.py:696-704, 776-785`; comment at `:776`: "If output_schema is simple json example, we use it as example but for real JSON schemas we enforce structured outputs").
- **`JSONDecodeError`-tolerant parsing is the convention in workflow runtime utilities** (fallback to raw value: `transform_node_utils.py:118`, `transform_node.py:263,275`, `utils.py:107`); `RecursionError` handling is not a convention in workflow code.
- **Validation errors are plain dicts with an `error_type` string, `message`, `errors[]` of `WorkflowValidationErrorDetail`** (`schema.py:74-141, 604-608`; `resources.py:168`); the enum is the only registry of `error_type` values.
- **`IteratorInputNotFoundError` is the non-retryable iteration-key failure** (`workflow.py:748-749`, `workflow_models.py:355`): raised for `KeyError`/`ValueError` from `get_iterable_items_by_key/pointer`.
- "Workflow-level `validation` and `require_input_contracts` are not declared fields; read from `config.yaml_config`" (spec Design): `yaml_config` holds the execution_config YAML text on `WorkflowConfig` (`workflow_config.py:103`); it is non-null only when set; `parse_execution_config` returns early when empty (`:241-246`). **Holds** for availability; `config.` is a naming shorthand, not an existing symbol.
- "`WorkflowConfig` is a table model" (spec): **holds** (`workflow_config.py:321`, `table=True`, `__tablename__ = "workflows"`).
- "`validation/schema.py` wraps `input_schema` problems in a `schema_validation` error with `state_id` and path (as in `schema.py:131-148`)": **holds** for the pattern - `_build_error_detail` (`schema.py:130-141`) fills `state_id`, `path`, `config_line`; but the schema stage works from the parsed YAML dict and JSON Schema, not from `WorkflowState` objects, and it does not parse any contract text today.
- "`RecursionError` is caught with `JSONDecodeError`" (spec): no existing contract-parsing code catches `RecursionError` (P2); `load_and_validate_schema` catches only `JSONDecodeError` - **keeps in place** an existing runtime reader (`assistant_service.py:696`, consumed by `_prepare_workflow_system_prompt` at `:778` and the schema/example split).
- "Built-in tools: by name through `ToolDiscoveryService.find_tool_by_name` ... in process" and "the lookup the save already performs" (spec / AC-38 "the save makes no lookup beyond the one it already performs"): `find_tool_by_name` exists at the cited line, but **the save does not call it**; the existing save lookup is `ToolsService.find_toolkit_for_tool` (+ one MCP call for non-dynamic MCP servers, `resources.py:376`). Contradicted as a description of the current save path; holds as a description of an available in-process lookup. Also: provider tools hit the first branch with an empty `args_schema`; `find_provider_tool` instantiates each provider toolkit.
- "Dynamic MCP servers (`resolve_dynamic_values_in_arguments`) ... UNVERIFIED" (spec): flag exists on `MCPServerDetails` and the existing save already treats it as "cannot validate" (`resources.py:373-375`). **Holds** (and **keeps** that mechanism in place; consumers: `resources.py`, dormant `config_resources_validation.py:178`, `mcp/access_control.py:43`).
- "Linker rejections use one new `WorkflowErrorType` member `contract_validation`" (spec): six members exist, none named that; **keeps in place** `schema_validation` for AC-16 (producers `schema.py:125`). Consumers of the enum's string values outside the repo (UI whitelist): **unverified** here.
- "A user-built wait: `iter_key` list output ignored; placeholder `{{_}}`" (ticket Context): templates use literal `_` (10 occurrences in 6 files) and `unused` (csunit-01/02/03) - the `{{_}}` spelling is the ticket's, the files use bare `_`/`unused`. Runtime: `continue_iteration` reads `state_schema[iter_key]` first, else parses the last message and calls `get_iterable_items_by_key` / by pointer (`workflow.py:737-749`); a key miss raises `IteratorInputNotFoundError`. Holds for "non-retryable" (`workflow_models.py:355`).
- `WorkflowState` (gains fields): 217 referencing callers (codegraph), incl. `service/aws_bedrock/bedrock_flow_service.py`, `workflows/nodes/*` (`bedrock_flow_node.py`, `sub_workflow_node.py`), `workflows/workflow.py`; persisted inside the `workflows.states` JSONB via `PydanticListType`; every YAML state goes through `WorkflowState(**state_data)` in `parse_execution_config` (`workflow_config.py:259`).
- `WorkflowErrorType` (gains a member): 5 importing modules - `validation/schema.py`, `validation/resources.py`, `workflows/workflow.py`, dormant `config_yaml_validation.py`, `config_resources_validation.py`; string values reach API clients through `ValueError(dict)` (`workflow.py:219-221`) and the routers' error formatting; UI whitelist outside this repo.
- `execution_config_schema.yaml` (gains keys): loaded once at import by `validation/schema.py:629-630` (`Draft202012Validator.check_schema`); consumers `validate_workflow_execution_config_yaml` (callers P3). A malformed schema file raises `SchemaError` at import.
- `validate_workflow(...)` signature/behaviour: callers listed in P3 (6 in `src/`); `workflow_generator/nodes/validation.py:364` passes `error_format="json"` and consumes the dict.
- `ToolDiscoveryService.find_tool_by_name`: if used on a new path, existing consumers are `tool_execution_service.py:191,563` and `get_formatted_tool_schema` (`routers/tool.py:85`).
