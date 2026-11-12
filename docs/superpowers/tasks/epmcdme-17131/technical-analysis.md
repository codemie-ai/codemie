# Technical Research

**Task**: workflow diagram schema_url
**Generated**: 2026-11-11
**Research path**: codegraph

---

## 1. Original Context

Review https://jiraeu.epam.com/browse/EPMCDME-17131 and implement the fix. Details are available docs/superpowers/tasks/epmcdme-17131/implementation-brief.md, you can check it at the investigation stage first.

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: after a workflow save, draw its diagram and keep a link to it on the workflow row.
- `src/codemie/rest_api/routers/workflow.py:904` `update_workflow_schema(workflow_config, user)` - runs `WorkflowExecutor.validate_workflow_and_draw` (Mermaid, external call); on any exception logs and passes `None`; then `workflow_service.save_workflow_schema(workflow_config, schema)`. Queued with `background_tasks.add_task(run_in_thread_pool, update_workflow_schema, <in-memory config>, user)` at `:559` (create), `:698` (update), `:1051` (rollback). `run_in_thread_pool` (`routers/utils.py:154`) submits to a shared `ThreadPoolExecutor` (`utils.py:43`, `THREAD_POOL_MAX_WORKERS`) and returns the future; no ordering between jobs.
- `src/codemie/service/workflow_service.py:802-817` `save_workflow_schema`: if schema bytes non-empty -> `FileRepositoryFactory().get_current_repository().write_file(name=f"workflows/{id}.svg", owner=config.CODEMIE_STORAGE_BUCKET_NAME, mime_type=MermaidMimeType.SVG)`, sets `workflow_config.schema_url = result.to_encoded_url()`, then `workflow_config.update()` (`:815`). Whole body in `try/except Exception` that logs `Failed to save workflow schema` and swallows. Empty/None schema: nothing happens.
- `src/codemie/rest_api/models/base.py:532` `BaseModelWithSQLSupport.update(refresh, validate, touch_timestamp=True)`: sets `update_date = now()`, `validate_fields()`, then in a session `session.get(type(self), id)` (raises `StaleDataError` if gone) and `session.merge(self)` - writes every attribute of the in-memory copy. `touch_timestamp=False` parameter already exists (used at `workflow_service.py:495`).
- `src/codemie/core/workflow_models/workflow_config.py:328` `WorkflowConfig.update(refresh, validate, yaml_config_history)`: `find_by_id` (full row), re-reads only `yaml_config_history` from it, then `super().update`. Table `workflows`; `schema_url: Optional[str]` (`:99`, `AutoString`, nullable, alembic `8f7d26d3ff1c_create_workflows.py:47`); `yaml_config: Optional[str]` (`:103`).
- Callers of `WorkflowConfig.update()` (whole row): `workflow_service.py:815` (schema job), `:949` and `:960` (`_update_workflow_values`), `workflow/workflow_version_service.py:117` (rollback). Grep over `src` for other workflow-row `.update(` found none.
- Targeted-write precedent: `src/codemie/repository/workflow_config_repository.py` - class `WorkflowConfigRepository`; `set_publish_state` (`:28`) raw `text("UPDATE workflows SET ... WHERE id = :wf_id")` inside `WorkflowConfig.get_engine().begin()`, raises `NotFoundException` on `rowcount == 0`; `recompute_unique_users_count` (`:47`) same style, same raise. Consumed only by `service/workflow_config/workflow_marketplace_service.py` (module-level `_workflow_config_repository = WorkflowConfigRepository()` at `:33`; calls at `:40`, `:111`, `:119`). `workflow_service.py` does not import this repository today (it imports `WorkflowMarketplaceService`, `:57`, and builds `_marketplace_service` at `:62`).
- Diagram without persistence: `POST /workflows/diagram` (`workflow.py:810` `create_workflow_diagram`) calls `validate_workflow_and_draw` and returns base64; does not touch `schema_url`.
- Consumers of `schema_url`: `workflow_config_index_service.py:163` (list response), `WorkflowConfigListResponse`/response model field (`workflow_config.py:463`), openapi baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json`. Only writer in `src`: `workflow_service.py:814`.

Other touched code: `workflow_service.py:916-960` `_update_workflow_values` (history prepend at `:948-958`; restores `old_yaml` at `:944-946` when `_yaml_content_changed` is False); `workflow_version_service.py:96-117` (rollback sets `workflow.yaml_config = target.yaml_config`, `workflow.states = []`, `parse_execution_config()`, then `update(yaml_config_history=[...])`); `workflow_service.py:216-223` `create_workflow` (`workflow_config.save(refresh=True)`).

### Decisions Already in the Code
- Background draw gets the save-time in-memory object, not an id (`workflow.py:559/698/1051`); alternative: pass id and reload. Reason: none found.
- Diagram persistence failures are swallowed (`workflow_service.py:816`; router `:907-910` comment: "The diagram is optional: a rendering failure must never block saving the workflow"). Convention to keep.
- Convention for single-column writes to `workflows`: repository class with raw `text()` UPDATE on `WorkflowConfig.get_engine().begin()` (`workflow_config_repository.py:28,47`); services hold a module-level repository instance and tests patch that name (`tests/unit/service/workflow_config/test_workflow_marketplace_service.py:315`, `_CONFIG_REPO`).
- Both existing repository methods raise `NotFoundException` on zero rows (`:44-45`, `:61-62`); the brief wants the new one not to (zero rows = unchanged URL).
- Data direction: `schema_url` is derived from `FileObject.to_encoded_url()` of fixed name `workflows/{id}.svg` + bucket + mime type, so the value is stable per workflow (stored as written by `:814`). The brief needs the stored `yaml_config` read separately from `schema_url`; `yaml_config` is nullable (`:103`), so "row missing" vs "column NULL" is not distinguishable from a bare scalar read.
- Tests mock rather than hit Postgres in this area (memory note + no real-DB workflow repository test exists; `tests/codemie/repository/test_workflow_config_repository.py` has only a stale `.pyc`, no source).

### Task Statements Checked Against the Code
- "`save_workflow_schema` is at `workflow_service.py:800`, `update()` at `:813`" - line numbers drifted: now `:802` and `:815`; history code at `:944-958` (brief says `:942-955`; `:947`/`:958` now `:949`/`:960`).
- "Create/update/rollback queue the job at `workflow.py:559/698/1051`" - holds.
- "Jobs run in a thread pool, can finish out of order" - holds (`utils.py:43,154`).
- "`WorkflowConfig.update()` re-reads only `yaml_config_history`, then merge writes every column" - holds (`workflow_config.py:328-341`, `base.py:540-546`).
- "Only callers of `WorkflowConfig.update()` are schema job, save, rollback" - holds for `src` (grep above).
- "Rollback sets `workflow.yaml_config = target.yaml_config` before `update()`" - holds (`workflow_version_service.py:97,117`).
- "Update: `_update_workflow_values` restores `old_yaml` when YAML did not really change" - holds (`workflow_service.py:944-946`).
- "Create: saved as is" - holds at the service (`workflow_service.py:223`); request-to-config rewriting before it was not traced here.
- "`set_publish_state` at `:28`, `recompute_unique_users_count` at `:47`" - hold.
- "`FileObject.to_encoded_url` encodes mime type, bucket and `workflows/{id}.svg`" - unverified (`FileObject` is in the external `codemie_tools` package; only the call site `:814` and the write args `:806-811` are in this repo).
- "Bug existed since initial commit `afc61fa125`" - unverified (git history not examined).
- "Router tests patch `save_workflow_schema` entirely" - holds with one exception: `tests/codemie/rest_api/routers/test_workflow.py:1307-1313` calls `update_workflow_schema` directly with `workflow_service` patched and asserts `save_workflow_schema.assert_called_once_with(workflow_config, None)`; it does not reach the new logic.
- Existing mechanism kept in place by the brief: the whole-row `WorkflowConfig.update()` stays for the save (`:949/:960`) and rollback (`workflow_version_service.py:117`) paths; consumers are those two callers plus tests in `test_workflow_service.py` and `test_workflow_versions.py`/`test_workflow_version_service.py`.

### Consumers of Changed Contracts
- `WorkflowService.save_workflow_schema(workflow_config, workflow_schema)` signature/behaviour: callers only `routers/workflow.py:911` (`update_workflow_schema`, itself called at `:559`, `:698`, `:1051`). Tests: `test_workflow_service.py:1331,1343,1508,1547`; router tests patch it (`test_workflow.py` ~30 sites, `test_idempotent_write.py:371`, `test_contract_baseline.py:524`) and `test_workflow.py:1313`.
- `workflow_config.schema_url` in-memory assignment (`:814`): after the job, the passed-in object (same object returned to the caller at update; `stored_config` is returned by `update_workflow`, `workflow_service.py:268`) carries the new URL; the HTTP responses at create/update are built before the job runs, so none read it afterwards in the code seen.
- `WorkflowConfigRepository` is shared with the marketplace service; adding a method does not change `set_publish_state`/`recompute_unique_users_count`.
- `update_date`: the old job bumped it through `update()` (`base.py:534`); readers of `update_date`: list response (`workflow_config_index_service.py:165`), sort/filters not enumerated.

### Architecture and Layers Affected
Router (`rest_api/routers/workflow.py`: job entry), Service (`service/workflow_service.py`: `save_workflow_schema`), Repository (`repository/workflow_config_repository.py`), Model/persistence (`core/workflow_models/workflow_config.py`, `rest_api/models/base.py`), File storage (`repository/repository_factory.py` -> GCP/Azure/AWS/FileSystem repos), external Mermaid renderer (`service/file_service/mermaid_service.py`, `workflows/workflow.py:300`).

### Integration Points
Mermaid renderer over HTTP (`MERMAID_SERVER_URL` / mermaid.ink); object storage via `FileRepositoryFactory`; PostgreSQL `workflows` table; marketplace service writes `is_global`/`categories`/`unique_users_count` on the same row concurrently.

### Patterns and Conventions
Layering per `.ai-run/guides/architecture/layered-architecture.md` and `data/repository-patterns.md`; module-level repository instances in services; `logger` from `codemie.configs`; no code comments (user preference); mock-based unit tests with `patch("codemie.service.workflow_service.<Name>")`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/data/repository-patterns.md`, `data/database-patterns.md` (flags raw SQL string interpolation; existing repository uses bound parameters), `testing/testing-service-patterns.md`, `testing/testing-patterns.md`, `architecture/service-layer-patterns.md`. Only the brief documents this bug (`docs/superpowers/tasks/epmcdme-17131/implementation-brief.md`).

