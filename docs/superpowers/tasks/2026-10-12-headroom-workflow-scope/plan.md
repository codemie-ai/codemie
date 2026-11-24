# Headroom Workflow Scope (EPMCDME-15645 slice 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Per-workflow and per-`assistants[]`-node Headroom config in workflow YAML, resolved node > workflow > assistant > off for LangGraph node agents.

**Architecture:** `HeadroomOverride` on `WorkflowConfigBase.headroom_config` (new JSONB column) and `WorkflowAssistant.headroom`; both flow via `AssistantExecutionOptions` into one grouped argument of `build_agent_for_workflow`, which calls an extended `_resolve_headroom_config`.

**Tech Stack:** SQLModel, Alembic, Pydantic, JSON Schema (YAML), pytest. Tests mirror src under `tests/codemie/`.

## Global Constraints

- Commit per task using the repository's existing convention: `EPMCDME-15645: Description`. Every commit leaves the repo importable and tests green.
- Implementation only: no whole-suite/lint gate tasks, no manual verification (later stages own them). Run only the narrow tests named per task.
- Slice 1 package `src/codemie/service/headroom/` untouched. No UI/SDK work, no sub-assistant propagation.
- `headroom_config` is NOT added to `_editable_non_boolean_fields` (PUT re-parses the stored row). `additionalProperties: false` only on the two new schema `headroom` objects.
- Follow guides: `development/configuration-patterns.md`, `standards/code-quality.md`, `testing/testing-patterns.md`, `data/database-patterns.md`, `workflows/langgraph-workflows.md`.

---

### Task 1: Models, YAML parse, schema

**Files:** Modify `src/codemie/core/workflow_models/workflow_config.py` (~121 field, ~273 parse), `src/codemie/core/workflow_models/workflow_models.py` (~52), `src/codemie/workflows/execution_config_schema.yaml` (top-level next to `pool_config` line 76; `Assistant` def line 90). Test: `tests/codemie/core/workflow_models/test_workflow_config.py`, `tests/codemie/workflows/test_config_yaml_validation.py`.

**Interfaces:** Produces `WorkflowConfigBase.headroom_config: HeadroomOverride | None`, `WorkflowAssistant.headroom: HeadroomOverride | None = None`.

Test-first: yes — `test_parse_execution_config_headroom` (model: `test_parse_execution_config_pool_config`, line 253) fails because `headroom_config` does not exist; plus schema tests (model: `test_pool_config_passes_schema`, line 799) for valid blocks, unknown key and bad `strategy` rejected inside both objects, unknown keys elsewhere still accepted; plus `test_schema_strategy_enum_matches_headroom_strategy` comparing the schema enum with `{s.value for s in HeadroomStrategy}`.

- [ ] Write the failing tests: top-level partial `headroom: {strategy: aggressive}` yields `HeadroomOverride(strategy=aggressive)` with other fields None; `assistants[].headroom` populated; absent or removed block gives None; template (`WorkflowConfigTemplate`) inherits the field.
- [ ] Add `headroom_config: Optional[HeadroomOverride] = SQLField(default=None, sa_column=Column(PydanticType(HeadroomOverride)))` after `pool_config`; in `parse_execution_config` next to the pool_config lines set `self.headroom_config = HeadroomOverride(**cfg) if (cfg := yaml_data.get("headroom")) else None`; add `headroom: HeadroomOverride | None = None` to `WorkflowAssistant`.
- [ ] Add schema property `headroom` (type object, `additionalProperties: false`, properties `enabled`, `compress_user_messages`, `compress_tool_outputs` booleans, `strategy` enum lossless/balanced/aggressive) at top level and the same object under `Assistant.properties`; use a shared `$defs` entry if the file already uses `$defs`/`$ref` for `Assistant`.
- [ ] Run the touched tests, then commit.

Note: the DB column does not exist yet; model-only tests must not need the DB. Task 2 follows immediately.

### Task 2: Alembic migration

