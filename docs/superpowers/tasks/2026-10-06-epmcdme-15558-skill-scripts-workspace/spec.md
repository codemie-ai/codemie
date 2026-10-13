# EPMCDME-15558 — Scripts Shipped in Skills Are Available in the Workspace

**Epic**: EPMCDME-15364 "Programmatic Tool Composition" (Story 2 of 14)
**Scope**: backend sub-task 1 (codemie repo), delivered as one MR in five ordered phases, one commit per phase. The codemie-ui upload-warning sub-task consumes the `bundle_warning` field and is not part of this spec.
**Requirements source**: the Jira ticket as updated on 2026-10-08 (copy: `docs/stories/2026-10-08-skill-files-in-agent-workspace.md`) and the architecture review of MR !4427.

## 1. Problem

A skill is a bundle: `SKILL.md` plus supporting ("companion") files stored with the skill. Today the model can only read a companion file one at a time. A script placed in a skill bundle cannot be run, because scripts only run against files already in the conversation's workspace. This story makes every companion file of an attached skill available as an ordinary workspace file, so scripts in a skill bundle work without the user uploading anything.

The first implementation copied each skill blob into every conversation's workspace storage. It made the first reply slow (synchronous seeding inside `get_tools()`), allowed a blob-before-row overwrite race, could leave a skill stuck in `seeding` after a crash, grew storage per conversation, and needed a state machine, lease, polling and retries. This spec replaces copying with **references to versioned skill blobs**.

## 2. Settled decisions (not reopened)

- Skill files are referenced, not copied. A user edit is already copy-on-write: `_upsert_workspace_file_content` writes a new `workspace-<id>` blob and repoints the row, so the skill's blob is never touched.
- Every version of a skill file is kept under its own content-addressed key; nothing overwrites a previous blob. Content is pinned at seeding time.
- The file set is frozen at the first seeding of a skill in a workspace. Files added to the skill later do not appear. Accepted limitation, documented.
- Legacy skills whose companion files are stored inline in the skill record (no `storage_key`) are skipped: no seeding, no copy fallback. A warning is logged and the model gets the notice "companion files of skill X are not available in the workspace; read them through the skill tool". Documented.
- Name collision: the first skill wins, the second is reported.
- Workflows get skill files like chats do.
- Reserved paths: root-only, case- and Unicode-insensitive, writes and deletes are blocked, reads are not. A single write or delete raises a validation error; a batch write skips the file with a warning (as today).
- Download tokens: a skill-owned blob is downloadable when a live file row in a workspace owned by the requesting user points at it.
- The unmerged migration `y1z2a3b4c5d6` is rewritten in place; no new migration is added.
- Gate (unchanged): the assistant has the script-execution tool among its selected tools, the `workspaceScriptBridge` switch is on (`resolve_tool_calling_settings()` is not `None`, which also requires `SandboxMode.JOBS`), and the request has a conversation id.
- Ceilings stay settings: per-file size, file count, total size (defaults 10 MB, 100 files, 50 MB). The settings for attempts, stale threshold and wait seconds are removed.

## 3. Design

### 3.1 Data model and migration

