# EPMCDME-15558 Skill Files by Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a skill's companion files available in an agent workspace by referencing versioned skill blobs in one database transaction, replacing the copy-based seeding already on the branch.

**Architecture:** Skill blobs are stored under content-addressed keys, so a workspace file row can point at `blob_owner="skill-<id>"`, `blob_name=<storage_key>` without copying. A slim seeder inserts one folder-owner row and the file rows (`ON CONFLICT DO NOTHING`) in one transaction. An explicit `ToolkitService.prepare_skill_workspace` step runs before `get_tools` in both agent builders and returns notices that are passed to the prompt builders as an argument.

**Tech Stack:** Python 3, FastAPI, SQLModel/SQLAlchemy 2 (PostgreSQL in production, in-memory SQLite in repository tests), Alembic, pytest, Poetry, Ruff.

**Spec:** `docs/superpowers/tasks/2026-10-06-epmcdme-15558-skill-scripts-workspace/spec.md` (approved). Research: `technical-analysis.md` section 8 in the same folder.

## Global Constraints

- Branch `EPMCDME-15558_skill-scripts-workspace` (HEAD `40fc9c99c` holds the copy implementation). Work in the current checkout; no worktree, no push, no MR. The only git operations are the five phase commits.
- Commit message format: `EPMCDME-15558: <description>`, then a blank line, `Generated with AI`, a blank line, `Co-Authored-By: codemie-ai <codemie.ai@gmail.com>`.
- Do NOT create a new Alembic migration. Rewrite `src/external/alembic/versions/y1z2a3b4c5d6_add_agent_workspace_seed_entries.py` in place (keep `revision = "y1z2a3b4c5d6"`, `down_revision = "f88ad134467d"`) and rename the file to `y1z2a3b4c5d6_add_agent_workspace_folder_owners.py` with `git mv`.
- New Python files start with the Apache 2026 EPAM license header (copy lines 1-13 of `src/codemie/service/file_service/workspace_path_rules.py`).
- Layering: tools call services, services call repositories (`.ai-run/guides/architecture/layered-architecture.md`). Tests mirror `src/` under `tests/codemie/...` or `tests/codemie_tools/...` and mock provider boundaries.
- Settings kept: `SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES` (10 MB), `SKILL_WORKSPACE_SEED_MAX_FILE_COUNT` (100), `SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES` (50 MB). Removed: `..._MAX_ATTEMPTS`, `..._STALE_THRESHOLD_MINUTES`, `..._WAIT_SECONDS`.
- Skill blob key: `<sha256(content).hexdigest()[:16]>/<normalized_path>` in owner `skill-<skill_id>`; the record also stores the full digest as `checksum`. Workspace file rows for skill files use `blob_owner="skill-<skill_id>"`, `blob_name=<storage_key>`, `version=1`, `checksum=<record checksum or "">`.
- Folder-owner table: `agent_workspace_folder_owners` (model `AgentWorkspaceFolderOwner`), columns `id, date, update_date, workspace_id, source_kind, source_id, folder`, unique `(workspace_id, source_kind, source_id)` and `(workspace_id, folder)`, FK `workspace_id -> agent_workspaces.id ON DELETE CASCADE`.
- Reserved paths: root-only (`.codemie_bridge/...` and root `codemie_runtime_sdk.py`), compared after separator normalization, NFKC and casefold; writes and deletes refused (single operation: `ValidationException`; batch write: skip with warning); reads unrestricted.

### Commands (used by every phase)

Load `.ai-run/guides/quality-gates.md`, `.ai-run/guides/development/setup-guide.md` and `.ai-run/guides/testing/testing-patterns.md` before the first command. Run from the repo root (`/Users/yevhen_slyva/codemie-dev/codemie`).

| Need | Command |
|---|---|
| One test file or test | `poetry run pytest <path>[::<name>] -q` |
| Several files | `poetry run pytest <path1> <path2> -q` |
| Format + lint (also the Stop hook) | `make ruff` |
| License headers | `make license-check` (fix: `make license-fix`) |
| Full suite (end of phase 5 only) | `poetry run pytest tests/ -q` |
| Alembic single head | `poetry run pytest tests/codemie/migrations -q` |
| Commit | `git add <files> && git commit -m "$(cat <<'EOF' ... EOF)"` with the message format above |

If a commit hook fails because tooling is missing (for example Docker for `sonar-local`), stop and report the exact error. Do not use `--no-verify`.

## Review Focus

Behaviors the spec implies that no single task's happy-path tests would catch; each has a test in the named task.

1. A skill whose files are all skipped (legacy inline, or every file over a limit) writes no folder-owner row, so its notice repeats on every turn and a later fix is not frozen out. (Task 1.5)
2. A skill detached and attached again, or renamed after seeding, hits `ALREADY_SEEDED`: no duplicate rows, files stay under the original folder, and the hint reads the folder from the owner row. (Tasks 1.4, 1.8)
3. A workspace deleted while seeding raises a foreign-key error that is reported as a notice, never as a name collision. (Task 1.4)
4. Look-alike reserved names (`.CODEMIE_BRIDGE/x`, fullwidth `．codemie_bridge/x`, `./a/../.codemie_bridge/x`, `Codemie_Runtime_SDK.py` at the root) are refused; the same names inside `skills/foo/` are allowed. (Task 3.2)
5. A skill-owned blob whose workspace row was soft-deleted, or that belongs to another user's workspace, is not downloadable; a missing skill blob does not abort a script run. (Tasks 1.6, 1.7)

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/external/alembic/versions/y1z2a3b4c5d6_add_agent_workspace_folder_owners.py` | rename + rewrite | creates `agent_workspace_folder_owners` only |
| `src/codemie/rest_api/models/agent_workspace.py` | modify | `AgentWorkspaceFolderOwner`, `SOURCE_KIND_SKILL`; drop `AgentWorkspaceSeedEntry`, `SEED_STATUS_*`, `seed_entry_id`; `ExecuteWorkspaceScriptResponse.warnings` |
| `src/codemie/repository/agent_workspace_repository.py` | modify | `seed_skill_references`, `get_folder_owner`, `user_references_blob`, `SeedOutcome`, `SkillFileReference`; remove all claim/lease/seed-entry methods |
| `src/codemie/service/skill_file_storage.py` | modify | `versioned_key` |
| `src/codemie/service/skill_service.py` | modify | versioned uploads, blob-reference comments, `bundle_warning` at the three detail-response sites |
| `src/codemie/rest_api/models/skill.py` | modify | `SkillCompanionFileRef`, `get_companion_file_refs`; later loses all policy |
| `src/codemie/service/skill_workspace_seeder.py` | rewrite | slim seeder and `SkillSeedStore` Protocol |
| `src/codemie/service/skill_bundle_seed_policy.py` | create (phase 2) | `SeedLimits`, plan, messages, `compute_bundle_warning` |
| `src/codemie/service/file_service/workspace_path_rules.py` | modify | `skill_workspace_folder`, `MAX_SKILL_NAME_LENGTH`, delegate reserved check |
| `src/codemie_tools/data_management/code_executor/reserved_paths.py` | create (phase 3) | the single `is_reserved_path` |
| `src/codemie/service/agent_workspace_service.py` | modify | `WorkspaceInputFiles`, guarded reads, `skill_location_hint`, delete refusal, remove `seed_file_if_absent` |
| `src/codemie/service/file_service/download_rules.py` | modify | skill-blob rule |
| `src/codemie/agents/tools/skill/skill_tool.py` | modify | use `skill_location_hint`; drop repository/seeder imports |
| `src/codemie/service/tools/toolkit_service.py` | modify | `prepare_skill_workspace`; remove `selected_toolkits` plumbing |
| `src/codemie/service/tools/toolkit_settings_service.py` | modify | drop `script_tool_selected` |
| `src/codemie_tools/data_management/workspace/toolkit.py` | modify | remove seeding hook and field |
| `src/codemie/service/assistant_service.py` | modify | call prepare step; notices argument |
| `src/codemie/core/models.py` | modify | remove `_seed_warnings` |
| `docs/skill-files-in-workspace.md` | create (phase 5) | behavior, limits, frozen file set, legacy skills, workflows |

---

# Phase 1 — Storage core

### Task 1.1: Baseline

**Files:** none changed.

- [ ] **Step 1: Run the phase scope on the untouched branch**

Run: `poetry run pytest tests/codemie/repository/test_agent_workspace_repository.py tests/codemie/service/test_skill_workspace_seeder.py tests/codemie/service/test_skill_workspace_seeder_concurrency.py tests/codemie/service/test_skill_service.py tests/codemie/service/test_skill_file_storage.py tests/codemie/service/test_agent_workspace_service_tool_calling.py tests/codemie/service/file_service tests/codemie/migrations tests/codemie/agents/tools/skill -q`
Expected: all pass. Note any pre-existing failure and do not fix it here.

### Task 1.2: Folder-owner model and rewritten migration

**Files:**
- Rename: `src/external/alembic/versions/y1z2a3b4c5d6_add_agent_workspace_seed_entries.py` -> `..._add_agent_workspace_folder_owners.py`
- Modify: `src/codemie/rest_api/models/agent_workspace.py`
- Rename + rewrite: `tests/codemie/migrations/test_add_agent_workspace_seed_entries.py` -> `test_add_agent_workspace_folder_owners.py`

**Interfaces:**
- Produces: `AgentWorkspaceFolderOwner` (SQLModel table), `SOURCE_KIND_SKILL = "skill"`. `AgentWorkspaceFile` no longer has `seed_entry_id`.

Test-first: yes - the renamed migration test asserts `upgrade()` creates `agent_workspace_folder_owners` with exactly the columns and the two unique constraints and touches no other table; it fails because the module and table do not exist yet.

- [ ] **Step 1: Rename the files**

```bash
git mv src/external/alembic/versions/y1z2a3b4c5d6_add_agent_workspace_seed_entries.py src/external/alembic/versions/y1z2a3b4c5d6_add_agent_workspace_folder_owners.py
git mv tests/codemie/migrations/test_add_agent_workspace_seed_entries.py tests/codemie/migrations/test_add_agent_workspace_folder_owners.py
```

- [ ] **Step 2: Rewrite the migration test (failing)**

Keep the `_OpRecorder` helper and the module-loading code already in the test file. Replace the assertions with:

```python
def test_upgrade_creates_only_the_folder_owner_table():
    recorder = _run_upgrade()  # existing helper that patches `op` and calls upgrade()
    created = [c for c in recorder.calls if c[0] == "create_table"]
    assert [c[1][0] for c in created] == ["agent_workspace_folder_owners"]
    column_names = {col.name for col in created[0][1][1] if isinstance(col, sa.Column)}
    assert column_names == {"id", "date", "update_date", "workspace_id", "source_kind", "source_id", "folder"}
    unique = {(c[1][0], c[1][2]) for c in recorder.calls if c[0] == "create_unique_constraint"}
    assert unique == {
        ("uq_agent_workspace_folder_owners_workspace_source", ("workspace_id", "source_kind", "source_id")),
        ("uq_agent_workspace_folder_owners_workspace_folder", ("workspace_id", "folder")),
    }
    assert not [c for c in recorder.calls if c[0] in {"add_column", "drop_column"}]


def test_downgrade_drops_the_table():
    recorder = _run_downgrade()
    assert ("drop_table", "agent_workspace_folder_owners") in recorder.calls
