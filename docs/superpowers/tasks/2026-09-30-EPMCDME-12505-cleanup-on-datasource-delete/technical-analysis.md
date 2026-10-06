# Technical Research

**Task**: datasource deletion assistant context cleanup
**Generated**: 2026-09-30
**Research path**: filesystem (codegraph MCP not available — `mcp__codegraph__search` returned tool-not-found; research done directly with Grep/Read/Bash instead of Explore threads)

---

## 1. Original Context

EPMCDME-12505 "Assistant becomes broken if attached datasource is deleted" (Major). Option A (tolerant init: build_agent drops deleted datasources in memory, commit 52b80281d) is already on the branch and stays. Now add option B: when a datasource is deleted, detach it from every assistant in the SAME project whose context contains that datasource's (repo_name, context_type). Persist the change. Pre-verified facts: assistants reference datasources by Context(name, context_type) scoped to assistant.project (src/codemie/service/tools/toolkit_service.py:1417, src/codemie/service/assistant_service.py:963); create/update drop datasources outside the target project (src/codemie/rest_api/routers/assistant.py:757, :891). THREE delete paths must trigger the cleanup: src/codemie/rest_api/routers/index.py delete_index (index.delete() and ProviderDatasourceDeletionService branches; GuardrailService.remove_guardrail_assignments_for_entity at :434) and src/codemie/service/aws_bedrock/bedrock_knowledge_base_service.py :329 and :368. Implement once as a service function. Match on name AND context_type (Context.index_info_type). Tests: same-project assistant loses the ds; other-project assistant with a same-named ds untouched; other ds of the assistant stay; each delete path triggers cleanup. Out of scope: user-facing chat notice.

---

## 2. Codebase Findings

### Existing Implementations

**Data model**
- `src/codemie/rest_api/models/assistant.py:107` — `Context(BaseModel)`: `context_type: ContextType` (`knowledge_base` | `code` | `provider`), `name: str`; `__eq__` compares both fields.
- `assistant.py:119-137` — `Context.index_info_type(index)` / `index_info_type_from_index_type(index_type)`: maps `IndexInfo.index_type` to `ContextType` by prefix via `IndexTypeByContextTypeMapping` (`models/index.py:89`); falls back to `KNOWLEDGE_BASE`.
- `assistant.py:670` — `AssistantBase.context: list[Context]`, column `Column(PydanticListType(Context))`. `PydanticListType` (`models/base.py:295`) has `impl = JSONB`, so it is stored as a JSONB array of `{"context_type": ..., "name": ...}` objects.
- `assistant.py:720` — `Index('ix_assistants_context', 'context', postgresql_using="gin")`, a GIN index. Containment (`@>`) queries are indexed.
- `assistant.py:1072` — `Assistant(BaseModelWithSQLSupport, AssistantBase, table=True)`, table `assistants`, has a `project` column.
- `assistant.py:1240` — `AssistantConfiguration` (table `assistant_configurations`) is the version snapshot. It has its own `context: list[Context]` JSONB column (`:1271`) and FK `assistant_id` with ON DELETE CASCADE.

**Existing query helper (reuse it)**
- `assistant.py:1220-1231` — `Assistant.by_datasource_run(datasource: IndexInfo)` already selects exactly the target set:
  ```python
  select(cls).where(and_(
      cls.project == datasource.project_name,
      cls.context.cast(JSONB).contains([{'name': datasource.repo_name, 'context_type': Context.index_info_type(datasource)}]),
  ))
  ```
  It returns `AssistantListResponse` DTOs rather than entities, so it can't be saved as-is. The WHERE clause is the one to reuse. Its only caller is `GET /index/{index_id}/assistants` (`routers/index.py:364-371`), the "used by assistants" endpoint.

**Option A, already on the branch (commit 52b80281d)**
- `src/codemie/service/assistant_service.py:950-968` — `_existing_context_keys(project, names)` → `{(repo_name, ContextType)}` from `index_info`; `drop_missing_context(assistant)` changes the context in memory only. This lookup is useful for the "is there still another datasource with the same key" guard (see Risks).

**Direct precedent for "detach X from all assistants on delete"**
- `src/codemie/repository/skill_repository.py:689-712` — `SkillRepository.remove_skill_from_all_assistants(skill_id)`: one `Session(...)`, `select(Assistant).where(Assistant.skill_ids.contains([skill_id]))`, filters the list in Python, sets `assistant.updated_date = datetime.now(UTC)`, `session.add`, one `session.commit()`, returns a count. Called from `SkillService.delete_skill` (`service/skill_service.py:748`) before the delete. Test: `tests/codemie/service/test_skill_service.py:790-812` patches `Session` and asserts list mutation, `add.call_count` and `commit.assert_called_once()`.
- Note: the skill precedent does **not** create an assistant version, because `skill_ids` is not a versioned field. `context` **is** versioned (see below).

