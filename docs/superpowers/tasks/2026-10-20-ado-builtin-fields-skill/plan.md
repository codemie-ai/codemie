# ADO Built-in Work-Item Fields Skill (EPMCDME-13989) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the full built-in Azure DevOps work-item field reference (display name → REST field name) as a new preconfigured Skill, paired with the existing ADO Work Item toolkit via `Skill.toolkits`, instead of pasting it into any system prompt.

**Architecture:** One new YAML template under `config/templates/skill/`, seeded at startup by the existing `manage_preconfigured_skills()` pipeline. That pipeline currently drops any `toolkits` key silently, so `create_or_update_skill()` gets a small, test-first extension to parse and persist it as `ToolKitDetails`. No other runtime code changes — `ToolkitService._merge_skill_toolkits` and `SkillTool.execute()` already do the rest unmodified.

**Tech Stack:** Python, SQLModel/Pydantic, pytest, YAML.

**Spec:** `docs/superpowers/tasks/2026-10-20-ado-builtin-fields-skill/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- `content` is a markdown table transcribed directly from `https://learn.microsoft.com/en-us/azure/devops/boards/work-items/guidance/work-item-field?view=azure-devops` — never fabricated or reconstructed from memory.
- `name: azure-devops-work-item-fields` (kebab-case), `project: codemie`, `visibility: public`, `categories` from the existing `SkillCategory` enum only (e.g. `development`, `project_management`) — no new category.
- No code changes in `src/codemie_tools/azure_devops/work_item/` or in `src/codemie/service/assistant_service.py`'s prompt assembly (`_skills_suffix`, `_prepare_system_prompt`, `_prepare_workflow_system_prompt`) — the table is delivered only through `SkillTool`, never injected into a system prompt.
- No live "fetch ADO fields" tool; no code enforcement of "toolkit enabled implies skill required" (stays documentation-only, via the skill's own `description`); no new feature flag — ships PUBLIC unconditionally like the 7 existing templates.

## Review Focus

- The 7 existing templates have no `toolkits` key — `create_or_update_skill()` must keep defaulting them to `[]`, no regression.
- A malformed `toolkits` entry must log and fall back to `[]` for that one skill, not raise and abort every other template's load in the same startup pass.
- The **update** path (skill already exists) must carry `toolkits` through too, not just create — today's `updates` dict only has description/content/visibility/categories.
- The authored table must be the full reference, not a small partial list — a row-count floor test catches an accidental shrink.
- `SkillTool.execute("azure-devops-work-item-fields")` must return the *real* transcribed content end-to-end (YAML → DB → runtime), not just prove the mechanism works in the abstract.

---

### Task 1: Thread `toolkits` through the preconfigured-skill loader

**Files:**
- Modify: `src/external/deployment_scripts/preconfigured_skills.py:178-246` (`create_or_update_skill`)
- Test: `tests/external/deployment_scripts/test_preconfigured_skills.py` (extend `TestCreateOrUpdateSkill`)

**Interfaces:**
- Consumes: `codemie.rest_api.models.assistant.ToolKitDetails` (existing: `toolkit: str`, `tools: list[ToolDetails]`, `label: str = ""`).
- Produces: `create_or_update_skill(template: dict) -> str | None` now also sets `skill_data["toolkits"]` (create path) and `updates["toolkits"]` (update path) to `list[ToolKitDetails]`, parsed via a new `_parse_template_toolkits(template)` helper. Later tasks' tests depend on this key existing on both dicts.

**Test-first: yes — `create_or_update_skill` currently drops a template's `toolkits` key on both the create and update paths, and would raise on a malformed entry instead of falling back to `[]`.**

- [ ] **Step 1: Write the failing tests.** Add three tests to `TestCreateOrUpdateSkill`, following that class's existing `@patch('external.deployment_scripts.preconfigured_skills.SkillRepository....')` decorator style (copy `test_create_new_skill`'s shape):
  1. `test_create_passes_toolkits_through` — template has `'toolkits': [{"toolkit": "Azure DevOps Work Item", "tools": [], "label": "Azure DevOps Work Item"}]`; assert `mock_create.call_args[0][0]['toolkits'][0].toolkit == ToolSet.AZURE_DEVOPS_WORK_ITEM` (import `ToolSet` from `codemie_tools.base.models`, `ToolKitDetails` from `codemie.rest_api.models.assistant`).
  2. `test_update_passes_toolkits_through` — same template, but `mock_get_by_name` returns an existing skill so the update branch runs; assert `mock_update.call_args[0][1]['toolkits'][0].toolkit == ToolSet.AZURE_DEVOPS_WORK_ITEM`.
  3. `test_a_malformed_toolkit_entry_falls_back_to_empty_instead_of_raising` — template has `'toolkits': [{'not_a_valid_field': True}]`; assert the call still returns the skill id and `mock_create.call_args[0][0]['toolkits'] == []`.

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `poetry run pytest tests/external/deployment_scripts/test_preconfigured_skills.py -k toolkit -v`
Expected: FAIL — `KeyError`/`AssertionError`, since `toolkits` isn't in either dict yet.