```

Adapt `_run_upgrade/_run_downgrade` to the file's existing helper names and update the module path in `importlib.import_module` to `...y1z2a3b4c5d6_add_agent_workspace_folder_owners`.

- [ ] **Step 3: Run it to see it fail**

Run: `poetry run pytest tests/codemie/migrations/test_add_agent_workspace_folder_owners.py -q`
Expected: FAIL (old table name / extra columns).

- [ ] **Step 4: Rewrite the migration**

Docstring `add agent_workspace_folder_owners`. `upgrade()`:

```python
def upgrade() -> None:
    op.create_table(
        "agent_workspace_folder_owners",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("date", sa.DateTime(), nullable=True),
        sa.Column("update_date", sa.DateTime(), nullable=True),
        sa.Column("workspace_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("folder", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["agent_workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_workspace_folder_owners_workspace_id"),
        "agent_workspace_folder_owners",
        ["workspace_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_agent_workspace_folder_owners_workspace_source",
        "agent_workspace_folder_owners",
        ["workspace_id", "source_kind", "source_id"],
    )
    op.create_unique_constraint(
        "uq_agent_workspace_folder_owners_workspace_folder",
        "agent_workspace_folder_owners",
        ["workspace_id", "folder"],
    )


def downgrade() -> None:
    op.drop_table("agent_workspace_folder_owners")
```

Keep the license header, imports, `revision`, `down_revision`, `branch_labels`, `depends_on` unchanged. Remove the `add_column`/`seed_entry_id` parts.

- [ ] **Step 5: Update the model**

In `agent_workspace.py` delete `SEED_STATUS_*`, `AgentWorkspaceSeedEntry`, and the `seed_entry_id` field (and the now-unused `Column/ForeignKey/String` imports only if nothing else uses them). Add:

```python
SOURCE_KIND_SKILL = "skill"


class AgentWorkspaceFolderOwner(BaseModelWithSQLSupport, table=True):
    """Records which source (a skill) owns a folder of a workspace, so a folder is seeded once and by one source."""

    __tablename__ = "agent_workspace_folder_owners"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "source_kind", "source_id", name="uq_agent_workspace_folder_owners_workspace_source"
        ),
        UniqueConstraint("workspace_id", "folder", name="uq_agent_workspace_folder_owners_workspace_folder"),
    )

    workspace_id: str = SQLField(
        sa_column=Column(
            String, ForeignKey("agent_workspaces.id", ondelete="CASCADE"), index=True, nullable=False
        )
    )
    source_kind: str
    source_id: str
    folder: str
```

- [ ] **Step 6: Run the migration test and check the single head**

Run: `poetry run pytest tests/codemie/migrations -q`
Expected: PASS (the credentialtypes test asserts a single alembic head).

Do not commit yet; the phase commit is Task 1.9.

### Task 1.3: Versioned skill blobs and file references

**Files:**
- Modify: `src/codemie/service/skill_file_storage.py`, `src/codemie/service/skill_service.py`, `src/codemie/rest_api/models/skill.py`
- Test: `tests/codemie/service/test_skill_file_storage.py`, `tests/codemie/service/test_skill_service.py`, `tests/codemie/rest_api/models/` (add `test_skill_companion_file_refs.py`)

**Interfaces:**
- Produces: `SkillFileStorage.versioned_key(normalized_path: str, content: bytes) -> tuple[str, str]` returning `(storage_key, full_sha256_hex)`; `SkillCompanionFileRef(SkillCompanionFileMetadata)` with `storage_key: str | None = None`, `checksum: str = ""`; `Skill.get_companion_file_refs() -> list[SkillCompanionFileRef]`.

Test-first: yes - `versioned_key("scripts/run.py", b"x")` returns `("<16 hex>/scripts/run.py", <64 hex>)`; uploading the same content twice yields the same `storage_key`, changed content a different one while the first blob still reads back; both fail because none of this exists.

- [ ] **Step 1: Write the failing tests**

In `test_skill_file_storage.py`:

```python
import hashlib


def test_versioned_key_is_content_addressed():
    key, digest = SkillFileStorage.versioned_key("scripts/run.py", b"print(1)")
    assert digest == hashlib.sha256(b"print(1)").hexdigest()
    assert key == f"{digest[:16]}/scripts/run.py"
    assert SkillFileStorage.versioned_key("scripts/run.py", b"print(1)")[0] == key
    assert SkillFileStorage.versioned_key("scripts/run.py", b"print(2)")[0] != key
```

In `test_skill_service.py` (follow the style of the existing `_upload_companion_files` tests; find them with `grep -n "_upload_companion_files" tests/codemie/service/test_skill_service.py`):

```python
def test_upload_companion_files_writes_a_new_version_per_content():
    repo = InMemoryFileRepository()  # reuse the fake/MagicMock the neighbouring tests use
    first = SkillService._upload_companion_files("s1", [_payload("a.py", "one")], repo)
    again = SkillService._upload_companion_files("s1", [_payload("a.py", "one")], repo)
    changed = SkillService._upload_companion_files("s1", [_payload("a.py", "two")], repo)
    assert first[0]["storage_key"] == again[0]["storage_key"]
    assert changed[0]["storage_key"] != first[0]["storage_key"]
    assert first[0]["checksum"] == hashlib.sha256(b"one").hexdigest()
    # the first version is still stored
    assert repo.read_file(file_name=first[0]["storage_key"], owner="skill-s1", mime_type=None).content == b"one"
```

If the neighbouring tests use a `MagicMock` repository, assert `write_file` was called with `name=first[0]["storage_key"]` instead of reading back. Update any existing assertion `storage_key == path` to expect `"<16 hex>/" + path` (find with `grep -rn '"storage_key"' tests`).

In `tests/codemie/rest_api/models/test_skill_companion_file_refs.py`:

```python
from codemie.rest_api.models.skill import Skill


def test_refs_expose_storage_key_and_checksum_and_never_content():
    skill = Skill(
        name="s", description="d", content="c" * 100, project="p",
        companion_files=[
            {"path": "a.py", "mime_type": "text/x-python", "encoding": "text", "size_bytes": 3,
             "storage_key": "ab12/a.py", "checksum": "ff"},
            {"path": "old.md", "mime_type": "text/markdown", "encoding": "text", "size_bytes": 2, "content": "hi"},
        ],
    )
    refs = skill.get_companion_file_refs()
    assert (refs[0].storage_key, refs[0].checksum) == ("ab12/a.py", "ff")
    assert (refs[1].storage_key, refs[1].checksum) == (None, "")
    assert not hasattr(refs[1], "content")
```

(Adjust the `Skill(...)` constructor arguments to whatever required fields the model has; copy from an existing skill model test.)

- [ ] **Step 2: Run them to see them fail**

Run: `poetry run pytest tests/codemie/service/test_skill_file_storage.py tests/codemie/service/test_skill_service.py tests/codemie/rest_api/models/test_skill_companion_file_refs.py -q`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement**

`skill_file_storage.py`: add `import hashlib` and

```python
    @staticmethod
    def versioned_key(normalized_path: str, content: bytes) -> tuple[str, str]:
        """Return (storage key, full sha-256 hex) for a blob; the key changes whenever the content does."""
        digest = hashlib.sha256(content).hexdigest()
        return f"{digest[:16]}/{normalized_path}", digest
```

`skill_service.py` `_upload_companion_files`: replace the write and record with

```python
            storage_key, checksum = SkillFileStorage.versioned_key(normalized_path, raw_content)
            storage.write_file(name=storage_key, mime_type=mime_type, content=raw_content)
            records.append(
                {
                    "path": normalized_path,
                    "mime_type": mime_type,
                    "encoding": file_payload.encoding,
                    "size_bytes": len(raw_content),
                    "storage_key": storage_key,
                    "checksum": checksum,
                }
            )
```

Add these comments (exact intent, keep short):
- above `update_skill`'s `_upload_companion_files` call: `# Skill blobs are versioned by content and may be referenced by agent workspaces; they must never be overwritten or deleted without a reference check.`
- inside `delete_skill`, after the access check: `# Skill blobs are not deleted here on purpose: workspaces may reference them. Add a reference check before ever deleting them.`

`models/skill.py`: after `SkillCompanionFileMetadata` add

```python
class SkillCompanionFileRef(SkillCompanionFileMetadata):
    """Companion file metadata plus where its bytes live; never exposed by the API."""

    storage_key: str | None = Field(default=None, description="Blob key; None for legacy inline files")
    checksum: str = Field(default="", description="sha-256 of the content when known")
```

and on `Skill`, next to `get_companion_file_metadata`:

```python
    def get_companion_file_refs(self) -> list[SkillCompanionFileRef]:
        """Companion file metadata with blob keys, for server-side use (workspace seeding)."""
        return [
            SkillCompanionFileRef.model_validate({k: v for k, v in file_data.items() if k != "content"})
            for file_data in self.companion_files or []
        ]
```

- [ ] **Step 4: Run the tests again**

Run: same command as Step 2. Expected: PASS.

- [ ] **Step 5: Add a filesystem round-trip test for hash-prefixed names**

In the filesystem repository test file (`tests/codemie/repository/test_file_system_file_repository.py`) add a test writing `name="ab12cd34ef56ab12/scripts/run.py"` under owner `skill-s1` and reading it back; run it. Expected: PASS without source changes (`file_system_repository._resolve_safe_path` allows subdirectories).

### Task 1.4: Repository - single-transaction seeding

**Files:**
- Modify: `src/codemie/repository/agent_workspace_repository.py`
- Test: `tests/codemie/repository/test_agent_workspace_repository.py` (replace the seed sections)

**Interfaces:**
- Consumes: `AgentWorkspaceFolderOwner`, `AgentWorkspaceFile`, `SOURCE_KIND_SKILL` (Task 1.2).
- Produces:
  - `class SeedOutcome(str, Enum): SEEDED = "seeded"; ALREADY_SEEDED = "already_seeded"; COLLISION = "collision"`
  - `@dataclass(frozen=True) class SkillFileReference: path: str; blob_name: str; mime_type: str; checksum: str; size: int` (`path` is the full workspace path)
  - `AgentWorkspaceRepository.seed_skill_references(self, workspace_id: str, source_id: str, folder: str, files: list[SkillFileReference]) -> SeedOutcome`
  - `AgentWorkspaceRepository.get_folder_owner(self, workspace_id: str, source_kind: str, source_id: str) -> AgentWorkspaceFolderOwner | None`
  - `AgentWorkspaceRepository.user_references_blob(self, user_id: str, blob_owner: str, blob_name: str) -> bool`

Test-first: yes - the new `TestSeedSkillReferences` tests fail because the methods do not exist; they cover seeding, idempotence, collision, tombstones, atomicity, concurrency and the foreign-key case.

- [ ] **Step 1: Replace the fixtures**

In the test file, import `AgentWorkspaceFolderOwner` instead of `AgentWorkspaceSeedEntry`, patch `get_engine` for it in the `repository` fixture, add it to `create_all(tables=[...])`, and after `create_all` create the production uniqueness that the model does not declare:

```python
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_agent_workspace_files_workspace_path "
            "ON agent_workspace_files (workspace_id, path)"
        )
```

Delete the classes `TestCreateFileIfAbsent`, `TestSeedLedger`, `TestFindSeedEntryByFolder` and the test `test_delete_by_execution_id_removes_seed_entries_too` (rewrite it in Step 5). Keep the other tests.

- [ ] **Step 2: Write the failing tests**

