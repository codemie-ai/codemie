# Export API Implementation Plan (EPMCDME-15313)

**Goal:** `GET /v1/export/{kind}/{id}` returns `{meta, spec}`, byte-stable, no secret value, no id, no server-managed field.
**Conventions:** Commit per task using the repository's existing convention, only with explicit user approval. Cyclomatic complexity <= 15 per function (one small exporter per kind or type, no branching mega-function). Tests: pytest + FastAPI `TestClient` under `tests/codemie/rest_api/routers/` (see `test_skill_router.py`, `test_index.py`, `test_project_settings.py` for the app/auth override pattern). Quality gates and manual verification are owned by the calling flow, not tasks here.
**Layout:** core in new package `src/codemie/service/iac_export/` (confirm no name clash first); router `src/codemie/rest_api/routers/export.py`.

Verified read paths (reuse, do not reimplement access checks):
- assistant: `routers/assistant/catalog.py:255-265` `get_assistant_by_id_or_raise` + `_build_assistant_detail_response`
- workflow: `routers/workflow.py:485-507` `workflow_service.get_workflow` + `Ability(user).can(Action.READ, ...)`
- skill: `SkillService.get_skill_by_id(skill_id, user)` (`routers/skill.py:374-389`); companion files are separate (`SkillService.list_companion_files`)
- category: `routers/category.py:88-96` `get_assistant_category` via `CategoryRepository`
- integration: no GET-by-id route. Use `Settings.get_by_id` + `_check_permission` as in `project_settings.py:207-213` and the user-settings equivalent in `user_settings.py`; credential keys become `env:` references; stored values are never read.
- datasource: `IndexInfo.get_by_id` as in `routers/index/queries.py:197-215`; per-type fields mirror the create/update request models in `routers/index/{code_repositories,kb_atlassian,kb_azure_devops,kb_git_faq,kb_google,kb_sharepoint,kb_xwiki,providers}.py`.
- registration: `main.py` router imports (~line 62) and `include_router` block (~1030).

---

### Task 1 — Serialiser core and document envelope
Files: `iac_export/document.py`, test `tests/codemie/service/iac_export/test_document.py`.
Build `ExportDocument(meta, spec)` and `to_canonical_bytes(doc)`: key order by field declaration/alias, nulls omitted, sorted lists via a caller-supplied natural key, no timestamps. Define the `Exporter` protocol (`export(id, user) -> ExportDocument`) and the `ExportError` type.
Test-first: yes — two serialisations of the same model with shuffled list input are byte-identical, nulls absent, key order stable.

### Task 2 — Credential reference derivation + golden vectors
Files: `iac_export/credential_ref.py`, test + `tests/.../iac_export/env_ref_golden.json`.
`env_ref(identifier, key)` -> `env:{ID}_{KEY}`: upper-case, each non-alphanumeric run becomes one `_`, strip leading/trailing `_`; fixed fallback identifier when no alias. Never accepts a value argument. Vectors are a JSON file so other repos can share them.
Test-first: yes — each golden vector (spaces, dashes, dots, unicode, leading/trailing punctuation, empty alias) yields the expected string.

### Task 3 — Reference resolver and ambiguity check
Files: `iac_export/references.py`, test.
`resolve_name(kind, id, scope)` for assistant, skill and integration ids; raise `ExportError` naming the clashing name when the name is not unique in scope (project for assistant/workflow/integration alias, author+project for skill as stored). One batched lookup per call, not per reference.
Test-first: yes — unique name resolves; duplicate name raises with the name in the message.

### Task 4 — Router skeleton, kind registry, registration
Files: `routers/export.py`, edit `main.py` (import + `app.include_router(export.router)` beside the others), test `tests/codemie/rest_api/routers/test_export_router.py`.
`APIRouter(prefix="/v1", dependencies=[Depends(authenticate)])`; `GET /export/{kind}/{id}` dispatches via a `dict[kind -> Exporter]`; unknown kind -> 404/422 naming the kind; `ExportError` -> `ExtendedHTTPException`; response is the canonical bytes (`application/json`), never partial.
Test-first: yes — unknown kind errors naming it; unknown id returns 404 with no body document; unauthenticated is rejected.

### Task 5 — Category exporter
Files: `iac_export/exporters/category.py`, test. `meta.name` = category name; spec = `CategoryCreateRequest` fields only.
Test-first: yes — golden fixture contains no `id`/timestamps and equals the create-request fields.

