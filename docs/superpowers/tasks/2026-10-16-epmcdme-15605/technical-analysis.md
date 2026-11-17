# Technical Research

**Task**: workflow icon upload assistant icon storage
**Generated**: 2026-10-16T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

---
Workflows: allow uploading an icon file instead of only providing an icon URL

## Summary
Allow users to upload an icon image for a workflow, in addition to setting an icon URL.

## Description
When creating or editing an assistant, users can upload an icon image. Workflows currently only support an icon_url, so users must host an image elsewhere and rely on that link remaining available.
Let users upload a workflow icon using the same mechanism and limits as assistant icons, while preserving the existing icon URL option and compatibility with workflow YAML configurations.

## Preconditions
- The user is authenticated and can create or edit the relevant workflow.
- The assistant icon upload mechanism and its file type and size limits are available for reuse.

## Scenarios of Use
1. A user creates or edits a workflow and chooses to upload an icon image.
2. The form validates the selected file and shows a preview before saving.
3. The user can replace or remove the uploaded icon, or use the existing URL input as an alternative.
4. On save, the uploaded icon is stored using the assistant icon mechanism and its resulting URL is saved in the workflow's icon_url field.
5. The workflow icon is displayed wherever it currently appears, including the workflow list, workflow page, conversations, and user preferences.
6. Existing workflows configured with an icon_url continue to display their icons without migration.

## Affected Areas
- codemie (backend): workflow icon upload, validation, storage, and persistence.
- codemie-ui (frontend): workflow create/edit form and workflow icon display locations.
- Workflow YAML configuration and existing icon_url handling.
- Workflow edit permissions.

## Acceptance Criteria
1. A user can upload an image as a workflow icon during workflow creation and editing, and sees a preview before saving.
2. Allowed file types and maximum file size match those for assistant icons. Invalid files are rejected with a clear message.
3. After saving, the uploaded icon is displayed wherever the workflow icon currently appears, including the workflow list, workflow page, conversations, and user preferences.
4. A user can replace or remove an uploaded icon.
5. A user can still set a workflow icon using a URL.
6. Existing workflows with an icon_url continue working without migration, and YAML icon_url configuration continues to work as before.
7. Only users who can edit a workflow can change its icon.
8. Assistant icon behavior remains unchanged.
9. Icon cropping and editing tools are out of scope.
---

---

## 2. Codebase Findings

### Existing Implementations
- Assistant icon upload: **not located** in the codegraph index. Queries on "upload icon", "icon router", "icon mime size" returned no assistant icon handler. The only assistant-side field found is `AssistantRequest.icon_url: Optional[str]` (`src/codemie/rest_api/models/assistant.py:326`), a plain string.
- `Assistant.get_by_icon_url` (`src/codemie/rest_api/models/assistant.py:1198`) — `select(cls).where(cls.icon_url == icon_url)`. Lookup by URL string only.
- Workflow request models, both with `icon_url: Optional[str] = None` and no upload field:
  - `CreateWorkflowRequest` (`src/codemie/core/workflow_models/workflow_models.py:425`)
  - `UpdateWorkflowRequest` (`src/codemie/core/workflow_models/workflow_models.py:444`)
- `create_workflow` router (`src/codemie/rest_api/routers/workflow.py:604`) builds `WorkflowConfig(**request.model_dump())`, then `_persist_and_finalize_new_workflow` (`:522`) calls `workflow_service.create_workflow`.
- `update_workflow` router (`src/codemie/rest_api/routers/workflow.py:646`) loads the stored workflow, checks `Ability(user).can(Action.WRITE, workflow)` (`:662`), then calls `workflow_service.update_workflow`.
- `WorkflowService.create_workflow` (`src/codemie/service/workflow_service.py:134`) — `save(refresh=True)`.
- `WorkflowService.update_workflow` (`src/codemie/service/workflow_service.py:181`) → `_update_workflow_values` (`:777`), which copies fields listed in `self._editable_non_boolean_fields` (list definition not in the extracted source).
- `WorkflowConfig` (`src/codemie/core/workflow_models/workflow_config.py:320`, table `workflows`) extends `WorkflowConfigBase`. The `icon_url` column definition on `WorkflowConfigBase` was **not** in the extracted source.
- Storage pattern used elsewhere: `GenerateImageTool._store_b64_image` (`src/codemie_tools/data_management/file_system/generate_image_tool.py:55`) calls `file_repository.write_file(name, mime_type, content, owner)` and returns `sandbox:/v1/files/{stored_file.to_encoded_url()}`. `SkillFileStorage` (`src/codemie/service/skill_file_storage.py:33`) wraps the same repository with an owner prefix.

### Architecture and Layers Affected
- REST router: `src/codemie/rest_api/routers/workflow.py` (create, update, persist helpers).
- Service: `src/codemie/service/workflow_service.py` (create, update, `_update_workflow_values`, `_yaml_content_changed`).
- Model and persistence: `src/codemie/core/workflow_models/workflow_config.py` (`WorkflowConfig`, `parse_execution_config`), `src/codemie/core/workflow_models/workflow_models.py` (request models).
- File storage layer (candidate reuse): `FileRepository` `write_file` / `read_file` / `to_encoded_url`. Class module path not resolved in this pass.