```python
def _workspace(session, workspace_id="w1", user_id="u1"):
    session.add(AgentWorkspace(id=workspace_id, conversation_id="c-" + workspace_id, user_id=user_id))
    session.commit()


def _ref(path, blob="ab12/a.py", size=3):
    return SkillFileReference(path=path, blob_name=blob, mime_type="text/x-python", checksum="ff", size=size)


class TestSeedSkillReferences:
    def test_creates_owner_and_rows_pointing_at_the_skill_blobs(self, repository, sqlite_engine):
        with Session(sqlite_engine) as s:
            _workspace(s)
        outcome = repository.seed_skill_references("w1", "sk1", "skills/foo", [_ref("skills/foo/a.py")])
        assert outcome is SeedOutcome.SEEDED
        with Session(sqlite_engine) as s:
            owners = s.exec(select(AgentWorkspaceFolderOwner)).all()
            rows = s.exec(select(AgentWorkspaceFile)).all()
        assert [(o.source_kind, o.source_id, o.folder) for o in owners] == [("skill", "sk1", "skills/foo")]
        assert [(r.path, r.blob_owner, r.blob_name, r.version, r.deleted_at) for r in rows] == [
            ("skills/foo/a.py", "skill-sk1", "ab12/a.py", 1, None)
        ]

    def test_second_call_for_the_same_skill_changes_nothing(self, repository, sqlite_engine):
        with Session(sqlite_engine) as s:
            _workspace(s)
        repository.seed_skill_references("w1", "sk1", "skills/foo", [_ref("skills/foo/a.py")])
        outcome = repository.seed_skill_references(
            "w1", "sk1", "skills/foo", [_ref("skills/foo/a.py"), _ref("skills/foo/new.py")]
        )
        assert outcome is SeedOutcome.ALREADY_SEEDED
        with Session(sqlite_engine) as s:
            assert [r.path for r in s.exec(select(AgentWorkspaceFile)).all()] == ["skills/foo/a.py"]

    def test_other_skill_with_the_same_folder_is_a_collision_and_writes_nothing(self, repository, sqlite_engine):
        with Session(sqlite_engine) as s:
            _workspace(s)
        repository.seed_skill_references("w1", "sk1", "skills/foo", [_ref("skills/foo/a.py")])
        outcome = repository.seed_skill_references("w1", "sk2", "skills/foo", [_ref("skills/foo/b.py")])
        assert outcome is SeedOutcome.COLLISION
        with Session(sqlite_engine) as s:
            assert len(s.exec(select(AgentWorkspaceFolderOwner)).all()) == 1
            assert [r.path for r in s.exec(select(AgentWorkspaceFile)).all()] == ["skills/foo/a.py"]

    def test_live_and_soft_deleted_rows_are_never_touched(self, repository, sqlite_engine):
        with Session(sqlite_engine) as s:
            _workspace(s)
            now = datetime(2026, 1, 1)
            s.add(AgentWorkspaceFile(id="f1", workspace_id="w1", path="skills/foo/user.py", blob_owner="workspace-w1",
                                     blob_name="user.py", mime_type="text/plain", checksum="u", size=9, version=4))
            s.add(AgentWorkspaceFile(id="f2", workspace_id="w1", path="skills/foo/gone.py", blob_owner="workspace-w1",
                                     blob_name="gone.py", mime_type="text/plain", checksum="g", size=1, deleted_at=now))
            s.commit()
        repository.seed_skill_references(
            "w1", "sk1", "skills/foo", [_ref("skills/foo/user.py"), _ref("skills/foo/gone.py"), _ref("skills/foo/ok.py")]
        )
        with Session(sqlite_engine) as s:
            rows = {r.path: r for r in s.exec(select(AgentWorkspaceFile)).all()}
        assert rows["skills/foo/user.py"].blob_owner == "workspace-w1" and rows["skills/foo/user.py"].version == 4
        assert rows["skills/foo/gone.py"].deleted_at is not None and rows["skills/foo/gone.py"].blob_owner == "workspace-w1"
        assert rows["skills/foo/ok.py"].blob_owner == "skill-sk1"

    def test_a_failure_before_commit_leaves_nothing_behind(self, repository, sqlite_engine):
        with Session(sqlite_engine) as s:
            _workspace(s)
        with patch.object(Session, "commit", side_effect=RuntimeError("crash")):
            with pytest.raises(RuntimeError):
                repository.seed_skill_references("w1", "sk1", "skills/foo", [_ref("skills/foo/a.py")])
        with Session(sqlite_engine) as s:
            assert s.exec(select(AgentWorkspaceFolderOwner)).all() == []
            assert s.exec(select(AgentWorkspaceFile)).all() == []
        assert repository.seed_skill_references("w1", "sk1", "skills/foo", [_ref("skills/foo/a.py")]) is SeedOutcome.SEEDED

    def test_seeding_with_no_files_still_records_the_owner(self, repository, sqlite_engine):
        with Session(sqlite_engine) as s:
            _workspace(s)
        assert repository.seed_skill_references("w1", "sk1", "skills/foo", []) is SeedOutcome.SEEDED
        assert repository.get_folder_owner("w1", "skill", "sk1").folder == "skills/foo"

    def test_get_folder_owner_is_none_when_absent(self, repository):
        assert repository.get_folder_owner("w1", "skill", "nope") is None


def test_concurrent_seeding_leaves_exactly_one_set_of_rows(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ws.db'}", connect_args={"timeout": 30})
    SQLModel.metadata.create_all(
        engine, tables=[AgentWorkspace.__table__, AgentWorkspaceFile.__table__, AgentWorkspaceFolderOwner.__table__]
    )
    with engine.begin() as c:
        c.exec_driver_sql("CREATE UNIQUE INDEX uq_files ON agent_workspace_files (workspace_id, path)")
    with Session(engine) as s:
        _workspace(s)
    results: list[SeedOutcome] = []
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        results.append(
            AgentWorkspaceRepository().seed_skill_references(
                "w1", "sk1", "skills/foo", [_ref("skills/foo/a.py"), _ref("skills/foo/b.py")]
            )
        )

    with (
        patch.object(AgentWorkspace, "get_engine", return_value=engine),
        patch.object(AgentWorkspaceFile, "get_engine", return_value=engine),
        patch.object(AgentWorkspaceFolderOwner, "get_engine", return_value=engine),
    ):
        threads = [threading.Thread(target=worker) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
    assert results.count(SeedOutcome.SEEDED) == 1
    assert results.count(SeedOutcome.ALREADY_SEEDED) == 3
    with Session(engine) as s:
        assert len(s.exec(select(AgentWorkspaceFile)).all()) == 2
        assert len(s.exec(select(AgentWorkspaceFolderOwner)).all()) == 1


def test_a_database_error_is_raised_not_reported_as_a_collision(repository, sqlite_engine):
    # In production a workspace deleted mid-seeding makes the owner insert fail with a foreign-key error.
    with Session(sqlite_engine) as s:
        _workspace(s)
    with patch.object(Session, "execute", side_effect=IntegrityError("insert", {}, Exception("fk"))):
        with pytest.raises(IntegrityError):
            repository.seed_skill_references("w1", "sk1", "skills/foo", [_ref("skills/foo/a.py")])


class TestUserReferencesBlob:
    def test_true_only_for_a_live_row_in_a_workspace_of_that_user(self, repository, sqlite_engine):
        with Session(sqlite_engine) as s:
            _workspace(s, "w1", "u1")
            _workspace(s, "w2", "u2")
        repository.seed_skill_references("w1", "sk1", "skills/foo", [_ref("skills/foo/a.py", blob="ab12/a.py")])
        assert repository.user_references_blob("u1", "skill-sk1", "ab12/a.py") is True
        assert repository.user_references_blob("u2", "skill-sk1", "ab12/a.py") is False
        assert repository.soft_delete_file("w1", "skills/foo/a.py") is True
        assert repository.user_references_blob("u1", "skill-sk1", "ab12/a.py") is False
```

Add the imports the tests use (`threading`, `IntegrityError` from `sqlalchemy.exc`, `datetime`).

- [ ] **Step 3: Run them to see them fail**

Run: `poetry run pytest tests/codemie/repository/test_agent_workspace_repository.py -q`
Expected: FAIL (missing names).

- [ ] **Step 4: Implement the repository changes**

At the top of `agent_workspace_repository.py` replace the seed imports with:

```python
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.dialects import postgresql, sqlite

from codemie.rest_api.models.agent_workspace import (
    SOURCE_KIND_SKILL,
    AgentWorkspace,
    AgentWorkspaceFile,
    AgentWorkspaceFolderOwner,
)


class SeedOutcome(str, Enum):
    SEEDED = "seeded"
    ALREADY_SEEDED = "already_seeded"
    COLLISION = "collision"


@dataclass(frozen=True)
class SkillFileReference:
    """A workspace file that points at a skill blob instead of holding its own copy; ``path`` is the workspace path."""

    path: str
    blob_name: str
    mime_type: str
    checksum: str
    size: int


def _insert(session: Session, table):
    """INSERT supporting ON CONFLICT DO NOTHING on PostgreSQL (production) and SQLite (tests)."""
    dialect_insert = postgresql.insert if session.get_bind().dialect.name == "postgresql" else sqlite.insert
    return dialect_insert(table)
```

Remove the now unused imports (`timedelta`, `update`, `IntegrityError`, `SEED_STATUS_*`) after deleting the old methods. Delete: `create_file_if_absent`, `get_seed_entry`, `claim_seed_entry`, `update_seed_entry_status`, `reclaim_stale_seed_entry`, `reclaim_partial_seed_entry`, `_reclaim_seed_entry`, `record_collision`, `find_seed_entry_by_folder`. Add:

```python
    def seed_skill_references(
        self, workspace_id: str, source_id: str, folder: str, files: list[SkillFileReference]
    ) -> SeedOutcome:
        """Record ``source_id`` as owner of ``folder`` and add rows pointing at its blobs, in one transaction.

        Nothing is committed unless every statement succeeds, so an interrupted call leaves no state behind.
        Existing rows (live or soft-deleted) at a target path are never touched.
        """
        now = _utcnow()
        with Session(AgentWorkspaceFolderOwner.get_engine()) as session:
            claimed = session.execute(
                _insert(session, AgentWorkspaceFolderOwner.__table__)
                .values(
                    id=str(uuid4()),
                    date=now,
                    update_date=now,
                    workspace_id=workspace_id,
                    source_kind=SOURCE_KIND_SKILL,
                    source_id=source_id,
                    folder=folder,
                )
                .on_conflict_do_nothing()
            )
            if claimed.rowcount == 0:
                session.rollback()
                same_skill = session.exec(
                    select(AgentWorkspaceFolderOwner.id).where(
                        AgentWorkspaceFolderOwner.workspace_id == workspace_id,
                        AgentWorkspaceFolderOwner.source_kind == SOURCE_KIND_SKILL,
                        AgentWorkspaceFolderOwner.source_id == source_id,
                    )
                ).first()
                return SeedOutcome.ALREADY_SEEDED if same_skill is not None else SeedOutcome.COLLISION

            if files:
                rows = [
                    {
                        "id": str(uuid4()),
                        "date": now,
                        "update_date": now,
                        "workspace_id": workspace_id,
                        "path": reference.path,
                        "blob_owner": f"skill-{source_id}",
                        "blob_name": reference.blob_name,
                        "mime_type": reference.mime_type,
                        "checksum": reference.checksum,
                        "size": reference.size,
                        "version": 1,
                        "deleted_at": None,
                    }
                    for reference in files
                ]
                session.execute(_insert(session, AgentWorkspaceFile.__table__).on_conflict_do_nothing(), rows)
            session.commit()
            return SeedOutcome.SEEDED

    def get_folder_owner(
        self, workspace_id: str, source_kind: str, source_id: str
    ) -> Optional[AgentWorkspaceFolderOwner]:
        with Session(AgentWorkspaceFolderOwner.get_engine()) as session:
            return session.exec(
                select(AgentWorkspaceFolderOwner).where(
                    AgentWorkspaceFolderOwner.workspace_id == workspace_id,
                    AgentWorkspaceFolderOwner.source_kind == source_kind,
                    AgentWorkspaceFolderOwner.source_id == source_id,
                )
            ).first()

    def user_references_blob(self, user_id: str, blob_owner: str, blob_name: str) -> bool:
        """True when a live file row in a workspace owned by ``user_id`` points at this blob."""
        with Session(AgentWorkspaceFile.get_engine()) as session:
            statement = (
                select(AgentWorkspaceFile.id)
                .join(AgentWorkspace, AgentWorkspace.id == AgentWorkspaceFile.workspace_id)
                .where(
                    AgentWorkspace.user_id == user_id,
                    AgentWorkspaceFile.blob_owner == blob_owner,
                    AgentWorkspaceFile.blob_name == blob_name,
                    AgentWorkspaceFile.deleted_at.is_(None),
                )
                .limit(1)
            )
            return session.exec(statement).first() is not None
```

