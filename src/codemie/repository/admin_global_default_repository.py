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

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from codemie.rest_api.models.admin_global_default import AdminGlobalDefault


class AdminGlobalDefaultRepository:
    """Read and replace the singleton admin choice in one database transaction."""

    @staticmethod
    def get(session: Session) -> AdminGlobalDefault | None:
        return session.exec(select(AdminGlobalDefault).where(AdminGlobalDefault.id == 1)).first()

    @staticmethod
    def replace(session: Session, base_name: str, admin_id: str, admin_name: str) -> AdminGlobalDefault:
        choice = AdminGlobalDefaultRepository.get(session)
        if choice is None:
            choice = AdminGlobalDefault(id=1, base_name=base_name, updated_by_id=admin_id, updated_by_name=admin_name)
            session.add(choice)
            try:
                session.commit()
            except IntegrityError:
                # A concurrent first writer committed after our read. Retry as an update.
                session.rollback()
                choice = AdminGlobalDefaultRepository.get(session)
                if choice is None:
                    raise
            else:
                session.refresh(choice)
                return choice

        choice.base_name = base_name
        choice.updated_by_id = admin_id
        choice.updated_by_name = admin_name
        session.add(choice)
        session.commit()
        session.refresh(choice)
        return choice

    @staticmethod
    def clear(session: Session) -> None:
        session.exec(delete(AdminGlobalDefault).where(AdminGlobalDefault.id == 1))
        session.commit()


admin_global_default_repository = AdminGlobalDefaultRepository()