**Guardrail cleanup pattern (the one the ticket asks to mirror)**
- `src/codemie/service/guardrail/guardrail_service.py:463-469` — a thin `@staticmethod` on the service that delegates to `GuardrailRepository().remove_guardrail_assignments_for_entity(...)`. It is called right after every `entity.delete()` with `(GuardrailEntity.KNOWLEDGEBASE, str(entity.id))`. It is stateless and returns nothing.

### All datasource (IndexInfo) delete paths — more than the three in the ticket

| # | Location | Trigger | Guardrail cleanup today | In ticket |
|---|---|---|---|---|
| 1 | `rest_api/routers/index.py:432` `index.delete()` in `delete_index` | `DELETE /index/{id}` (non-provider) | yes, `:434` | yes |
| 2 | `routers/index.py:430` → `ProviderDatasourceDeletionService.run()` (`service/provider/datasource/provider_datasource_deletion_service.py:33-56`) | `DELETE /index/{id}` (provider) | yes, `:434` | yes |
| 3 | `service/aws_bedrock/bedrock_knowledge_base_service.py:324-329` `delete_entities(setting_id)` | `BedrockOrchestratorService.delete_all_entities` ← settings deletion (`routers/user_settings.py:271`, `routers/project_settings.py:218`) | yes | yes (:329) |
| 4 | `bedrock_knowledge_base_service.py:365-368` `validate_remote_entity_exists_and_cleanup` | remote KB gone; via `routers/index.py:2777` `_validate_remote_entities_and_raise` | yes | yes (:368) |
| 5 | `bedrock_knowledge_base_service.py:332-341` `unimport_entity(entity_id, user)` | `DELETE /vendors/{origin}/{entity}/{entity_id}` (`routers/vendor.py:428-441`) | **no** (existing gap) | **no** |
| 6 | `bedrock_knowledge_base_service.py:410-417` in `invoke_knowledge_base` | remote KB returns ResourceNotFound during a query (tool run) | yes | **no** |
| 7 | `src/external/deployment_scripts/preconfigured_assistants.py:101-105` `delete_context` | deployment script, CODEMIE project | no | no |

- Path 2 detail: `ProviderDatasourceDeletionService.run()` always calls `self.datasource.delete()` in `finally`, even when the remote provider delete fails (`_handle_error`). By the time control returns to `delete_index`, the row is gone in both branches, so a single call after the if/else at `:434` covers both.
- **Central hook alternative:** `IndexInfo.delete()` is overridden at `rest_api/models/index.py:1297-1328`. It already cascades cleanup to GitRepo, the ES index and scheduler/webhook integrations, then calls `super().delete()`. Every path 1-7 goes through it. Adding the assistant-detach call there (with a local import, as it already does for `SchedulerSettingsService`/`cleanup_local_repo`) covers all paths, including 5-7, with one line. The ticket instead asks for explicit calls next to `remove_guardrail_assignments_for_entity`; the planner has to choose between them (see Risks).

### Bedrock KB entity shape (paths 3-6)
- It is a plain `IndexInfo` row built in `_build_index_data` (`bedrock_knowledge_base_service.py:566-586`): `project_name = setting.project_name`, `repo_name = f"{setting.id}-{kb_detail['name']}"`, `index_type = "knowledge_base_bedrock"` → `ContextType.KNOWLEDGE_BASE`. Uniqueness is enforced by the partial unique index on `(bedrock_aws_settings_id, bedrock_knowledge_base_id)` (`models/index.py:395-404`), not by name.
- `delete_entities` gets every entity via `IndexInfo.get_by_bedrock_aws_settings_id(setting_id)` and loops over them. The cleanup runs once per entity, which means one assistant UPDATE per entity when an assistant references several KBs from the same setting. That is acceptable, or it could be batched.

### Architecture and Layers Affected
- **API / router**: `rest_api/routers/index.py` `delete_index` (and `routers/vendor.py` if path 5 is covered explicitly).
- **Service**: a new cleanup function, plus `service/aws_bedrock/bedrock_knowledge_base_service.py` call sites.
- **Model / DB-persistence**: `Assistant` (JSONB `context`), possibly `AssistantConfiguration` (versions), and `IndexInfo.delete()` if the central hook is chosen.
- **Repository** (optional): `repository/` holds precedent-style bulk updates (`skill_repository.py`).
- No migration is needed: this is a data update only, and the GIN index already exists.

