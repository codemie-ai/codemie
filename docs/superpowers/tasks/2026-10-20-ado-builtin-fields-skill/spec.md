# Spec: ADO Built-in Work-Item Fields Skill (EPMCDME-13989)

## Problem

Assistants using the Azure DevOps work-item toolkit must supply exact ADO REST
field-reference names (e.g. `System.Title`, `System.AssignedTo`) when calling
`CreateWorkItemTool`/`UpdateWorkItemTool`, whose `work_item_json.fields` keys are
sent verbatim to ADO's JSON Patch API
(`src/codemie_tools/azure_devops/work_item/tools.py`). No structured reference of
those names exists today; only scattered inline examples
(`System.Title`, `System.AssignedTo`, `System.State`) appear in
`models.py`/`tools_vars.py`. A hardcoded-system-prompt approach to this same
requirement was considered and rejected by the PO. This spec ships the
reference as a **Skill** instead.

## Approach

Add one new preconfigured Skill template, following the existing pattern of the
7 templates under `config/templates/skill/` (e.g.
`solution-architect.yaml`), seeded at startup by
`manage_preconfigured_skills()` (`src/external/deployment_scripts/preconfigured_skills.py`).

- **Name**: `azure-devops-work-item-fields` (kebab-case, satisfies
  `^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$`).
- **Content**: a single markdown table, two columns minimum — Display Name and
  REST reference name (e.g. `Title` / `System.Title`) — covering the built-in
  work-item fields documented at
  https://learn.microsoft.com/en-us/azure/devops/boards/work-items/guidance/work-item-field?view=azure-devops.
  The implementer must fetch and transcribe that page's field table directly
  (a later stage has web-fetch access); it must **not** fabricate or
  reconstruct field names from memory. A short
  usage note at the top of the content should tell the LLM when to consult the
  table (constructing/updating `fields` for ADO work-item create/update calls).
- **Visibility/categories**: `PUBLIC`, owned by `SYSTEM_USER`, same as the 7
  existing templates; categories drawn from the existing `SkillCategory` enum
  (e.g. `DEVELOPMENT`, `PROJECT_MANAGEMENT` — no ADO-specific category exists
  and none is added).
- **Toolkit coupling** (resolved open question): populate the template's
  `toolkits` field with the Azure DevOps Work Item toolkit's `ToolKitDetails`
  (matching `AzureDevOpsWorkItemToolkitUI`'s shape,
  `src/codemie_tools/azure_devops/work_item/toolkit.py`). This reuses the
  existing skill→toolkit merge, `ToolkitService._merge_skill_toolkits`
  (`src/codemie/service/tools/toolkit_service.py:409-440`), unchanged — no new
  code. Effect: attaching this skill to an assistant automatically ensures the
  ADO Work Item toolkit is present. The reverse direction — enabling the ADO
  Work Item toolkit implying the skill is required — has no enforcement
  mechanism anywhere in the codebase and stays a documentation/operational
  convention (stated in the skill's own `description`): admins who manually
  enable the toolkit without attaching this skill get no automatic nudge
  beyond the existing generic `_skills_suffix` prompt text
  (`src/codemie/service/assistant_service.py:81-86`, unmodified by this task).
- **Delivery mechanism**: unchanged. The skill's `content` is returned on
  demand by `SkillTool.execute(skill_name)`
  (`src/codemie/agents/tools/skill/skill_tool.py`) only when attached via
  `assistant.skill_ids`; no prompt-injection path is added.

This is the only new template-worthy change; no code in
`src/codemie_tools/azure_devops/work_item/` or `assistant_service.py` is
modified.

## Acceptance Criteria

- `config/templates/skill/azure-devops-work-item-fields.yaml` exists, matches
  the shape of the 7 existing templates, and loads without error through
  `manage_preconfigured_skills()` / `load_skill_templates()`.
- Its `content` is a markdown table of built-in ADO work-item fields (display
  name + REST reference name per row), transcribed from the cited Microsoft
  Learn page.
- Its `toolkits` field contains the Azure DevOps Work Item toolkit's
  `ToolKitDetails`, verified by a test asserting `ToolkitService._merge_skill_toolkits`
  adds `ToolSet.AZURE_DEVOPS_WORK_ITEM` when this skill is attached (precedent:
  `tests/codemie/service/tools/test_toolkit_service_skill_tool_wiring.py`).
- `SkillTool.execute("azure-devops-work-item-fields")` returns the table
  content wrapped as today (precedent:
  `tests/codemie/agents/tools/skill/test_skill_tool.py`).
- A template-loading test exists for this specific template (precedent:
  `tests/external/deployment_scripts/test_preconfigured_skills.py`), since no
  existing test exercises a *named* template end-to-end.
- No changes to `src/codemie_tools/azure_devops/work_item/` or to
  `assistant_service.py`'s prompt assembly.

## Non-goals

- Not a live "fetch ADO fields" tool call against Azure DevOps.
- Not a system-prompt injection — the approach the PO rejected.
- Not an enforcement of the toolkit→skill direction (toolkit enabled implies
  skill required); that direction remains a documented convention only.
- Not scoped to custom, org-specific, or process-template-specific fields
  beyond what the official built-in-fields reference documents.
- No change to `CreateWorkItemTool`/`UpdateWorkItemTool` schemas or behavior.
- No new feature flag for this skill; it ships unconditionally/PUBLIC like the
  7 existing preconfigured templates.

## Open Risks

- The field-table content itself is not yet fetched/verified in any prior
  stage (no web access was available during research or spec-writing); the
  implementer must pull it directly from the cited Microsoft Learn page.
- Preconfigured skills ship PUBLIC to every deployment unconditionally at
  startup (pre-existing platform behavior) — this skill becomes globally
  visible immediately upon merge.