`delete_by_execution_id`: remove the `session.exec(delete(AgentWorkspaceSeedEntry)...)` call (the FK cascade removes owner rows) and update the docstring; keep the file-row delete.

- [ ] **Step 5: Rewrite the delete test**

Replace `test_delete_by_execution_id_removes_seed_entries_too` with a version that seeds a folder owner via `seed_skill_references`, calls `delete_by_execution_id`, and asserts the workspace and file rows are gone. SQLite does not cascade without `PRAGMA foreign_keys=ON`; assert only the workspace and file rows, and add a comment that the owner row is removed by the production FK cascade (covered by the migration test).

- [ ] **Step 6: Run the repository tests**

Run: `poetry run pytest tests/codemie/repository/test_agent_workspace_repository.py -q`
Expected: PASS.

### Task 1.5: Slim seeder and temporary hook adaptation

**Files:**
- Rewrite: `src/codemie/service/skill_workspace_seeder.py`
- Modify: `src/codemie/rest_api/models/skill.py` (plan reason, drop three settings), `src/codemie_tools/data_management/workspace/toolkit.py` (adapt the hook), `src/codemie/service/agent_workspace_service.py` (remove `seed_file_if_absent`)
- Rewrite: `tests/codemie/service/test_skill_workspace_seeder.py`; delete `tests/codemie/service/test_skill_workspace_seeder_concurrency.py`; adapt `tests/codemie_tools/data_management/workspace/test_agent_workspace_toolkit.py`; edit `tests/codemie/rest_api/models/test_skill_workspace_seed_config.py`

**Interfaces:**
- Consumes: `SeedOutcome`, `SkillFileReference` (Task 1.4); `Skill.get_companion_file_refs()` (Task 1.3); `plan_bundle_seed`, `bundle_seed_skip_messages`, the three kept setting getters (still in `models/skill.py` until Task 2.3).
- Produces:
  - `class SkillSeedStore(Protocol): def seed_skill_references(self, workspace_id: str, source_id: str, folder: str, files: list[SkillFileReference]) -> SeedOutcome: ...`
  - `class SkillWorkspaceSeeder: def __init__(self, store: SkillSeedStore); def ensure_seeded(self, workspace_id: str, skills: list[Skill]) -> list[str]`
  - `SEED_SKIP_INLINE = "inline_content"` in `models/skill.py`.

Test-first: yes - the rewritten seeder tests fail on the new constructor and signature; they cover order and first-wins, collision notice, legacy notice with no marker written, over-limit notices on first seeding only, and error isolation.

- [ ] **Step 1: Write the failing seeder tests**

Replace the file with a `FakeStore`:

```python
class FakeStore:
    def __init__(self, outcomes=None):
        self.calls = []
        self._outcomes = outcomes or {}

    def seed_skill_references(self, workspace_id, source_id, folder, files):
        self.calls.append((workspace_id, source_id, folder, list(files)))
        return self._outcomes.get(source_id, SeedOutcome.SEEDED)


def _skill(skill_id, name, files):
    return Skill(id=skill_id, name=name, description="d", content="c" * 100, project="p", companion_files=files)


def _file(path, key="ab12/x", size=3, **extra):
    return {"path": path, "mime_type": "text/plain", "encoding": "text", "size_bytes": size,
            "storage_key": key, "checksum": "ff", **extra}
```

Tests (each one assertion-focused):
- `test_seeds_references_under_the_skill_folder`: one skill `foo` with file `a.py` key `ab12/a.py`; assert one store call with `folder == "skills/foo"`, file `path == "skills/foo/a.py"`, `blob_name == "ab12/a.py"`, `checksum == "ff"`; notices `[]`; the store never receives bytes (no blob read: pass a `skill_service` mock is gone, so assert only the store was called).
- `test_skills_are_processed_in_the_given_order`: two skills; calls follow list order.
- `test_collision_notice_names_skill_and_folder_not_uuid`: store returns `COLLISION` for `sk2`; the notice contains `"skills/foo"` and the skill name and not `"sk2"`.
- `test_already_seeded_gives_no_notice`.
- `test_legacy_inline_files_are_skipped_with_the_agreed_notice_and_no_marker`: skill with `{"path":"a.md",...,"content":"hi"}` (no `storage_key`); store not called; one notice containing `"not available in the workspace; read them through the skill tool"`; calling twice repeats the notice.
- `test_skill_without_files_writes_nothing`.
- `test_over_limit_notice_appears_when_seeded_and_not_when_already_seeded`: patch the three getters to `1, 10, 100`; skill with 2 files; first call returns a notice mentioning the 1-file limit and the store gets only the first file; store `ALREADY_SEEDED` for the second call returns `[]`.
- `test_store_error_becomes_a_notice_and_other_skills_continue`: store raises for `sk1`; the second skill is still seeded; notice mentions the skill name.
- `test_no_sleep_or_clock_dependencies`: `inspect.signature(SkillWorkspaceSeeder.__init__).parameters` has only `store`.

- [ ] **Step 2: Run to see it fail**

Run: `poetry run pytest tests/codemie/service/test_skill_workspace_seeder.py -q`
Expected: FAIL (old constructor).

- [ ] **Step 3: Rewrite the seeder**

```python
"""Makes skill bundle companion files available in an agent workspace by pointing at the skill's stored blobs."""

from __future__ import annotations

from typing import Protocol

from codemie.configs import logger
from codemie.repository.agent_workspace_repository import SeedOutcome, SkillFileReference
from codemie.rest_api.models.skill import (
    Skill,
    bundle_seed_skip_messages,
    get_skill_workspace_seed_max_file_count,
    get_skill_workspace_seed_max_file_size_bytes,
    get_skill_workspace_seed_max_total_size_bytes,
    plan_bundle_seed,
)
from codemie.service.file_service.workspace_path_rules import SKILL_FOLDER_PREFIX


class SkillSeedStore(Protocol):
    def seed_skill_references(
        self, workspace_id: str, source_id: str, folder: str, files: list[SkillFileReference]
    ) -> SeedOutcome: ...


class SkillWorkspaceSeeder:
    """Seeds each skill once per workspace; the first skill to claim a folder keeps it.

    The set of files is fixed at the first seeding: files added to a skill later do not appear.
    """

    def __init__(self, store: SkillSeedStore):
        self._store = store

    def ensure_seeded(self, workspace_id: str, skills: list[Skill]) -> list[str]:
        """Seed ``skills`` (already in attachment order) and return notices for the model."""
        notices: list[str] = []
        for skill in skills:
            try:
                notices.extend(self._seed_one(workspace_id, skill))
            except Exception as exc:  # noqa: BLE001 - seeding must never break building the agent
                logger.error(f"Failed to seed skill '{skill.name}' into workspace '{workspace_id}': {exc}")
                notices.append(
                    f"Skill '{skill.name}' could not be set up in the workspace due to an unexpected error. "
                    "Its companion files may be unavailable."
                )
        return notices

    def _seed_one(self, workspace_id: str, skill: Skill) -> list[str]:
        refs = skill.get_companion_file_refs()
        if not refs:
            return []
        folder = f"{SKILL_FOLDER_PREFIX}{skill.name}"
        limits = (
            get_skill_workspace_seed_max_file_count(),
            get_skill_workspace_seed_max_file_size_bytes(),
            get_skill_workspace_seed_max_total_size_bytes(),
        )
        plan = plan_bundle_seed(refs, folder, *limits)
        skip_notices = [f"Skill '{skill.name}': {message}" for message in bundle_seed_skip_messages(plan, *limits)]
        if not plan.to_copy:
            # Nothing to reference: record no folder owner, so the notice repeats and a later fix is not frozen out.
            return skip_notices

        files = [
            SkillFileReference(
                path=f"{folder}/{ref.path}",
                blob_name=ref.storage_key,
                mime_type=ref.mime_type,
                checksum=ref.checksum,
                size=ref.size_bytes,
            )
            for ref in plan.to_copy
        ]
        outcome = self._store.seed_skill_references(workspace_id, skill.id, folder, files)
        if outcome is SeedOutcome.COLLISION:
            logger.warning(f"Skill '{skill.name}' not seeded into workspace '{workspace_id}': folder {folder} is taken")
            return [
                f"Skill '{skill.name}' was not set up in the workspace because another skill already owns the folder "
                f"{folder}/. Rename one of the skills to make its companion files available."
            ]
        if outcome is SeedOutcome.ALREADY_SEEDED:
            return []
        return skip_notices
```

(`Skill.name` is a kebab-case validated string, so the folder cannot escape `skills/`. In Task 2.3 the folder line and `limits` move to `skill_workspace_folder` and `SeedLimits`.)

- [ ] **Step 4: Update `models/skill.py` for the new world**

- Add `SEED_SKIP_INLINE = "inline_content"`.
- In `plan_bundle_seed`, before the path check: `if not getattr(file_meta, "storage_key", None): skipped.append((file_meta, SEED_SKIP_INLINE)); continue`.
- In `bundle_seed_skip_messages`, add the template `SEED_SKIP_INLINE: "{n} companion file(s) are not available in the workspace; read them through the skill tool."`.
- Delete the settings `DEFAULT_/..._CONFIG_KEY` constants and getters for `STALE_THRESHOLD_MINUTES`, `MAX_ATTEMPTS`, `WAIT_SECONDS`.
- Update `test_plan_bundle_seed.py` fixtures to pass `SkillCompanionFileRef(path=..., storage_key="k/x", ...)` where a file is expected to be seeded, and add a case for the inline skip. Remove the tests of the three dropped getters from `test_skill_workspace_seed_config.py`.

- [ ] **Step 5: Adapt the temporary toolkit hook**

In `workspace/toolkit.py` `_seed_skills_if_enabled`, replace the seeder construction and call with

```python
            skills = SkillService.get_skills_by_ids(skill_ids, self.user)
            seed_warnings = SkillWorkspaceSeeder(AgentWorkspaceRepository()).ensure_seeded(resolved_workspace_id, skills)
```

(drop the `AgentWorkspaceService()` and `SkillService()` instances). This hook is removed in Task 4.4. Update the 7 tests in `test_agent_workspace_toolkit.py` that patch the old constructor accordingly (patch `SkillService.get_skills_by_ids` and `SkillWorkspaceSeeder`).

- [ ] **Step 6: Remove `seed_file_if_absent`**

Delete `AgentWorkspaceService.seed_file_if_absent` and its unused imports (`IntegrityError`, `hashlib` only if unused elsewhere - it is still used at lines 629 and 679, keep it). Delete `tests/codemie/service/test_skill_workspace_seeder_concurrency.py` (concurrency is covered at the repository level in Task 1.4) and any `seed_file_if_absent` tests (`grep -rn seed_file_if_absent tests src` must return nothing).

- [ ] **Step 7: Run the affected tests**

Run: `poetry run pytest tests/codemie/service/test_skill_workspace_seeder.py tests/codemie/rest_api/models tests/codemie_tools/data_management/workspace/test_agent_workspace_toolkit.py tests/codemie/service/test_agent_workspace_service_tool_calling.py -q`
Expected: PASS.

### Task 1.6: Missing referenced blobs do not fail a script run

**Files:**
- Modify: `src/codemie/service/agent_workspace_service.py`, `src/codemie/rest_api/models/agent_workspace.py`
- Test: `tests/codemie/service/test_agent_workspace_service_tool_calling.py` (plus the grep test file `tests/codemie/service/test_agent_workspace_glob.py`)

