# Technical Research

**Task**: ado azure-devops tools skill fields
**Generated**: 2026-10-20T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Implement EPMCDME-13989: When ADO (Azure DevOps) Tools are enabled, provide the LLM with a simple table listing all built-in ADO work-item field names. Decision from PO discussion: adopt the first option — ship this field-name table as a Skill (previously it was pasted into the System prompt). Note: this means that to work with ADO, both the built-in ADO tools AND the external skill need to be provided together. Full reference list of built-in fields: https://learn.microsoft.com/en-us/azure/devops/boards/work-items/guidance/work-item-field?view=azure-devops

---

## 2. Codebase Findings

### Existing Implementations

**Azure DevOps work-item toolkit** (the tools this skill must pair with):
- `src/codemie_tools/azure_devops/work_item/toolkit.py` — `AzureDevOpsWorkItemToolkitUI` (`ToolKit` subclass, `toolkit: ToolSet = ToolSet.AZURE_DEVOPS_WORK_ITEM`) lists 11 tools (search, create, update, get, link, get-relation-types, remove-relation, move, get-comments, create-comment, get-attachment-content); `AzureDevOpsWorkItemToolkit(DiscoverableToolkit)` exposes `get_definition()`.
- `src/codemie_tools/azure_devops/work_item/tools.py` — tool implementations; `CreateWorkItemTool`/`UpdateWorkItemTool` accept a raw `work_item_json` whose `fields` dict keys are ADO REST attribute names (e.g. `System.Title`, `System.AssignedTo`, `System.State`) sent verbatim to ADO's JSON Patch API.
- `src/codemie_tools/azure_devops/work_item/models.py` and `tools_vars.py` — only inline examples of field names (`System.Title`, `System.AssignedTo`, `System.State`); no structured reference table exists in current tool schemas/descriptions.
- `src/codemie/datasource/loader/azure_devops_work_item_loader.py` — independently hardcodes a handful of field constants for indexing (`_FIELD_TITLE = "System.Title"`, `System.AssignedTo`, `Microsoft.VSTS.Common.Priority`) — a separate, unrelated consumer of the same field names.

