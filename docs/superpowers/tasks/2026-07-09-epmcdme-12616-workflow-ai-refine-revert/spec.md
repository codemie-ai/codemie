# EPMCDME-12616: Workflow AI Refine — Rework Spec

**Date:** 2026-07-17
**Branch:** EPMCDME-12616_enhance-workflow-edit-ai-refine-revert
**Aligns with:** EPMCDME-10037 (workflow generation infrastructure)

---

## Overview

Rework the `POST /workflows/{id}/refine` endpoint to use a dedicated `WorkflowRefinerGraph` (LangGraph) instead of the ad-hoc `PromptGeneratorChain` introduced in the original 12616 implementation. The refiner graph reuses the node generation, assembly, and validation nodes from the existing `WorkflowGeneratorGraph` (10037), replacing only the entry point with a `RefinePlannerNode` that understands how to modify an existing workflow.

The revert endpoint and all revert-related code are removed — the feature is not used on the frontend.

---

## Scope

### What changes

| Component | Change |
|---|---|
| `src/codemie/workflows/workflow_generator/workflow_refiner.py` | **NEW** — `WorkflowRefinerGraph` class |
| `src/codemie/workflows/workflow_generator/nodes/refine_planner.py` | **NEW** — `RefinePlannerNode` LLM node |
| `src/codemie/templates/agents/workflow_generator/refine_planning.py` | **NEW** — refine planner prompt template (inside existing subpackage) |
| `src/codemie/workflows/workflow_generator/state.py` | **MODIFY** — add `existing_yaml_config` and `refine_prompt` optional fields |
| `src/codemie/service/workflow_generator_service.py` | **MODIFY** — `refine_workflow()` drops `PromptGeneratorChain`, uses `WorkflowRefinerGraph` |

### What is removed (current 12616 additions)

| Component | Reason |
|---|---|
| `src/codemie/templates/agents/workflow_generator_prompt.py` | Replaced by subpackage template |
| `WORKFLOW_REFINE_TEMPLATE`, `WorkflowRefineDetails` | Replaced by `RefinePlannerNode` structured output |
| `PromptGeneratorChain` import in `workflow_generator_service.py` | No longer used |
| `POST /workflows/{id}/revert` endpoint | Not used on frontend |
| `WorkflowService.revert_workflow()` | No longer needed |
| `WorkflowRevertRequest` model | No longer needed |
| `YamlConfigHistory.change_type` field | Nothing sets it once revert is gone and refine is stateless |
| `change_type` param on `WorkflowService._update_workflow_values()` | Follows from above |
| Revert monitoring methods and metric constants | Follows from above |

### What is unchanged (12616 additions that survive)

- `POST /workflows/{id}/refine` endpoint (same path, same request/response models)
- `WorkflowRefineRequest`, `WorkflowRefineResponse` models
- AI refine monitoring metrics (`WORKFLOW_AI_REFINE_TOTAL_METRIC`)
- `WorkflowGeneratorGraph` — not touched

---

## Architecture

The `WorkflowRefinerGraph` is a sibling to `WorkflowGeneratorGraph`. Both live under `src/codemie/workflows/workflow_generator/` and share the same node implementations.

```
WorkflowRefinerGraph flow:

  refine_planner
       │
       ▼
  node_generator_router ──► node_generator ──► tools_selector ──► (back to router)
       │
       ▼ (all steps generated)
  config_assembly
       │
       ▼
  validation ──► END (success)
       │
       ▼ (errors, retries remaining)
  node_regenerator ──► config_assembly
```

**Reused without modification:** `NodeGeneratorRouterNode`, `NodeGeneratorNode`, `ToolsSelectorNode`, `ConfigAssemblyNode`, `ValidationNode`, `NodeRegeneratorNode`.

---

## State Extension

`WorkflowGeneratorState` (`state.py`) gains two optional fields:

```python
existing_yaml_config: Optional[str]   # set by the refine path; absent on the generate path
refine_prompt: Optional[str]          # user's refinement instruction
```

These fields are `None` on every `WorkflowGeneratorGraph` run — no behaviour change for the existing generation flow.

---

## RefinePlannerNode

**Location:** `src/codemie/workflows/workflow_generator/nodes/refine_planner.py`

**Inputs from state:** `existing_yaml_config`, `refine_prompt`, `available_tools`, `user`, `project`

**Single LLM call** with structured output. The prompt (`refine_planning.py`) instructs the LLM to:

1. Parse the existing `yaml_config` to understand the current step topology, assistant configurations, and tool assignments.
2. Interpret the `refine_prompt` to determine which steps to preserve, modify, add, or remove — or whether a full regeneration is needed.
3. Output a `RefinePlan` (structured Pydantic model) containing:
   - `workflow_name: str` — from existing workflow unless the refine_prompt explicitly changes it
   - `workflow_description: str` — same rule
   - `steps: list[WorkflowStep]` — complete desired step list (preserved + modified + new; removed steps absent)
   - `step_plans: list[StepPlan]` — one per step in `steps`; preserved steps carry the original node configuration as context so the generator reproduces them faithfully; changed/new steps carry the desired change description