- [ ] **Step 3: Implement the minimal change.** Import `ToolKitDetails` at the top of `src/external/deployment_scripts/preconfigured_skills.py`, add this helper, and add `"toolkits": _parse_template_toolkits(template)` to both the `updates` dict (update branch, `:205-210`) and the `skill_data` dict (create branch, `:225-234`):

```python
def _parse_template_toolkits(template: dict) -> list[ToolKitDetails]:
    """Parse a template's `toolkits` key into validated ToolKitDetails, never raising.

    A malformed entry must not abort the whole preconfigured-skills startup pass — it
    falls back to an empty list for this one skill and logs the problem instead.
    """
    try:
        return [ToolKitDetails(**tk) for tk in template.get('toolkits', [])]
    except Exception as e:
        logger.error(f"Invalid 'toolkits' entry for skill '{template.get('name')}': {e}")
        return []
```

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `poetry run pytest tests/external/deployment_scripts/test_preconfigured_skills.py -v`
Expected: PASS, including every pre-existing test in the file (no regression for templates without a `toolkits` key).

---

### Task 2: Author the ADO built-in-fields skill template

**Files:**
- Create: `config/templates/skill/azure-devops-work-item-fields.yaml`
- Test: `tests/external/deployment_scripts/test_preconfigured_skills.py` (new `TestAdoWorkItemFieldsTemplate` class)

**Interfaces:**
- Consumes: Task 1's toolkits-aware `create_or_update_skill(template: dict)`; the YAML shape of the 7 existing templates (`name`/`description`/`content`/`project`/`visibility`/`categories`), plus a `toolkits` key shaped like `AzureDevOpsWorkItemToolkitUI` (`src/codemie_tools/azure_devops/work_item/toolkit.py`).
- Produces: the template file, consumed unmodified by `load_skill_templates()`/`manage_preconfigured_skills()` at startup — no other task touches this file.

**Test-first: yes — the template file doesn't exist yet (`FileNotFoundError`); once created, a version with no `toolkits` entry or only a small partial field table must still fail.**

- [ ] **Step 1: Fetch the authoritative field list.** Fetch `https://learn.microsoft.com/en-us/azure/devops/boards/work-items/guidance/work-item-field?view=azure-devops` directly and transcribe its field table (Display Name, REST reference name) row by row. Do **not** reconstruct or fabricate the list from memory — every row must come from the fetched page.

- [ ] **Step 2: Write the failing tests.** Add a `TestAdoWorkItemFieldsTemplate` class to `tests/external/deployment_scripts/test_preconfigured_skills.py` that loads `Path(__file__).resolve().parents[3] / "config/templates/skill/azure-devops-work-item-fields.yaml"` via `yaml.safe_load` and asserts:
  1. `template["name"] == "azure-devops-work-item-fields"` and `ToolKitDetails(**template["toolkits"][0]).toolkit == ToolSet.AZURE_DEVOPS_WORK_ITEM`.
  2. `template["content"].count("\n|") - 1 > 20` (markdown table rows exceed a small-partial-list floor; the `- 1` discounts the header separator row).
  3. Passing the loaded template straight into `create_or_update_skill` (with `SkillRepository.get_by_name_author_project` and `.create` patched, same style as Task 1) results in `mock_create.call_args[0][0]['toolkits'][0].toolkit == ToolSet.AZURE_DEVOPS_WORK_ITEM`.

- [ ] **Step 3: Run the tests to verify they fail.**

Run: `poetry run pytest tests/external/deployment_scripts/test_preconfigured_skills.py -k Ado -v`
Expected: FAIL — `FileNotFoundError`, since the template doesn't exist yet.

- [ ] **Step 4: Write the template.**

```yaml
name: azure-devops-work-item-fields
description: >-
  Reference table of built-in Azure DevOps work-item field REST names (e.g.
  System.Title, System.AssignedTo) for constructing or updating the `fields`
  object passed to Azure DevOps work-item create/update calls. Attaching this
  skill automatically adds the Azure DevOps Work Item toolkit; enabling that
  toolkit on its own does not attach this skill, so pair them explicitly when
  configuring an assistant. Use when creating or updating an ADO work item, or
  when the exact REST reference name of a field is needed.
content: |-
  ## Usage
  Consult this table whenever building or updating the `fields` object for
  `CreateWorkItemTool`/`UpdateWorkItemTool`. Field keys sent to Azure DevOps
  must match the "REST reference name" column exactly.

  | Display Name | REST Reference Name |
  |---|---|
  <!-- replace with every row transcribed from the Microsoft Learn page in Step 1 -->
project: codemie
visibility: public
categories:
  - development
  - project_management
toolkits:
  - toolkit: "Azure DevOps Work Item"
    tools: []
    label: "Azure DevOps Work Item"
    settings_config: true
```