### Architectural Decisions
None recorded for the diagram job or `schema_url`; the only comment is the router's "diagram is optional" note.

### Derived Conventions
See "Decisions Already in the Code".

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/test_workflow_service.py`: `test_save_workflow_schema_persists_via_update` (`:1331`, asserts `update.assert_called_once_with()`); `test_update_then_save_schema_preserves_history` (`:1474`, `mock_update.assert_called_once_with()` at `:1511` after save_schema); `test_update_then_save_schema_prepends_history_then_persists_schema` (`:1516`, expects `update.call_count == 2`, second call args/kwargs empty, `:1551-1555`). Fixtures: `workflow_config` (`:74`, `yaml_config=""`), `workflow_service` (`:123`), helper `_stub_update` (`:134`) replacing the instance's `update` with a MagicMock.
- Router: `tests/codemie/rest_api/routers/test_workflow.py:1307` (`update_workflow_schema` swallows Mermaid errors), rollback in `tests/codemie/rest_api/routers/test_workflow_versions.py`, `tests/codemie/service/test_workflow_version_service.py`.
- Marketplace repository usage: `tests/unit/service/workflow_config/test_workflow_marketplace_service.py` (patches `_workflow_config_repository`), `tests/codemie/rest_api/routers/test_workflow_marketplace_router.py`, `test_workflow_executions_marketplace.py`.

### Testing Framework and Patterns
pytest, `unittest.mock` (MagicMock/patch), FastAPI TestClient in router tests; `object.__setattr__` to stub pydantic/SQLModel methods.

### Coverage Gaps
- No tests for `WorkflowConfigRepository` itself (no test file with source; its methods are only tested through mocks in the marketplace tests).
- No test covers a late/stale diagram job, a deleted workflow mid-job, or `save_workflow_schema` with empty schema.

---

## 5. Configuration and Environment

### Environment Variables
`CODEMIE_STORAGE_BUCKET_NAME` (owner arg at `workflow_service.py:809`), `MERMAID_SERVER_URL`, `MERMAID_USE_MERMAID_INC`, `MERMAID_SERVER_TIMEOUT` (`mermaid_service.py:69,116`), `THREAD_POOL_MAX_WORKERS` (`routers/utils.py:43`), file storage type selection in `repository_factory.py`.

### Configuration Files
None specific. Alembic migration `src/external/alembic/versions/8f7d26d3ff1c_create_workflows.py` defines `schema_url` nullable string.

### Feature Flags and Deployment Concerns
None found for this feature. Verification guides: `.ai-run/guides/testing/local-verification.md`, `quality-gates.md`.

---

## 6. Risk Indicators

- The brief's guard (stored `yaml_config` vs job's copy) rests on the in-memory `yaml_config` equalling the stored text on all three paths; verified at service level for update and rollback, create only at `workflow_service.py:223`. `yaml_config` is nullable (`workflow_config.py:103`); the fixture uses `""`; None vs `""` round-trip through Postgres is not checked here.
- Kept mechanism: whole-row `WorkflowConfig.update()` / `session.merge` remains for save and rollback (`workflow_service.py:949,960`; `workflow_version_service.py:117`); see Task Statements section.
- Speculative: the new repository methods sit on a table written concurrently by the marketplace repository; both are single-column raw UPDATEs, but no test exercises real SQL (repository has none; area uses mocks).
- Speculative: `WorkflowConfigRepository` is not yet imported by `workflow_service.py`; import cycle risk is low (`workflow_service.py` already imports `WorkflowMarketplaceService`, which imports the repository) but unchecked.
- Three existing tests assert the removed `update()` call and must change (`test_workflow_service.py:1331,1474,1516`); router tests stub the whole method.
- Line numbers in the brief drifted by about 2 lines in `workflow_service.py`; use the numbers in this document.
- Remaining check-to-write window and overlapping PUT requests losing history entries are acknowledged out of scope in the brief (`workflow_service.py:922-960` and `workflow_version_service.py:96-117` read-modify-write whole row without locks).
- Impact beyond `yaml_config` (marketplace `is_global`/`categories`, `unique_users_count`) is only documented in the brief; confirmed that those columns are written by the repository at `:28`/`:47` and by the whole-row merge.

---

## 7. Summary for Complexity Assessment

The change sits behind one service method, `WorkflowService.save_workflow_schema` (`workflow_service.py:802`), which is the only place that writes `schema_url` and is reached by three router call sites through `update_workflow_schema`. Layers touched: service, repository (an existing class, `WorkflowConfigRepository`, with two raw-SQL single-column UPDATE methods as the in-repo precedent), and tests. No router, model, migration or config change is indicated by the code found. The persistence behavior being replaced is the generic `BaseModelWithSQLSupport.update()` merge (`base.py:532`), which stays in place for the save and rollback paths.

Technical novelty is low: the pattern (repository method with `text()` UPDATE and a module-level repository instance patched in tests) already exists and is used by the marketplace service. Concurrency is the subject (late thread-pool job vs newer save), but the code has no locks or versions on this table and the area is tested only with mocks. Test posture: the three existing tests that pin `update()` in `test_workflow_service.py` must be rewritten; router tests patch the method and are unaffected; `WorkflowConfigRepository` has no direct tests.

Key risk factors: equality of in-memory and stored `yaml_config` on the create path and None-vs-empty handling; distinguishing a missing row from a NULL `yaml_config`; the external `FileObject.to_encoded_url` behavior the brief relies on being unverified in this repo; and the unchanged whole-row writers that continue to share the row.

---

## 8. External References

Resolved: `/Users/bohdan_maliar/Projects/codemie-dev/codemie/docs/superpowers/tasks/epmcdme-17131/implementation-brief.md` (read in full). Facts the task needs, close to verbatim:
- Root cause: `save_workflow_schema` calls `workflow_config.update()` on a save-time copy; `session.merge` writes every column over the current row, so a late diagram job reverts `yaml_config` (and `assistants`, `states`, `custom_nodes`, `tools`, `name`, `description`, `icon_url`, `mode`, `project`, `supervisor_prompt`, `meta_config`, `start_hint`, `categories`, `shared`, `updated_by`, `update_date`, `is_global`, `unique_users_count`); `yaml_config_history` is safe.
- Fix 1: new method next to `set_publish_state`, raw `text()` UPDATE on `WorkflowConfig.get_engine().begin()`: `UPDATE workflows SET schema_url = :schema_url WHERE id = :wf_id AND schema_url IS DISTINCT FROM :schema_url`; do not raise on `rowcount == 0`; do not touch `update_date`.
- Fix 2: before writing the image, read only the stored `yaml_config`; if it differs from `workflow_config.yaml_config`, or row gone, log (debug/info) and return without writing file or column; compare content not `update_date`; read must distinguish missing row from NULL column (sentinel, tuple, or select `id, yaml_config`); keep existing `try/except` that logs and swallows; skip entirely when `workflow_schema` is falsy (no DB read).
- Suggested shape: `stored_yaml = _workflow_config_repository.get_yaml_config(id)`; `_workflow_config_repository.set_schema_url(id, url)` (names are suggestions).
- Tests: unit tests with mocks, no real-Postgres tests; rewrite the three tests listed in Section 4; new cases: stale job writes nothing and calls neither `set_schema_url`, `WorkflowConfig.update` nor `merge`; current job writes file and calls `set_schema_url` with the encoded URL; deleted mid-job writes nothing and raises nothing; no diagram -> no DB read/no write; `save_workflow_schema` never calls `WorkflowConfig.update()`; optional mocked sequence test for history order (acceptance criterion 2). Repository-method tests in the style of `tests/unit/service/workflow_config/test_workflow_marketplace_service.py`.
- Out of scope: overlapping PUT/rollback requests losing a history entry (no lock); ticket comment that impact covers all columns including marketplace unpublish.
- Ticket URL https://jiraeu.epam.com/browse/EPMCDME-17131: not opened (outside repository, no access); acceptance criteria numbering (1-4) is known only through the brief.