### Integration Points
- Guardrails: `GuardrailService.sync_guardrail_assignments_for_entity` runs inside the create and update save path (router `:553`, `:694`).
- YAML: `_rewrite_yaml_config_from_objects` (router `:141`) re-serializes `yaml_config` on save; `_yaml_content_changed` (service `:75`) decides whether to write YAML history.
- Workflow pool: `WorkflowPool._config_hash` (`src/codemie/service/workflow_pool.py:104`) hashes `yaml_config` only.
- UI consumers (workflow list, workflow page, conversations, user preferences): **not indexed**. `codemie-ui` is not in this codegraph index.

### Patterns and Conventions
- Optional request fields default to `None`; update request requires `name`, `description`, `project`.
- Errors use `ExtendedHTTPException(code, message, details, help)` (`src/codemie/core/exceptions.py:59`).
- Write gate on update: `Ability(user).can(Action.WRITE, ...)` then `raise_access_denied` (router `:662`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
Guides present under `.ai-run/guides/` (found by Glob, not read in this pass): `architecture/service-layer-patterns.md`, `api/rest-api-patterns.md`, `api/endpoint-conventions.md`, `data/database-patterns.md`, `development/security-patterns.md`, `development/configuration-patterns.md`. No guide covering icons or file upload was identified by name.

### Architectural Decisions
None located in the extracted source.

### Derived Conventions
- Workflow `icon_url` is a free-form optional string with no validation in the request models.
- Update permission is enforced in the router, not the service.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_workflow.py` — workflow router (create/update endpoints).
- `tests/codemie/service/test_workflow_service.py` — `update_workflow` service behaviour.
- `tests/codemie/core/workflow_models/test_workflow_models.py` — `WorkflowState`, `WorkflowTool`, `WorkflowAssistant`, `WorkflowPoolConfig` models.
- `tests/codemie/core/workflow_models/test_workflow_config.py` — YAML parse, `start_hint`, retry policy.
- `tests/codemie/service/test_workflow_pool.py` — pool.

### Testing Framework and Patterns
pytest with class-based tests, `@pytest.fixture` for `User` objects (`project_names`, `admin_project_names`), and `patch.object` / `patch(...)` on `Session` and `get_all_by_fields`.

### Coverage Gaps
- No workflow icon test surfaced in any codegraph query. The `icon_url` field is referenced by request models only.
- No assistant icon upload test located (consistent with the handler not being found).

---

## 5. Configuration and Environment

### Environment Variables
No icon-related environment variable surfaced. `config` (`src/codemie/configs/config.py:1132`) is the global settings object; no upload size or type setting was found in the queried symbols.

### Configuration Files
- `src/codemie/configs/config.py` — `Config` class.
- Workflow YAML is validated by `WorkflowExecutor.validate_workflow` against a JSON schema (per router comment at `workflow.py:614`).

### Feature Flags and Deployment Concerns
- `customer_config.is_feature_enabled("subWorkflow")` gates sub-workflow nodes (`src/codemie/workflows/nodes/sub_workflow_node.py:65`). No icon-related flag found.

---

## 6. Risk Indicators

- Assistant icon upload mechanism not located in the index. The ticket requires reusing it, so the reuse target (endpoint, validator, limits, storage call) is unresolved.
- `codemie-ui` is not indexed. All frontend display locations and the create/edit form are unresolved.
- `WorkflowConfigBase.icon_url` definition not confirmed. Whether `icon_url` survives `WorkflowConfig(**request.model_dump())` and `_editable_non_boolean_fields` is unverified (`workflow_service.py:777`).
- YAML `icon_url` handling is unverified: `_rewrite_yaml_config_from_objects` (`workflow.py:141`) rewrites YAML on save.
- No workflow icon tests surfaced (`test_workflow.py`, `test_workflow_service.py`).
- Speculative: a stored uploaded icon likely needs a distinct reference field or a URL write-back. This is a design choice for the spec, not a finding.
- Speculative: any cleanup of replaced or removed uploaded icons would need a new storage delete path. Not present for workflows.
- Update permission is already enforced (`workflow.py:662`), so AC7 maps to existing behaviour once the icon field is covered by the same gate.

---

## 7. Summary for Complexity Assessment

The change touches four backend files confirmed in the index: the workflow router, the workflow service, the `WorkflowConfig` model and the request models in `workflow_models.py`, plus the Postgres `workflows` table behind them. The frontend (`codemie-ui`) is outside the index, so UI surface is unmeasured. Backend `icon_url` is already a string on both request models, so the backend novelty is the upload path itself. Storage precedent exists through `FileRepository.write_file` (used by generated images and skills).

The reuse target is the main unknown: the assistant icon upload mechanism was not found in codegraph, so its validation and limits cannot be cited from source. Test posture is thin for workflows: model, service and router tests exist, none touch the icon. Key risk factors are the unlocated assistant upload handler, the unconfirmed `icon_url` persistence path, and unindexed UI display locations.

---

## 8. External References

None named by the task.
