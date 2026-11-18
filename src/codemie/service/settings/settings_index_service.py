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

import json
from collections.abc import Set as AbstractSet
from math import ceil
from typing import Optional, Dict, Any

from fastapi import status
from sqlalchemy import ColumnElement

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import Settings, SettingType
from codemie.rest_api.models.assistant import CreatedByUser
from codemie_tools.base.models import CredentialTypes
from codemie.rest_api.security.user import User
from codemie.service.filter.filter_services import SettingsFilter
from codemie.service.settings.base_settings import BaseSettingsService
from codemie.service.settings.settings import SettingsService
from sqlmodel import and_, or_, select, Session, func, not_

INVALID_FILTERS_MESSAGE = "Invalid filters"
INVALID_FILTERS_HELP = 'Pass `filters` as a URL-encoded JSON object, for example {"search": "jira"}.'


class SettingsIndexService(BaseSettingsService):
    LIST_OF_SENSITIVE_FIELDS = SettingsService.LIST_OF_SENSITIVE_FIELDS

    @classmethod
    def _parse_filters(cls, raw: str) -> dict[str, Any]:
        """Parse a raw ``filters`` query value.

        Raises:
            ExtendedHTTPException: 422 when ``raw`` is not valid JSON, is nested too deeply to parse, or is not a
                JSON object.
        """
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                message=INVALID_FILTERS_MESSAGE,
                details=f"`filters` is not valid JSON: {e.msg}.",
                help=INVALID_FILTERS_HELP,
            ) from e
        except RecursionError as e:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                message=INVALID_FILTERS_MESSAGE,
                details="`filters` is nested too deeply to parse.",
                help=INVALID_FILTERS_HELP,
            ) from e
        if not isinstance(parsed, dict):
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                message=INVALID_FILTERS_MESSAGE,
                details=f"`filters` must be a JSON object, got {type(parsed).__name__}.",
                help=INVALID_FILTERS_HELP,
            )
        return parsed

    @classmethod
    def run(
        cls,
        user: User,
        scopes: AbstractSet[SettingType],
        page: int = 0,
        per_page: int = 10,
        filters: Optional[Dict[str, Any]] | str | None = None,
    ) -> dict[str, Any]:
        """List the integrations ``user`` may see in any of ``scopes`` as one page, newest first.

        Each scope keeps its own visibility rule; a row is listed when it passes the rule of its scope.
        Sensitive credential values are masked.

        ``filters`` is either an already-parsed mapping or a raw URL-encoded JSON string; a raw string is
        parsed and validated here (422 on malformed input).

        Raises:
            ExtendedHTTPException: 422 when a raw ``filters`` string is not valid JSON, is nested too deeply
                to parse, or is not a JSON object.
            ValueError: ``scopes`` is empty, or holds an unknown scope.
        """
        parsed_filters = cls._parse_filters(filters) if isinstance(filters, str) else (filters or {})

        data, total = cls._query_postgres(
            user=user, scopes=scopes, page=page, per_page=per_page, filters=parsed_filters
        )

        data = [
            cls.hide_sensitive_fields(data=item, force_all=item.credential_type == CredentialTypes.ENVIRONMENT_VARS)
            for item in data
        ]

        meta = {"page": page, "per_page": per_page, "total": total, "pages": ceil(total / per_page)}

        return {"data": data, "pagination": meta}

    @classmethod
    def _scope_condition(cls, user: User, scope: SettingType) -> ColumnElement[bool]:
        """Return the rows of one scope that ``user`` may list, as a single SQL condition.

        This is the one definition of each scope's visibility rule for the list query (``get_users``, the
        Created-by options source, keeps its own copy). The condition carries its own
        ``setting_type`` match, so conditions of several scopes can be ORed into one query.

        Raises:
            ValueError: ``scope`` is not ``USER`` or ``PROJECT``.
        """
        if scope == SettingType.PROJECT:
            clauses = []
            if not user.is_admin:
                clauses.append(Settings.project_name.in_(user.admin_project_names))
            if not user.is_admin_or_maintainer:
                clauses.append(
                    not_(
                        (Settings.credential_type == CredentialTypes.LITE_LLM)
                        & Settings.alias.like(f"{SettingsService.PROJECT_BUDGET_ALIAS_PREFIX}%")
                    )
                )
        elif scope == SettingType.USER:
            clauses = [
                Settings.user_id == user.id,
                not_(Settings.alias.like(f"{SettingsService.INTERNAL_PREFIX}%")),
            ]
        else:
            raise ValueError(f"Unknown settings scope: {scope!r}")

        return and_(Settings.setting_type == scope, *clauses)

    @classmethod
    def _query_postgres(
        cls,
        user: User,
        scopes: AbstractSet[SettingType],
        page: int = 0,
        per_page: int = 10,
        filters: Optional[Dict[str, Any]] = None,
    ) -> tuple[list[Settings], int]:
        """List the rows ``user`` may see in any of ``scopes``, newest first, one page at a time.

        Raises:
            ValueError: ``scopes`` is empty, or holds an unknown scope.
        """
        if not scopes:
            raise ValueError("At least one settings scope is required")

        with Session(Settings.get_engine()) as session:
            query = select(Settings).where(or_(*(cls._scope_condition(user, scope) for scope in scopes)))

            if filters:
                query = SettingsFilter.add_sql_filters(query, model_class=Settings, raw_filters=filters)

            # Apply sorting; id breaks date ties so every row lands on exactly one page
            query = query.order_by(Settings.date.desc(), Settings.id.asc())

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
