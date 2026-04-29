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

from datetime import UTC, datetime
from typing import Optional
from uuid import uuid4

from sqlmodel import Session, select

from codemie.rest_api.models.agent_workspace import AgentWorkspace, AgentWorkspaceFile


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
