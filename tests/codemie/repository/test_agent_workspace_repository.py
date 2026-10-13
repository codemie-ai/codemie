# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""AgentWorkspaceRepository: delete_by_execution_id, plus real-session (in-memory SQLite) tests for
single-transaction skill seeding and the blob-reference lookup."""

from __future__ import annotations

import threading
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from codemie.repository.agent_workspace_repository import (
    AgentWorkspaceRepository,
    SeedOutcome,
    SeedFileReference,
)
from codemie.rest_api.models.agent_workspace import (
    SOURCE_KIND_SKILL,
    AgentWorkspace,
    AgentWorkspaceFile,
    AgentWorkspaceFolderOwner,
)

_TABLES = [AgentWorkspace.__table__, AgentWorkspaceFile.__table__, AgentWorkspaceFolderOwner.__table__]


def _create_schema(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    # Production enforces one row per (workspace, path) through a migration; the model does not declare it.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_agent_workspace_files_workspace_path ON agent_workspace_files (workspace_id, path)"
        )


def _patch_engine(engine: Engine):
    return (
        patch.object(AgentWorkspace, "get_engine", return_value=engine),
        patch.object(AgentWorkspaceFile, "get_engine", return_value=engine),
        patch.object(AgentWorkspaceFolderOwner, "get_engine", return_value=engine),
    )


@pytest.fixture()
def sqlite_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:")
    _create_schema(engine)
    return engine


@pytest.fixture()
def repository(sqlite_engine) -> AgentWorkspaceRepository:
    patches = _patch_engine(sqlite_engine)
    for active in patches:
        active.start()
    try:
        yield AgentWorkspaceRepository()
    finally:
        for active in patches:
            active.stop()


def _session_patch(workspace: object | None) -> tuple[MagicMock, MagicMock]:
    session = MagicMock()
    session.exec.return_value.first.return_value = workspace
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    return ctx, session


def _workspace(session: Session, workspace_id: str = "w1", user_id: str = "u1") -> None:
    session.add(AgentWorkspace(id=workspace_id, conversation_id=f"c-{workspace_id}", user_id=user_id))
    session.commit()


def _ref(path: str, blob: str = "ab12/a.py", size: int = 3) -> SeedFileReference:
    return SeedFileReference(path=path, blob_name=blob, mime_type="text/x-python", checksum="ff", size=size)


def test_delete_by_execution_id_removes_files_then_workspace() -> None:
    workspace = MagicMock(id="ws-1")
    ctx, session = _session_patch(workspace)
    with patch("codemie.repository.agent_workspace_repository.Session", return_value=ctx):
        removed = AgentWorkspaceRepository().delete_by_execution_id("exec-1", "user-1")

    assert removed is True
    session.delete.assert_called_once_with(workspace)
    session.commit.assert_called_once()
    # workspace lookup, then bulk file delete; folder owners go with the workspace through the FK cascade
    assert session.exec.call_count == 2


def test_delete_by_execution_id_without_workspace_is_noop() -> None:
    ctx, session = _session_patch(None)
    with patch("codemie.repository.agent_workspace_repository.Session", return_value=ctx):
        removed = AgentWorkspaceRepository().delete_by_execution_id("exec-1", "user-1")

    assert removed is False
    session.delete.assert_not_called()


class TestSeedReferences:
    def test_unknown_source_kind_raises_and_writes_nothing(self, repository, sqlite_engine):
        with Session(sqlite_engine) as session:
            _workspace(session)

        with pytest.raises(ValueError):
            repository.seed_references("w1", "assistant", "a1", "assistants/a", [_ref("assistants/a/x.py")])

        with Session(sqlite_engine) as session:
            assert session.exec(select(AgentWorkspaceFolderOwner)).all() == []
            assert session.exec(select(AgentWorkspaceFile)).all() == []

    def test_creates_owner_and_rows_pointing_at_the_skill_blobs(self, repository, sqlite_engine):
        with Session(sqlite_engine) as session:
            _workspace(session)

        outcome = repository.seed_references("w1", SOURCE_KIND_SKILL, "sk1", "skills/foo", [_ref("skills/foo/a.py")])

        assert outcome is SeedOutcome.SEEDED
        with Session(sqlite_engine) as session:
            owners = session.exec(select(AgentWorkspaceFolderOwner)).all()
            rows = session.exec(select(AgentWorkspaceFile)).all()
        assert [(o.source_kind, o.source_id, o.folder) for o in owners] == [("skill", "sk1", "skills/foo")]
        assert [(r.path, r.blob_owner, r.blob_name, r.version, r.deleted_at) for r in rows] == [
            ("skills/foo/a.py", "skill-sk1", "ab12/a.py", 1, None)
        ]

    def test_second_call_for_the_same_skill_changes_nothing(self, repository, sqlite_engine):
        with Session(sqlite_engine) as session:
            _workspace(session)
        repository.seed_references("w1", SOURCE_KIND_SKILL, "sk1", "skills/foo", [_ref("skills/foo/a.py")])

        outcome = repository.seed_references(
            "w1", SOURCE_KIND_SKILL, "sk1", "skills/foo", [_ref("skills/foo/a.py"), _ref("skills/foo/new.py")]
        )

        assert outcome is SeedOutcome.ALREADY_SEEDED
        with Session(sqlite_engine) as session:
            assert [r.path for r in session.exec(select(AgentWorkspaceFile)).all()] == ["skills/foo/a.py"]

    def test_other_skill_with_the_same_folder_is_a_collision_and_writes_nothing(self, repository, sqlite_engine):
        with Session(sqlite_engine) as session:
            _workspace(session)
        repository.seed_references("w1", SOURCE_KIND_SKILL, "sk1", "skills/foo", [_ref("skills/foo/a.py")])

        outcome = repository.seed_references("w1", SOURCE_KIND_SKILL, "sk2", "skills/foo", [_ref("skills/foo/b.py")])

        assert outcome is SeedOutcome.COLLISION
        with Session(sqlite_engine) as session:
            assert len(session.exec(select(AgentWorkspaceFolderOwner)).all()) == 1
            assert [r.path for r in session.exec(select(AgentWorkspaceFile)).all()] == ["skills/foo/a.py"]

    def test_live_and_soft_deleted_rows_are_never_touched(self, repository, sqlite_engine):
        with Session(sqlite_engine) as session:
            _workspace(session)
            session.add(
                AgentWorkspaceFile(
                    id="f1",
                    workspace_id="w1",
                    path="skills/foo/user.py",
                    blob_owner="workspace-w1",
                    blob_name="user.py",
                    mime_type="text/plain",
                    checksum="u",
                    size=9,
                    version=4,
                )
            )
            session.add(
                AgentWorkspaceFile(
                    id="f2",
                    workspace_id="w1",
                    path="skills/foo/gone.py",
                    blob_owner="workspace-w1",
                    blob_name="gone.py",
                    mime_type="text/plain",
                    checksum="g",
                    size=1,
                    deleted_at=datetime(2026, 1, 1),
                )
            )
            session.commit()

        repository.seed_references(
            "w1",
            SOURCE_KIND_SKILL,
            "sk1",
            "skills/foo",
            [_ref("skills/foo/user.py"), _ref("skills/foo/gone.py"), _ref("skills/foo/ok.py")],
        )

        with Session(sqlite_engine) as session:
            rows = {r.path: r for r in session.exec(select(AgentWorkspaceFile)).all()}
        assert (rows["skills/foo/user.py"].blob_owner, rows["skills/foo/user.py"].version) == ("workspace-w1", 4)
        assert rows["skills/foo/gone.py"].deleted_at is not None
        assert rows["skills/foo/gone.py"].blob_owner == "workspace-w1"
        assert rows["skills/foo/ok.py"].blob_owner == "skill-sk1"

    def test_a_failure_before_commit_leaves_nothing_behind(self, repository, sqlite_engine):
        with Session(sqlite_engine) as session:
            _workspace(session)

        with patch.object(Session, "commit", side_effect=RuntimeError("crash")):
            with pytest.raises(RuntimeError):
                repository.seed_references("w1", SOURCE_KIND_SKILL, "sk1", "skills/foo", [_ref("skills/foo/a.py")])

        with Session(sqlite_engine) as session:
            assert session.exec(select(AgentWorkspaceFolderOwner)).all() == []
            assert session.exec(select(AgentWorkspaceFile)).all() == []
        outcome = repository.seed_references("w1", SOURCE_KIND_SKILL, "sk1", "skills/foo", [_ref("skills/foo/a.py")])
        assert outcome is SeedOutcome.SEEDED

    def test_a_database_error_is_raised_not_reported_as_a_collision(self, repository, sqlite_engine):
        # In production a workspace deleted mid-seeding makes the owner insert fail with a foreign-key error.
        with Session(sqlite_engine) as session:
            _workspace(session)

        with patch.object(Session, "execute", side_effect=IntegrityError("insert", {}, Exception("fk"))):
            with pytest.raises(IntegrityError):
                repository.seed_references("w1", SOURCE_KIND_SKILL, "sk1", "skills/foo", [_ref("skills/foo/a.py")])

    def test_seeding_with_no_files_still_records_the_owner(self, repository, sqlite_engine):
        with Session(sqlite_engine) as session:
            _workspace(session)

        assert repository.seed_references("w1", SOURCE_KIND_SKILL, "sk1", "skills/foo", []) is SeedOutcome.SEEDED
        assert repository.get_folder_owner("w1", "skill", "sk1").folder == "skills/foo"

    def test_get_folder_owner_is_none_when_absent(self, repository):
        assert repository.get_folder_owner("w1", "skill", "nope") is None


def test_concurrent_seeding_leaves_exactly_one_set_of_rows(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'ws.db'}", connect_args={"timeout": 30})
    _create_schema(engine)
    with Session(engine) as session:
        _workspace(session)
    results: list[SeedOutcome] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        results.append(
            AgentWorkspaceRepository().seed_references(
                "w1", SOURCE_KIND_SKILL, "sk1", "skills/foo", [_ref("skills/foo/a.py"), _ref("skills/foo/b.py")]
            )
        )

    patches = _patch_engine(engine)
    for active in patches:
        active.start()
    try:
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        for active in patches:
            active.stop()

    assert results.count(SeedOutcome.SEEDED) == 1
    assert results.count(SeedOutcome.ALREADY_SEEDED) == 3
    with Session(engine) as session:
        assert len(session.exec(select(AgentWorkspaceFile)).all()) == 2
        assert len(session.exec(select(AgentWorkspaceFolderOwner)).all()) == 1


class TestUserReferencesBlob:
    def test_true_only_for_a_live_row_in_a_workspace_of_that_user(self, repository, sqlite_engine):
        with Session(sqlite_engine) as session:
            _workspace(session, "w1", "u1")
            _workspace(session, "w2", "u2")
        repository.seed_references(
            "w1", SOURCE_KIND_SKILL, "sk1", "skills/foo", [_ref("skills/foo/a.py", blob="ab12/a.py")]
        )

        assert repository.user_references_blob("u1", "skill-sk1", "ab12/a.py") is True
        assert repository.user_references_blob("u2", "skill-sk1", "ab12/a.py") is False
        assert repository.soft_delete_file("w1", "skills/foo/a.py") is True
        assert repository.user_references_blob("u1", "skill-sk1", "ab12/a.py") is False
