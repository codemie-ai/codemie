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

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CostCenter
from codemie.repository.application_repository import application_repository
from codemie.repository.cost_center_repository import cost_center_repository
from codemie.rest_api.security.user import User


class CostCenterService:
    MAX_DESCRIPTION_LENGTH = 500

    @classmethod
    def validate_name(cls, name: str) -> str:
        if not re.fullmatch(config.COST_CENTER_NAME_PATTERN, name):
            raise ExtendedHTTPException(
                code=400,
                message="Invalid cost center name",
                details="Cost center names must contain exactly one '-' between lowercase letters or digits.",
                help="Examples: 'eng-123' and 'aa-bb2' are valid. 'eng--123' is not.",
            )
        return name

    @classmethod
    def validate_description(cls, description: str | None) -> str | None:
        if description is None:
            return None
        if len(description) > cls.MAX_DESCRIPTION_LENGTH:
            raise ExtendedHTTPException(code=400, message="Cost center description cannot exceed 500 characters")
        return description

    @classmethod
    def ensure_admin(cls, user: User) -> None:
        if not user.is_admin_or_maintainer:
            raise ExtendedHTTPException(code=403, message="Access denied")

    @classmethod
    def create(cls, session: Session, *, user: User, name: str, description: str | None) -> CostCenter:
        cls.ensure_admin(user)
        validated_name = cls.validate_name(name)
        validated_description = cls.validate_description(description)

        existing = cost_center_repository.get_by_name_case_insensitive(session, validated_name)
        if existing:
            raise ExtendedHTTPException(code=409, message=f"Cost center '{existing.name}' already exists")

        try:
            return cost_center_repository.create(
                session,
                name=validated_name,
                description=validated_description,
                created_by=user.id,
            )
        except IntegrityError as exc:
            raise ExtendedHTTPException(code=409, message=f"Cost center '{validated_name}' already exists") from exc

    @classmethod
    def list_paginated(cls, session: Session, *, user: User, search: str | None, page: int, per_page: int):
        cls.ensure_admin(user)
        return cost_center_repository.list_paginated(session, search=search, page=page, per_page=per_page)

    @classmethod
    def get_or_404(cls, session: Session, *, user: User, cost_center_id: UUID) -> CostCenter:
        cls.ensure_admin(user)
        cost_center = cost_center_repository.get_active_by_id(session, cost_center_id)
        if not cost_center:
            raise ExtendedHTTPException(code=404, message="Cost center not found")
        return cost_center

    @classmethod
    def update(cls, session: Session, *, user: User, cost_center_id: UUID, description: str | None) -> CostCenter:
        cost_center = cls.get_or_404(session, user=user, cost_center_id=cost_center_id)
        validated_description = cls.validate_description(description)
        return cost_center_repository.update(session, cost_center, description=validated_description)

    @classmethod
    def ensure_exists_for_project(cls, session: Session, cost_center_id: UUID | None) -> CostCenter | None:
        if cost_center_id is None:
            return None
        cost_center = cost_center_repository.get_active_by_id(session, cost_center_id)
        if not cost_center:
            raise ExtendedHTTPException(code=404, message="Selected cost center not found")
        return cost_center

    @classmethod
    def delete(cls, session: Session, *, user: User, cost_center_id: UUID) -> None:
        cost_center = cls.get_or_404(session, user=user, cost_center_id=cost_center_id)
        project_count = application_repository.count_active_projects_by_cost_center_id(session, cost_center.id)
        if project_count:
            raise ExtendedHTTPException(code=409, message="Cost center has linked active projects")
        cost_center.deleted_at = datetime.now()
        cost_center.update_date = datetime.now()
        session.add(cost_center)
        session.flush()


cost_center_service = CostCenterService()