### Integration Points
- **Import-cycle hazard:** `service/assistant_service.py:53` imports `BedrockOrchestratorService`, which imports `bedrock_knowledge_base_service`. Putting the cleanup on `AssistantService` and importing it at module level from `bedrock_knowledge_base_service.py` creates a circular import. Options: a new small module that imports only models (e.g. `service/assistant/assistant_datasource_cleanup_service.py`), a classmethod/repository function next to `Assistant.by_datasource_run`, or a function-local import. `models/assistant.py` already imports `IndexInfo` and `GuardrailService`. `models/index.py` must not import `models/assistant.py` at module level (assistant imports index), so a hook in `IndexInfo.delete()` needs a local import.
- **Versioning:** `Assistant.update_assistant` → `AssistantVersionService.create_new_version` (`service/assistant/assistant_version_service.py:82-139`) snapshots `context` into `AssistantConfiguration`. `apply_version_to_assistant` (`:344-373`, used by `service/tools/assistant_factory.py:145` for chat-with-version and `routers/assistant.py:2106`) and `rollback_to_version` (`:223-303`) copy `config.context` back. Updating only `Assistant.context` leaves every historical version still referencing the deleted datasource. Option A covers that at runtime (build_agent drops it). The planner must decide whether to (a) touch only the master row (like the skill precedent), (b) also create a new version with a system change note, or (c) rewrite historical `AssistantConfiguration.context`. The last option breaks the documented rule that "configurations are immutable once created" (`assistant.py:1244`).
- **`Assistant.update()`** (`models/base.py:518`) runs `validate_fields()` (slug uniqueness, categories, prompt vars, mcp names, assistant_ids, `assistant.py:923-1033`). Calling it on every affected assistant may raise on unrelated legacy data. A direct `session.add` + `commit` (skill precedent) skips validation.
- **Other datasource references outside the assistant context (not covered by option B):**
  - Workflow virtual assistants reference datasources by **id** in YAML (`core/workflow_models/workflow_models.py:61` `WorkflowAssistant.datasource_ids`), validated in `workflows/validation/resources.py:330`. These will still dangle.
  - `Conversation` `AssistantDetails.context` / `LegacyChatDetails.context` (`models/conversation.py:84,97`) is a display snapshot and harmless.
  - `is_global`/marketplace publishing is a flag on the same `assistants` row, not a copy, so the cleanup covers it automatically.

### Patterns and Conventions
- Stateless `@staticmethod`/`@classmethod` services, e.g. `GuardrailService.remove_guardrail_assignments_for_entity`.
- Raw `Session(Model.get_engine())` + `select(...)` + `session.add` + `session.commit()` for bulk JSONB list edits (`skill_repository.py:702-712`).
- JSONB containment filter: `cls.context.cast(JSONB).contains([{...}])` (`assistant.py:1224`); `skill_ids.contains([id])` for plain JSONB.
- Cleanup side effects in `IndexInfo.delete()` are wrapped in try/except with `logger.warning` so the delete never fails (`models/index.py:1316-1326`). The same best-effort semantics fit here: a failed detach should not fail the datasource delete, and Option A still protects runtime.
- The Assistant master row has both `update_date` (base) and `updated_date` (`assistant.py:658`). The skill precedent sets `updated_date`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` exists: `architecture/{layered-architecture,service-layer-patterns,project-structure}.md`, `data/{database-patterns,repository-patterns,database-optimization}.md`, `testing/{testing-patterns,testing-service-patterns,testing-api-patterns}.md`, `development/{error-handling,logging-patterns}.md`, `project.md`, `quality-gates.md`.
- `testing/testing-service-patterns.md`: mock repositories/providers in service tests; repository tests live separately under `tests/codemie/repository/`.

### Architectural Decisions
- `AssistantConfiguration` docstring (`assistant.py:1241-1246`): "Configurations are immutable once created."
- EPMCDME-14150 comment in `create_new_version` (`assistant_version_service.py:103-107`): honor the partial-update contract; omitted fields are snapshotted from the merged master.
- Commit 52b80281d (Option A): the fail-fast `check_context`/`MissingContextException` was removed and replaced by in-memory drop with a warning log.

### Derived Conventions
- Delete-time cascades live either at the call site (guardrails) or inside the model `delete()` override (ES index, GitRepo, scheduler/webhook). Both conventions exist in the codebase.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_index.py:166-190` `test_index_deletion`: FastAPI `TestClient` on a router-only app, `@patch` on `Ability.can`, `IndexInfo.get_by_id` (returns a MagicMock index), `GuardrailService.remove_guardrail_assignments_for_entity`, `AgentMonitoringService.send_count_metric`. Checks only status and message. No provider-branch delete test was found. `test_index_deletion_not_found` follows. The auth fixture `authenticated_user` overrides `authenticate` (`:47-66`).
- `tests/codemie/service/aws_bedrock/test_bedrock_knowledge_base_service.py`: `test_delete_entities_deletes_all_indexes` (`:747`), `test_validate_remote_entity_exists_and_cleanup_resource_not_found` (`:913`), both patching the guardrail call; `test_unimport_entity_kb_*` (`:1040-1070`); `test_invoke_knowledge_base_*` (`:770-845`), with no test for the ResourceNotFound → delete branch at `:410`.
- `tests/codemie/service/test_assistant_service_drop_missing_context.py` and `test_assistant_service_build_agent_deleted_datasource.py` (Option A): `MagicMock(spec=Assistant)` + patch of `AssistantService._existing_context_keys`.
- `tests/codemie/rest_api/routers/test_assistant_filter_invalid_datasources.py` (Option A router).
- `tests/codemie/service/test_skill_service.py:790-812`: a template for testing a Session-based bulk detach.
- `tests/codemie/rest_api/models/test_index_info.py` exists, with no `delete()` tests found.
- No tests found for `ProviderDatasourceDeletionService` or for `Assistant.by_datasource_run`.

