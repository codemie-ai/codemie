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

from math import ceil
from typing import Optional, Dict, Any

from codemie.rest_api.models.settings import Settings, SettingType
from codemie.rest_api.models.assistant import CreatedByUser
from codemie_tools.base.models import CredentialTypes
from codemie.rest_api.security.user import User
from codemie.service.filter.filter_services import SettingsFilter
from codemie.service.settings.base_settings import BaseSettingsService
from codemie.service.settings.settings import SettingsService
from sqlmodel import select, Session, func, not_


class SettingsIndexService(BaseSettingsService):
    LIST_OF_SENSITIVE_FIELDS = SettingsService.LIST_OF_SENSITIVE_FIELDS

    @classmethod
    def run(
        cls,
        user: Optional[User] = None,
        settings_type: Optional[SettingType] = None,
        page: int = 0,
        per_page: int = 10,
        filters: Optional[Dict[str, Any]] = None,
    ):
        data, total = cls._query_postgres(
            user=user, settings_type=settings_type, page=page, per_page=per_page, filters=filters
        )

        data = [
            cls.hide_sensitive_fields(data=item, force_all=item.credential_type == CredentialTypes.ENVIRONMENT_VARS)
            for item in data
        ]

        meta = {"page": page, "per_page": per_page, "total": total, "pages": ceil(total / per_page)}

        return {"data": data, "pagination": meta}

    @classmethod
    def _query_postgres(
        cls,
        user: Optional[User] = None,
        settings_type: Optional[SettingType] = None,
        page: int = 0,
        per_page: int = 10,
        filters: Optional[Dict[str, Any]] = None,
    ):
        # PostgreSQL implementation
        with Session(Settings.get_engine()) as session:
            query = select(Settings)
            query = query.where(Settings.setting_type == settings_type)
            if settings_type == SettingType.PROJECT:
                if not user.is_admin:
                    query = query.where(Settings.project_name.in_(user.admin_project_names))
            else:
                query = query.where(Settings.user_id == user.id)
                query = query.where(not_(Settings.alias.like(f"{SettingsService.INTERNAL_PREFIX}%")))

            if filters:
                query = SettingsFilter.add_sql_filters(query, model_class=Settings, raw_filters=filters)

            # Apply sorting
            query = query.order_by(Settings.date.desc())

            # Apply pagination
            total = session.exec(select(func.count()).select_from(query.subquery())).one()
            query = query.offset(page * per_page).limit(per_page)

            # Execute query
            results = session.exec(query).all()

        return results, total

    @classmethod
    def get_users(cls, user: User, settings_type: Optional[SettingType] = None) -> list[CreatedByUser]:
        """
        Get list of users who created settings

        Args:
            user: The user making the request
            settings_type: Type of settings to filter by (USER or PROJECT)

        Returns:
            List of unique users who created settings,
            excluding None values and users with empty names
        """
        with Session(Settings.get_engine()) as session:
            # Query distinct created_by values
            query = select(Settings.created_by).distinct()

            if settings_type:
                query = query.where(Settings.setting_type == settings_type)

            # Apply visibility filters
            if settings_type == SettingType.PROJECT:
                if not user.is_admin:
                    query = query.where(Settings.project_name.in_(user.admin_project_names))
            elif settings_type == SettingType.USER:
                # For user settings, only show settings created by the current user
                query = query.where(Settings.user_id == user.id)

            # Execute the query and get results
            result = session.exec(query).all()

        # Filter out None values and users with empty names
        users = [creator for creator in result if creator and creator.name]

        # Sort by name
        users.sort(key=lambda x: x.name)

        return users