The placeholder comment must not remain in the final file — replace it with the real rows from Step 1.

- [ ] **Step 5: Run the tests to verify they pass.**

Run: `poetry run pytest tests/external/deployment_scripts/test_preconfigured_skills.py -v`
Expected: PASS.

---

### Task 3: Prove the attached skill's toolkit reaches the assistant

**Files:**
- Test: `tests/codemie/service/tools/test_toolkit_service.py` (extend `TestMergeSkillToolkits`)

**Interfaces:**
- Consumes: `ToolkitService._merge_skill_toolkits(assistant)` (unmodified, `src/codemie/service/tools/toolkit_service.py:409-440`); `ToolKitDetails`/`ToolSet.AZURE_DEVOPS_WORK_ITEM` (same as Tasks 1–2).
- Produces: nothing for later tasks — this is the runtime-wiring proof the spec's acceptance criteria calls for.

**Test-first: yes — a real `ToolKitDetails(toolkit=ToolSet.AZURE_DEVOPS_WORK_ITEM, ...)` attached via `skill_ids` not showing up in `_merge_skill_toolkits`'s result.**

No production code changes — `_merge_skill_toolkits` already unions by `.toolkit` name. Using a real `ToolKitDetails`/`ToolSet` pair (not a bare `Mock`, unlike this class's other tests) means the test actually fails if the enum value or the merge logic ever drifts.

- [ ] **Step 1: Write the test.** Add `test_merges_the_real_ado_work_item_toolkit_from_an_attached_skill` to `TestMergeSkillToolkits`, reusing its existing `_make_assistant`/`_make_skill` helpers (`skill_ids=["skill-1"]`, assistant `toolkits=[]`) and the class's existing `patch("codemie.service.skills.skill_contributions.SkillRepository")` pattern to return a skill whose `toolkits=[ToolKitDetails(toolkit=ToolSet.AZURE_DEVOPS_WORK_ITEM, tools=[], label="Azure DevOps Work Item")]`. Assert `any(tk.toolkit == ToolSet.AZURE_DEVOPS_WORK_ITEM for tk in ToolkitService._merge_skill_toolkits(assistant))`.

- [ ] **Step 2: Run the test to verify it passes.**

Run: `poetry run pytest tests/codemie/service/tools/test_toolkit_service.py -k real_ado_work_item -v`
Expected: PASS — the merge mechanism is generic and already handles this; the test pins the specific toolkit identity this feature depends on.

---

### Task 4: Prove `SkillTool` delivers the real field table by name

**Files:**
- Test: `tests/codemie/agents/tools/skill/test_skill_tool.py`

**Interfaces:**
- Consumes: `SkillTool.execute(skill_name)` (unmodified, `src/codemie/agents/tools/skill/skill_tool.py`); the real template file written in Task 2.
- Produces: nothing for later tasks — closes the spec's last acceptance criterion (content delivery end-to-end).

**Test-first: yes — `SkillTool.execute("azure-devops-work-item-fields")` not returning `"System.Title"` from the real, on-disk template content.**

No production code changes. Loading the actual YAML file from Task 2 means a passing test proves the real transcribed content (not a stand-in) reaches `SkillTool`'s output. `System.Title` is asserted because it is the one field guaranteed present in any correct transcription of the reference (every ADO work item has a title).

- [ ] **Step 1: Write the test.** Add `test_the_ado_fields_skill_returns_its_real_field_table`, following this file's existing `_execute` helper pattern (build a `SkillTool` via `patch.object(SkillTool, "_load_available_skills")`, set `tool.available_skills = [{"id": "ado-1", "name": "azure-devops-work-item-fields", "description": "d"}]`, patch `SkillRepository.get_by_id` to return `SimpleNamespace(id="ado-1", name="azure-devops-work-item-fields", content=<content loaded from `Path(__file__).resolve().parents[5] / "config/templates/skill/azure-devops-work-item-fields.yaml"` via `yaml.safe_load(...)["content"]`>)`, and patch `SkillTool._send_metric` and `skill_location_hint` as the existing tests do). Call `tool.execute("azure-devops-work-item-fields")` and assert `"System.Title" in output`.

- [ ] **Step 2: Run the test to verify it passes.**

Run: `poetry run pytest tests/codemie/agents/tools/skill/test_skill_tool.py -k real_field_table -v`
Expected: PASS. If it fails on `AssertionError`, the authored table in Task 2 is missing the Title row and Task 2's content must be corrected, not this test loosened.