### Testing Framework and Patterns
- pytest 8 + pytest-asyncio + pytest-mock + pytest-xdist (`-n 2`), `--import-mode=importlib`, `pythonpath = src` (`pytest.ini`). `PG_URL` points to a local dummy; no testcontainers were found. **All DB access is mocked.** A real JSONB `@>` query is never run in tests, so the "other-project assistant untouched" criterion can only be proved at the WHERE-clause/statement level (compile the statement, or assert that the filter is built with `project`) or by Python-side filtering on mocked rows.
- Memory note (Oleksii): codemie tests fail under `LANG=uk_UA`; prefix runs with `LC_ALL=en_US.UTF-8`.

### Coverage Gaps
- Provider branch of `delete_index` (path 2).
- `invoke_knowledge_base` ResourceNotFound delete (path 6).
- `unimport_entity` has no guardrail cleanup and no cleanup test (path 5).
- `Assistant.by_datasource_run` query shape (project + containment) is untested.
- There's no DB-level test that would catch a wrong JSONB containment literal (e.g. passing the `ContextType` enum instead of `.value`).

---

## 5. Configuration and Environment

### Environment Variables
- None specific to this feature. Tests use `ENV=local`, `PG_URL=postgresql://pg:pg123@localhost:111/postgres` (unreachable dummy; DB is always mocked).

### Configuration Files
- `pytest.ini`, `pyproject.toml` (test deps). No config knobs for datasource/assistant cleanup.

### Feature Flags and Deployment Concerns
- No feature flag. No migration: JSONB data update only, and the GIN index `ix_assistants_context` already exists. Behavior is irreversible for users: a detached datasource has to be re-attached manually if a same-named datasource is recreated. Today (Option A) a recreated same-name datasource silently "reattaches" at runtime; after option B it will not.

---

## 6. Risk Indicators

