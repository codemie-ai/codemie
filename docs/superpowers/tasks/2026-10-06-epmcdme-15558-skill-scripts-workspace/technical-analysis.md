# Technical Research

**Task**: skill workspace seeder script-execution (EPMCDME-15558, Story 2 of epic EPMCDME-15364 "Programmatic Tool Composition")
**Generated**: 2026-10-06
**Research path**: filesystem (codegraph MCP tool not available in this environment)

---

## 1. Original Context

Jira EPMCDME-15558 "Scripts Shipped in Skills Are Available in the Workspace" (Story 2 of epic EPMCDME-15364 "Programmatic Tool Composition").

Story: As a skill author, I want the scripts in my skill bundle to be available in the workspace of every conversation that uses the skill, so that any assistant with the skill can run them without the user uploading anything.

Context:
- A skill is a bundle: main instructions (SKILL.md) plus supporting files, uploaded as a zip and stored with the skill. Assistants attach skills; a workflow's assistants can attach skills too. Today the model can only read a skill's supporting files, one at a time, into its context.
- Skill files are not available in the workspace, so a script placed in a skill bundle cannot be run. Scripts in the workspace do run today, through the existing "run script from workspace" capability.
- Files reach a workspace either by reference to a stored file or as the workspace's own copy; an edited file becomes the conversation's own copy, the original is never changed.
- Pre-loaded files are copied into the workspace once, at the assistant's first request in a conversation or run (snapshot semantics; later skill updates not seen by already-open sessions).
- SKILL.md itself is NOT copied — it's given to the model at assistant-build time, not from the workspace.
- The workspace capability is built per assistant, at the moment its tools are assembled (AgentWorkspaceToolkit.get_tools), for chats and for workflow assistants alike — ONE hook only (no second hook at workflow start).
- A script finds its own files via its own location (Path(__file__).parent), so layout is unaffected.