**Interfaces:**
- Produces: `class WorkspaceInputFiles(NamedTuple): files: list[FileObject]; warnings: list[str]`; `AgentWorkspaceService.get_workspace_input_files(workspace_id, user) -> WorkspaceInputFiles`; `ExecuteWorkspaceScriptResponse.warnings: list[str] = []`.

Test-first: yes - a workspace with one readable file and one row whose `read_file` raises `FileNotFoundError` returns the readable file plus a warning naming the missing path, and `execute_workspace_script` still runs and reports the warning; fails because the call raises today.

- [ ] **Step 1: Write the failing tests**

```python
def test_input_files_skip_a_missing_blob_with_a_warning(service_with_two_files):
    result = service.get_workspace_input_files("w1", user)  # fixture: second blob raises FileNotFoundError
    assert [f.name for f in result.files] == ["ok.py"]
    assert result.warnings == ["Skipped 'skills/foo/missing.py': its stored file could not be read."]


def test_execute_script_runs_and_reports_skipped_files(...):
    response = service.execute_workspace_script("w1", "ok.py", user)
    assert response.warnings == ["Skipped 'skills/foo/missing.py': its stored file could not be read."]
    assert response.output  # the script still ran


def test_grep_skips_an_unreadable_file(...):
    matches = service.grep_files("w1", "needle", user)
    assert [m.file_path for m in matches] == ["ok.py"]
```

Reuse the fixture style of the existing tests in these files (mocked `repository.list_files` and `file_repository.read_file`); read the nearest existing test first to copy its setup. Update every existing assertion on `get_workspace_input_files` (`grep -rn get_workspace_input_files tests`) to use `.files`.

- [ ] **Step 2: Run to see failures**

Run: `poetry run pytest tests/codemie/service/test_agent_workspace_service_tool_calling.py tests/codemie/service/test_agent_workspace_glob.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `agent_workspace_service.py` add `from typing import NamedTuple` (if absent) and

```python
class WorkspaceInputFiles(NamedTuple):
    files: list[FileObject]
    warnings: list[str]
```

Rewrite `get_workspace_input_files` to read each row through a helper that returns `None` on failure:

```python
    def _read_input_file(self, path: str, blob_name: str, blob_owner: str, mime_type: str) -> FileObject | None:
        try:
            file_object = self.file_repository.read_file(file_name=blob_name, owner=blob_owner, mime_type=mime_type)
        except Exception as exc:  # noqa: BLE001 - backends raise different not-found errors
            logger.warning(f"Workspace file '{path}' skipped: blob '{blob_owner}/{blob_name}' is unreadable: {exc}")
            return None
        return FileObject(name=path, mime_type=mime_type, owner=blob_owner, path=path, content=file_object.content)
```

The method builds `files` and `warnings` (`f"Skipped '{path}': its stored file could not be read."`) from both loops (workspace rows and conversation uploads) and returns `WorkspaceInputFiles(files, warnings)`. In `execute_workspace_script` use `input_files = self.get_workspace_input_files(...)`, pass `input_files.files` to the runner and `warnings=input_files.warnings` to `ExecuteWorkspaceScriptResponse`. In `grep_files` wrap the `read_file` call: on exception log a warning with the path and `continue`. Add `warnings: list[str] = Field(default_factory=list)` to `ExecuteWorkspaceScriptResponse` in `models/agent_workspace.py`.

- [ ] **Step 4: Run again**

Run: same command. Expected: PASS.

### Task 1.7: Download rule for skill-owned blobs

**Files:**
- Modify: `src/codemie/service/file_service/download_rules.py`
- Test: `tests/codemie/service/file_service/test_download_rules.py`

**Interfaces:**
- Consumes: `AgentWorkspaceRepository.user_references_blob` (Task 1.4).

Test-first: yes - `can_download` returns False today for `FileObject(owner="skill-sk1", name="ab12/a.py")` even when the user's workspace references it; the new tests fail.

- [ ] **Step 1: Write the failing tests**

Follow the existing test style (a `MagicMock` `workspace_repo`):

```python
def test_skill_blob_is_downloadable_when_the_users_workspace_references_it():
    repo = MagicMock()
    repo.user_references_blob.return_value = True
    assert can_download(FileObject(name="ab12/a.py", owner="skill-sk1", mime_type="text/plain"), user, None, "tok", repo)
    repo.user_references_blob.assert_called_once_with(user.id, "skill-sk1", "ab12/a.py")


def test_skill_blob_is_refused_when_no_workspace_of_the_user_references_it():
    repo = MagicMock()
    repo.user_references_blob.return_value = False
    repo.get_by_conversation_for_user.return_value = None
    assert not can_download(FileObject(name="ab12/a.py", owner="skill-sk1", mime_type="text/plain"), user, None, "tok", repo)


def test_other_owners_never_consult_the_skill_rule():
    repo = MagicMock()
    can_download(FileObject(name="x", owner="somebody", mime_type="text/plain"), user, None, "tok", repo)
    repo.user_references_blob.assert_not_called()
```

- [ ] **Step 2: Run to see failures**, then implement

Run: `poetry run pytest tests/codemie/service/file_service/test_download_rules.py -q` (FAIL).

Add near `_WORKSPACE_OWNER_PREFIX`: `_SKILL_OWNER_PREFIX = "skill-"` and

```python
def _references_skill_blob(file_object: FileObject, user: User, workspace_repo: AgentWorkspaceRepository) -> bool:
    """Rule D2: a skill-owned blob a live file of the requester's own workspace points at."""
    if not file_object.owner.startswith(_SKILL_OWNER_PREFIX):
        return False
    return workspace_repo.user_references_blob(user.id, file_object.owner, file_object.name)
```

In `can_download`, directly after Rule D add:

```python
    # Rule D2: skill blob referenced by a file in the requester's own workspace (no skill lookup is needed).
    if _references_skill_blob(file_object, user, workspace_repo):
        return True
```

Run the test file again. Expected: PASS.

### Task 1.8: Skill tool goes through the workspace service

**Why in phase 1:** the old tool imports the seed-entry model, status constants and `SOURCE_KIND_SKILL` from the seeder, all of which phase 1 removes; the tool must move to the service in the same commit.

**Files:**
- Modify: `src/codemie/service/agent_workspace_service.py`, `src/codemie/agents/tools/skill/skill_tool.py`
- Test: `tests/codemie/service/test_agent_workspace_service_skill_hint.py` (new), `tests/codemie/agents/tools/skill/test_skill_tool.py`

**Interfaces:**
- Produces: `AgentWorkspaceService.skill_location_hint(self, conversation_id: str, user: User, skill_id: str) -> str | None`.
- Removes: `build_workspace_hint`, `SkillTool._workspace_hint`'s repository usage and all imports of `AgentWorkspaceRepository`, `AgentWorkspaceSeedEntry`, `SEED_STATUS_*`, `SOURCE_KIND_SKILL` from the seeder.

Test-first: yes - hint returned only when the folder-owner row for this skill exists in the user's workspace (text names `owner.folder`, not the current skill name), `None` for no workspace, no owner, or another skill's owner; and `skill_tool.py` has no import from `agent_workspace_repository` or `skill_workspace_seeder`.

- [ ] **Step 1: Write the failing tests**

```python
def test_hint_names_the_folder_stored_on_the_owner_row():
    service = AgentWorkspaceService.__new__(AgentWorkspaceService)
    service.repository = MagicMock()
    service.repository.get_by_conversation_for_user.return_value = SimpleNamespace(id="w1")
    service.repository.get_folder_owner.return_value = SimpleNamespace(folder="skills/old-name")
    hint = service.skill_location_hint("c1", user, "sk1")
    assert "skills/old-name/" in hint and "read them with the skill tool" in hint
    service.repository.get_folder_owner.assert_called_once_with("w1", SOURCE_KIND_SKILL, "sk1")


@pytest.mark.parametrize("workspace,owner", [(None, None), (SimpleNamespace(id="w1"), None)])
def test_no_hint_without_workspace_or_owner(workspace, owner): ...


def test_skill_tool_module_does_not_reach_into_repository_or_seeder():
    source = inspect.getsource(skill_tool_module)
    assert "agent_workspace_repository" not in source and "skill_workspace_seeder" not in source
```

In `test_skill_tool.py`, replace the tests of `build_workspace_hint` with one that patches `AgentWorkspaceService` and asserts the tool output appends the returned hint (and swallows an exception from the service with a log).

- [ ] **Step 2: Run to fail**, **Step 3: Implement**

In `agent_workspace_service.py`:

```python
    def skill_location_hint(self, conversation_id: str, user: User, skill_id: str) -> str | None:
        """Where a skill's files are in this conversation's workspace, from its folder-owner record (not its current name)."""
        workspace = self.repository.get_by_conversation_for_user(conversation_id, user.id)
        if workspace is None:
            return None
        owner = self.repository.get_folder_owner(workspace.id, SOURCE_KIND_SKILL, skill_id)
        if owner is None:
            return None
        return (
            f"Files of this skill are in the workspace at {owner.folder}/. "
            "Files over the size or count limits, and files stored inline in older skills, are not there; "
            "read them with the skill tool."
        )