- **Delete-path scope mismatch:** the ticket lists 3 paths (1+2 via delete_index, 3, 4), but `IndexInfo` is also deleted at `bedrock_knowledge_base_service.py:339` (`unimport_entity`, user-facing vendor un-import), `:414` (`invoke_knowledge_base`) and `preconfigured_assistants.py:105`. Explicit call sites would leave paths 5-7 dangling. The one-line alternative is a hook in `IndexInfo.delete()` (`models/index.py:1297`). The planner must decide and state it.
- **Same-key collision:** `_index_unique_check` (`routers/index.py:2750`) enforces unique `(project_name, repo_name)` for API-created datasources, but Bedrock KB rows are not name-checked (unique only on settings+kb id), and legacy rows may duplicate. If another `IndexInfo` with the same `(project, repo_name, context_type)` still exists, detaching would break a valid reference. Guard: after delete, re-check with `AssistantService._existing_context_keys(project, {repo_name})` (or an equivalent query) and skip the detach if the key still resolves.
- **Order of operations:** the cleanup must run after a successful delete (the key-still-exists guard only works post-delete). In path 2 the delete always happens in `finally`, so the cleanup should run regardless. In `delete_index` an ES `NotFoundError` from `index.delete()` skips `:434` and returns 404, so the cleanup placed there is skipped too, even though the row may or may not be gone.
- **Versioning ambiguity:** `context` is versioned (`AssistantConfiguration.context`). A master-only update means version history, chat-with-version (`assistant_factory.py:145`) and rollback (`assistant_version_service.py:285,303`) still carry the dead entry. Option A handles runtime, but rollback will re-persist the dead entry into master. Creating a new version needs a `User` (`create_new_version(..., user)`), which system paths (settings deletion, the remote-not-found cleanup) don't naturally have; `validate_remote_entity_exists_and_cleanup` receives no user. This decision is needed before planning.
- **Validation side effects:** `Assistant.update()` runs `validate_fields()` and may raise on unrelated invalid legacy assistants, which would abort a bulk detach mid-way. Prefer a direct session update (skill precedent) and best-effort try/except + `logger.warning` so the datasource delete never fails.
- **Circular import:** placing the function on `AssistantService` and importing it from `bedrock_knowledge_base_service.py` at module level creates a cycle (`assistant_service.py:53` → orchestration → KB service). Needs a standalone module or a local import.
- **JSONB literal correctness:** containment must use the serialized form (`ContextType.KNOWLEDGE_BASE.value` == `"knowledge_base"`). `by_datasource_run` passes the enum object inside a dict to `.contains()`; it works because `ContextType` is a `str` enum, but mocked tests cannot catch a regression here.
- **No DB-backed tests:** all persistence is mocked, so the project-scoping criterion ("other-project same-named ds untouched") is proven only at the filter-construction or Python-filter level. Python-side re-filtering on `assistant.project == project_name` after the query gives a mock-testable guarantee.
- **Existing gap:** `unimport_entity` (`bedrock_knowledge_base_service.py:332-341`) doesn't remove guardrail assignments either. This is adjacent tech debt; flag it, don't fix silently.
- **Out-of-scope dangling refs:** workflow YAML `datasource_ids` (by id) are not cleaned. Worth one line in the plan/MR as known residue.
- **Bulk effect on large projects:** one query plus N row updates per deleted datasource, and per entity in `delete_entities` loops. Fine given the GIN index; there's no pagination concern at realistic scale.

---

## 7. Summary for Complexity Assessment

The change touches three layers: a new stateless cleanup function (service or repository level), its call sites in the API router (`routers/index.py` `delete_index`) and in the Bedrock KB service (`bedrock_knowledge_base_service.py` `delete_entities`, `validate_remote_entity_exists_and_cleanup`, and possibly `unimport_entity`/`invoke_knowledge_base`), and the `Assistant` JSONB `context` column. The query already exists almost verbatim in `Assistant.by_datasource_run` (project filter + JSONB containment, GIN-indexed), and a direct precedent for bulk detaching from assistants exists in `SkillRepository.remove_skill_from_all_assistants`. The expected surface is about 3-4 production files (new module or model method, `index.py`, `bedrock_knowledge_base_service.py`, optionally `vendor.py`/`models/index.py`) and 3-4 test files (new unit test for the cleanup, `test_index.py`, `test_bedrock_knowledge_base_service.py`). No migration or config is involved.

Technical novelty is low: established patterns cover the query, the bulk update and the call-site placement. The complexity is in the decisions, not the code. There are four: (1) which delete paths to cover (the ticket names 3; the code has 6-7 `IndexInfo.delete()` sites, and a single hook in `IndexInfo.delete()` would cover all of them); (2) whether to version the change (context is a versioned field; the skill precedent is not versioned; system paths have no user); (3) the post-delete "same key still exists" guard, since Bedrock KB rows aren't name-unique; (4) avoiding the `assistant_service` ↔ bedrock import cycle.

Test posture is mixed. Unit tests with patched `Session`/`MagicMock` are the norm and there is no real Postgres in tests, so project scoping has to be proven by asserting the constructed filter or a Python-side project check. Router delete coverage is thin (the happy path only asserts the message; the provider branch is untested), `invoke_knowledge_base`'s delete branch is untested, and the Option A tests provide ready fixtures. Recommended score: low-to-moderate implementation complexity with moderate decision risk, which should be resolved at the spec gate.
