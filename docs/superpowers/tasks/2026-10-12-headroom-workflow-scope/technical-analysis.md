# Technical Research

**Task**: headroom workflow node yaml headroom_config build_agent_for_workflow migration
**Generated**: 2026-10-12
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15645 slice 3 (workflow-scope): per-workflow and per-workflow-node Headroom context-compression configuration in the CodeMie backend. Slices 1 and 2 are on this branch (stacked, MR !4461): slice 1 is the src/codemie/service/headroom/ package (HeadroomConfig, HeadroomOverride, ResolvedHeadroomConfig, resolver.resolve(node, workflow, assistant) -> ResolvedHeadroomConfig | None, build_headroom_pre_model_hook, settings, features:headroom flag); slice 2 added assistants.headroom_config (JSONB column, migration f8bd0b700a64), LangGraphAgent headroom_config kwarg with the hook last in the single-agent and supervisor chains, and AssistantService.build_agent resolving resolve(None, None, stored) for LangGraph agents only. This branch also has origin/main merged in, so it contains the EPMCDME-15759 fix (commit c90704f68 "Preserve workflow YAML as sent on save"), which changed src/codemie/rest_api/routers/workflow.py, workflow_config.py and workflow_models.py. The alembic head is now f8bd0b700a64 (parent 5189d4d2afaf).

Slice 3 must: (a) add workflows.headroom_config nullable JSONB column + one alembic migration (down_revision = the live single head, verify with ScriptDirectory.get_heads(); model on t1u2v3w4x5z7_add_sub_workflow_pool_config.py), (b) parse a top-level `headroom:` block (HeadroomOverride so unset fields inherit) from workflow YAML in WorkflowConfigBase next to pool_config (workflow_config.py), and a node-level `headroom:` on `assistants[]` entries via a typed WorkflowAssistant.headroom: HeadroomOverride | None field (workflow_models.py ~line 52), (c) add both to src/codemie/workflows/execution_config_schema.yaml AND the second validator (config_yaml_validation.py / validation/schema.py: two validators share the YAML), (d) confirm the workflow save/update path (routers/workflow.py POST/PUT, post-15759) now preserves a `headroom:` block as sent, and persist the parsed value on the column, (e) plumb the workflow-level config and the node override through AssistantExecutionOptions (workflows/utils/utils.py ~517 and its three call sites: agent_node.py ~277, workflow.py ~677, supervisor_workflow.py) into AssistantService.build_agent_for_workflow (assistant_service.py ~785) and resolve with resolve(node, workflow, assistant) so precedence is node > workflow > assistant > off, passing the LangGraphAgent kwarg only for LangGraph agents and only when non-None, (f) tests, (g) update the Headroom integration guide (.ai-run/guides/integration/headroom-integration.md) with workflow/node YAML and precedence. Out of scope: UI and CodeMie SDK repos, AIToolsAgent/Bedrock/A2A support, versioning, structured-output compression.