```

In `skill_tool.py`: delete the repository/model/seeder imports and `build_workspace_hint`; add `from codemie.service.agent_workspace_service import AgentWorkspaceService` (if this creates an import cycle, import it inside `_workspace_hint`); rewrite

```python
    def _workspace_hint(self, skill_id: str) -> str | None:
        # Looked up at execute time: on the first turn the workspace is created after this tool is built.
        if not self.conversation_id:
            return None
        try:
            return AgentWorkspaceService().skill_location_hint(self.conversation_id, self.user, skill_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not check workspace location for skill '{skill_id}': {e}")
            return None
```

- [ ] **Step 4: Run** `poetry run pytest tests/codemie/service/test_agent_workspace_service_skill_hint.py tests/codemie/agents/tools/skill -q`. Expected: PASS.

### Task 1.9: Phase 1 sweep and commit

**Files:** all of Phase 1.

- [ ] **Step 1: Search for leftovers**

Run: `grep -rn "SeedEntry\|seed_entry\|SEED_STATUS\|claim_seed\|reclaim_\|create_file_if_absent\|seed_file_if_absent\|record_collision\|find_seed_entry\|SEED_STALE\|SEED_MAX_ATTEMPTS\|SEED_WAIT\|build_workspace_hint" src tests`
Expected: no matches. Fix anything that remains now.

- [ ] **Step 2: Lint, license and phase tests**

Run: `make ruff && make license-check && poetry run pytest tests/codemie/repository tests/codemie/service/test_skill_workspace_seeder.py tests/codemie/service/test_skill_service.py tests/codemie/service/test_skill_file_storage.py tests/codemie/service/test_agent_workspace_service_tool_calling.py tests/codemie/service/file_service tests/codemie/migrations tests/codemie/rest_api/models tests/codemie_tools/data_management/workspace tests/codemie/agents/tools/skill -q`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add -A src tests
git commit -m "$(cat <<'EOF'
EPMCDME-15558: reference versioned skill blobs from workspaces instead of copying them

Generated with AI

Co-Authored-By: codemie-ai <codemie.ai@gmail.com>
EOF
)"
```

---

# Phase 2 — Policy and layering

### Task 2.1: Baseline

- [ ] **Step 1: Run** `poetry run pytest tests/codemie/rest_api tests/codemie/service/test_skill_workspace_seeder.py tests/codemie/service/file_service -q`. Expected: pass.

### Task 2.2: Constants and the folder function in one place

**Files:**
- Modify: `src/codemie/service/file_service/workspace_path_rules.py`, `src/codemie/rest_api/models/skill.py`
- Test: `tests/codemie/service/file_service/test_workspace_path_rules.py`, `tests/codemie/service/file_service/test_workspace_path_parity.py`

**Interfaces:**
- Produces: `skill_workspace_folder(skill_name: str) -> str` returning `f"{SKILL_FOLDER_PREFIX}{skill_name}"`; `MAX_SKILL_NAME_LENGTH = 64` stays defined only here; `models/skill.py` uses it for every `max_length`.

Test-first: yes - `skill_workspace_folder("foo") == "skills/foo"` and a source-scan test asserting `"skills/"` is not built by string formatting anywhere else under `src/codemie` fail now.

- [ ] **Step 1: Write the failing tests**

```python
def test_skill_workspace_folder():
    assert skill_workspace_folder("foo") == "skills/foo"


def test_the_folder_name_is_built_in_one_place():
    offenders = []
    for path in pathlib.Path("src/codemie").rglob("*.py"):
        text = path.read_text()
        if "SKILL_FOLDER_PREFIX}" in text and path.name != "workspace_path_rules.py":
            offenders.append(str(path))
    assert offenders == []


def test_skill_name_max_length_is_shared():
    from codemie.rest_api.models.skill import SkillCreateRequest
    assert SkillCreateRequest.model_fields["name"].metadata  # constraint exists
    assert any(getattr(m, "max_length", None) == MAX_SKILL_NAME_LENGTH for m in SkillCreateRequest.model_fields["name"].metadata)
```

- [ ] **Step 2: Run to fail**; **Step 3: Implement**

Add `skill_workspace_folder` to `workspace_path_rules.py`. In `models/skill.py` replace the four literal `max_length=64` with `max_length=MAX_SKILL_NAME_LENGTH` (import it) and the folder construction in `to_detail_response` with `skill_workspace_folder(self.name)` (that call is removed in Task 2.3). In the seeder replace `f"{SKILL_FOLDER_PREFIX}{skill.name}"` with `skill_workspace_folder(skill.name)`.

- [ ] **Step 4: Run** `poetry run pytest tests/codemie/service/file_service tests/codemie/rest_api/models -q`. Expected: PASS.

### Task 2.3: Policy module with `SeedLimits`

**Files:**
- Create: `src/codemie/service/skill_bundle_seed_policy.py`
- Modify: `src/codemie/rest_api/models/skill.py` (delete policy), `src/codemie/service/skill_workspace_seeder.py`, `src/codemie/service/skill_service.py`, `src/codemie/rest_api/routers/skill.py`
- Move/rewrite tests: `tests/codemie/rest_api/models/test_plan_bundle_seed.py` and `test_skill_workspace_seed_config.py` -> `tests/codemie/service/test_skill_bundle_seed_policy.py`; update `tests/codemie/rest_api/routers/test_skill_config_endpoint.py`, `tests/codemie/service/file_service/test_workspace_path_parity.py`

**Interfaces:**
- Produces (module `codemie.service.skill_bundle_seed_policy`):
  - `@dataclass(frozen=True) class SeedLimits: max_count: int; max_file_size: int; max_total_size: int` with `@classmethod current(cls) -> SeedLimits`
  - `@dataclass(frozen=True) class BundleSeedPlan: to_seed: list[SkillCompanionFileRef]; skipped: list[tuple[SkillCompanionFileRef, str]]; limits: SeedLimits` with `skipped_count(reason) -> int`
  - `plan_bundle_seed(refs: list[SkillCompanionFileRef], folder: str, limits: SeedLimits) -> BundleSeedPlan`
  - `bundle_seed_skip_messages(plan: BundleSeedPlan) -> list[str]`
  - `compute_bundle_warning(skill: Skill) -> str | None`
  - constants `SEED_SKIP_*`, `SKILL_WORKSPACE_SEED_*_CONFIG_KEY`, `DEFAULT_SKILL_WORKSPACE_SEED_*`.
- `models/skill.py` keeps only `get_skill_max_content_length` and its constants (they are validation, not seeding policy) and `SkillCompanionFileRef`/metadata models.

Test-first: yes - moved tests import from the new module and assert that `plan.limits` equals the limits used and that every message contains the numbers from `plan.limits`, plus a boundary test at `MAX_SKILL_BUNDLE_PATH_LENGTH` (952 accepted, 953 skipped as `invalid_path`); they fail because the module does not exist.

- [ ] **Step 1: Move the tests and write the new assertions**

`git mv tests/codemie/rest_api/models/test_plan_bundle_seed.py tests/codemie/service/test_skill_bundle_seed_policy.py`, merge the useful cases of `test_skill_workspace_seed_config.py` (bounds, bad types) into it and delete that file. Add:

```python
def test_plan_carries_the_limits_it_was_made_with():
    limits = SeedLimits(max_count=2, max_file_size=10, max_total_size=15)
    plan = plan_bundle_seed([_ref("a", 8), _ref("b", 8), _ref("c", 8)], "skills/foo", limits)
    assert plan.limits is limits
    assert [r.path for r in plan.to_seed] == ["a"]  # b would exceed the 15-byte total
    assert any("15" in m for m in bundle_seed_skip_messages(plan))


def test_current_limits_read_the_three_settings(monkeypatch):
    ...  # patch DynamicConfigService.get_typed_value_safe, assert SeedLimits.current() fields and defaults (100, 10 MB, 50 MB)


@pytest.mark.parametrize("length,seeded", [(MAX_SKILL_BUNDLE_PATH_LENGTH, True), (MAX_SKILL_BUNDLE_PATH_LENGTH + 1, False)])
def test_bundle_path_length_boundary(length, seeded):
    plan = plan_bundle_seed([_ref("a" * length, 1)], "skills/foo", SeedLimits(100, 100, 100))
    assert bool(plan.to_seed) is seeded


def test_inline_files_are_skipped_with_the_agreed_wording():
    plan = plan_bundle_seed([SkillCompanionFileRef(path="a.md", size_bytes=1)], "skills/foo", SeedLimits(1, 1, 1))
    assert "not available in the workspace; read them through the skill tool" in " ".join(bundle_seed_skip_messages(plan))
```

(`_ref` builds a `SkillCompanionFileRef` with a `storage_key`.)

- [ ] **Step 2: Run to fail**, **Step 3: Implement the module**

Create `skill_bundle_seed_policy.py` (license header) containing the moved code, reshaped:

```python
def _int_setting(key: str, default: int) -> int:
    value = DynamicConfigService.get_typed_value_safe(key, int, default=default)
    return default if isinstance(value, bool) or value < 1 else value


@dataclass(frozen=True)
class SeedLimits:
    max_count: int
    max_file_size: int
    max_total_size: int

    @classmethod
    def current(cls) -> SeedLimits:
        return cls(
            max_count=_int_setting(SKILL_WORKSPACE_SEED_MAX_FILE_COUNT_CONFIG_KEY, DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_COUNT),
            max_file_size=_int_setting(SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES_CONFIG_KEY, DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES),
            max_total_size=_int_setting(SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES_CONFIG_KEY, DEFAULT_SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES),
        )
```

`plan_bundle_seed` order of checks per file: inline (`not file.storage_key`) -> `validate_relative_path(file.path, MAX_SKILL_BUNDLE_PATH_LENGTH)` and `validate_relative_path(f"{folder}/{file.path}", MAX_WORKSPACE_PATH_LENGTH)` (invalid_path) -> too large -> count -> total, returning `BundleSeedPlan(to_seed, skipped, limits)`. `bundle_seed_skip_messages(plan)` builds its templates from `plan.limits`. `compute_bundle_warning(skill)`:

```python
def compute_bundle_warning(skill: Skill) -> str | None:
    plan = plan_bundle_seed(skill.get_companion_file_refs(), skill_workspace_folder(skill.name), SeedLimits.current())
    messages = bundle_seed_skip_messages(plan)
    return " ".join(messages) + " These files will not be available in an assistant workspace." if messages else None
```

Delete from `models/skill.py`: the six seed constants pairs, the three getters that remain (`..._max_file_size_bytes`, `..._max_file_count`, `..._max_total_size_bytes`), `BundleSeedPlan`, `plan_bundle_seed`, `bundle_seed_skip_messages`, `compute_bundle_warning`, the `SEED_SKIP_*` constants, and now-unused imports (`dataclass`, `validate_relative_path`, `MAX_WORKSPACE_PATH_LENGTH`; keep `DynamicConfigService` for `get_skill_max_content_length`). `Skill.to_detail_response(..., bundle_warning: str | None = None)` passes the parameter through and no longer imports policy.

Seeder: replace the three getter calls with `limits = SeedLimits.current()`, `plan_bundle_seed(refs, folder, limits)`, `plan.to_seed`, `bundle_seed_skip_messages(plan)`.

`skill_service.py`: at the three `skill.to_detail_response(` sites (about lines 388, 535, 675) add `bundle_warning=compute_bundle_warning(skill)`.

`routers/skill.py` `get_skill_config`: `limits = SeedLimits.current()` and fill `bundle_seed_max_file_count=limits.max_count`, `bundle_seed_max_file_size_bytes=limits.max_file_size`, `bundle_seed_max_total_size_bytes=limits.max_total_size`; drop the three getter imports. Update `test_skill_config_endpoint.py` to patch `SeedLimits.current`. Update `test_workspace_path_parity.py` `_seeder_accepts` to `plan_bundle_seed([SkillCompanionFileRef(path=path, size_bytes=1, storage_key="k/x")], FOLDER, SeedLimits(100, 100, 100))` and `.to_seed`.

- [ ] **Step 4: Run** `poetry run pytest tests/codemie/service/test_skill_bundle_seed_policy.py tests/codemie/service/test_skill_workspace_seeder.py tests/codemie/service/test_skill_service.py tests/codemie/rest_api tests/codemie/service/file_service -q`. Expected: PASS. Then `grep -rn "get_skill_workspace_seed\|plan_bundle_seed\|compute_bundle_warning" src | grep -v skill_bundle_seed_policy` must show only call sites that import from the new module.

### Task 2.4: Seeder and policy import hygiene

**Files:** `src/codemie/service/skill_workspace_seeder.py`; test `tests/codemie/service/test_skill_workspace_seeder.py`.

Test-first: yes - a test that parses the seeder module's imports and asserts it imports nothing from `codemie.service.agent_workspace_service` or `codemie.agents`, and that `SkillSeedStore` is a `typing.Protocol`; it passes after Task 1.5/2.3 or flags a stray import to fix.

- [ ] **Step 1: Add the test**

```python
def test_seeder_depends_only_on_the_store_protocol():
    import ast, inspect, codemie.service.skill_workspace_seeder as module
    imported = {n.module for n in ast.walk(ast.parse(inspect.getsource(module))) if isinstance(n, ast.ImportFrom)}
    assert not {m for m in imported if m and m.startswith(("codemie.agents", "codemie.service.agent_workspace_service"))}
    assert getattr(module.SkillSeedStore, "_is_protocol", False)
```

- [ ] **Step 2: Run, fix any stray import, rerun**

Run: `poetry run pytest tests/codemie/service/test_skill_workspace_seeder.py -q`. Expected: PASS.

### Task 2.5: Phase 2 sweep and commit

- [ ] **Step 1:** `grep -rn "SKILL_FOLDER_PREFIX}" src | grep -v workspace_path_rules` -> must return nothing. `grep -rn "DynamicConfigService" src/codemie/rest_api/models/skill.py` -> only the content-length getter.
- [ ] **Step 2:** `make ruff && make license-check && poetry run pytest tests/codemie/service tests/codemie/rest_api tests/codemie/agents tests/codemie/repository -q` -> pass.
- [ ] **Step 3: Commit** with message `EPMCDME-15558: move skill bundle policy to a service and define the skill folder name once` and the attribution lines.

---

# Phase 3 — Reserved paths

### Task 3.1: Baseline

- [ ] **Step 1: Run** `poetry run pytest tests/codemie/service/test_agent_workspace_service_reserved_paths.py tests/codemie/service/file_service/test_workspace_path_rules.py tests/codemie_tools/data_management/code_executor/test_job_bridge.py tests/codemie_tools/data_management/code_executor/test_batch_job_runner.py -q`. Expected: pass.

### Task 3.2: One reserved-path definition

**Files:**
- Create: `src/codemie_tools/data_management/code_executor/reserved_paths.py`
- Modify: `src/codemie_tools/data_management/code_executor/job_bridge.py` (remove `is_bridge_path`), `src/codemie_tools/data_management/code_executor/batch_job_runner.py`, `src/codemie/service/file_service/workspace_path_rules.py`, `src/codemie/service/agent_workspace_service.py`
- Test: `tests/codemie_tools/data_management/code_executor/test_reserved_paths.py` (new), `test_job_bridge.py` (move the `is_bridge_path` cases), `tests/codemie/service/test_agent_workspace_service_reserved_paths.py`, `tests/codemie/service/file_service/test_workspace_path_rules.py`

**Interfaces:**
- Produces: `reserved_paths.is_reserved_path(path: str) -> bool`; `RUNTIME_SDK_FILE_NAME` defined next to `BRIDGE_DIR_NAME` in the SDK module (the SDK is standalone and ships to the sandbox, so the constants stay there and are imported everywhere else).
- Removes: `job_bridge.is_bridge_path`, `workspace_path_rules.RUNTIME_SDK_FILE_NAME`.

Test-first: yes - the parametrized cases in Review Focus item 4 plus the allowed nested names; `reserved_paths` does not exist yet.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("path", [
    ".codemie_bridge", ".codemie_bridge/x", ".CODEMIE_BRIDGE/x", "．codemie_bridge/x",
    "./a/../.codemie_bridge/x", "\\.codemie_bridge\\x", "/.codemie_bridge/x",
    "codemie_runtime_sdk.py", "Codemie_Runtime_SDK.py", "./codemie_runtime_sdk.py",
])
def test_reserved(path):
    assert is_reserved_path(path)


@pytest.mark.parametrize("path", [
    "skills/foo/.codemie_bridge/x", "skills/foo/codemie_runtime_sdk.py", "a.py", ".codemie_bridge_extra/x",
    "codemie_runtime_sdk.py.bak",
])
def test_not_reserved(path):
    assert not is_reserved_path(path)
```

Add workspace-service tests: `delete_file(w, ".CODEMIE_BRIDGE/x", user)` and `delete_file(w, "codemie_runtime_sdk.py", user)` raise `ValidationException`; `read`/`get_file_content` of those paths does not raise a reserved error; a batch write containing `.codemie_bridge/x` and `ok.py` writes only `ok.py` and logs a warning.

- [ ] **Step 2: Run to fail**; **Step 3: Implement**

SDK module: unchanged (its public surface is pinned by `test_sdk_public_surface.py`); `reserved_paths.RUNTIME_SDK_FILE_NAME` is the SDK module's name plus `.py`. Create `reserved_paths.py` (license header):

```python
"""The single definition of workspace paths owned by the script runtime."""

from __future__ import annotations

import posixpath
import unicodedata

from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    BRIDGE_DIR_NAME,
    RUNTIME_SDK_FILE_NAME,
)


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


