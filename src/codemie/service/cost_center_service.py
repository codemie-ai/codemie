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
from codemie.configs.customer_config import customer_config
from codemie.configs.logger import logger
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
    def ensure_feature_enabled(cls) -> None:
        if not customer_config.is_feature_enabled("costCenters"):
            raise ExtendedHTTPException(
                code=403,
                message="Feature not available",
                details="Cost centers are not enabled for this customer.",
                help="Contact your system administrator to enable the 'features:costCenters' component.",
            )

    @classmethod
    def create(cls, session: Session, *, user: User, name: str, description: str | None) -> CostCenter:
        cls.ensure_feature_enabled()
        cls.ensure_admin(user)
        validated_name = cls.validate_name(name)
        validated_description = cls.validate_description(description)

        existing = cost_center_repository.get_by_name_case_insensitive(session, validated_name)
        if existing:
            raise ExtendedHTTPException(code=409, message=f"Cost center '{existing.name}' already exists")

        try:
            return cls._insert(session, name=validated_name, description=validated_description, created_by=user.id)
        except IntegrityError as exc:
            raise ExtendedHTTPException(code=409, message=f"Cost center '{validated_name}' already exists") from exc

    @classmethod
    def get_or_create(cls, session: Session, *, name: str, created_by: str) -> tuple[CostCenter, bool]:
        """Return the active cost center with this name, creating it when missing. Caller must authorize.

        Flushes without committing; an IntegrityError from a concurrent insert propagates so the caller can retry.
        """
        cls.ensure_feature_enabled()
        validated_name = cls.validate_name(name)

        existing = cost_center_repository.get_by_name_case_insensitive(session, validated_name)
        if existing:
            if existing.deleted_at is not None:
                raise ExtendedHTTPException(
                    code=409,
                    message=f"Cost center '{existing.name}' has been deleted",
                    help="Restore the cost center or use a different EPAM project code.",
                )
            return existing, False

        return cls._insert(session, name=validated_name, description=None, created_by=created_by), True

    @classmethod
    def _insert(cls, session: Session, *, name: str, description: str | None, created_by: str) -> CostCenter:
        cost_center = cost_center_repository.create(session, name=name, description=description, created_by=created_by)
        logger.info(
            f"cost_center_created: cost_center_id={cost_center.id}, name={name!r}, "
            f"by={created_by}, domain=project_management"
        )
        return cost_center

    @classmethod
    def list_paginated(cls, session: Session, *, user: User, search: str | None, page: int, per_page: int):
        cls.ensure_feature_enabled()
        cls.ensure_admin(user)
        return cost_center_repository.list_paginated(session, search=search, page=page, per_page=per_page)

    @classmethod
    def get_or_404(cls, session: Session, *, user: User, cost_center_id: UUID) -> CostCenter:
        cls.ensure_feature_enabled()
        cls.ensure_admin(user)
        cost_center = cost_center_repository.get_active_by_id(session, cost_center_id)
        if not cost_center:
            raise ExtendedHTTPException(code=404, message="Cost center not found")
        return cost_center

    @classmethod
    def update(cls, session: Session, *, user: User, cost_center_id: UUID, description: str | None) -> CostCenter:
        cost_center = cls.get_or_404(session, user=user, cost_center_id=cost_center_id)
        validated_description = cls.validate_description(description)
        updated = cost_center_repository.update(session, cost_center, description=validated_description)
        logger.info(
            f"cost_center_updated: cost_center_id={cost_center_id}, name={cost_center.name!r}, "
            f"updated_fields=['description'], by={user.id}, domain=project_management"
        )
        return updated

    @classmethod
    def ensure_exists_for_project(cls, session: Session, cost_center_id: UUID | None) -> CostCenter | None:
        if cost_center_id is None:
            return None
        cls.ensure_feature_enabled()
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
        logger.info(
            f"cost_center_deleted: cost_center_id={cost_center_id}, name={cost_center.name!r}, "
            f"by={user.id}, domain=project_management"
        )


cost_center_service = CostCenterService()