**Files:** Create `src/external/alembic/versions/<newrev>_add_headroom_config_to_workflows.py` (model on `t1u2v3w4x5z7_add_sub_workflow_pool_config.py`, header style of `f8bd0b700a64`). Test: `tests/codemie/migrations/test_add_headroom_config_to_workflows.py` (copy `test_add_headroom_config_to_assistants.py`).

**Interfaces:** Produces revision whose `down_revision` is the live single head.

Test-first: yes — migration test fails on import because the module is missing.

- [ ] Immediately before writing, re-verify the single head via `ScriptDirectory.get_heads()` (expected `f8bd0b700a64` on 2026-10-12; use whatever it is at that moment) and set `down_revision` to it.
- [ ] Upgrade `op.add_column("workflows", sa.Column("headroom_config", postgresql.JSONB(), nullable=True))`; downgrade `op.drop_column("workflows", "headroom_config")`.
- [ ] Test asserts: add_column/drop_column calls (patch `op` via `patch.object`), `down_revision == EXPECTED_DOWN_REVISION` (its own parent constant), exactly one head, and own revision reachable by walking from the head. Never assert `get_heads() == [rev]`.
- [ ] Grep `tests/codemie/clients/test_enterprise_migrations.py`, startup/integration tests and `tests/external/alembic/` for hardcoded head ids (earlier grep found none: only `"head"` and non-pinning `get_heads()` uses); fix any the new migration breaks.
- [ ] Run the migration tests, then commit.

### Task 3: Router POST/PUT preservation and malformed headroom

**Files:** Test only: `tests/codemie/rest_api/routers/test_workflow.py` (YAML fixture with `pool_config` near line 358; POST/PUT preservation tests added by 15759). Modify `src/codemie/rest_api/routers/workflow.py` only if a test proves a gap.

Test-first: yes — `test_post_workflow_preserves_headroom_blocks`, `test_put_workflow_preserves_headroom_blocks` (stored `yaml_config` equals sent text; second case with a model id needing normalization still contains both top-level and node `headroom:`), `test_put_workflow_malformed_headroom_returns_400` (bad `strategy`, `WORKFLOW_CONFIGURATION_ERROR`), `test_post_workflow_malformed_headroom_defers_to_validation`. Expected: preservation tests pass once Task 1 exists (15759 already preserves keys); if one fails, fix the minimal router cause.

- [ ] Write the four tests following the neighbouring 15759 tests; assert parsed `headroom_config` is persisted on the stored object.
- [ ] Add a PUT test that removing the block clears `headroom_config` to None.
- [ ] Run them, commit.

### Task 4: Response whitelist decision

**Files:** Test: `tests/codemie/core/workflow_models/test_workflow_config.py`.

Test-first: yes — `test_headroom_config_visibility_in_responses`: `headroom_config` is in `WorkflowConfigBase.model_fields` (so `WorkflowConfigTemplate` and `WorkflowListResponse.data` of `WorkflowConfigBase` expose it) and is intentionally absent from `WorkflowConfigListResponse` (`workflow_config.py:435`, the minimal list model that excludes heavy fields). Decision: leave `WorkflowConfigListResponse` unchanged; the editor reads the detail endpoint. The test fails only if someone changes either side, pinning the decision.

- [ ] Write the test, run it (passes after Task 1; no source change), add a one-line comment above `WorkflowConfigListResponse` recording the decision, commit.

### Task 5: Options plumbing and resolver precedence

**Files:** Modify `src/codemie/service/assistant_service.py` (`_resolve_headroom_config` ~478, `build_agent_for_workflow` ~800, LangGraph block ~927, new dataclass), `src/codemie/workflows/utils/utils.py` (`AssistantExecutionOptions` ~517, `initialize_assistant` ~533), `src/codemie/workflows/nodes/agent_node.py` (~277), `src/codemie/workflows/workflow.py` (~677). Tests: `tests/codemie/service/test_assistant_service_workflow_mapping.py` (helper `_build_agent_for_workflow`), `tests/codemie/workflows/utils/test_utils.py` (or the existing utils test file), existing agent_node/workflow tests.

