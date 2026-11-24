# Headroom per workflow and node (slice 3: workflow scope) - Spec

Ticket: EPMCDME-15645, slice 3 of 3. Size M (18/36), no split. Branch `EPMCDME-15645_headroom-workflow-scope`, stacked on slices 1-2
(MR !4461). Contains the EPMCDME-15759 fix, so a `headroom:` block survives workflow POST and PUT.
Design (D1-D8, Appendix A): `docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md`.
Slice specs: `docs/superpowers/tasks/2026-10-08-headroom-per-assistant-workflow/spec.md`, `docs/superpowers/tasks/2026-10-09-headroom-assistant-scope/spec.md`.

## Goal

A workflow author can set Headroom compression for a whole workflow and override it per `assistants[]` node in the workflow YAML.
When the workflow runs a LangGraph node agent, the resolved config (node > workflow > assistant > off) drives the slice 1 hook.
Workflows without a `headroom:` block behave exactly as today.

## Behavior

1. **Storage.** `workflows.headroom_config` nullable JSONB, one new alembic migration modelled on
   `t1u2v3w4x5z7_add_sub_workflow_pool_config.py`. `down_revision` is the live single head (`f8bd0b700a64` on 2026-10-12); re-verify with
   `ScriptDirectory.get_heads()` immediately before writing and before merge. The node override needs no column: `WorkflowAssistant` is
   stored as JSON inside the existing assistants column.
2. **Models.** `WorkflowConfigBase.headroom_config: HeadroomOverride | None`, `SQLField(default=None, sa_column=Column(PydanticType(HeadroomOverride)))`,
   next to `pool_config` (`workflow_config.py` ~121). `WorkflowAssistant.headroom: HeadroomOverride | None = None`
   (`workflow_models.py` ~52). Both are `HeadroomOverride` so unset fields inherit from the next layer.
3. **Parse.** `parse_execution_config` (`workflow_config.py` ~273) sets `headroom_config` from top-level `headroom:` as
   `HeadroomOverride(**cfg) if cfg else None`, so removing the block on PUT clears it (it re-parses the stored row; do not add the field to
   `_editable_non_boolean_fields`). Node `headroom:` is populated by the existing `WorkflowAssistant(**assistant_data)`.
4. **Schema.** `execution_config_schema.yaml` gets a top-level `headroom` property and an `Assistant.headroom` property. Both new objects use
   `additionalProperties: false` and `strategy` as an enum of the values in `strategies.py`; the rest of the schema stays open. That file is the
   only key list: `config_yaml_validation.py` and `validation/schema.py` load it, and `validation/models.py` / `transformers.py` hold none.
5. **Plumbing.** Add `headroom_workflow: HeadroomOverride | None` to `AssistantExecutionOptions` (`workflows/utils/utils.py` ~517), set from
   `self.workflow_config.headroom_config` in the two construction sites (`agent_node.py` ~277, `workflow.py` ~677; supervisor reaches
   `workflow.py` via `initialize_assistant`). `initialize_assistant` passes ONE new grouped argument to `build_agent_for_workflow`
   carrying the workflow-level value and the node's `workflow_assistant.headroom`; the function's signature grows by one parameter only.
   Construction sites accept only `isinstance(..., HeadroomOverride)` values (workflow_config is often a Mock in tests).
6. **Resolve.** In `build_agent_for_workflow`, extend `_resolve_headroom_config` (`assistant_service.py` ~478) to take optional node and workflow
   layers and call `resolve(node, workflow, stored)`. Each layer is isinstance-guarded (`HeadroomOverride` for node/workflow, `HeadroomConfig`
   for stored); anything else counts as absent. Slice 2's `build_agent` call `(None, None, stored)` and its tests stay unchanged. The stored
   value is the loaded assistant's `headroom_config`. `agent_kwargs["headroom_config"]` is set only inside the `if agent_class == LangGraphAgent:`
   block (~927) and only when resolve returns non-None. `AIToolsAgent` never receives the kwarg.