_BRIDGE_DIR = _fold(BRIDGE_DIR_NAME)
_SDK_FILE = _fold(RUNTIME_SDK_FILE_NAME)


def is_reserved_path(path: str) -> bool:
    """True for the bridge directory (and anything under it) or the root runtime SDK file, however spelled."""
    folded = _fold(posixpath.normpath(path.replace("\\", "/")).lstrip("/"))
    return folded.split("/", 1)[0] == _BRIDGE_DIR or folded == _SDK_FILE
```

`workspace_path_rules.py`: delete `RUNTIME_SDK_FILE_NAME` and the `BRIDGE_DIR_NAME` import; `is_reserved_workspace_path(normalized_path)` becomes `return is_reserved_path(normalized_path)` (keep the function so callers stay unchanged) importing from `reserved_paths`. `job_bridge.py`: delete `is_bridge_path` (and `posixpath` if unused); `batch_job_runner.py`: import `is_reserved_path` from `reserved_paths` and replace both `is_bridge_path` uses. In `agent_workspace_service.py` `delete_file`: `normalized_path = self._normalize_write_path(file_path)` and extend the `_normalize_write_path` docstring to "written or deleted". Move the `is_bridge_path` tests from `test_job_bridge.py` into `test_reserved_paths.py` (remove from the old file).

- [ ] **Step 4: Run** `poetry run pytest tests/codemie_tools/data_management/code_executor tests/codemie/service/test_agent_workspace_service_reserved_paths.py tests/codemie/service/file_service -q`. Expected: PASS. Then `grep -rn "is_bridge_path\|RUNTIME_SDK_FILE_NAME" src` shows only `reserved_paths.py`, the SDK module and imports.

### Task 3.3: Phase 3 commit

- [ ] **Step 1:** `make ruff && make license-check && poetry run pytest tests/codemie_tools tests/codemie/service/file_service tests/codemie/service/test_agent_workspace_service_reserved_paths.py -q` -> pass.
- [ ] **Step 2: Commit** `EPMCDME-15558: define workspace reserved paths once and refuse deleting them` with the attribution lines.

---

# Phase 4 — Preparation step and workflows

### Task 4.1: Baseline

- [ ] **Step 1: Run** `poetry run pytest tests/codemie/service/tools tests/codemie/service/test_assistant_service_methods.py tests/codemie/service/test_assistant_service_workflow_mapping.py tests/codemie_tools/data_management/workspace -q`. Expected: pass.

### Task 4.2: `ToolkitService.prepare_skill_workspace`

**Files:**
- Modify: `src/codemie/service/tools/toolkit_service.py`
- Test: `tests/codemie/service/tools/test_toolkit_service_prepare_skill_workspace.py` (new)

**Interfaces:**
- Consumes: `SkillWorkspaceSeeder`, `AgentWorkspaceRepository`, `SkillService.get_skills_by_ids`, `AgentWorkspaceService.create_workspace`, `resolve_tool_calling_settings`.
- Produces: `ToolkitService.prepare_skill_workspace(cls, assistant: Assistant, request: AssistantChatRequest | None, user: User) -> list[str]`.

Test-first: yes - table of gate cases (no skills, no request or conversation id, script tool not among `_merge_skill_toolkits`, `resolve_tool_calling_settings()` is `None`) each returning `[]` without touching the seeder; the passing case calls `ensure_seeded` with the workspace id and the ordered skills and returns its notices; an exception returns one generic notice and does not raise.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("case", ["no_skills", "no_conversation", "tool_not_selected", "switch_off"])
def test_gate_closed_returns_nothing(case, ...):
    ...  # patch ToolkitService._merge_skill_toolkits, resolve_tool_calling_settings, SkillWorkspaceSeeder
    assert ToolkitService.prepare_skill_workspace(assistant, request, user) == []
    seeder_cls.assert_not_called()


def test_gate_open_seeds_in_attachment_order_and_returns_notices(...):
    skills = [skill_a, skill_b]
    get_skills.return_value = skills
    seeder_cls.return_value.ensure_seeded.return_value = ["note"]
    assert ToolkitService.prepare_skill_workspace(assistant, request, user) == ["note"]
    seeder_cls.return_value.ensure_seeded.assert_called_once_with("w1", skills)
    get_skills.assert_called_once_with(assistant.skill_ids, user)


def test_unexpected_error_becomes_a_notice(...):
    get_skills.side_effect = RuntimeError("db down")
    notices = ToolkitService.prepare_skill_workspace(assistant, request, user)
    assert len(notices) == 1 and "could not be set up" in notices[0]
```

- [ ] **Step 2: Run to fail**; **Step 3: Implement**

Add to `ToolkitService` (imports at the top: `SkillService`, `SkillWorkspaceSeeder`, `AgentWorkspaceRepository`, `AgentWorkspaceService`, `CreateAgentWorkspaceRequest`, `resolve_tool_calling_settings`; use local imports if a cycle appears):

```python
    @classmethod
    def prepare_skill_workspace(cls, assistant: Assistant, request, user: User) -> list[str]:
        """Make the assistant's skill files available in the conversation's workspace; return notices for the model.

        Runs before the tools are built, for chat and workflow assistants alike. It needs: attached skills, a
        conversation, the script-execution tool among the selected tools, and the script bridge enabled.
        The three other get_toolkit_methods() call sites (script step, tool invocation, workflow resolution) have
        no assistant skill context and intentionally do not seed. Never raises.
        """
        skill_ids = getattr(assistant, "skill_ids", None) or []
        conversation_id = getattr(request, "conversation_id", None) if request is not None else None
        if not skill_ids or not conversation_id:
            return []
        if not cls._is_tool_selected(
            cls._merge_skill_toolkits(assistant), AGENT_WORKSPACE_TOOLKIT, EXECUTE_WORKSPACE_SCRIPT_TOOL.name
        ):
            return []
        if resolve_tool_calling_settings() is None:
            return []
        try:
            workspace = AgentWorkspaceService().create_workspace(
                CreateAgentWorkspaceRequest(conversation_id=conversation_id), user
            )
            skills = SkillService.get_skills_by_ids(skill_ids, user)
            return SkillWorkspaceSeeder(AgentWorkspaceRepository()).ensure_seeded(workspace.id, skills)
        except Exception as exc:  # noqa: BLE001 - preparing skill files must never fail building the agent
            logger.error(f"Failed to prepare skill files for conversation '{conversation_id}': {exc}")
            return ["Skill files could not be set up in the workspace due to an unexpected error. Companion files may be unavailable."]
```

Copy the exact `create_workspace(...)` call shape from `AgentWorkspaceToolkit.get_tools()` so both resolve the same workspace.

- [ ] **Step 4: Run** `poetry run pytest tests/codemie/service/tools/test_toolkit_service_prepare_skill_workspace.py -q`. Expected: PASS.

### Task 4.3: Builders call the step and pass notices as an argument

**Files:**
- Modify: `src/codemie/service/assistant_service.py`
- Test: `tests/codemie/service/test_assistant_service_methods.py`, `tests/codemie/service/test_assistant_service_workflow_mapping.py`

**Interfaces:**
- Produces: `_prepare_system_prompt(cls, assistant, user, request, thread_generator=None, workspace_notices: list[str] | None = None)` and `_prepare_workflow_system_prompt(cls, workflow_assistant, assistant, user, workflow_state, mcp_server_args_preprocessor, workspace_notices: list[str] | None = None)`; a shared helper `_append_workspace_notices(system_prompt: str, notices: list[str] | None) -> str`.

Test-first: yes - both prompt builders append "**Workspace setup notices (skill files):**" with the notices when given and nothing when empty or `None`; `build_agent` and `build_agent_for_workflow` call `prepare_skill_workspace` once before `get_tools` and pass its result.

- [ ] **Step 1: Write the failing tests**

```python
def test_chat_prompt_appends_notices():
    prompt = AssistantService._prepare_system_prompt(assistant, user, request, workspace_notices=["a", "b"])
    assert prompt.endswith("**Workspace setup notices (skill files):**\n- a\n- b")


def test_workflow_prompt_appends_notices():
    prompt, _ = AssistantService._prepare_workflow_system_prompt(wa, assistant, user, None, None, workspace_notices=["a"])
    assert "**Workspace setup notices (skill files):**\n- a" in prompt


@pytest.mark.parametrize("notices", [None, []])
def test_no_notice_block_without_notices(notices): ...


def test_builders_prepare_before_tools_and_pass_notices(): ...  # patch ToolkitService.prepare_skill_workspace / get_tools with a shared Mock and assert call order via mock_calls
```