(Remaining read-first/focus instructions of the task were followed; see sections below.)

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/service/headroom/models.py` — `HeadroomConfig` (all fields defaulted, `enabled=False`), `HeadroomOverride` (all fields `| None`), frozen dataclass `ResolvedHeadroomConfig`.
- `src/codemie/service/headroom/resolver.py` — `resolve(node, workflow, assistant)`: kill switch + customer flag checked first; per-field first non-None across (node, workflow, assistant) then `HeadroomConfig()` default; returns None when `enabled` resolves falsy. Layers are read with `getattr(layer, field, None)`, so a Mock layer would yield Mock values (risk).
- `src/codemie/service/assistant_service.py`:
  - line ~478 `_resolve_headroom_config(assistant)` — slice 2 helper: returns None unless `assistant.headroom_config` is a real `HeadroomConfig`; calls `resolve_headroom_config(None, None, stored)`. Imports at lines 54-55.
  - line ~650 in `build_agent`: after `_configure_langgraph_agent`, `if (cfg := cls._resolve_headroom_config(assistant)) is not None: agent_kwargs["headroom_config"] = cfg` (inside `agent_class == LangGraphAgent`).
  - line ~790 `_select_agent_class_for_workflow` — LangGraphAgent only if `config.ENABLE_LANGGRAPH_AITOOLS_AGENT`, not Bedrock, and model not in react LLMs; else `AIToolsAgent`.
  - line ~800 `build_agent_for_workflow(...)` — keyword params end at `is_global`. `assistant` is loaded by `_load_and_configure_workflow_assistant` (saved assistant via `assistant_id` or a virtual one built from the inline YAML entry). Agent kwargs built ~907; the `if agent_class == LangGraphAgent:` block ~927 calls `_configure_langgraph_agent(...)` and does NOT yet pass `headroom_config` (the slice-3 insertion point). It does not set `is_subagent`.
- `src/codemie/rest_api/models/assistant.py` — `AssistantBase.headroom_config: HeadroomConfig | None` (line 346) and `Assistant.headroom_config` column via `Column(PydanticType(HeadroomConfig))` (line 675). This is the exact pattern to mirror on workflows.
- `src/codemie/workflows/utils/utils.py` line 517 `AssistantExecutionOptions` (dataclass, fields thought_queue ... `is_global`); `initialize_assistant(user_input, user, workflow_assistant, workflow_state, options)` (line 533) forwards each option to `AssistantService.build_agent_for_workflow(...)` (line ~541). Only these callers of `initialize_assistant` exist in src: `workflows/nodes/agent_node.py:277`, `workflows/workflow.py:678`, `workflows/supervisor_workflow.py:104` (the last goes through `self.initialize_assistant` i.e. the workflow.py method, so the real option construction sites are agent_node.py and workflow.py: two `AssistantExecutionOptions(` literals, not three). `build_agent_for_workflow` has no other src caller.
- `src/codemie/workflows/nodes/agent_node.py` ~265-297: builds options; `self.workflow_config` available (used for `.project`, `.is_global`, `.created_by`). `workflow_assistant` here may be a `model_copy` with resolved model (`workflow_assistant.model_copy(update=...)`), so a typed `headroom` field survives the copy.
- `src/codemie/workflows/workflow.py` ~677 `initialize_assistant(self, assistant, workflow_state=None)` — options built from `self.workflow_config` (project, is_global). Used by supervisor (supervisor_workflow.py:104 loops `workflow_config.assistants`, one agent per assistant at graph build time).

### Workflow config model and persistence
- `src/codemie/core/workflow_models/workflow_config.py`:
  - `WorkflowConfigBase` (line 76) holds columns via `SQLField(sa_column=Column(PydanticType(...)))`; `pool_config` (line 121) is `Optional[WorkflowPoolConfig] = SQLField(default=None, sa_column=Column(PydanticType(WorkflowPoolConfig)))`, then `max_nesting_level`.
  - `parse_execution_config()` (line 239) re-derives assistants/custom_nodes/tools/states/retry_policy/etc. from `yaml_config`; pool_config at lines 273-274: `pool_cfg = yaml_data.get("pool_config"); self.pool_config = WorkflowPoolConfig(**pool_cfg) if pool_cfg else None`. Assistants are built as `WorkflowAssistant(**{**assistant_data, "skill_ids": ...})` (line ~247), so a typed `headroom` field on `WorkflowAssistant` is populated automatically; an absent key gives None and a present key is silently ignored today (WorkflowAssistant has no `extra` config; pydantic default ignores unknown).
  - `WorkflowConfig(BaseModelWithSQLSupport, WorkflowConfigBase, table=True)` (line 320) with private `_source_yaml_config`. `WorkflowConfigTemplate(WorkflowConfigBase)` (line 369) inherits all columns, so a new base field becomes a template/response field automatically; its `from_yaml` goes through `CommonBaseModel.from_yaml` (and then probably `parse_execution_config`) with a `model_construct` fallback that lists fields explicitly and does not set pool_config either (so no change required for parity).
- `src/codemie/core/workflow_models/workflow_models.py` line 52 `WorkflowAssistant(BaseModel)`: fields id, assistant_id, name, model, temperature, system_prompt, limit_tool_output_tokens, tools, datasource_ids, exclude_extra_context_tools, mcp_servers, skill_ids. No `headroom` yet. It stores nested typed models inside `PydanticListType(WorkflowAssistant)` JSON in the DB, so no column change is needed for the node override.
- `src/codemie/service/workflow_service.py`: `update_workflow` -> `_update_workflow_values` (line 777): copies `_editable_non_boolean_fields` from the proposed config onto the stored row (only when non-None), then if `yaml_config` was updated calls `stored_config.parse_execution_config()` on the STORED row. So the stored `headroom_config` is recomputed from YAML on every PUT (removing the block yields None, which is desired) with no extra wiring. Then `_yaml_content_changed` restores `old_yaml` if semantically unchanged. Create path (`create_workflow` POST) persists the parsed `WorkflowConfig` from the router. `pool_config` is also queried at `workflow_service.py:988` (pool-only use).

### 15759 fix (routers/workflow.py) — facts
- `_rewrite_yaml_config_from_objects` (line 141) now: `yaml.safe_load` the sent text, patch only `model` values of `assistants`/`custom_nodes` entries (matched by `id`, via `_patch_model_ids_in_yaml`), and only if something changed re-dump with `sort_keys=False` (and record `_source_yaml_config`). Otherwise `yaml_config` is left byte-for-byte untouched. All other keys (including any top-level `headroom:` and node `headroom:`) survive in both cases: they are kept in the loaded dict and dumped back (only comments/format change when a model id was normalized). It no longer rebuilds YAML from objects, so the old risk 1 (key dropping) is resolved by this fix; no allow-list to extend.
- Call sites: POST at line 625 (inside `else:` of try on `parse_execution_config()`), PUT at line 674 (inside the try that also calls `parse_execution_config()`). `_normalize_workflow_model_ids` runs first on the parsed objects.
- POST: parse errors are deferred to `WorkflowExecutor.validate_workflow` (JSON-schema, path-aware errors). PUT: any parse exception becomes HTTP 400 `WORKFLOW_CONFIGURATION_ERROR`, so an invalid `headroom:` (e.g. bad strategy enum rejected by the typed `HeadroomOverride`) is a 400 on PUT and a validation-path error on POST.
- Confirmed by reading only; no test exercises a `headroom:` key yet. A router test with a top-level and node `headroom:` asserting the stored `yaml_config` equals the sent text (and with a model id needing normalization, still containing `headroom`) is the verification.

### Validators
- Validator 1: `src/codemie/workflows/execution_config_schema.yaml` (JSON Schema 2020-12, top-level `properties:`, no `additionalProperties: false` anywhere, so unknown keys such as `headroom` already pass today). `pool_config` at line 76 (`type: object`, `properties` with defaults); `Assistant` def at line 90 (`required: [id]`, props id ... `exclude_extra_context_tools`, then `anyOf: [assistant_id | system_prompt]`).
- Validator 2: `src/codemie/workflows/config_yaml_validation.py` loads that schema (`_load_schema_from_file`) and formats errors; `src/codemie/workflows/validation/schema.py` is the Draft202012Validator wrapper (docstring: schema "defined in" the same YAML via `WORKFLOW_EXECUTION_CONFIG_SCHEMA_YAML_FILE_PATH`) plus cross-reference validation. grep found no `pool_config`/`max_nesting_level` mention in either Python file, so both validators derive from the one YAML file; no second hand-maintained key list found. Verify `validation/models.py` / `transformers.py` for typed models during planning (not read in depth).

### Sub-assistants
- `build_agent_for_workflow` sets no `is_subagent` and has no sub-assistant construction in the workflow path; `is_subagent` appears only in `build_agent` (line 525/649). Sub-assistants of a saved node assistant are built by the normal `build_agent`/toolkit path with their own stored `headroom_config` (slice 2), so they do NOT receive the node/workflow override unless explicitly plumbed. Treat as out of scope / document in guide (spec risk 9).

### Integration Points
- Run time: workflow-level config is on `self.workflow_config` (WorkflowConfig row) in both `agent_node.py` and `workflow.py`; node override is on the `WorkflowAssistant` passed to `initialize_assistant`. The plumbing can take both from there, so only `AssistantExecutionOptions` (new `headroom_workflow: HeadroomOverride | None`) and the node's `workflow_assistant.headroom` need to reach `build_agent_for_workflow`; the stored assistant value comes from the loaded `assistant.headroom_config`.
- Type mismatch: `WorkflowConfig.headroom_config` should be `HeadroomOverride | None` (spec: unset fields inherit) while `Assistant.headroom_config` is `HeadroomConfig`. Resolver accepts both shapes by field name.

### Patterns and Conventions
- JSONB pydantic columns use `PydanticType(Model)` in `SQLField(sa_column=Column(...))`; migration via `op.add_column("workflows", sa.Column("...", postgresql.JSONB(), nullable=True))`, template `t1u2v3w4x5z7_add_sub_workflow_pool_config.py` (same `Union[str, None]` header style as f8bd0b700a64).
- Execution options grouped in `AssistantExecutionOptions` to keep parameter counts bounded (Sonar-style); `build_agent_for_workflow` signature already long, so add one param carefully.
- Guides: `.ai-run/guides/integration/headroom-integration.md` exists (from slice 1/2) and is the file to update.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/headroom-integration.md` (target of (g)); `.ai-run/guides/data/database-patterns.md`, `.ai-run/guides/workflows/langgraph-workflows.md`, `.ai-run/guides/testing/testing-patterns.md` apply.
- Specs read from caller's list: `docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md`, `docs/headroom/research-findings.md`, slice 1/2 analyses exist under docs/superpowers/tasks/ (not re-litigated; their workflow risks 1, 9 are re-verified above: 1 resolved by 15759, 2 still true until the typed field is added, 9 sub-assistants not propagated, 14 templates inherit the base field).

### Architectural Decisions
- Precedence node > workflow > assistant > off, per-field inheritance via `HeadroomOverride`; LangGraph agents only.

### Derived Conventions
- Optional YAML sub-objects are parsed in `parse_execution_config` as `Model(**cfg) if cfg else None`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/test_assistant_service_workflow_mapping.py` — helper `_build_agent_for_workflow(workflow_assistant, mock_assistant, get_tools_spy)` patches collaborators at seams and calls `build_agent_for_workflow`; uses `Mock(spec=User)` (line 40) and `Mock(spec=Assistant)` (line 70). Extend for headroom kwarg.
- `tests/codemie/service/test_assistant_service_headroom.py` and `tests/codemie/service/headroom/` — slice 1/2 resolver and build_agent tests.
- `tests/codemie/core/workflow_models/test_workflow_config.py` — `test_parse_execution_config_pool_config` (line 253) is the model for a headroom parse test.
- `tests/codemie/workflows/test_config_yaml_validation.py` — `test_pool_config_passes_schema` (line 799) inline YAML templates.
- `tests/codemie/rest_api/routers/test_workflow.py` — YAML fixture with `pool_config:` at line 358; POST/PUT tests present (15759 added preservation tests, see file).
- `tests/codemie/migrations/test_add_headroom_config_to_assistants.py` — slice 2 migration test pattern: revision, `down_revision == EXPECTED_DOWN_REVISION` (its parent), patches `op` and checks `add_column`, downgrade `drop_column`, and `test_migration_graph_has_single_head` which asserts `len(get_heads()) == 1` and that its revision is in the walk. It does NOT pin the head id.

### Testing Framework and Patterns
- pytest; migrations tested by importing the module with `importlib` and `patch.object(module, "op")`. A new migration test must copy this (assert own parent, single head, own revision reachable), never `get_heads() == [NEW_REV]`. Note: adding the new migration makes `f8bd0b700a64` no longer head; the slice-2 test only asserts a single head, so it stays green.

### Coverage Gaps
- No tests for `headroom` in workflow YAML parse, schema, router save, `AssistantExecutionOptions`, supervisor path, or `build_agent_for_workflow` kwarg passing; none for sub-assistants.

---

## 5. Configuration and Environment

### Environment Variables
- Slice 1 settings: `config.HEADROOM_ENABLED` (kill switch) and customer feature `features:headroom` (`customer_config.is_feature_enabled("headroom")`), both checked inside `resolve`.

### Configuration Files
- `src/codemie/workflows/execution_config_schema.yaml` (workflow YAML schema); alembic versions in `src/external/alembic/versions/`. Alembic head is `f8bd0b700a64` (parent `5189d4d2afaf`); the only other duplicate-parent pairs in the tree are historic. Re-verify with `ScriptDirectory.get_heads()` before writing the migration (pull may move it).

### Feature Flags and Deployment Concerns
- Flag/kill switch need no change. Migration is additive nullable column (no backfill).

---

## 6. Risk Indicators

- Resolver tolerates only real models: `getattr(layer, field)` on a Mock/MagicMock `workflow_config` or `WorkflowAssistant` returns truthy Mock values. In `agent_node.py`/`workflow.py` tests, `self.workflow_config` is often a Mock, so `workflow_config.headroom_config` would be a Mock and the plumbing must guard with `isinstance(x, HeadroomOverride)` (as `_resolve_headroom_config` does for `HeadroomConfig`), or existing tests that use `Mock(spec=WorkflowConfig)` would break/produce junk kwargs. `Mock(spec=Assistant)` in the mapping test has `headroom_config` auto-attribute via spec once added; guarded by the existing isinstance helper.
- `_resolve_headroom_config` currently hardcodes `(None, None, stored)`; refactor to accept node/workflow without altering the slice-2 `build_agent` behavior and tests in `test_assistant_service_headroom.py`.
- Resolver returns None when `enabled` resolves falsy; a workflow `headroom: {strategy: aggressive}` alone (enabled unset, assistant unset) is OFF. Document in guide; a node-level `enabled: true` on an inline assistant (no stored HeadroomConfig) enables via defaults.
- Inline YAML assistants have no stored `headroom_config` (virtual assistant); saved assistants (`assistant_id`) do. Both flow through the same call; verify `_load_and_configure_workflow_assistant` preserves `headroom_config` from the stored assistant (not read in detail).
- Sub-assistants/toolkit-built agents under a node do not get node/workflow override (not plumbed; `build_agent_for_workflow` has no sub-agent path). Document as known behavior.
- AIToolsAgent (react/Bedrock/flag off) path: kwarg must be omitted; `AIToolsAgent(**agent_kwargs)` would raise on unknown kwarg.
- Unknown-key leniency: `WorkflowAssistant` and schema ignore/allow unknown keys; a typo (`headrom:`) is silently ignored. Schema additions should use `enum` for strategy and `additionalProperties: false` only on the new `headroom` object (consider; existing objects are open).
- `PydanticType` and `_update_workflow_values`: copying only non-None editable fields; the headroom value is not copied but recomputed through `parse_execution_config` on stored config. Do not add `headroom_config` to `_editable_non_boolean_fields` (would be redundant and block clearing).
- `model_construct` fallback in `WorkflowConfigTemplate.from_yaml` does not set new fields; harmless (default None) but templates containing `headroom:` and unresolved placeholders will not surface it.
- 15759 rewrite still re-dumps via `yaml.safe_dump` when a model id is normalized: comments lost but `headroom` keys preserved. Existing behavior, not a regression.
- Test pinning: slice-2 migration test and any other `get_heads` assertion are non-pinning; new test must stay so. Check `tests/codemie/clients/test_enterprise_migrations.py` and startup integration tests for hardcoded head ids (not read).
- `build_agent_for_workflow` already has 18 parameters; adding more may trip Ruff/Sonar parameter-count rules; pass via a single new param or reuse a grouped object.
- Speculative: workflow responses (`WorkflowConfig` list/detail) will include `headroom_config` automatically since it lives on the base class; confirm no explicit response field whitelists (`WorkflowConfigListResponse` not inspected).
- Speculative: copy/export/fork paths for workflows (not found in `workflow_service.py` by grep for copy/export/fork) likely re-parse yaml, so need no change.

---

## 7. Summary for Complexity Assessment

Layers touched: model/DB (workflows column + one alembic migration, WorkflowConfigBase and WorkflowAssistant fields), YAML parsing (`parse_execution_config`), schema validation (one YAML schema shared by both validators), workflow runtime plumbing (`AssistantExecutionOptions`, `initialize_assistant`, two options construction sites, `build_agent_for_workflow`), service (resolver call with node/workflow/assistant), tests and one guide. Roughly 8-10 source files and 5-6 test files, all following established slice-2 and pool_config patterns; no new dependency.

Technical novelty is low: every step has a close precedent (pool_config for the column and parsing, `headroom_config` on assistants for the column, slice-2 kwarg for the agent). The 15759 fix removes the main save-path risk (YAML key dropping): header-level keys survive POST and PUT; the PUT path re-parses on the stored row, so persistence needs no extra wiring.

Test posture is good for patterns (mapping, config, schema, router, migration tests exist and are copyable) but there is no headroom coverage in workflow code yet. Key risks are Mock-typed workflow_config values leaking into the resolver, the long `build_agent_for_workflow` signature, sub-assistants not receiving the override, and migration tests that must not pin the head.

---

## 8. External References

None named by the task (repo-local docs listed in the task were consulted only as pointers; their decisions were not re-litigated).