7. **Gating and semantics.** Both gates stay inside `resolve()`. Stored config is kept while a gate is off and ignored at runtime.
   Node `enabled: false` beats a workflow that is on. Node `enabled: true` with no workflow block enables with defaults. A workflow block with
   `strategy` set but `enabled` unset at every layer resolves to OFF (documented).
8. **Open item resolved.** `_load_and_configure_workflow_assistant` (`assistant_service.py` ~708) returns the DB `Assistant` from `get_by_id` and only
   overrides `llm_model_type` and `temperature`, so a saved assistant's `headroom_config` is preserved. Virtual (inline) assistants carry none.
9. **Docs.** Update `.ai-run/guides/integration/headroom-integration.md`: workflow and node YAML example, precedence, per-field inheritance,
   the strategy-without-enabled OFF case, and the limitations below.

## Acceptance criteria

- W1. Migration adds nullable JSONB `workflows.headroom_config`; upgrade and downgrade work; its own parent is asserted, a single head exists and its revision is reachable (no `get_heads() == [rev]`).
- W2. `parse_execution_config` yields `HeadroomOverride` from top-level `headroom:` (partial fields kept unset) and from `assistants[].headroom`; absent or removed block gives None.
- W3. Schema accepts valid blocks and rejects unknown keys and bad `strategy` inside either `headroom` object, while unknown keys elsewhere remain accepted.
- W4. Workflow POST and PUT store `yaml_config` with the `headroom:` blocks intact (also when a model id is normalized). Malformed headroom: PUT returns 400, POST defers to the validation path.
- W5. `build_agent_for_workflow` precedence matrix: node beats workflow beats assistant beats off, per field; node `enabled: false` beats workflow on; node `enabled: true` alone enables with defaults; strategy-only workflow block is OFF.
- W6. `headroom_config` kwarg is passed only for `LangGraphAgent` and only when resolved non-None; never to `AIToolsAgent`.
- W7. Unconfigured workflows, flag off or kill switch off: agent kwargs identical to before (no `headroom_config` key); Mock `workflow_config`, `Mock(spec=Assistant)` and `Mock(spec=WorkflowAssistant)` inputs work unchanged.
- W8. Slice 2 tests in `test_assistant_service_headroom.py` pass untouched.
- W9. Guide updated with the content in Behavior 9.

## Ticket AC mapping

Slice 3 satisfies: AC3, AC4, AC5 for workflows and nodes; AC6 for workflows (W7); remaining AC7 (workflow tests) and AC8 (guide update). With slices 1-2 the backend is complete.

## Non-goals

- UI and CodeMie SDK repos (contract is design Appendix A).
- Per-state overrides beyond the `assistants[]` node entry.
- New strategies; versioning; structured-output compression.
- Any change to slice 1 package code (`src/codemie/service/headroom/`).
- Propagating the override to sub-assistants or `AIToolsAgent`/Bedrock/A2A.

## Known limitations (stated in the guide)

- Sub-assistants and toolkit-built agents under a node do not receive node or workflow override (`build_agent_for_workflow` has no sub-agent path); they resolve from their own saved config.
- `AIToolsAgent`, Bedrock and A2A never compress.
- `headroom` is unversioned; the hook is synchronous and blocking and fails open.
- A typo'd top-level or node key outside the new objects is silently ignored (the rest of the schema is open).

## Tests

Parse (model: `test_parse_execution_config_pool_config`); schema (model: `test_pool_config_passes_schema`); router POST/PUT preserve and reject in
`tests/codemie/rest_api/routers/test_workflow.py`; `build_agent_for_workflow` matrix and regression extending
`test_assistant_service_workflow_mapping.py`; plumbing for `AssistantExecutionOptions`; migration test copying slice 2's. Run only when requested; commands from `.ai-run/guides/quality-gates.md`.