**Interfaces:**
- Produces in `assistant_service.py`: `@dataclass(frozen=True) class WorkflowHeadroomLayers: node: HeadroomOverride | None = None; workflow: HeadroomOverride | None = None`.
- `AssistantExecutionOptions.headroom_workflow: HeadroomOverride | None = None`.
- `build_agent_for_workflow(..., headroom_layers: WorkflowHeadroomLayers | None = None)`: ONE new keyword parameter, last.
- `_resolve_headroom_config(assistant, node=None, workflow=None)`: slice 2's `build_agent` call and tests stay unchanged.

Test-first: yes — `test_build_agent_for_workflow_headroom_*` matrix fails because no `headroom_config` kwarg is ever passed: node beats workflow beats assistant beats off per field; node `enabled: false` beats workflow on; node `enabled: true` alone (inline assistant) enables with defaults; strategy-only workflow block is OFF; kwarg only for `LangGraphAgent`, never `AIToolsAgent`; flag/kill switch off or nothing configured means no `headroom_config` key in agent kwargs. Regression cases: `Mock(spec=Assistant)`, `Mock(spec=WorkflowAssistant)`, Mock `workflow_config` give identical kwargs to before. Plumbing tests: `initialize_assistant` passes `WorkflowHeadroomLayers(node=workflow_assistant.headroom, workflow=options.headroom_workflow)`; the two construction sites set `headroom_workflow` only when `isinstance(self.workflow_config.headroom_config, HeadroomOverride)`, else None.

- [ ] Write failing tests above (patch `resolve` seams like `test_assistant_service_headroom.py` does for flags).
- [ ] Extend `_resolve_headroom_config`: isinstance-guard each layer (`HeadroomOverride` for node/workflow, `HeadroomConfig` for stored), anything else is absent; call `resolve_headroom_config(node, workflow, stored)`.
- [ ] In `build_agent_for_workflow`, inside the existing `if agent_class == LangGraphAgent:` block after `_configure_langgraph_agent`, set `agent_kwargs["headroom_config"]` only when the resolved value is non-None, using `headroom_layers` node/workflow and the loaded assistant's `headroom_config`.
- [ ] Add the field to `AssistantExecutionOptions`, forward the grouped argument in `initialize_assistant`, and set the field in both construction sites with the isinstance guard. Supervisor reaches `workflow.py` via `initialize_assistant`; no change there.
- [ ] Confirm `tests/codemie/service/test_assistant_service_headroom.py` passes untouched, run the touched tests, commit.

### Task 6: Guide update

**Files:** Modify `.ai-run/guides/integration/headroom-integration.md`.

Test-first: no — documentation only, no behaviour to fail.

- [ ] Add: workflow and node YAML example, precedence node > workflow > assistant > off, per-field inheritance, strategy-without-enabled resolves OFF, node `enabled: true` alone enables with defaults, `workflows.headroom_config` column, list response excludes it. Limitations: sub-assistants and toolkit-built agents do not receive the override; `AIToolsAgent`/Bedrock/A2A never compress; unversioned, synchronous hook that fails open; typo'd keys outside the new objects are silently ignored. Commit.

---

## Self-review

- Spec coverage: W1 Task 2; W2/W3 Task 1; W4 Task 3; W5/W6/W7/W8 Task 5; W9 Task 6; response whitelist Task 4.
- negative-constraints: slice 1 untouched (no task edits it); no sub-assistant propagation (Task 5 touches only `build_agent_for_workflow`); `headroom_config` kept out of `_editable_non_boolean_fields` (no task edits it); `AIToolsAgent` gets no kwarg (Task 5 guard); no UI/SDK; `additionalProperties: false` only on new objects (Task 1); no `get_heads() == [rev]` (Task 2).
