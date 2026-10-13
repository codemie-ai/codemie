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

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from sqlalchemy import Table
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql.dml import Insert
from sqlmodel import Session, col, delete, select

from codemie.service.file_service.blob_ref_rules import source_blob_owner
from codemie.rest_api.models.agent_workspace import (
    AgentWorkspace,
    AgentWorkspaceFile,
    AgentWorkspaceFolderOwner,
)


class SeedOutcome(str, Enum):
    SEEDED = "seeded"
    ALREADY_SEEDED = "already_seeded"
    COLLISION = "collision"


@dataclass(frozen=True)
class SeedFileReference:
    """A workspace file that points at a seeding source's blob instead of holding its own copy; ``path`` is the
    workspace path."""

    path: str
    blob_name: str
    mime_type: str
    checksum: str
    size: int


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _insert(session: Session, table: Table) -> Insert:
    """INSERT supporting ON CONFLICT DO NOTHING on PostgreSQL (production) and SQLite (tests)."""
    dialect_insert = postgresql.insert if session.get_bind().dialect.name == "postgresql" else sqlite.insert
    return dialect_insert(table)


class AgentWorkspaceRepository:
    def get_by_id_for_user(self, workspace_id: str, user_id: str) -> Optional[AgentWorkspace]:
        with Session(AgentWorkspace.get_engine()) as session:
            statement = select(AgentWorkspace).where(
                AgentWorkspace.id == workspace_id, AgentWorkspace.user_id == user_id
            )
            return session.exec(statement).first()

    def get_by_conversation_for_user(self, conversation_id: str, user_id: str) -> Optional[AgentWorkspace]:
        with Session(AgentWorkspace.get_engine()) as session:
            statement = select(AgentWorkspace).where(
                AgentWorkspace.conversation_id == conversation_id,
                AgentWorkspace.user_id == user_id,
            )
            return session.exec(statement).first()

    def list_for_user(self, user_id: str) -> list[AgentWorkspace]:
        with Session(AgentWorkspace.get_engine()) as session:
            statement = (
                select(AgentWorkspace)
                .where(AgentWorkspace.user_id == user_id)
                .order_by(AgentWorkspace.update_date.desc())
            )
            return list(session.exec(statement).all())

    def save_workspace(self, workspace: AgentWorkspace) -> AgentWorkspace:
        workspace.save()
        return workspace

    def get_file(self, workspace_id: str, path: str, include_deleted: bool = False) -> Optional[AgentWorkspaceFile]:
        with Session(AgentWorkspaceFile.get_engine()) as session:
            statement = select(AgentWorkspaceFile).where(
                AgentWorkspaceFile.workspace_id == workspace_id,
                AgentWorkspaceFile.path == path,
            )
            if not include_deleted:
                statement = statement.where(AgentWorkspaceFile.deleted_at.is_(None))
            return session.exec(statement).first()

    def find_by_blob(
        self,
        workspace_id: str,
        blob_owner: str,
        blob_name: str,
        mime_type: str | None = None,
    ) -> Optional[AgentWorkspaceFile]:
        with Session(AgentWorkspaceFile.get_engine()) as session:
            conditions = [
                AgentWorkspaceFile.workspace_id == workspace_id,
                AgentWorkspaceFile.blob_owner == blob_owner,
                AgentWorkspaceFile.blob_name == blob_name,
                # Deliberately no deleted_at filter — see D-4.
            ]
            if mime_type is not None:
                conditions.append(AgentWorkspaceFile.mime_type == mime_type)
            return session.exec(select(AgentWorkspaceFile).where(*conditions)).first()

    def list_files(self, workspace_id: str, include_deleted: bool = False) -> list[AgentWorkspaceFile]:
        with Session(AgentWorkspaceFile.get_engine()) as session:
            statement = select(AgentWorkspaceFile).where(AgentWorkspaceFile.workspace_id == workspace_id)
            if not include_deleted:
                statement = statement.where(AgentWorkspaceFile.deleted_at.is_(None))
            statement = statement.order_by(AgentWorkspaceFile.path.asc())
            return list(session.exec(statement).all())

    def save_file(self, workspace_file: AgentWorkspaceFile) -> AgentWorkspaceFile:
        if not workspace_file.id:
            workspace_file.id = str(uuid4())

        now = datetime.now(UTC).replace(tzinfo=None)
        if not workspace_file.date:
            workspace_file.date = now
        workspace_file.update_date = now

        with Session(AgentWorkspaceFile.get_engine()) as session:
            session.add(workspace_file)
            session.commit()
            session.refresh(workspace_file)
            return workspace_file

    def seed_references(
        self, workspace_id: str, source_kind: str, source_id: str, folder: str, files: list[SeedFileReference]
    ) -> SeedOutcome:
        """Record the source as owner of ``folder`` and add rows pointing at its blobs, in one transaction.

        Nothing is committed unless every statement succeeds, so an interrupted call leaves no state behind.
        Existing rows (live or soft-deleted) at a target path are never touched. An unknown ``source_kind`` raises
        ValueError before anything is written.
        """
        blob_owner = source_blob_owner(source_kind, source_id)
        now = _utcnow()
        with Session(AgentWorkspaceFolderOwner.get_engine()) as session:
            claimed = session.execute(
                _insert(session, AgentWorkspaceFolderOwner.__table__)
                .values(
                    id=str(uuid4()),
                    date=now,
                    update_date=now,
                    workspace_id=workspace_id,
                    source_kind=source_kind,
                    source_id=source_id,
                    folder=folder,
                )
                .on_conflict_do_nothing()
            )
            if claimed.rowcount == 0:
                session.rollback()
                same_source = session.exec(
                    select(AgentWorkspaceFolderOwner.id).where(
                        AgentWorkspaceFolderOwner.workspace_id == workspace_id,
                        AgentWorkspaceFolderOwner.source_kind == source_kind,
                        AgentWorkspaceFolderOwner.source_id == source_id,
                    )
                ).first()
                return SeedOutcome.ALREADY_SEEDED if same_source is not None else SeedOutcome.COLLISION

            if files:
                rows = [
                    {
                        "id": str(uuid4()),
                        "date": now,
                        "update_date": now,
                        "workspace_id": workspace_id,
                        "path": reference.path,
                        "blob_owner": blob_owner,
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

    def get_folder_owner(self, workspace_id: str, source_kind: str, source_id: str) -> AgentWorkspaceFolderOwner | None:
        with Session(AgentWorkspaceFolderOwner.get_engine()) as session:
            statement = select(AgentWorkspaceFolderOwner).where(
                AgentWorkspaceFolderOwner.workspace_id == workspace_id,
                AgentWorkspaceFolderOwner.source_kind == source_kind,
                AgentWorkspaceFolderOwner.source_id == source_id,
            )
            return session.exec(statement).first()

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

    def soft_delete_file(self, workspace_id: str, path: str) -> bool:
        with Session(AgentWorkspaceFile.get_engine()) as session:
            statement = select(AgentWorkspaceFile).where(
                AgentWorkspaceFile.workspace_id == workspace_id,
                AgentWorkspaceFile.path == path,
                AgentWorkspaceFile.deleted_at.is_(None),
            )
            workspace_file = session.exec(statement).first()
            if not workspace_file:
                return False

            now = datetime.now(UTC).replace(tzinfo=None)
            workspace_file.deleted_at = now
            workspace_file.update_date = now
            session.add(workspace_file)
            session.commit()
            return True

    def delete_by_execution_id(self, execution_id: str, user_id: str) -> bool:
        """Delete the (execution_id, user) workspace and all its file rows; False when none exists.

        Workflow runs key their workspace by execution id as the conversation id. Blobs are not touched;
        folder-owner rows are removed by the foreign-key cascade.
        """
        with Session(AgentWorkspace.get_engine()) as session:
            statement = select(AgentWorkspace).where(
                AgentWorkspace.conversation_id == execution_id,
                AgentWorkspace.user_id == user_id,
            )
            workspace = session.exec(statement).first()
            if workspace is None:
                return False

            session.exec(delete(AgentWorkspaceFile).where(col(AgentWorkspaceFile.workspace_id) == workspace.id))
            session.delete(workspace)
            session.commit()
            return True