**State updates emitted:**

```python
{
    sk.INTENT: WorkflowIntent(workflow_name=..., workflow_description=..., steps=...),
    sk.STEP_PLANS: [StepPlan(...), ...],   # same length as INTENT.steps
    sk.CURRENT_NODE_INDEX: 0,
    sk.PREVIOUS_NODE: None,
    # NODE_PLAN intentionally omitted — router/generator initialise it from scratch
}
```

After emitting, the existing `node_generator_router` loop takes over exactly as in the creation flow.

---

## WorkflowRefinerGraph

**Location:** `src/codemie/workflows/workflow_generator/workflow_refiner.py`

Mirrors `WorkflowGeneratorGraph` in structure. Key difference: entry point is `refine_planner` instead of `intent_analysis` + `step_planner`.

```python
class WorkflowRefinerGraph:
    def __init__(self, llm_model: str, request_id: Optional[str] = None): ...
    def _build_graph(self) -> CompiledGraph: ...
    def run(self, initial_state: WorkflowGeneratorState) -> WorkflowGeneratorState: ...
```

Graph wiring:

```python
workflow.set_entry_point("refine_planner")
workflow.add_edge("refine_planner", "node_generator_router")
# all remaining edges identical to WorkflowGeneratorGraph
```

---

## Service Layer

`WorkflowGeneratorService.refine_workflow()` is updated to:

1. Resolve `llm_model` (same fallback as `generate()`)
2. Load `available_tools` via `ToolsInfoService.get_tools_info(user=user, exclude_toolkits=["Plugin"])`
3. Build `WorkflowGeneratorState` with `existing_yaml_config`, `refine_prompt`, `user`, `project`, `available_tools`, all other fields as `None`/defaults
4. Instantiate `WorkflowRefinerGraph(llm_model, request_id)` and call `.run(state)`
5. On success: return `WorkflowRefineResponse(yaml_config=state["result"].yaml_config)`
6. On `state["error"]`: raise `ExtendedHTTPException(code=500, ...)`
7. Emit `WORKFLOW_AI_REFINE_TOTAL_METRIC` on success (unchanged from current 12616)
8. `finally`: clear `request_summary_manager`

Error handling mirrors `generate()` exactly.

---

## API Layer

`POST /workflows/{id}/refine` — unchanged signature:

```python
Request:  WorkflowRefineRequest(yaml_config, refine_prompt, llm_model, project)
Response: WorkflowRefineResponse(yaml_config)
```

The endpoint continues to:
- Load the workflow by ID and check write permission
- Set logging context and LLM context
- Call `WorkflowGeneratorService.refine_workflow(yaml_config, refine_prompt, user, llm_model, request_id)`

`POST /workflows/{id}/revert` — **deleted**.

---

## Prompt Template

**Location:** `src/codemie/templates/agents/workflow_generator/refine_planning.py`

Placed inside the existing `workflow_generator/` subpackage alongside `intent_analysis.py`, `node_generation.py`, etc. The prompt:

- Provides the full existing workflow YAML as context
- Instructs the LLM to reason step-by-step about which parts need to change
- Enforces the same tool-name constraint as the current refine template (only use tool names from the existing config)
- Requests structured output as `RefinePlan`

---

## Testing

| Test file | What it covers |
|---|---|
| `tests/codemie/workflows/workflow_generator/nodes/test_refine_planner.py` | `RefinePlannerNode` unit test — mock LLM; assert correct `INTENT` + `STEP_PLANS` for preserve-all and full-restructure instructions |
| `tests/codemie/workflows/workflow_generator/test_workflow_refiner.py` | `WorkflowRefinerGraph` integration test — mock all LLM calls; assert `state["result"].yaml_config` is valid YAML reflecting the instruction |
| `tests/codemie/service/test_workflow_generator_service.py` | `refine_workflow()` unit test — mock graph `.run()`; assert response on success, `ExtendedHTTPException` on error |
| `tests/codemie/rest_api/routers/test_workflow.py` | Router tests — 200 with `yaml_config`, 403 no permission, 404 missing workflow |

**Removed tests:** any current 12616 tests covering `PromptGeneratorChain`-based refine, revert endpoint, `revert_workflow()`, `change_type`, and revert metrics.

---

## What is not in scope

- Frontend changes
- Persisting the refined YAML automatically (refine remains stateless — frontend applies the returned YAML via the existing PUT endpoint)
- Streaming the refine result
- Revert functionality (removed)