**Skills system** (the delivery mechanism the PO decided on):
- `src/codemie/rest_api/models/skill.py` — `Skill`/`SkillBase` SQLModel: `content` (markdown), `visibility` (PRIVATE/PROJECT/PUBLIC), `categories`, `toolkits: list[ToolKitDetails]` ("Required tools for this skill to execute correctly"), `mcp_servers`, `companion_files`, `enabled_builtin_subagents`. `SkillCreateRequest`/`SkillUpdateRequest` mirror these fields with validators (kebab-case name, content length via `get_skill_max_content_length()`, max 3 categories).
- `src/codemie/repository/skill_repository.py` — `SkillRepository` CRUD (`get_by_name_author_project`, `create`, `update`, `get_by_ids`, …).
- `src/codemie/service/skill_service.py` — `SkillService`: attach/detach skill to assistant (`assistant.skill_ids`), `get_skills_by_ids` (ordered by attachment), companion-file upload/retrieval, content-length enforcement.
- `src/codemie/service/skills/skill_contributions.py` — `SkillContributionsResolver.resolve_skills_for_assistant()` fetches the `Skill` objects referenced by `assistant.skill_ids` (tolerant of mocked/partial objects).
- `src/codemie/service/tools/toolkit_service.py:409-440` — `ToolkitService._merge_skill_toolkits(assistant)`: for every skill attached via `assistant.skill_ids`, unions `skill.toolkits` into the assistant's effective toolkit list (de-duplicated by `toolkit` name). **Direction is skill → toolkit** (an attached skill can add a toolkit the assistant didn't explicitly select); there is no discovered toolkit → skill direction (enabling a toolkit does not auto-attach or require a skill).
- `src/codemie/agents/tools/skill/skill_tool.py` — `SkillTool`/`SkillCompanionFileTool` (`CodeMieTool`s), created only `if assistant.skill_ids` (`create_skill_tool_if_needed`). `SkillTool.execute(skill_name)` loads a `Skill` by id from `available_skills` (built from `assistant.skill_ids` via `SkillService.get_skills_by_ids`) and returns its `content` wrapped in `<skill_content name="...">`. Tool description is dynamically rendered with the list of attached skills' `name`/`description`.
- `src/codemie/service/assistant_service.py:81-86,439-460,747-770` — `AssistantService._skills_suffix` is appended to both `_prepare_system_prompt` (chat) and `_prepare_workflow_system_prompt` (workflow) **only when `assistant.skill_ids` is truthy**: `"...Use the \`skill\` tool to load a required skill by name whenever you need domain-specific guidance..."`. This is the only mechanism today that tells the LLM skills exist; it is keyed off `skill_ids`, not off any toolkit.
- `src/external/deployment_scripts/preconfigured_skills.py` — `manage_preconfigured_skills()` runs at startup, loads YAML (or bundle-dir) templates from `config.SKILL_TEMPLATES_DIR` (`config/templates/skill/`), and `create_or_update_skill()` upserts each as a `PUBLIC` marketplace `Skill` owned by `SYSTEM_USER`. Template dict keys map 1:1 to `Skill` fields: `name`, `description`, `content`, `project` (defaults to `CODEMIE_PROJECT_NAME`), `visibility`, `categories`, optional `companion_files` (via `references/`/`assets/` subdirs for bundle-style templates with `skill.yaml`/`skill.yml`).
- `config/templates/skill/*.yaml` — 7 existing preconfigured skill templates (`technical-documentation-writer.yaml`, `meeting-summary.yaml`, `linkedin-ai-content.yaml`, `solution-architect.yaml`, `codemie-speech-presentation-content.yaml`, `diagram-generator.yaml`, `interview-assessment.yaml`). None currently declare a `toolkits:` key (none of today's preconfigured skills require a specific built-in toolkit).
- `src/external/alembic/versions/c3f7a1b2d9e4_add_toolkits_to_skills.py` — migration adding the `toolkits` JSONB column (nullable=False, default `[]`) to `skills`; confirms `toolkits` is a real, migrated column, not just a request-model artifact.

### Architecture and Layers Affected
- **Tool/toolkit layer** (`src/codemie_tools/azure_devops/work_item/`) — existing ADO work-item toolkit; this task does not need new tools, only a skill that documents it.
- **Skill content/config layer** (`config/templates/skill/`, `src/external/deployment_scripts/preconfigured_skills.py`) — where a new built-in/preconfigured skill would be authored and seeded, following the existing 7-template pattern.
- **Service layer** (`src/codemie/service/skill_service.py`, `src/codemie/service/tools/toolkit_service.py`, `src/codemie/service/skills/skill_contributions.py`) — governs how an attached skill's declared `toolkits` and `content` reach an assistant/agent at runtime.
- **Agent tool layer** (`src/codemie/agents/tools/skill/skill_tool.py`) — the runtime mechanism (`SkillTool`) that actually delivers skill `content` (the field table) into the LLM's context when invoked.
- **Prompt assembly** (`src/codemie/service/assistant_service.py`) — where the generic `_skills_suffix` nudge is injected; no ADO-specific prompt code exists on the current branch.

### Integration Points
- Internal: Skill → `ToolKitDetails`/`ToolSet.AZURE_DEVOPS_WORK_ITEM` (via `Skill.toolkits`, merged by `ToolkitService._merge_skill_toolkits`); Skill → `SkillRepository`/Postgres (`skills` table); Skill → `SkillTool` (runtime loading); preconfigured skill → `SKILL_TEMPLATES_DIR` loader at startup.
- External: the ticket's source-of-truth is the public Microsoft Learn page `https://learn.microsoft.com/en-us/azure/devops/boards/work-items/guidance/work-item-field?view=azure-devops` (not fetchable from this sandboxed environment — see Section 8). No runtime HTTP call to Azure DevOps is implicated; the azure-devops-python-api client used by the toolkit (`src/codemie_tools/azure_devops/work_item/tools.py`) is unrelated to this task except as the consumer of whatever field names the LLM supplies.

### Patterns and Conventions
- **Preconfigured/marketplace skill as YAML template**: drop a `<name>.yaml` (or a `<name>/skill.yaml` bundle dir with `references/`/`assets/`) into `config/templates/skill/`; `manage_preconfigured_skills()` upserts it as a `PUBLIC` skill owned by `SYSTEM_USER` on every startup, matched by `(name, SYSTEM_USER.id, project)`.
- **Skill declares required toolkit(s) via `toolkits: list[ToolKitDetails]`**, consumed by `ToolkitService._merge_skill_toolkits` — this is the only in-repo precedent for coupling a skill to a specific built-in toolkit (`ToolSet.AZURE_DEVOPS_WORK_ITEM` in this case), but the coupling only flows skill→toolkit (attaching the skill can add the toolkit); there is no discovered mechanism that flows toolkit→skill (enabling the ADO toolkit does not look up or require any skill).
- **Skill discovery/loading is on-demand via the `skill` tool**, not prompt injection of content — `SkillTool` description lists available (attached) skills by name/description; the LLM calls the tool to fetch full `content`. This mirrors the general "Skills as a Tool" architecture documented in `skills_architecture.md` (root of repo; written for a different, Claude-Code-style skills design, not literally this codebase's implementation, but conceptually consistent with what exists here).
- **Attachment is explicit and per-assistant** (`assistant.skill_ids`, `SkillAttachRequest`), not automatic — no code path auto-attaches a skill when a toolkit is selected during assistant creation/edit (checked `skill_ids` usages across `skill_service.py`, `external_entities_collector.py`, `workflow_service.py`, `skill_workspace_preparation.py`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/agents/agent-tools.md` — tool ownership convention: reusable integrations under `src/codemie_tools/`, agent runtime adapters under `src/codemie/agents/tools/`. `SkillTool` correctly lives under the latter.
- `.ai-run/guides/agents/custom-tool-creation.md`, `.ai-run/guides/agents/tool-overview.md` — toolkit layering guides (not read in depth; relevant if new tool code were needed, which this task does not appear to require).
- `.ai-run/guides/integration/external-services.md` — integration boundary guide for external service calls (relevant only if the skill content itself were to be fetched live from ADO, which the PO decision explicitly ruled out).
- No guide file specifically documents the Skills system's `toolkits` field or the preconfigured-skill-template mechanism; those conventions were derived from code (`skill.py`, `preconfigured_skills.py`, the 7 existing templates).

### Architectural Decisions
- **A different approach was considered and rejected by the PO**: hardcoding a small field-name table directly into the chat/workflow system prompts (appended whenever the ADO Work Item toolkit is enabled), rather than delivering it as a Skill. Historically, ADO field knowledge was supplied ad hoc by customer-authored external skills outside this repository, which was inconsistent across models. **The current ticket's PO decision explicitly supersedes the system-prompt-injection approach**: ship the table *as a Skill* instead, and ensure the tools and the skill are provided together. Do not reintroduce prompt-injection of field names as part of this task.
- `src/external/alembic/versions/c3f7a1b2d9e4_add_toolkits_to_skills.py` (commit metadata: "Create Date: 2026-02-19") established the `toolkits` column on `skills` for an earlier, unrelated purpose (skill declaring its own tool requirements) — this is the closest existing structural link between "a skill" and "a toolkit," though its directionality (skill→toolkit, requirement metadata) differs from what the ticket describes (toolkit enabled → provide skill).

### Derived Conventions
- Preconfigured skill templates are plain YAML with `name`, `description`, `content` (markdown), optional `project`, `visibility` (defaults `public` on create), `categories` (validated against `SkillCategory` enum, max 3) — see any file under `config/templates/skill/` for the exact shape.
- Skill `name` must be kebab-case (`^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$`), `description` 10–1000 chars, `content` ≥100 chars and ≤ configurable max (default 30000, `SKILL_MAX_CONTENT_LENGTH` dynamic config key).
- `categories` values must come from `SkillCategory` (e.g. `DEVELOPMENT`, `PROJECT_MANAGEMENT`, `DOCUMENTATION` — no ADO-specific category exists; the closest fits are `PROJECT_MANAGEMENT`/`DEVELOPMENT`/`DOCUMENTATION`).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie_tools/azure_devops/` — ADO work-item/wiki/test-plan toolkit unit tests (tool-level, not prompt/skill related).
- `tests/external/deployment_scripts/test_preconfigured_skills.py` — covers `create_or_update_skill`, `load_skill_templates`, `manage_preconfigured_skills` with mocked `Skill`/`SkillRepository`; this is the direct precedent for testing a new preconfigured-skill template end to end (create vs. update path, `SYSTEM_USER` ownership, visibility).
- `tests/codemie/service/test_skill_service.py`, `tests/codemie/service/tools/test_toolkit_service_skill_tool_wiring.py`, `tests/codemie/agents/tools/skill/test_skill_tool.py` — cover skill attach/detach, toolkit-merge wiring, and `SkillTool` execution respectively; relevant if the new skill needs `toolkits` populated with `ToolSet.AZURE_DEVOPS_WORK_ITEM`.
- `tests/codemie/repository/test_skill_repository.py`, `tests/codemie/rest_api/routers/test_skill_router.py`, `tests/codemie/rest_api/models/test_skill_response_format.py`, `test_skill_content_limit.py` — Skill model/API-level tests.
- No test currently references Azure DevOps work-item field names outside the toolkit's own inline examples (`System.Title`, `System.AssignedTo`, `System.State` in `tools.py`/`tools_vars.py`/loader tests).

### Testing Framework and Patterns
- pytest (per `.ai-run/guides/testing/testing-patterns.md`, not deeply read this pass); existing skill/ADO tests use `unittest.mock.MagicMock`/`patch` with `spec=` on SQLModel classes (e.g. `mock_skill = MagicMock(spec=Skill)` in `test_preconfigured_skills.py`).

### Coverage Gaps
- No existing test verifies that a specific preconfigured skill template seeds correctly by name/content (each existing test is generic/templated, not template-specific).
- No test asserts any relationship between the ADO work-item toolkit being enabled and a specific skill being present, attached, or recommended — because no such code path exists yet.
- No test validates the full built-in ADO work-item field list against the Microsoft Learn reference the current ticket points to.

---

## 5. Configuration and Environment

### Environment Variables
- No feature flag or env var specific to "ADO fields" or "ADO skill" was found. `customer_config.is_feature_enabled(...)` (used elsewhere in `assistant_service.py` for unrelated features like `interactiveElements`) is the general feature-flag mechanism in this codebase, if one were needed.

### Configuration Files
- `src/codemie/configs/config.py:114` — `SKILL_TEMPLATES_DIR: Path = Path(__file__).absolute().parents[3] / "config/templates/skill"`, the directory `manage_preconfigured_skills()` scans at startup.
- `config/templates/skill/*.yaml` — the 7 existing preconfigured skill template files (see Section 2).

### Feature Flags and Deployment Concerns
- None found specific to this feature area. Preconfigured skills are loaded unconditionally at startup for every deployment (`manage_preconfigured_skills()`), so a new built-in ADO skill template would ship to all environments by default unless explicitly gated.

---

## 6. Risk Indicators

- A hardcoded-system-prompt approach to this same requirement was considered and explicitly rejected by the PO (per `task_context`) in favor of shipping the field table as a Skill; care is needed not to reintroduce prompt-injection of field names.
- The ticket asks for the **full** built-in ADO work-item field reference from the Microsoft Learn page. The authoritative source page could not be fetched from this environment (see Section 8) — the field list itself is not yet verified in this research pass.
- The task's framing — "to work with ADO, both the built-in ADO tools AND the external skill need to be provided together" — describes a coordination requirement for which no automatic enforcement mechanism was found in the codebase. `Skill.toolkits` only flows skill→toolkit (attaching a skill can pull in a toolkit); there is no discovered toolkit→skill direction (enabling `ToolSet.AZURE_DEVOPS_WORK_ITEM` does not surface or require any skill). Speculative: how "provided together" should be enforced or merely documented is a design decision for the spec/plan stage, not something resolved by existing code.
- `Skill.toolkits` is request/DB-modeled as `list[ToolKitDetails]` (the full toolkit shape with tools, label, settings, etc.), not a lightweight `ToolSet` reference — populating it correctly for a new preconfigured skill (if that field is used) requires matching `AzureDevOpsWorkItemToolkitUI`'s shape; none of the 7 existing preconfigured-skill YAML templates currently populate `toolkits`, so there is no existing YAML-template precedent for serializing a `ToolKitDetails` value inside a skill template file.
- Preconfigured skills seed as `PUBLIC`/marketplace by default (`visibility` defaults to `"public"` in `create_or_update_skill`) and apply to every deployment unconditionally at startup — no feature flag currently gates this, so a new built-in skill becomes globally visible unless the implementation adds one.
- No test currently exercises a specific named preconfigured-skill template end-to-end (all existing `test_preconfigured_skills.py` tests use synthetic templates), so there is no close test precedent to copy verbatim for a new ADO-specific template.

---

## 7. Summary for Complexity Assessment

This task touches the Skills content/config layer (`config/templates/skill/`, `src/external/deployment_scripts/preconfigured_skills.py`) and, depending on design, the Skill↔toolkit coupling in `src/codemie/service/tools/toolkit_service.py` and the `Skill.toolkits` field (`src/codemie/rest_api/models/skill.py`). The existing Azure DevOps work-item toolkit (`src/codemie_tools/azure_devops/work_item/`) needs no code changes — the task is about documenting its field names, not altering tool behavior. The core deliverable is new static content (a field-name reference table) delivered through the existing, unmodified Skill→`SkillTool` runtime path, which is a mature, well-covered mechanism (attach, load, companion files, monitoring).

Complexity drivers: (1) the ticket's authoritative field list (the full Microsoft Learn built-in-fields reference) is not currently present in the repo and was not independently fetchable from this sandboxed environment, so its content must be sourced and verified separately; (2) the "tools and skill must be provided together" requirement has no existing automatic-coupling mechanism in the toolkit→skill direction (only skill→toolkit exists via `Skill.toolkits`), so the spec stage must decide whether this is enforced in code or left as an operational/documentation convention; (3) no existing preconfigured-skill YAML template populates the `toolkits` field, so there's no copy-paste precedent for that part of the shape if it's used.

Test coverage is otherwise strong and reusable: `test_preconfigured_skills.py` is a direct template for template-loading tests, and `test_skill_tool.py`/`test_toolkit_service_skill_tool_wiring.py` cover the runtime delivery path this task reuses unmodified.

---

## 8. External References

- **`https://learn.microsoft.com/en-us/azure/devops/boards/work-items/guidance/work-item-field?view=azure-devops`** (named in `task_context` as "Full reference list of built-in fields"): **unresolved**. This is an external web URL; the available tools in this research pass are filesystem/Bash-only (no web-fetch capability), and the task scope is filesystem research. The full built-in Azure DevOps work-item field catalog could not be retrieved or verified in this pass. Whoever authors the skill content downstream must fetch this page directly (or use a tool capable of web access) rather than fabricating or reconstructing the list from memory.