Decisions (already made, from architecture spec D3, D4, D4a, D7, D24 and ticket's own Decisions section):
- Copy, not reference: skill update replaces stored files in place, so bytes must be copied into a workspace-owned blob, read through SkillService (handles both storage forms: separate file records and inline JSONB content).
- Every bundle file is copied (except SKILL.md), subject to size ceiling (10 MB) and file-count ceiling (100 files) per skill — settings, configurable.
- Folder layout: skills/<skill-name>/... — the model passes the skill name to SkillTool, so it must know the path. Two attached skills with the same name: the first wins (SkillTool's own resolution order), the other is skipped with a logged warning. Must use the SAME deterministic order as SkillTool (today SkillRepository.get_by_ids uses IN with no ORDER BY — this needs a shared, deterministic ordering function, likely order of skill_ids attachment).
- Recorded in a seed ledger on the workspace: (source_kind, source_id, source_version, folder). A recorded source is never copied again (so deleted files are not restored). Decide in this story whether it's a new table or a JSON column on the workspace.
- Insert-if-absent write: the seeder must NOT use the existing ordinary workspace file write path (which does an upsert that would restore a soft-deleted file via include_deleted=True + clearing deleted_at). It needs a new "create only if path has no row" operation on the workspace file write path.
- Gated: only runs when the assistant has the script-execution tool AND the environment-wide switch (features:workspaceScriptBridge, resolve_tool_calling_settings from story 1) is on AND jobs sandbox mode is active.
- Serialized per workspace (row lock) — concurrent requests / parallel workflow nodes building the toolkit at the same time must not double-copy or half-write.
- The seeder owns the "skills/" prefix; writes to ".codemie_bridge/" or a root "codemie_runtime_sdk.py" must be refused as workspace paths (reserved paths).
- One workspace shared by several workflow nodes/assistants: first skill seeded under a folder name keeps it; a later skill with the same name but different id is skipped with logged warning (accepted limitation for name clashes across nodes).
- Over-ceiling bundles: upload-time warning in skill editor (separate sub-task, codemie-ui, out of scope for backend sub-task but the backend bundle-preview response needs a field reporting the affected file count — check skill upload/preview endpoint).

---

## 2. Codebase Findings

### Existing Implementations

**Workspace toolkit assembly (the single hook):**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie_tools/data_management/workspace/toolkit.py` — `AgentWorkspaceToolkit.get_tools()`: instantiates one `AgentWorkspaceService()`, resolves/creates the workspace via `create_workspace(...)`, then builds and returns the tool list (`ListWorkspaceFilesTool`, `ReadWorkspaceFileTool`, `WriteWorkspaceFileTool`, `EditWorkspaceFileTool`, `DeleteWorkspaceFileTool`, `GrepWorkspaceFilesTool`, `ExecuteWorkspaceScriptTool`, `GenerateWorkspaceImageToolV2`, `InspectWorkspaceImageTool`), each wired with `conversation_id`, `user`, `workspace_service`, `workspace_id`. This is where a skill-seeding step would be inserted (seed before/while resolving `resolved_workspace_id`, then let the tool list proceed as today).
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/tools/toolkit_settings_service.py` (`ToolkitSettingService.get_agent_workspace_toolkit`, ~line 234-254) — guards on `request.conversation_id`, calls `AgentWorkspaceToolkit.get_toolkit(...).get_tools()`.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/tools/toolkit_service.py` (`ToolkitService.get_toolkit_methods()`, ~line 162-215) — the dict mapping `AGENT_WORKSPACE_TOOLKIT` to a lambda calling `ToolkitSettingService.get_agent_workspace_toolkit(...)`. **Confirmed single hook**: consumed by both the chat path (`tool_execution_service.py:229`) and the workflow assistant node path (`/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/workflows/nodes/tool_node.py:203`). Any change here propagates to both call sites automatically — matches the ticket's "ONE hook only" statement.

**Workspace file write/upsert and soft-delete semantics:**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/rest_api/models/agent_workspace.py` — `AgentWorkspaceFile` / `AgentWorkspace` models; `deleted_at`, `version`, `checksum`, `blob_owner`, `blob_name`, `mime_type`, `size`, `path`, `workspace_id`.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/repository/agent_workspace_repository.py`:
  - `get_file(workspace_id, path, include_deleted=False)` — filters `deleted_at IS NULL` unless `include_deleted=True`.
  - `list_files(workspace_id, include_deleted=False)`.
  - `find_by_blob(...)` — no deleted_at filter by design (comment references "D-4").
  - `save_file(workspace_file)` — plain insert-or-update via `session.add/commit/refresh`.
  - `soft_delete_file(workspace_id, path)` — sets `deleted_at`.
  - **No `get_or_create`/insert-if-absent method exists today** — this is the gap the seeder needs filled.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/agent_workspace_service.py`:
  - `_upsert_workspace_file_content(workspace_id, file_path, content)` (line ~540) and `_upsert_workspace_file_reference(workspace_id, file_path, file_object)` (line ~578): the canonical **upsert** pattern — normalize path, write blob, compute checksum/mime, `get_file(..., include_deleted=True)`, if found update in place (increment `version`, **clear `deleted_at`** — i.e. revive soft-deleted file), else insert new row, then `save_file`. **This revive-on-upsert behavior is exactly what the ticket says the seeder must NOT trigger** — confirms the need for a new "create only if path has no row" repository operation that does not look at soft-deleted rows and does not revive them.
  - `execute_workspace_script(...)` (line ~503-525) — actual script execution entry point via `WorkspaceScriptRunner`, fed by `get_workspace_input_files(workspace_id, user)` (line ~461-501) which reads from `self.repository.list_files(...)`. Confirms: once seeded via a workspace file row, seeded skill files will automatically surface to script execution with no further wiring needed.
  - Imports and calls `resolve_tool_calling_timeout()` from `workspace_script_bridge.py` (line 41, used ~line 512-517) to wire the tool-calling timeout into `WorkspaceScriptRunner`.

**Skill bundle read paths (dual storage forms):**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/rest_api/models/skill.py` — `Skill.companion_files: list[dict]` (JSONB column). Each record is either:
  - current/new form: `{path, mime_type, encoding, size_bytes, storage_key}` (content fetched on demand via file repo by `storage_key`)
  - legacy form: `{path, mime_type, encoding, size_bytes, content}` (content inline, base64 or text, in the JSONB column itself)
  - `get_companion_file_metadata()` builds `SkillCompanionFileMetadata` (path/mime_type/encoding/size_bytes, no content) — the natural "list files in bundle" call to drive a seeding loop.
  - SKILL.md is **never** stored as a companion file (service rejects a companion path matching `skill.md`); its content lives solely in `Skill.content`.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/skill_service.py`:
  - `get_companion_file(skill_id, path, user)` (line ~400-444) — the authoritative dual-storage read: if `storage_key` present, reads bytes via `SkillFileStorage(skill_id, file_repo).read_file(...)`; otherwise returns inline JSONB content directly (legacy fallback). This is the method the seeder should call per bundle file.
  - `list_companion_files(skill_id, user)` (line ~393-398) — metadata-only listing to enumerate what to seed.
  - `_upload_companion_files(...)` — confirms current uploads always use the storage-key (separate blob) form; inline JSONB is legacy-only.
  - `preview_skill_bundle(file_name, archive_content)` (line ~211-288) — the zip-upload preview endpoint; **no size/count ceiling enforcement found here today** (see Risk Indicators).
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/skill_file_storage.py` — `SkillFileStorage` wraps the generic `FileRepository` with `owner=f"skill-{skill_id}"`; same abstraction `AgentWorkspaceService` uses with `owner=f"workspace-{workspace_id}"`. `_normalize_companion_file_path` already guards path traversal (absolute paths, `..`, drive letters) — reusable precedent for validating seeded paths under `skills/<skill-name>/...`.

**SkillTool resolution/ordering (the determinism gap):**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/agents/tools/skill/skill_tool.py`:
  - `SkillBaseTool._load_available_skills()` (line ~95-108) calls `SkillService.get_skills_by_ids(skill_ids, user)`, built from `assistant_config.skill_ids` in attachment order.
  - `SkillBaseTool._find_skill(skill_name)` (line ~110-112): `next((s for s in self.available_skills if s["name"] == skill_name), None)` — **first-wins** lookup by name.
  - `SkillTool.execute()` renders `skill.content` (SKILL.md body). `SkillCompanionFileTool.execute()` resolves the same way via `_find_skill`.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/repository/skill_repository.py` lines 386-393 — `get_by_ids(skill_ids)`: `select(Skill).where(Skill.id.in_(skill_ids))`, **confirmed no `.order_by(...)`** — Postgres may return rows in arbitrary order. Since `_find_skill` is first-wins over this list, duplicate-name resolution is non-deterministic today. **The seeder's deterministic ordering function must be built new** — most natural fix is re-sorting repository results by input `skill_ids` order at the call site (`SkillService.get_skills_by_ids`, line ~446-462) or inside `_load_available_skills`, and the seeder should reuse that exact same ordering function so SkillTool and the seeder agree on which skill "wins" a name collision.

### Architecture and Layers Affected

- **Tool layer** (`codemie_tools/data_management/workspace/*`, `codemie/agents/tools/skill/skill_tool.py`): toolkit assembly, individual workspace file tools, skill resolution tools.
- **Service layer** (`codemie/service/agent_workspace_service.py`, `codemie/service/skill_service.py`, `codemie/service/skill_file_storage.py`, `codemie/service/tools/toolkit_settings_service.py`, `codemie/service/workspace_script_bridge.py`): business logic, dual-storage normalization, feature gating, upsert/soft-delete handling.
- **Repository layer** (`codemie/repository/agent_workspace_repository.py`, `codemie/repository/skill_repository.py`): raw SQLModel/session CRUD, no ordering/insert-if-absent guarantees today — both are gaps this story must fill.
- **Hook/wiring layer** (`codemie/service/tools/toolkit_service.py::get_toolkit_methods`): the one dict consumed identically by chat and workflow node execution paths.
- **Runtime SDK / sandbox layer** (`codemie_tools/data_management/code_executor/{sandbox_guard.py,runtime_sdk/*}`): defines reserved paths the seeder must avoid.
- **DB/migration layer**: likely needs a new table (seed ledger) or JSON column on `AgentWorkspace`, plus a new repository method for insert-if-absent writes — follows the precedent of `u1v2w3x4y5z6_enforce_agent_workspace_uniqueness.py` (dedicated Alembic migration for workspace-related DB constraints).

### Integration Points

- `AgentWorkspaceToolkit.get_tools()` → `AgentWorkspaceService` (shared instance) → `AgentWorkspaceFile` repository — the seeder slots into this exact chain, before the tool list is returned.
- `SkillService.get_skills_by_ids` / `list_companion_files` / `get_companion_file` → the seeder's read side.
- `resolve_tool_calling_timeout()` and `customer_config.is_feature_enabled("workspaceScriptBridge")` (`/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/workspace_script_bridge.py`) — Story 1's switch; **already exists in main** (see Documentation Findings) and is the model for the new "jobs sandbox mode + script-execution tool present" gate this story needs to add.
- `ExecuteWorkspaceScriptTool` / `SandboxMode.JOBS` (`codemie_tools/data_management/code_executor/models.py` line 40) — gate condition "jobs sandbox mode is active" maps directly to `SandboxMode.JOBS` vs `SandboxMode.SHARED`, configured via env var `CODE_EXECUTOR_SANDBOX_MODE`.
- `BRIDGE_DIR_NAME = ".codemie_bridge"` constant in `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie_tools/data_management/code_executor/runtime_sdk/codemie_runtime_sdk.py` — single source of truth for the reserved bridge directory; `execute_workspace_script_tool.py` already imports and excludes it from file-change scans. The seeder's path-reservation check (refuse writes to `.codemie_bridge/` and root `codemie_runtime_sdk.py`) should import this same constant rather than hardcoding the string.
- `DynamicConfigService` (`/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/dynamic_config_service.py`) and the `SKILL_MAX_CONTENT_LENGTH` pattern in `skill.py` (lines ~136-182) — the reusable template for the new per-skill size (10MB) and file-count (100) ceiling settings.

### Patterns and Conventions

- **Upsert pattern** (`_upsert_workspace_file_content`): normalize path → write blob → checksum/mime → `get_file(include_deleted=True)` → update-in-place-or-insert → `save_file`. The new insert-if-absent variant should follow the same steps minus the revive branch — i.e., `get_file(..., include_deleted=False)` only, and no-op (skip) if any row exists at all (even soft-deleted), per the ticket's "a recorded source is never copied again" rule.
- **Dynamic ceiling config pattern** (`skill.py`): module constants `DEFAULT_*`/`MIN_*` plus a `*_CONFIG_KEY` string, accessor function via `DynamicConfigService.get_typed_value_safe(KEY, int, default=DEFAULT)`, guards against bool/out-of-range, never raises. Reuse this exact shape for `SKILL_WORKSPACE_FILE_MAX_SIZE_BYTES` (default 10MB) and `SKILL_WORKSPACE_MAX_FILE_COUNT` (default 100).
- **Feature-flag gating pattern** (`workspace_script_bridge.py`): `customer_config.is_feature_enabled(FEATURE_KEY)` + `customer_config.get_feature_setting(FEATURE_KEY, "settingName")`, shipped via `config/customer/customer-config.yaml` component block. The new gate ("script-execution tool present AND workspaceScriptBridge on AND jobs sandbox mode active") composes this existing flag with a `SandboxMode.JOBS` check and a tool-presence check — no new flag type needed, likely compose at the call site in `AgentWorkspaceToolkit.get_tools()`.
- **Optimistic concurrency, not row locks**: workspace creation handles races via unique DB constraint (`uq_agent_workspaces_conversation_user`) + `IntegrityError` catch + re-fetch, not `SELECT FOR UPDATE`. **No existing per-workspace row-lock pattern exists.** A separate reusable primitive, `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/utils/leader_lock.py` (`LeaderLockContext`, Postgres advisory lock via raw SQL), exists for leader election elsewhere and is the closest adoptable mechanism if the ticket's "serialized per workspace (row lock)" requirement needs enforcing — grep found no `with_for_update()`/`FOR UPDATE` usage anywhere in workspace code today.
- **First-wins + logged-warning pattern for name collisions**: already established in `SkillBaseTool._find_skill` (first match by name wins silently today, no warning log yet) — the seeder's "same name, different id → skip + log warning" behavior should add the missing warning log and reuse the same ordering fix needed for `SkillRepository.get_by_ids`.
- **DI style**: tools/services receive pre-resolved context via constructor fields (`user`, `workspace_service`, `workspace_id`) rather than fetching dependencies internally — any new seeder component should follow this convention when wired into `AgentWorkspaceToolkit.get_tools()`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

No guide in `.ai-run/guides/` documents workspace architecture, skill architecture, or agent-toolkit assembly for this product domain (checked `agents/agent-tools.md`, `agents/custom-tool-creation.md`, `agents/tool-overview.md`, `data/database-patterns.md`, `data/repository-patterns.md`, `architecture/*.md` — all generic, no workspace/skill domain content).

**No architecture spec document matching "D3, D4, D4a, D7, D24" or ticket IDs EPMCDME-15364 / EPMCDME-15400 / EPMCDME-15558 / "Programmatic Tool Composition" exists anywhere in the repository.** If such a spec exists, it is external (Confluence/Jira) and not checked into the codebase. This is itself a risk — the decisions quoted in the ticket (D3, D4, D4a, D7, D24) cannot be cross-checked against a committed source of truth.

The closest adjacent prior-art in the repo is a **different but related ticket, EPMCDME-15401** ("script tool calls" / `workspaceScriptBridge`), with full spec/plan/technical-analysis docs already committed:
- `/Users/yevhen_slyva/codemie-dev/codemie/docs/superpowers/tasks/2026-09-29-script-tool-calls-channel-sdk/{spec,plan,technical-analysis}.md`
- `/Users/yevhen_slyva/codemie-dev/codemie/docs/superpowers/tasks/2026-09-30-script-tool-calls-jobs-mode-bridge/{spec,plan,technical-analysis}.md`

### Architectural Decisions

- Confirmed via code (not docs): Story 1's building blocks (`resolve_tool_calling_timeout`, the `workspaceScriptBridge` feature flag, the `codemie_runtime_sdk` SDK/bridge, jobs-mode dispatch via `job_bridge.py` and `SandboxMode.JOBS`) **already exist in main with full test coverage** — confirmed by `tests/codemie/service/test_workspace_script_bridge.py`, `tests/codemie_tools/data_management/code_executor/test_job_bridge.py`, `test_sandbox_dispatch.py`, `test_batch_job_runner.py`, `test_sandbox_guard.py`, `runtime_sdk/test_codemie_runtime_sdk.py`, `runtime_sdk/test_workspace_bootstrap.py`. This story (EPMCDME-15558) can build directly on top of these without re-implementing them.
- The EPMCDME-15401 technical-analysis explicitly notes the `README.md` for `code_executor` still describes the bridge as "pooled-sandbox-mode only," which is stale relative to current jobs-mode support — a pre-existing doc-drift issue, not something this story needs to fix but worth being aware of when reading that README.
- Workspace uniqueness is DB-enforced via a dedicated Alembic migration (`u1v2w3x4y5z6_enforce_agent_workspace_uniqueness.py`) — confirms the project's convention of expressing workspace invariants as DB constraints, a precedent to follow for the new seed-ledger schema change.

### Derived Conventions

- `SkillRepository` is imported into `codemie/service/tools/toolkit_service.py` specifically so tests can patch it there (explicit NOTE comment) — if the seeder is wired through `toolkit_service.py` or a sibling module, follow the same test-patchability convention.
- No standalone guide exists for `SkillService`/`SkillTool`/`AgentWorkspaceToolkit` — conventions must be derived from code (as done above): layered service/repository/tool structure, dual-storage normalization at the service layer, DI via constructor fields.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/test_agent_workspace_glob.py`, `test_agent_workspace_service_tool_calling.py`, `test_workspace_script_bridge.py` — `AgentWorkspaceService` behavior (glob, write/read, script-bridge gating).
- `tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py`, `test_workspace_glob_tools.py`, `test_inspect_workspace_image_tool.py` — tool-level tests for the workspace toolkit; `WriteWorkspaceFileTool` has **no dedicated test file**.
- `tests/codemie/service/test_skill_service.py`, `test_skill_file_storage.py` (mocks underlying file repo, asserts `owner="skill-<id>"` delegation), `test_skill_event_service.py`, `test_skill_user_interaction_service.py` — SkillService layer.
- `tests/codemie/repository/test_skill_repository.py` — no test currently asserts ordering of `get_by_ids` (consistent with the confirmed missing `ORDER BY`).
- `tests/codemie/rest_api/models/test_skill_content_limit.py` — the canonical example of a configurable-ceiling test suite (monkeypatches the accessor function, tests boundary/validation via `pytest.raises`).
- `tests/codemie/configs/test_customer_config.py` (lines ~821-837) — asserts the shipped `features:workspaceScriptBridge` component defaults to disabled.

### Testing Framework and Patterns

- Plain **pytest**, classes grouped `class Test<Area>:` rather than flat functions.
- `monkeypatch.setattr(module, "function_name", lambda ...)` to stub module-level accessor functions and classmethods.
- `unittest.mock.MagicMock()` for repository/service mocking with `.assert_called_once_with(...)`.
- `pytest.raises(ValidationError)` / `pytest.raises(ExtendedHTTPException)` for boundary checks.
- Inline ticket-ID comments in test modules (e.g. `# EPMCDME-13541`) — convention to mirror with `# EPMCDME-15558`.

### Coverage Gaps

- **No test file matching `*seed*`, `*bundle*workspace*`, or `skill*to*workspace*` exists anywhere under `tests/`** — this is a fully greenfield feature from a testing standpoint.
- No existing module implements copying/seeding skill companion files into an `AgentWorkspace` at all.
- No test asserts deterministic ordering of `SkillRepository.get_by_ids` (matches the confirmed code gap).
- No test exists for an insert-if-absent workspace-file write operation (doesn't exist yet).
- `WriteWorkspaceFileTool` itself has no dedicated test file — worth adding coverage alongside the new seeder work since the seeder will interact with the same write path area.

---

## 5. Configuration and Environment

### Environment Variables

- `CODE_EXECUTOR_SANDBOX_MODE` — selects `SandboxMode.JOBS` (default) vs `SandboxMode.SHARED`, in `codemie_tools/data_management/code_executor/models.py` line 40. This is the mechanism behind the "jobs sandbox mode is active" gate condition.
- Static feature-flag convention in `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/configs/config.py` (`class Config(BaseSettings)`): `<AREA>_<SUBAREA>_ENABLED: bool` fields (e.g. `SUBWORKFLOW_POOL_ENABLED`, `MCP_AUTH_ENABLED`, `TOOL_SELECTION_ENABLED`) — the pattern to follow if the new seeder's on/off switch should be a static deploy-time env var rather than a runtime `DynamicConfig` key.

### Configuration Files

- `/Users/yevhen_slyva/codemie-dev/codemie/config/customer/customer-config.yaml` (~line 318) — ships the `features:workspaceScriptBridge` component (`enabled: false`, `timeoutSeconds: 120`, `name`, `description`). The new skill-seeding gate composes with this existing flag rather than needing a wholly separate customer-config component (per the ticket's own gating description).
- `DynamicConfig` DB table (`src/codemie/rest_api/models/dynamic_config.py`) + `DynamicConfigService` — runtime-editable settings surface, used today for `SKILL_MAX_CONTENT_LENGTH`; the two new ceilings (10MB size, 100 files) should likely be `DynamicConfig` INT keys following the exact same accessor-function template, admin-editable via the `/skills/config`-style router pattern (see `test_skill_config_endpoint.py`).

### Feature Flags and Deployment Concerns

- The gate per the ticket is a three-way AND: (1) assistant has the script-execution tool, (2) `features:workspaceScriptBridge` customer-config flag is on (`resolve_tool_calling_timeout()` returning non-None, or a direct `is_feature_enabled` check), (3) `SandboxMode.JOBS` is active. All three primitives already exist in the codebase; this story only needs to compose them at the point `AgentWorkspaceToolkit.get_tools()` decides whether to run the seeder.
- No existing precedent for a per-entity file-count ceiling (only the single content-length-ceiling precedent for `SKILL_MAX_CONTENT_LENGTH`) — the per-file-size and per-skill-file-count ceilings will be new settings with no sibling beyond that one template to copy structurally.
- The skill upload/preview endpoint (`POST /skills/import-bundle-preview`, `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/rest_api/routers/skill.py` lines ~517-535) currently enforces **no file-size cap, no file-count cap, and no zip-bomb protection** at all (only: exactly-one-SKILL.md, no companion path named SKILL.md, no duplicate paths, path-traversal guard). The ticket's required "bundle-preview response field reporting the affected file count" (for the separate UI warning sub-task) will need new logic here, as there is currently nothing counting or limiting bundle files on the preview path.

---

## 6. Risk Indicators

- No architecture spec document for decisions D3/D4/D4a/D7/D24 or ticket IDs EPMCDME-15364/15400/15558 exists in the repo — the ticket's cited decisions cannot be cross-verified against a committed source; implementers will be relying solely on the ticket text.
- `SkillRepository.get_by_ids` has no `ORDER BY` — confirmed non-deterministic skill-name-collision resolution today in `SkillBaseTool._find_skill`. This story must introduce a shared deterministic ordering function used by BOTH SkillTool and the new seeder, or the two will disagree on which same-named skill "wins."
- No insert-if-absent operation exists on the workspace file write path — the only existing write primitives (`_upsert_workspace_file_content` / `_upsert_workspace_file_reference`) explicitly fetch with `include_deleted=True` and revive soft-deleted rows, which is exactly the behavior the ticket says the seeder must avoid. This requires a new repository method, not a parameter tweak to the existing one (to avoid accidentally changing behavior for other callers).
- No row-locking (`SELECT ... FOR UPDATE`) pattern exists anywhere in workspace code; the only two adoptable concurrency precedents are optimistic unique-constraint+retry (workspace creation) or `LeaderLockContext` Postgres advisory locks (used elsewhere for leader election, not workspace files). The ticket's "serialized per workspace (row lock)" requirement has no direct precedent to copy — a design decision is needed on which mechanism to adopt.
- Decision open per ticket: new table vs JSON column for the seed ledger — no existing per-workspace ledger-like structure to pattern-match against; nearest analog is the `companion_files` JSONB array on `Skill`, which could be mirrored as a JSONB column on `AgentWorkspace`, but this is a schema design choice, not a given.
- No test coverage exists at all for skill-to-workspace seeding (fully greenfield) — complexity-assessor should weight this as new, untested surface, not an extension of a well-tested area.
- Skill bundle upload/preview endpoint has zero file-size/file-count/zip-bomb protection today — the new per-skill ceilings (10MB/100 files) are being added into a previously unguarded path, raising the risk surface of the change beyond just workspace seeding.
- `.codemie_bridge/` and `codemie_runtime_sdk.py` reserved-path enforcement must be added net-new in the seeder; the only existing reference is `BRIDGE_DIR_NAME` being excluded from a post-execution file-scan in `execute_workspace_script_tool.py` — there is no existing "reject this write path" validation function to call, it must be added for both the seeder and ideally reused by `WriteWorkspaceFileTool` for consistency.
- Two different storage forms for companion files (storage-key blob vs legacy inline JSONB) must both be correctly handled by the seeder via `SkillService.get_companion_file` — missing the legacy branch would silently fail to seed older skills' bundle files.
- The "ONE hook only" assumption is confirmed correct in code (`toolkit_service.py::get_toolkit_methods` feeds both chat and workflow node paths), which de-risks the wiring approach, but also means a bug in the seeder will affect both chat and workflow execution simultaneously with no isolation.

---

## 7. Summary for Complexity Assessment

This story touches four architectural layers: the **tool layer** (`AgentWorkspaceToolkit.get_tools()` in `codemie_tools/data_management/workspace/toolkit.py`, where the seeding call must be inserted, and `codemie/agents/tools/skill/skill_tool.py`, where the ordering fix must be shared), the **service layer** (a new seeding method, likely a new `skill_workspace_seeder.py`-style service paralleling `skill_file_storage.py`, plus changes to `SkillService.get_skills_by_ids`/`SkillRepository.get_by_ids` for deterministic ordering, and composition with the existing `workspace_script_bridge.py` gate), the **repository layer** (a new insert-if-absent write operation on `AgentWorkspaceFileRepository`, since the existing upsert path explicitly revives soft-deleted rows which this feature must not do), and the **DB/migration layer** (a new table or JSONB column for the seed ledger, requiring an Alembic migration, following the precedent of `u1v2w3x4y5z6_enforce_agent_workspace_uniqueness.py`). Estimated file-change surface is moderate-to-large: new seeder service + tests, repository method + tests, migration, ordering fix in `SkillRepository`/`SkillService` + tests, path-reservation validation reusing `BRIDGE_DIR_NAME`, two new `DynamicConfig` ceiling settings + accessor functions, and a bundle-preview endpoint field for file-count reporting.

Technical novelty is high in several places and low in others. The gating composition (script-execution tool present + `workspaceScriptBridge` flag + `SandboxMode.JOBS`) is low-novelty — all three primitives already exist in main from Story 1 and simply need to be composed at one call site. The ceiling-setting pattern is low-novelty — there is a direct, well-tested template (`SKILL_MAX_CONTENT_LENGTH` in `skill.py` + `DynamicConfigService`) to copy twice. However, three pieces are genuinely novel with no in-repo precedent: (1) the insert-if-absent repository write (the existing primitive does the opposite — revive-on-upsert — so this cannot be a parameter tweak, it needs new logic carefully isolated from the existing upsert callers), (2) per-workspace serialization/row-locking (no `FOR UPDATE` pattern exists anywhere in this codebase; the only candidate primitive, `LeaderLockContext`, was built for an unrelated leader-election use case and its fit here is unproven), and (3) the seed-ledger storage design itself (new table vs JSON column is an explicitly open decision with no analogous ledger structure elsewhere to copy, only the loosely-similar `companion_files` JSONB array on `Skill`).

Test coverage posture is mixed-to-weak: the adjacent systems this story extends (`AgentWorkspaceService`, `SkillService`, the `workspaceScriptBridge` gate from Story 1) are well-tested, giving a solid foundation to extend from and clear test patterns to follow (pytest classes, monkeypatch for accessor functions, MagicMock for repository mocking). But the feature itself — skill-bundle-to-workspace seeding — has zero existing test coverage anywhere in the repo, and the `SkillRepository.get_by_ids` ordering gap it must fix is also currently untested. Key risk factors for complexity scoring: the missing architecture-spec document in-repo (decisions D3/D4/D4a/D7/D24 cannot be cross-checked), the open schema-design decision (table vs JSON column) that should ideally be resolved before implementation starts, the two genuinely novel concurrency/write-semantics pieces with no copyable precedent, and the fact that the skill-bundle upload/preview endpoint currently has zero size/count/zip-bomb protection, meaning the new ceilings are being retrofitted onto a previously-unguarded path rather than extended from an existing guard.

---

## 8. Addendum 2026-10-08: change of approach (copy → reference versioned skill blobs)

Sections 1–7 describe the original "copy" design. After the architecture review of MR !4427 and the updated ticket, the workspace points at versioned skill blobs. Facts below come from reading the branch at `40fc9c99c`.

### Current implementation to be replaced
- `src/codemie/service/skill_workspace_seeder.py` (242 lines): claim-first state machine (`seeding`/`complete`/`partial`/`collision`), bounded retries, stale takeover, `_wait_for_other_seeder` with injected `sleep`/`clock`, per-file blob copy via `AgentWorkspaceService.seed_file_if_absent` (blob write before row insert).
- `agent_workspace_seed_entries` table (migration `src/external/alembic/versions/y1z2a3b4c5d6_add_agent_workspace_seed_entries.py`, `down_revision = f88ad134467d`, the only revision touching the seed table and the only single head): columns `status`, `attempts`, `created_at` (claim token); unique `(workspace_id, source_kind, source_id)` and `(workspace_id, folder)`; plus `agent_workspace_files.seed_entry_id` (FK, SET NULL, indexed) which nothing reads.
- `AgentWorkspaceRepository` seed methods: `get_seed_entry`, `claim_seed_entry`, `update_seed_entry_status`, `reclaim_stale_seed_entry`, `reclaim_partial_seed_entry`, `record_collision`, `find_seed_entry_by_folder`, `create_file_if_absent` (check-then-insert); `delete_by_execution_id` also hard-deletes seed entries explicitly while the FK already cascades.
- Six `DynamicConfig` settings in `rest_api/models/skill.py:155-288`; three are used only by the retry/wait machinery (`SKILL_WORKSPACE_SEED_MAX_ATTEMPTS`=3, `..._STALE_THRESHOLD_MINUTES`=10, `..._WAIT_SECONDS`=30) and are referenced only by `models/skill.py`, the seeder and their tests (no docs, yaml, helm).
- Seeding hook: `AgentWorkspaceToolkit._seed_skills_if_enabled` called from `get_tools()`; gated by the field `script_tool_selected`, threaded through `ToolkitService.get_toolkit_methods` → `_is_tool_selected` → `ToolkitSettingService.get_agent_workspace_toolkit` → `AgentWorkspaceToolkit.get_toolkit`. Only caller passing `selected_toolkits` is `add_tools_with_creds` (`toolkit_service.py:1148-1152`). Tests: `test_toolkit_service.py:2113-2137`, `test_agent_workspace_toolkit.py` (lines 30, 41, 129).
- Warnings: `request._seed_warnings` declared `core/models.py:643`, set in `workspace/toolkit.py:133`, read only in chat `_prepare_system_prompt` (`assistant_service.py:473`); `_prepare_workflow_system_prompt` (`:738-787`) never reads it.

### Skill blob storage (needed for versioning)
- Only writers of skill blobs: `SkillService._upload_companion_files` (`skill_service.py:164-209`), called from create (`:522-525`) and update (`:652`). Key = normalized bundle path in owner `skill-<skill_id>`; the record stores `storage_key = normalized_path`. Re-uploading a path overwrites the same key today.
- `get_companion_file` already reads by `storage_key` (`:411-416`); legacy records (no `storage_key`) use inline JSONB content (`:439`). `preconfigured_skills.py:89-209` seeds inline records with no `storage_key`, so those skills are "legacy" under the new rule.
- `list_companion_files` returns `SkillCompanionFileMetadata` (path, mime_type, encoding, size_bytes) with no `storage_key`; `Skill.get_companion_file_metadata()` is `models/skill.py:931-937`. The seeder needs `storage_key`.
- Export (`export_skill`) exports SKILL.md only; `import_skill` creates a skill without companion files; marketplace publish only flips visibility; no clone/copy exists; preview reads the zip and writes no blobs. So `_upload_companion_files` is the only writer (confirms the ticket); nothing else needs changing for `storage_key`.
- `update_skill` replaces the companion list wholesale and never deletes old blobs; `delete_skill` deletes no blobs; `SkillFileStorage` has no delete method. With content-addressed keys, orphaned blobs accumulate (cleanup is out of scope; add the "do not delete without reference check" comment).
- File repositories (AWS, Azure, GCP, filesystem) accept slashes in blob names: AWS key `{owner}/{name}`, Azure container/blob, GCP bucket/blob, filesystem `<base>/<owner>/<name>` with a safe-path check. A name like `ab12cd34ef56ab12/scripts/run.py` works everywhere; `is_safe_blob_ref` accepts it. Worst case length: owner `skill-<uuid>` (42) + `/` + 16-char hash + `/` + path up to 952 = about 1012 < S3's 1024-byte key limit. Azure `write_file` logs and swallows upload errors, so a failed upload still yields a `storage_key` (pre-existing behavior; risk to note).

### Workspace behaviour for rows owned by `skill-<id>`
- `get_workspace_input_files` (`agent_workspace_service.py:526-566`) already reads via `blob_owner`/`blob_name` but has no per-file guard: one missing blob aborts the whole call (also `grep_files`). Ticket requires skip + warning.
- `_upsert_workspace_file_content` (`:624-660`) always writes a new blob to `workspace-{id}/{path}` and repoints `blob_owner`/`blob_name`, so edits are copy-on-write and never touch the skill blob. `_upsert_workspace_file_reference` stores the foreign owner/name (catches only `FileNotFoundError`).
- `delete_file` only soft-deletes the row; no blob is deleted anywhere by owner. Soft-delete keeps the row, so ON CONFLICT DO NOTHING on `(workspace_id, path)` leaves tombstones in place.
- **Risk found:** `download_rules.can_download` (`:127-168`) allows an owner only if it equals the user id, the MCP image owner, `workspace-<id>` owned by the user, or a share grant. A `skill-<id>` owner matches none, so `get_file_sandbox_url` (token embeds the blob owner) would yield 403 at `routers/files.py:218`, and `validate_run_files` (`:222`) also calls `can_download`. The REST download path (`agent_workspace.py:188`) reads directly and works. Needs a decision in the spec (extend the rule for skill-owned blobs the user can read, or route those reads through the workspace service).
- Existing unique constraint on `agent_workspace_files(workspace_id, path)` is in `u1v2w3x4y5z6`; `on_conflict_do_nothing` is not used anywhere in `src/` (precedent for PostgreSQL upserts: `pg_insert(...).on_conflict_do_update` in `budget_repository.py:323-333`, `project_spend_tracking_repository.py`). Repository methods open one session per call; a multi-insert method can use one session and a single commit.

### Workflow paths
- Three sites call `ToolkitService.get_toolkit_methods()` without `selected_toolkits`: `workflows/nodes/tool_node.py:203`, `service/tools/tool_execution_service.py:229`, `service/script_tool_calls/workflow_resolution.py:36` (they build a script-step or tool-invocation toolkit; none are the assistant-node build path).
- Assistant tools are built in `ToolkitService.get_tools` (`toolkit_service.py:489`) where `selected_toolkits = _merge_skill_toolkits(assistant)` is known; chat (`build_agent`, `:582`) and workflow assistant nodes (`build_agent_for_workflow`, `:861`) both funnel through it. That is the natural place for a separate preparation step. Ticket decision: workflows get skill files, and their notices must reach the model (workflow prompt builder must consume them).

### Skill tool / layering
- `agents/tools/skill/skill_tool.py` imports `AgentWorkspaceRepository` (`:27`), seed status constants and `AgentWorkspaceSeedEntry` (`:28-33`) and `SOURCE_KIND_SKILL` from the seeder (`:36`); `_workspace_hint` (`:224-237`) builds its own repository; `build_workspace_hint` (`:400`) branches on status.
- Skill order: `SkillService.order_skills_by_attachment(skills, skill_ids)` (`skill_service.py:447,467`) is used by `get_skills_by_ids`; `_find_skill` is first-by-name over that list. The seeder calls `get_skills_by_ids([skill_id])` one skill at a time, so it does not decide the winner.
- Folder name `skills/<name>` is built in two places: seeder (`:118`) and `models/skill.py:927` (`to_detail_response`).
- `models/skill.py` holds `BundleSeedPlan` (`:638`), `plan_bundle_seed` (`:651`), `bundle_seed_skip_messages` (`:683`), `compute_bundle_warning` (`:696`), the six config getters, and `DynamicConfigService`/path-rule imports. `routers/skill.py:366-371` reads the three limits separately. `max_length=64` appears at `models/skill.py:335,416,547,755` and `MAX_SKILL_NAME_LENGTH = 64` in `workspace_path_rules.py`.

### Reserved paths
- `is_reserved_workspace_path` (`workspace_path_rules.py:65`, root-only name check) is called only from `_normalize_write_path` (`:818`) and `_skip_reserved` (`:825`). `job_bridge.is_bridge_path` (`:77`, also strips backslash/leading slash) is used by `batch_job_runner.py:393,409`. `BRIDGE_DIR_NAME = ".codemie_bridge"` lives in `runtime_sdk/codemie_runtime_sdk.py:31`; `RUNTIME_SDK_FILE_NAME` is defined only in `workspace_path_rules.py` (the SDK file name appears as a literal in `sandbox_guard.py:58`, `workspace_bootstrap.py:28`).
- `_normalize_path` (validating) is used by register_generated_files, delete_file, `_sync_execution_files`, `_get_workspace_file_or_raise` (read/download/edit/grep) and uploads; `_normalize_write_path` (adds the reserved check) by the content/reference upserts and `seed_file_if_absent`. Delete and read are not reserved-checked today.

### Tests touching the area (to rewrite or drop)
- Seeder: `test_skill_workspace_seeder.py` (30), `test_skill_workspace_seeder_concurrency.py` (2). Repository seed methods in `test_agent_workspace_repository.py` (17). Migration: `tests/codemie/migrations/test_add_agent_workspace_seed_entries.py` (5). Skill tool: `test_skill_tool.py` (10). Config/plan: `test_plan_bundle_seed.py` (12), `test_skill_workspace_seed_config.py`, `routers/test_skill_config_endpoint.py`. Reserved paths: `test_agent_workspace_service_reserved_paths.py` (10), `test_workspace_path_rules.py` (5), `test_workspace_path_parity.py` (2), `test_job_bridge.py` (22). Input files: `test_agent_workspace_service_tool_calling.py` (7). Alembic single head is asserted in `tests/codemie/migrations/test_credentialtypes_enum_migrations.py:89-90`.

## 9. Addendum 2026-10-13: review round 4 (blob key, shared seeding core)

### Blob key and uploads
- `SkillFileStorage.versioned_key` used a 16-hex (64-bit) prefix; the only writer of skill blobs is `SkillService._upload_companion_files`, called on create, update and every startup from `preconfigured_skills._store_companion_files`. Repositories overwrite unconditionally, so every start re-uploaded every built-in skill file.
- Readers (`get_companion_file`, `skill_workspace_seeder`, `skill_bundle_seed_policy`) use the stored `storage_key`, so widening the prefix needs no data migration.
- S3 key budget: `skill-<uuid>` (42) + 1 + 32 + 1 + `MAX_SKILL_BUNDLE_PATH_LENGTH` (943) = 1019 <= 1024. The limit counts characters; S3 counts UTF-8 bytes.

### Owner rules and seeding
- Before round 4 the skill prefix lived in `blob_ref_rules` (`SKILL_BLOB_OWNER_PREFIX`, `skill_blob_owner`, `is_skill_blob_ref`) and was repeated at three call sites in `agent_workspace_service.py` (`sync_uploaded_files`, `_generated_file_path`, the virtual upload view); `_upsert_workspace_file_reference` had no check.
- `seed_skill_references` hard-coded `SOURCE_KIND_SKILL` and `skill_blob_owner`; the seeder depends on it through the structural `SkillSeedStore` Protocol, so both must be renamed together.
- `blob_ref_rules` importing `SOURCE_KIND_SKILL` from `rest_api.models.agent_workspace` creates no import cycle (checked in the venv).

### Run-file tokens
- `POST /workflows/{id}/executions` → `validate_run_files` and `WorkflowService.create_workflow_execution` → `register_run_files` both go through `_decode_safe_run_file`. Before round 4 a `skill-` token passed validation when the user had a row for it (rule D2) and was then dropped silently in `sync_uploaded_files`.
- codemie-ui sends only `fileId` values from upload responses (`WorkflowStartExecutionPopup.tsx`, `ChatPrompt.tsx`), owned by the user; workspace API responses never carry a token.
- The workflow resume endpoint passes `file_names` without `validate_run_files` (pre-existing gap).