### Task 6 — Skill exporter
Files: `iac_export/exporters/skill.py`, test. Content-only spec from the skill update request fields; add the explicit statement that companion files are excluded; reuse `SkillService.get_skill_by_id` for 404/403. Leave `/skills/{id}/export` untouched.
Test-first: yes — golden fixture has content and the exclusion note, no companion files; forbidden user gets 403.

### Task 7 — Integration exporter
Files: `iac_export/exporters/integration.py`, test. `meta.name` = alias. Every credential value becomes `env_ref(alias, key)` (never read from the stored value); `openapi_spec` exported inline (documented exception). Covers project and user scope settings with the existing permission checks.
Test-first: yes — a setting holding a real-looking secret exports `env:` references only and the secret string is absent from the body; `openapi_spec` literal is present.

### Task 8 — Assistant exporter
Files: `iac_export/exporters/assistant.py`, test. `meta.name` = slug. `skill_ids` -> skill names, integration references in tool/toolkit/MCP settings -> aliases (via Task 3); tool/toolkit/MCP credentials, `mcp_connect_auth_token` and sensitive prompt variables -> `env_ref`. Split into small helpers per surface to hold complexity down.
Test-first: yes — fixture assistant with skill, toolkit setting, MCP token and sensitive variable exports names and `env:` refs, with no ids or secrets.

### Task 9 — Workflow exporter and yaml_config inverse converter
Files: `iac_export/exporters/workflow.py`, `iac_export/workflow_converter.py`, test. Convert stored flattened `yaml_config` to a structured definition with `assistant_id` -> assistant name (workflow assistants have no name field, so resolve via Task 3). Re-rendering must equal the stored normalised text.
Test-first: yes — render(export(workflow)) equals stored `yaml_config`; a clashing assistant name fails naming it.

### Task 10 — Datasource dispatch and first exporters (code git/svn, confluence, jira, google)
Files: `iac_export/exporters/datasource/{__init__,base,code,confluence,jira,google}.py`, test. A `DatasourceExporter` protocol plus registry keyed by index type; `knowledge_base_file` and unknown types raise `ExportError` naming the type. Export every field the update endpoint accepts (svn is a destructive full-replace PUT), including `project_name` (the envelope requires it); `setting_id` -> integration alias; inline credentials -> `env_ref`.
Test-first: yes — per-type fixture covers every update-request field; unsupported type error names the type.

### Task 11 — Datasource exporters: xray, azure_devops_wiki, azure_devops_work_item
Files: matching modules under `exporters/datasource/`, test. No converter precedent: derive fields from `kb_atlassian.py` and `kb_azure_devops.py` update models.
Test-first: yes — per-type fixture covers every update field with credentials as `env:` refs.

### Task 12 — Datasource exporters: git_faq, sharepoint, xwiki, provider
Files: matching modules under `exporters/datasource/`, test. sharepoint and provider updates are destructive; provider update is keyed by id, so the document carries the provider name and fields only. Cross-check the type set against the apply engine and record any mismatch as a question to the caller rather than adding or dropping a type silently.
Test-first: yes — per-type fixture covers every update field.

### Task 13 — Cross-cutting: PUT round trip
Files: `tests/codemie/rest_api/routers/test_export_roundtrip.py`. For all six kinds and 11 datasource types: convert the document to the kind's update request (credentials set to the mask), submit it, assert the stored entity is unchanged except timestamps and datasource `error`.
Test-first: yes — a deliberately omitted field makes the round trip fail (guards completeness).

### Task 14 — Cross-cutting: determinism and leak/id scan
Files: `tests/codemie/rest_api/routers/test_export_invariants.py`. Parametrised over every kind and credential-bearing surface: two exports byte-identical; recursive scan finds no stored id, no `created_by`/timestamp/state key, no mask string, no planted secret, and every reference is a name.
Test-first: yes — a planted secret in each surface never reaches the body.

### Task 15 — Cross-cutting: skill companion-file integrity
Files: `tests/codemie/rest_api/routers/test_export_skill_companions.py`. Export a skill with companion files, update it from the document, assert companions unchanged. If the update path drops them, stop and report to the caller instead of patching silently (spec open risk).
Test-first: yes — companion files equal before and after the update.

---
Negative-constraint pass: no change to `/skills/{id}/export` or `/index/{id}/export` (Tasks 6, 10 add new code only); no secret read or mask change (Tasks 2, 7); no `project_name`, ids, or companion files exported (Tasks 6, 10); no `knowledge_base_file` (Task 10); no guardrails, bulk export, SDK or CLI (no task); no shared code with the diff/apply engine (no task).