- The seed ledger becomes a folder-owner record. One name is used everywhere: table `agent_workspace_folder_owners`, model `AgentWorkspaceFolderOwner`, repository methods and tests.
- Columns: `id`, `date`, `update_date` (from `BaseModelWithSQLSupport`), `workspace_id` (FK to `agent_workspaces.id`, `ON DELETE CASCADE`, indexed), `source_kind` (plain string), `source_id`, `folder`. Unique constraints on `(workspace_id, source_kind, source_id)` and `(workspace_id, folder)`.
- Removed: `status`, `attempts`, `created_at` (claim token) and `agent_workspace_files.seed_entry_id` (never read) with its FK and index.
- `y1z2a3b4c5d6` keeps its revision id and is rewritten to create the new table; the file is renamed `y1z2a3b4c5d6_add_agent_workspace_folder_owners.py` and its test is renamed to match. `downgrade()` drops the table. The head stays single: after the 2026-10-13 rebase on `main` its `down_revision` is `a8f23cf09c37` (main's newest revision; it was `f88ad134467d`).
- `SOURCE_KIND_SKILL = "skill"` lives next to the model in `rest_api/models/agent_workspace.py`.
- The explicit seed-entry delete in `delete_by_execution_id` is removed; the FK cascade is the only mechanism.

### 3.2 Seeding is one transaction

`AgentWorkspaceRepository.seed_references(workspace_id, source_kind, source_id, folder, files)` runs in one session and commits once:

1. `INSERT ... ON CONFLICT DO NOTHING RETURNING id` for the folder owner (without a conflict target, so both unique constraints are covered; a foreign-key error still raises and is not mistaken for a collision).
2. If a row was returned: bulk `INSERT` of the file rows with `ON CONFLICT (workspace_id, path) DO NOTHING`. Existing rows, including soft-deleted ones, are never touched. Each row: `path = <folder>/<relative path>`, `blob_owner = source_blob_owner(source_kind, source_id)` (`"skill-<skill_id>"` for skills; an unknown `source_kind` raises `ValueError` before anything is written), `blob_name = <storage_key>`, `mime_type`, `size`, `checksum` (see 3.3), `version = 1`.
3. If no row was returned: look the owner up by `(workspace, source)`. Found: the skill is already seeded; return `ALREADY_SEEDED`. Not found: the folder belongs to another skill; return `COLLISION`. Collisions are not stored, so they repeat as a notice and clear themselves when the other skill is detached.
4. Return `SEEDED` or the outcome. No blob is read or written; there is no polling or sleep. A crash at any point leaves nothing committed.

Removed with this: statuses `seeding/partial/collision`, the claim token, stale takeover, `reclaim_*`, `claim_seed_entry`, `update_seed_entry_status`, `record_collision`, `find_seed_entry_by_folder`, `create_file_if_absent`, `AgentWorkspaceService.seed_file_if_absent`, `_wait_for_other_seeder`, injected `sleep/clock`, `attempts`.

### 3.3 Versioned skill blobs

- `SkillService._upload_companion_files` (the only writer of skill blobs) writes each blob under `<sha256(content).hexdigest()[:32]>/<normalized_path>` (`blob_ref_rules.content_addressed_key`) in owner `skill-<skill_id>`, stores that as `storage_key`, and adds the full sha-256 hex digest as `checksum` on the record (an additive key in the JSONB dict). Same content gives the same key; changed content gives a new key and never overwrites the previous blob.
- The key prefix is 128 bits (`CONTENT_KEY_HEX_LENGTH = 32`): with 64 bits a crafted collision could substitute a stored blob. Records saved with the earlier 16-character prefix keep reading through their stored `storage_key`.
- `_upload_companion_files(..., existing_records)`: a file whose key and full `checksum` already match one of the skill's current records is not uploaded again and that record is reused as is; `update_skill` and the preconfigured-skills update pass the current records, create passes none. This saves uploads (built-in skills are no longer re-uploaded on every start); it is not a protection, and a blob missing from storage is not restored. After deploy each file with a 16-character key is written once more under its new key, and the old blob stays unreferenced.
- `get_companion_file` reads by `storage_key` and does not change. Existing skills keep `storage_key == path` (no data migration); after their first update their old blob is no longer overwritten, so references created before it become a snapshot.
- A new model-level accessor `Skill.get_companion_file_refs()` returns `SkillCompanionFileRef(path, mime_type, encoding, size_bytes, storage_key, checksum)`; legacy inline records yield `storage_key = None`. `list_companion_files` and its API model stay as they are (no key leaks to clients).
- `AgentWorkspaceFile.checksum` is a required string, and an empty value is accepted (`get_file_content` computes it lazily when empty). Skill-owned rows therefore store the record's `checksum`, or an empty string for skills saved before this change.
- Comments on `SkillService.update_skill` and `delete_skill`: skill blobs may be referenced by workspaces and must not be deleted without a reference check (today nothing deletes them; `SkillFileStorage` has no delete method).
- Other writers: export writes `SKILL.md` only, import and marketplace publish create no blobs, preview reads the zip only, and there is no clone. Nothing else changes. The AWS, Azure, GCP and filesystem repositories accept slash-separated blob names; the worst-case S3 key (owner 42 + 1 + 32 + 1 + 943 = 1019) stays under 1024. Add a test that a hash-prefixed name round-trips on the filesystem repository.

### 3.4 Policy module, limits and the seeder

- New `service/skill_bundle_seed_policy.py` takes over from `rest_api/models/skill.py`: the three limit settings (keys, defaults, one generic `_int_setting(key, default, minimum)` helper replacing the six getters), `BundleSeedPlan`, `plan_bundle_seed`, `bundle_seed_skip_messages`, `compute_bundle_warning`.
- `SeedLimits` is a frozen dataclass `(max_count, max_file_size, max_total_size)` with `SeedLimits.current()`. `plan_bundle_seed(refs, folder, limits)` returns a plan that carries its `limits`; the skip messages are produced from the plan, so the numbers in the text are the numbers applied. `routers/skill.py` uses `SeedLimits.current()` for `/skills/config`.
- `Skill.to_detail_response` no longer calls policy. The router (and any service that needs it) adds `bundle_warning` by calling `compute_bundle_warning`.
- The plan marks refs without a `storage_key` as skipped with reason `inline_content`, and the message is the agreed notice. Paths are checked against `MAX_SKILL_BUNDLE_PATH_LENGTH` (fixes the 952 vs 1024 mismatch).
- `skill_workspace_folder(name: str) -> str` in `workspace_path_rules.py` is the only place that builds `skills/<name>`. The seeder, `compute_bundle_warning`/`to_detail_response` and `SkillTool` all call it.
- `SkillWorkspaceSeeder.ensure_seeded(workspace_id, skills)` takes the already-ordered list of skills (from `SkillService.get_skills_by_ids`, attachment order) and returns a list of notices. It has no `user`, clock or sleep. Its one collaborator, the repository, is typed with a `Protocol` (`SkillSeedStore`). It processes skills in order, so the first skill claims a folder. Notices are produced from the plan (named by skill name and folder, never by UUID) and from `COLLISION`; `ALREADY_SEEDED` produces none.
- Repeat-request behavior: the plan for an already-seeded skill is not recomputed; notices about skipped files appear on the turn the skill is first seeded. Later turns rely on the location hint (3.6), which always says that files over the limits and inline files are not in the folder.

### 3.5 Preparation step and notices

- `ToolkitService.prepare_skill_workspace(assistant, request, user) -> list[str]` runs the gate (section 2), resolves or creates the workspace with the same `AgentWorkspaceService.create_workspace(...)` call the toolkit uses (idempotent), loads the ordered skills and calls the seeder. Any exception is logged and turned into a notice; it never fails the build.
- `AssistantService.build_agent` and `build_agent_for_workflow` call it immediately before `ToolkitService.get_tools(...)` and pass the notices as an explicit argument to `_prepare_system_prompt` and `_prepare_workflow_system_prompt`, which append the "Workspace setup notices (skill files)" block. Chat and workflow assistants therefore get the same notices, and the ordering is enforced by data flow.
- Removed: `AgentWorkspaceToolkit._seed_skills_if_enabled` and its imports, the `script_tool_selected` field and parameter in `AgentWorkspaceToolkit`, `ToolkitSettingService.get_agent_workspace_toolkit`, `ToolkitService._is_tool_selected` usage for it and the matching `get_toolkit_methods` plumbing, and `AssistantChatRequest._seed_warnings`. `AgentWorkspaceToolkit.get_tools()` has no side effects.
- `tool_node.py:206` and `tool_execution_service.py:229` build script-step and tool-invocation toolkits and have no assistant skill context. They intentionally do not seed. A workflow gets skill files through its assistant nodes; the files are visible to later script steps in the same run workspace. This is documented in the module docstring and in the guide.

### 3.6 Tool layering

- `AgentWorkspaceService.skill_location_hint(conversation_id, user, skill_id) -> str | None` returns the hint text when the skill owns its folder in the user's workspace for that conversation (using the repository), else `None`. `SkillTool` calls it and no longer imports `AgentWorkspaceRepository`, the folder-owner model, status constants or anything from the seeder module. There is no status interpretation left.
- The hint says the files are under the folder stored on the owner record and that files over the limits and inline files are not there and must be read through the skill tool. The tool never builds a folder name; the folder comes from the owner record that the seeder wrote using `skill_workspace_folder`, so the hint stays correct if the skill is renamed later.
- `SkillTool._find_skill` keeps picking the first skill by name from the ordered list. If the folder owner is another skill (for example after a re-attach), the hint is `None` for this skill. This is a deliberate, documented divergence from the story's "after a re-attach" wording (decided in the 2026-10-09 review, CR-002): making `_find_skill` follow the folder owner would add a database lookup to a base tool for a rare case.

### 3.7 Downloads and missing blobs

- `download_rules.can_download` rule D2 (source blob referenced by a live row of the user's own workspace) covers owners with a registered source prefix (today `skill-`): allowed when `AgentWorkspaceRepository` finds a non-deleted file row with that `blob_owner`/`blob_name` in a workspace owned by the requesting user. No skill lookup is made.
- `get_workspace_input_files` reads each referenced blob in a guarded block. A missing or unreadable blob is skipped and a warning naming the path is returned together with the files; the script run proceeds and shows the warning in its result. `grep_files` skips unreadable files and logs.

### 3.8 Reserved paths and constants

- `BRIDGE_DIR_NAME` stays defined once in `runtime_sdk/codemie_runtime_sdk.py` (that file is standalone, stdlib-only and shipped to the sandbox, and its public surface is pinned by a test, so it does not gain a second constant). The SDK file's name is derived from that module's name (not its file path, so a `.pyc`-only image is still covered) instead of being repeated as a literal. A new module `codemie_tools/data_management/code_executor/reserved_paths.py` holds the single `is_reserved_path(path)`: normalize separators, strip a leading slash, apply NFKC and casefold, then true when the first segment equals the bridge directory or the whole path equals the runtime SDK file name (root only). `workspace_path_rules.is_reserved_workspace_path` calls it, and `job_bridge.is_bridge_path` is removed in favor of it (callers in `batch_job_runner.py` updated). `workspace_path_rules` keeps importing from `codemie_tools` in the same direction as before, now only the standalone `reserved_paths` module, and `workspace_path_rules` no longer defines `RUNTIME_SDK_FILE_NAME` itself.
- `MAX_SKILL_NAME_LENGTH = 64` is defined once in `workspace_path_rules.py`; `models/skill.py` uses it for the four `max_length` values.
- `_normalize_write_path` keeps refusing reserved paths on writes, and a new delete normalizer refuses them on delete. Reads, listing, grep and download of workspace files are not filtered. One deliberate exception (decided in the 2026-10-09 review, CR-002): a conversation upload that is only a virtual workspace entry (not persisted) is hidden from listing, grep and script inputs when its name is reserved. It could never be registered in the workspace, and as a script input it would collide with the runtime\'s own file.

### 3.9 Shared seeding core (review round 4)

Blob storage and protection are shared by every seeding source; planning, limits, the trigger and the hint stay with each source (`SkillWorkspaceSeeder`, `skill_bundle_seed_policy`, `skill_workspace_preparation`, `skill_workspace_hint`).

- `service/file_service/blob_ref_rules.py` holds the blob naming rules: `content_addressed_key(path, content)`, the owner-prefix registry `_SOURCE_OWNER_PREFIXES` (`{"skill": "skill-"}`; stories 5 and 6 add assistant and workflow), `source_blob_owner` (the only place an owner string is built), `is_source_owned_blob[_ref]`, and `skill_blob_owner` as a thin wrapper.
- `AgentWorkspaceRepository.seed_references(workspace_id, source_kind, source_id, folder, files: list[SeedFileReference])` serves any registered source.
- `AgentWorkspaceService._is_registrable_blob_ref` (safe ref and not source-owned) is the one check for registering a blob. `sync_uploaded_files` and `_generated_file_path` skip refused refs with a warning; `_upsert_workspace_file_reference` raises `ValidationException` before reading the blob, so a future writer cannot link a source blob. The virtual conversation-upload view hides source-owned and unsafe refs (hiding, not registration).
- Behaviour change: a workflow run (`validate_run_files` / `register_run_files`) carrying a source-owned file token is refused with "File '<name>' is not available to this user" (HTTP 400) instead of losing the file silently. The UI sends only upload-response tokens owned by the user, so only hand-made API calls are affected.

## 4. Data flow

1. A chat or workflow assistant build calls `prepare_skill_workspace`.
2. The gate passes (script tool selected, switch on, jobs mode, conversation id). Otherwise nothing is seeded and no notice is produced.
3. The workspace is resolved, the assistant's skills are loaded in attachment order, and the seeder runs per skill: compute the plan from refs and limits, run the single transaction, collect notices.
4. Tools are built as before; the prompt builders append the notices.
5. Script runs read the referenced skill blobs through `get_workspace_input_files`; edits become workspace-owned blobs (copy-on-write).

## 5. Error handling

- Preparation never raises into the build; failures become a logged error and a notice.
- A transaction failure leaves no rows for that skill; the next request retries from scratch, because nothing is recorded.
- A missing blob at run time is skipped with a warning (3.7). A failed upload of a new skill blob version is not detected on Azure (the repository logs and swallows upload errors); this is pre-existing and noted as a risk.

## 6. Testing

- Repository: single-transaction seeding creates exactly one owner and the expected rows; a second call returns `ALREADY_SEEDED` and changes nothing; a second skill with the same folder returns `COLLISION`; a soft-deleted row at a target path is left untouched (tombstone); a pre-existing row is not overwritten; a foreign-key failure is not reported as a collision; two concurrent calls (real database, if the repository tests have a fixture; otherwise two sessions in a test) leave exactly one set of rows.
- Migration: upgrade and downgrade of `agent_workspace_folder_owners`, no `seed_entry_id` column, single alembic head.
- Skill service: content-addressed key and `checksum` recorded; same content gives the same key, changed content a new key with the old blob intact; legacy record still readable; hash-prefixed names round-trip on the filesystem repository.
- Policy: `SeedLimits.current()` and generic setting helper (bounds, bad values); plan carries its limits and the messages match them; legacy `inline_content` skip; the 943-character boundary.
- Seeder: order and first-wins, collision notice naming skill and folder, legacy notice, no blob read, no sleep or clock; unexpected error becomes a notice.
- Preparation step: gate cases (tool not selected, switch off, non-jobs mode, no conversation id, no skills); chat and workflow builders pass the notices into the prompt; workflow assistant nodes receive skill files; the three script-step sites do not seed.
- Workspace service: input files skip a missing blob with a warning; edit of a skill-owned row leaves the skill blob intact; delete of a skill-owned row only soft-deletes; reserved write and delete refused (including case and Unicode variants), reads allowed, batch write skips with a warning.
- Download rules: a skill-owned blob is allowed for a workspace owner who references it, refused otherwise, refused after the row is soft-deleted.
- Skill tool: imports nothing from the repository or the seeder (a test on module imports); the hint comes from the service.
- Removed or rewritten: `test_skill_workspace_seeder.py`, `test_skill_workspace_seeder_concurrency.py`, seed-method tests in `test_agent_workspace_repository.py`, `test_skill_workspace_seed_config.py`, `test_plan_bundle_seed.py` (moved with the policy), `test_job_bridge.py` cases for `is_bridge_path`, `script_tool_selected` cases in `test_toolkit_service.py` and `test_agent_workspace_toolkit.py`, and the migration test.

## 7. Phases (one MR, one commit each)

1. **Storage core**: migration rewrite and model rename, versioned `storage_key` and refs, `seed_skill_references`, the slim seeder, removal of the retry/lease/polling code and the three settings, missing-blob tolerance, download rule, and `SkillTool` through `skill_location_hint` (it moves here because the old tool imports the seed-entry model, status constants and seeder names that this phase removes).
2. **Policy and layering**: `skill_bundle_seed_policy.py` with `SeedLimits`, `skill_workspace_folder`, `models/skill.py` cleanup, Protocol for the seeder collaborator, single constants.
3. **Reserved paths**: `reserved_paths.py`, single definition, delete refusal, `job_bridge.is_bridge_path` removal.
4. **Preparation step and workflows**: `prepare_skill_workspace`, builder changes, notices as arguments, removal of `script_tool_selected` and `_seed_warnings`.
5. **Docs and test cleanup**: guide update (frozen file set, legacy skills, workflows, the three non-seeding sites), rollout notes, corrected spec/plan statements and MR description, remaining test rewrites.

## 8. Out of scope

- Showing files added to a skill after the first seeding; automatic conversion of legacy inline skills; cleaning up stored skill versions that are no longer referenced; changing the first-skill-wins rule.
- The codemie-ui upload-warning component; any non-skill seed source (the shared core in 3.9 and the plain `source_kind` column let one be added without a migration).
- Known gaps left for follow-ups: the workflow resume endpoint passes `file_names` without `validate_run_files`; the 1019-byte key budget counts characters while S3 counts UTF-8 bytes, and must be re-derived when a longer owner prefix is registered; `source_blob_owner` does not check the shape of `source_id` (ids are server-generated); an owner id that itself starts with a source prefix would be treated as source-owned.
- Hiding reserved files from reads or listings.

## 9. Documentation and rollout notes

- Rollout: the settings `SKILL_WORKSPACE_SEED_MAX_ATTEMPTS`, `SKILL_WORKSPACE_SEED_STALE_THRESHOLD_MINUTES` and `SKILL_WORKSPACE_SEED_WAIT_SECONDS` are removed; no environment or config file sets them. The migration is unmerged, so no environment has the old table.
- MR description and earlier spec/plan statements to correct: there is no `lock_workspace_for_seeding`, `has_seeded` or `record_seeded`; "Deviations: none known" is wrong; "first `FOR UPDATE` in the codebase" is wrong (`project_service.py:764`, `user_project_repository.py:212`, `mcp_config_service.py:641`, `settings_transfer_service.py:258`, `startup_recovery.py:66`, `conversation_analysis_queue_repository.py:134`); commit `327ecdda6` does not exist.
