# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

import math
from typing import Any, Dict, List, Optional
from datetime import datetime, UTC

from sqlmodel import and_, select, Session, func

from codemie.core.models import CreatedByUser
from codemie.rest_api.models.guardrail import (
    Guardrail,
    GuardrailAssignment,
    GuardrailEntity,
    GuardrailMode,
    GuardrailSource,
)
from codemie.rest_api.security.user import User


class GuardrailRepository:
    """
    Repository for managing Guardrail entities.
    """

    DEFAULT_PER_PAGE = 10000

    def query(
        self,
        user: User,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 0,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> Dict[str, Any]:
        """
        Query guardrails based on specified criteria.
        """
        with Session(Guardrail.get_engine()) as session:
            query = select(Guardrail)

            # Admins see all guardrails (no filter needed)
            if not user.is_admin:
                query = query.where(
                    (Guardrail.project_name.in_(user.admin_project_names))  # type: ignore - Admin shared projects
                    | (Guardrail.created_by["id"].astext == user.id)  # type: ignore - Own guardrails
                )

            if filters:
                if filters.get("project"):
                    query = query.where(Guardrail.project_name == filters["project"])
                if filters.get("setting_id"):
                    query = query.where(Guardrail.bedrock["bedrock_aws_settings_id"].astext == filters["setting_id"])  # type: ignore

            # Sort by update_date descending (if present)
            query = query.order_by(Guardrail.update_date.desc().nullslast(), Guardrail.date.desc().nullslast())  # type: ignore

            # Pagination
            total = session.exec(select(func.count()).select_from(query.subquery())).one()
            query = query.offset(page * per_page).limit(per_page)
            guardrails = session.exec(query).all()

        pages = math.ceil(total / per_page)

        data = [
            {
                "guardrailId": g.id,
                "name": g.bedrock.bedrock_name if g.bedrock else "",
                "description": g.description,
            }
            for g in guardrails
        ]

        return {
            "data": data,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": pages,
            },
        }

    def get_guardrails_by_ids(self, guardrail_ids: List[str]) -> List[Guardrail]:
        """
        Retrieve multiple guardrails by their IDs.
        """
        if not guardrail_ids:
            return []

        with Session(Guardrail.get_engine()) as session:
            guardrails = session.exec(select(Guardrail).where(Guardrail.id.in_(guardrail_ids))).all()  # type: ignore

            return list(guardrails)

    def assign_guardrail_to_entity(
        self,
        entity_type: GuardrailEntity,
        entity_id: str,
        guardrail_id: str,
        source: GuardrailSource,
        mode: GuardrailMode,
        project_name: str,
        user: User,
        scope: Optional[GuardrailEntity] = None,
    ):
        with Session(GuardrailAssignment.get_engine()) as session:
            assignment = GuardrailAssignment(
                entity_type=entity_type,
                entity_id=entity_id,
                guardrail_id=guardrail_id,
                source=source,
                mode=mode,
                project_name=project_name,
                scope=scope,
                created_by=CreatedByUser(id=user.id, username=user.username, name=user.name),
                date=datetime.now(UTC),
                update_date=datetime.now(UTC),
            )
            session.add(assignment)
            session.commit()
            session.refresh(assignment)
            return assignment

    def get_all_effective_guardrail_ids_for_entity(
        self,
        entity_type: GuardrailEntity,
        entity_id: str,
        project_name: str,
        source: GuardrailSource,
    ) -> List[str]:
        """
        Get all unique guardrail IDs for an entity in a single query.

        This retrieves guardrail IDs from three levels:
        1. Project-level guardrails (entity_type=PROJECT, entity_id=project_name, scope=PROJECT)
        2. Project-entity-level guardrails (entity_type=PROJECT, entity_id=project_name, scope=entity_type)
        3. Entity-specific guardrails (entity_type=entity_type, entity_id=entity_id)

        Results are filtered by source (INPUT, OUTPUT, or BOTH) and automatically deduplicated by DISTINCT.

        Args:
            entity_type: The type of entity (ASSISTANT, WORKFLOW, KNOWLEDGEBASE)
            entity_id: The ID of the specific entity
            project_name: The project name
            source: The source to filter by (INPUT, OUTPUT, or BOTH)

        Returns:
            List of unique guardrail IDs
        """
        with Session(GuardrailAssignment.get_engine()) as session:
            # Build conditions for the three levels
            project_level = and_(
                GuardrailAssignment.entity_type == GuardrailEntity.PROJECT,
                GuardrailAssignment.entity_id == project_name,
                GuardrailAssignment.scope == GuardrailEntity.PROJECT,
            )

            project_entity_level = and_(
                GuardrailAssignment.entity_type == GuardrailEntity.PROJECT,
                GuardrailAssignment.entity_id == project_name,
                GuardrailAssignment.scope == entity_type,
            )

            entity_level = and_(
                GuardrailAssignment.entity_type == entity_type,
                GuardrailAssignment.entity_id == entity_id,
            )

            # Select DISTINCT guardrail_id only
            query = (
                select(GuardrailAssignment.guardrail_id)
                .distinct()
                .where(
                    and_(
                        # Match any of the three levels (OR condition)
                        (project_level | project_entity_level | entity_level),
                        # Filter by source (BOTH matches any source)
                        ((GuardrailAssignment.source == source) | (GuardrailAssignment.source == GuardrailSource.BOTH)),
                    )
                )
            )

            # This returns a list of strings directly
            guardrail_ids = session.exec(query).all()
            return list(guardrail_ids)

    def get_all_assignments_for_guardrail(self, guardrail_id: str) -> List[GuardrailAssignment]:
        with Session(GuardrailAssignment.get_engine()) as session:
            guardrails = session.exec(
                select(GuardrailAssignment)
                .where(
                    GuardrailAssignment.guardrail_id == guardrail_id,
                )
                .order_by(GuardrailAssignment.entity_type, GuardrailAssignment.entity_id)
            ).all()

            return list(guardrails)

    def get_guardrail_assignments_for_entity(self, entity_type: GuardrailEntity, entity_id: str):
        with Session(GuardrailAssignment.get_engine()) as session:
            return session.exec(
                select(GuardrailAssignment).where(
                    GuardrailAssignment.entity_type == entity_type,
                    GuardrailAssignment.entity_id == entity_id,
                )
            ).all()

    def get_entity_type_and_project_guardrail_assignments(
        self, project_name: str, entity_type: GuardrailEntity
    ) -> List[GuardrailAssignment]:
        with Session(GuardrailAssignment.get_engine()) as session:
            project_level = and_(
                GuardrailAssignment.entity_type == GuardrailEntity.PROJECT,
                GuardrailAssignment.entity_id == project_name,
                GuardrailAssignment.scope == GuardrailEntity.PROJECT,
            )

            project_entity_level = and_(
                GuardrailAssignment.entity_type == GuardrailEntity.PROJECT,
                GuardrailAssignment.entity_id == project_name,
                GuardrailAssignment.scope == entity_type,
            )

            assignments = session.exec(select(GuardrailAssignment).where(project_level | project_entity_level)).all()

            return list(assignments)

    def get_entity_guardrail_assignments(
        self, entity_type: GuardrailEntity, entity_id: str
    ) -> List[GuardrailAssignment]:
        with Session(GuardrailAssignment.get_engine()) as session:
            assignments = session.exec(
                select(GuardrailAssignment).where(
                    GuardrailAssignment.entity_type == entity_type,
                    GuardrailAssignment.entity_id == entity_id,
                )
            ).all()

            return list(assignments)

    def remove_guardrail_assignments_for_entity(self, entity_type: GuardrailEntity, entity_id: str):
        with Session(GuardrailAssignment.get_engine()) as session:
            assignments = session.exec(
                select(GuardrailAssignment).where(
                    GuardrailAssignment.entity_type == entity_type,
                    GuardrailAssignment.entity_id == entity_id,
                )
            ).all()
            if not assignments:
                return
            for a in assignments:
                session.delete(a)
            session.commit()

    def remove_guardrails_assignments_by_ids(self, assignment_ids: List[str]):
        if not assignment_ids:
            return

        with Session(GuardrailAssignment.get_engine()) as session:
            # Delete by ID to avoid detached object issues
            assignments = session.exec(
                select(GuardrailAssignment).where(GuardrailAssignment.id.in_(assignment_ids))  # type: ignore
            ).all()

            if not assignments:
                return

            for assignment in assignments:
                session.delete(assignment)

            session.commit()

    def remove_guardrail_assignments_by_guardrail_id(self, guardrail_id: str):
        with Session(GuardrailAssignment.get_engine()) as session:
            assignments = session.exec(
                select(GuardrailAssignment).where(
                    GuardrailAssignment.guardrail_id == guardrail_id,
                )
            ).all()

            if not assignments:
                return

            for assignment in assignments:
                session.delete(assignment)

            session.commit()
