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

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from codemie.rest_api.models.llm_model_status import LLMModelStatus


class LLMModelStatusRepository:
    """Repository for persisted LLM model status overrides."""

    @staticmethod
    def get_all(session: Session) -> list[LLMModelStatus]:
        return list(session.exec(select(LLMModelStatus)).all())

    @staticmethod
    def get_by_base_name(session: Session, base_name: str) -> LLMModelStatus | None:
        return session.exec(select(LLMModelStatus).where(LLMModelStatus.base_name == base_name)).first()

    @staticmethod
    def upsert(
        session: Session,
        base_name: str,
        status: str,
        admin_id: str,
        admin_name: str,
    ) -> LLMModelStatus:
        existing = session.exec(select(LLMModelStatus).where(LLMModelStatus.base_name == base_name)).first()

        if existing is None:
            model_status = LLMModelStatus(
                base_name=base_name,
                status=status,
                updated_by_id=admin_id,
                updated_by_name=admin_name,
            )
            session.add(model_status)
            try:
                session.commit()
            except IntegrityError:
                # Concurrency backstop: two first-time PUTs for the same base_name can both see
                # existing is None and both attempt to insert; the loser hits the unique
                # constraint on base_name. Roll back and retry as an update instead of leaking
                # an uncaught IntegrityError as a 500.
                session.rollback()
                existing = session.exec(select(LLMModelStatus).where(LLMModelStatus.base_name == base_name)).first()
                if existing is None:
                    raise
                existing.status = status
                existing.updated_by_id = admin_id
                existing.updated_by_name = admin_name
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing
            session.refresh(model_status)
            return model_status

        existing.status = status
        existing.updated_by_id = admin_id
        existing.updated_by_name = admin_name
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing


llm_model_status_repository = LLMModelStatusRepository()