For the builder test copy the heavy patching of the existing `build_agent_for_workflow` test in `test_assistant_service_workflow_mapping.py` and assert `prepare_skill_workspace` is called before `get_tools` and that the prompt builder receives `workspace_notices=<returned list>`.

- [ ] **Step 2: Run to fail**; **Step 3: Implement**

Add the helper (replacing the `getattr(request, "_seed_warnings", [])` block in `_prepare_system_prompt`):

```python
    @staticmethod
    def _append_workspace_notices(system_prompt: str, notices: list[str] | None) -> str:
        if not notices:
            return system_prompt
        joined = "\n".join(f"- {notice}" for notice in notices)
        return f"{system_prompt}\n\n**Workspace setup notices (skill files):**\n{joined}"
```

`_prepare_system_prompt`: new parameter, and `return cls._append_workspace_notices(system_prompt, workspace_notices)` as the last step. `_prepare_workflow_system_prompt`: same, applied before `return system_prompt, output_schema`. In `build_agent` (before `ToolkitService.get_tools(` at about line 582) add `workspace_notices = ToolkitService.prepare_skill_workspace(assistant, request, user)` and pass `workspace_notices=workspace_notices` into `_prepare_system_prompt`. In `build_agent_for_workflow` do the same before its `ToolkitService.get_tools(` (about line 861; place it inside the existing `try` only if `prepare_skill_workspace` could raise `ToolException` - it never raises, so put it just above the `try`) and pass the notices into `_prepare_workflow_system_prompt`.

- [ ] **Step 4: Run** `poetry run pytest tests/codemie/service/test_assistant_service_methods.py tests/codemie/service/test_assistant_service_workflow_mapping.py -q`. Expected: PASS.

### Task 4.4: Remove the toolkit hook, `script_tool_selected` and `_seed_warnings`

**Files:**
- Modify: `src/codemie_tools/data_management/workspace/toolkit.py`, `src/codemie/service/tools/toolkit_settings_service.py`, `src/codemie/service/tools/toolkit_service.py`, `src/codemie/core/models.py`
- Test: `tests/codemie_tools/data_management/workspace/test_agent_workspace_toolkit.py`, `tests/codemie/service/tools/test_toolkit_service.py`

Test-first: yes - a test that `AgentWorkspaceToolkit.get_tools()` performs no seeding (patch `SkillWorkspaceSeeder` and `SkillService` and assert neither is called, and `inspect.signature(AgentWorkspaceToolkit.get_toolkit)` has no `script_tool_selected`); a source test that `script_tool_selected` and `_seed_warnings` appear nowhere under `src`.

- [ ] **Step 1: Write the failing tests**

```python
def test_get_tools_has_no_side_effects(...):
    with patch("codemie_tools.data_management.workspace.toolkit.SkillWorkspaceSeeder", create=True) as seeder:
        toolkit.get_tools()
    seeder.assert_not_called()


def test_script_tool_selected_and_seed_warnings_are_gone():
    hits = [str(p) for p in pathlib.Path("src").rglob("*.py")
            if "script_tool_selected" in p.read_text() or "_seed_warnings" in p.read_text()]
    assert hits == []
```

- [ ] **Step 2: Run to fail**; **Step 3: Remove**

- `toolkit.py`: delete `_seed_skills_if_enabled`, its call in `get_tools`, the `script_tool_selected` field and `get_toolkit` parameter, and the imports `AgentWorkspaceRepository`, `SkillService`, `SkillWorkspaceSeeder`, `logger` (if unused).
- `toolkit_settings_service.py`: remove the `script_tool_selected` parameter and the keyword passed to `get_toolkit`.
- `toolkit_service.py`: remove `selected_toolkits` from `get_toolkit_methods` (signature, docstring, the `script_tool_selected=` argument) and from the `add_tools_with_creds` call at about line 1151; keep `_is_tool_selected` (used by `prepare_skill_workspace`).
- `core/models.py`: delete the `_seed_warnings` `PrivateAttr` (line ~643) and the unused `PrivateAttr` import if nothing else uses it.
- Tests: delete the `script_tool_selected` cases in `test_toolkit_service.py` (~2113-2137) and `test_agent_workspace_toolkit.py` (lines ~30, 41, 129) and the old hook tests there; the new tests above replace them.

- [ ] **Step 4: Run** `poetry run pytest tests/codemie_tools/data_management/workspace tests/codemie/service/tools tests/codemie/service/test_assistant_service_methods.py -q`. Expected: PASS. `grep -rn "script_tool_selected\|_seed_warnings" src tests` returns only the new negative tests.

### Task 4.5: Workflow coverage test and phase commit

**Files:** Test: `tests/codemie/service/test_assistant_service_workflow_mapping.py`.

Test-first: yes - an end-to-end seam test: a workflow assistant with `skill_ids`, the script tool selected and the switch on gets its skills seeded (`SkillWorkspaceSeeder.ensure_seeded` called with the workflow request's `conversation_id`) and the notices appear in the system prompt passed to the agent; and the three script-step sites (`tool_node.py:203`, `tool_execution_service.py:229`, `workflow_resolution.py:36`) never call `prepare_skill_workspace` (patch it and run their existing tests, asserting not called).

- [ ] **Step 1:** write the tests above, run them (`poetry run pytest tests/codemie/service/test_assistant_service_workflow_mapping.py tests/codemie/workflows/test_tool_node_script_step.py -q`), fix until PASS.
- [ ] **Step 2:** `make ruff && make license-check && poetry run pytest tests/codemie tests/codemie_tools -q -x` -> pass.
- [ ] **Step 3: Commit** `EPMCDME-15558: prepare skill workspace files before building tools, for chats and workflows` with the attribution lines.

---

# Phase 5 — Docs and test cleanup

### Task 5.1: Baseline and leftover search

- [ ] **Step 1:** `grep -rn "SeedEntry\|seed_entry\|ledger\|claim token\|lock_workspace_for_seeding\|has_seeded\|record_seeded" src tests docs .ai-run | grep -v "docs/superpowers/tasks/.*\(technical-analysis\|review\)"` -> every remaining hit is either removed, or is the history inside old review artifacts you leave alone. Fix hits in `src`/`tests`.

### Task 5.2: User documentation

**Files:** Create `docs/skill-files-in-workspace.md`.

Test-first: no - documentation only; verified by reading it against the spec sections 2, 3.4 and 3.5.

- [ ] **Step 1: Write the document** with these sections, each 3-8 lines, no placeholders:
  - What it does: a skill's companion files appear under `skills/<name>/` in a conversation's workspace; files are pointed at, not copied; a user's edit becomes the conversation's own copy.
  - When it happens: at the assistant's first request, for chats and for workflow assistant nodes; requires the script-execution tool, the `workspaceScriptBridge` switch and jobs sandbox mode. The script-step, tool-invocation and workflow-resolution paths do not seed (they have no assistant skills); a workflow's script steps see files seeded by earlier assistant nodes of the same run.
  - Limits: the three settings, defaults, and what is reported when exceeded.
  - Frozen file set: files added to a skill later do not appear in workspaces that already have the skill; content is pinned at seeding.
  - Older skills with inline files: skipped with a log warning and a notice to the model; read them through the skill tool.
  - Name conflicts: the first attached skill keeps the folder.
  - Reserved paths: `.codemie_bridge/` and root `codemie_runtime_sdk.py`; case/Unicode-insensitive; writes and deletes refused.
  - Skill blobs: versioned under `<sha256[:16]>/<path>`, never overwritten or deleted by skill update/delete; any future cleanup needs a reference check.
  - Rollout: removed settings `SKILL_WORKSPACE_SEED_MAX_ATTEMPTS`, `SKILL_WORKSPACE_SEED_STALE_THRESHOLD_MINUTES`, `SKILL_WORKSPACE_SEED_WAIT_SECONDS`.

### Task 5.3: Spec/plan corrections and MR description draft

**Files:** Modify `spec.md`; create `docs/superpowers/tasks/2026-10-06-epmcdme-15558-skill-scripts-workspace/mr-description.md`.

Test-first: no - documents.

- [ ] **Step 1:** Re-read `spec.md` sections 3.3, 3.6, 3.7 and 7 and confirm they match what was built (checksum rule, skill tool never builds a folder name, skill-tool change landed in phase 1, `reserved_paths` import direction). Fix any sentence that no longer matches.
- [ ] **Step 2:** Write `mr-description.md` with: summary of the change of approach; the decisions (references, versioned `storage_key`, frozen file set, legacy skills skipped, first skill wins, workflows included, reserved paths rule, download rule); rollout notes (removed settings; migration `y1z2a3b4c5d6` rewritten because unmerged); the corrected statements from spec section 9 (no `lock_workspace_for_seeding`/`has_seeded`/`record_seeded`; deviations are now listed here; `FOR UPDATE` is not new; commit `327ecdda6` does not exist); a "## Testing" section with the exact commands run and results; and the sentence "Please post `/sanity` on this MR: it changes download authorization, path validation and a database migration." (AGENTS.md repository rule).

### Task 5.4: Final verification and commit

- [ ] **Step 1:** `make ruff` -> clean. `make license-check` -> pass.
- [ ] **Step 2:** `poetry run pytest tests/ -q` -> record the exact summary line. Any failure not caused by this branch (compare with `git stash`-free reasoning: failures in files this branch did not touch) is reported, not hidden.
- [ ] **Step 3: Commit** `EPMCDME-15558: document skill files in the workspace and correct the spec and plan` with the attribution lines.
- [ ] **Step 4:** Report: the five commits (`git log --oneline -6`), the test summary, and anything skipped. Stop; do not push or open an MR.

# Phase 6 — Review round 4 (2026-10-13)

Source: architecture review round 4 (sections A and B), root spec D7 and D32. Each task was done test-first; the
work was squashed into `5c0dda415`. Design: spec §3.3 and §3.9.

### Task 6.1: A1 — 128-bit blob key prefix
`CONTENT_KEY_HEX_LENGTH = 32`; worst-case S3 key test (<= 1024); `[:16]` assertions and the filesystem round-trip test updated.

### Task 6.2: A2 — do not re-upload recorded files
`_upload_companion_files(..., existing_records)`; `update_skill` and the preconfigured-skills update pass the skill's current records. A file is skipped only when its key and full checksum match a record (review follow-up D2). Tests: repeat call writes nothing, changed content and new paths write, legacy records rewrite, removed paths are dropped, no shared dicts.

### Task 6.3: B4 — shared key function
`content_addressed_key` and the constant move to `blob_ref_rules`; `SkillFileStorage.versioned_key` removed; key tests move to `test_blob_ref_rules.py`.

### Task 6.4: B1 — owner-prefix registry
`_SOURCE_OWNER_PREFIXES`, `source_blob_owner` (ValueError on unknown kind), `is_source_owned_blob[_ref]`, `skill_blob_owner` wrapper; `SKILL_BLOB_OWNER_PREFIX` and `is_skill_blob_ref` removed; D2 helper renamed `_references_source_blob`.

### Task 6.5: B2 — `seed_references`
`seed_references(workspace_id, source_kind, source_id, folder, files: list[SeedFileReference])`; owner computed before the session; Protocol, repository and test `FakeStore` renamed together; unknown kind writes nothing (test).

### Task 6.6: B3 — one registrable-blob check
`_is_registrable_blob_ref` for `sync_uploaded_files` and `_generated_file_path`; guard at the top of `_upsert_workspace_file_reference` (before the blob read); `_decode_safe_run_file` refuses source-owned tokens (behaviour change: 400 instead of a silent drop). The virtual upload view hides source-owned and unsafe refs with `is_source_owned_blob_ref` plus `is_safe_blob_ref` (review follow-up E1).

### Task 6.7: Docs
Spec §3.2, §3.3, §3.7, §3.9 and §8, the user guide and the MR description updated.
