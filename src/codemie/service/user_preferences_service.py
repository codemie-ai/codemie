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

import logging
import math

from sqlalchemy import or_, func, cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import select

from codemie.clients.postgres import get_session
from codemie.core.ability import Ability
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.workflow_models.workflow_config import WorkflowConfig
from codemie.repository.skill_repository import SkillRepository
from codemie.repository.user_preferences_repository import user_preferences_repository
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.skill import Skill, SkillVisibility
from codemie.rest_api.models.user_preferences import (
    FavoriteItem,
    FavoritesData,
    FavoritesListResult,
    UserPreferences,
)
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_user_interaction_service import (
    get_reactions_by_user as get_assistant_reactions_by_user,
)
from codemie.service.skill_user_interaction_service import (
    get_reactions_by_user as get_skill_reactions_by_user,
)

logger = logging.getLogger(__name__)


class UserPreferencesService:
    """Business logic for user favorites and pinned-assistants preferences."""

    # =========================================================================
    # Profile CRUD
    # =========================================================================

    @staticmethod
    def get_profile(user_id: str) -> UserPreferences:
        with get_session() as session:
            profile = user_preferences_repository.get_by_user_id(session, user_id)
            if profile is None:
                logger.info(f"Profile not found for user {user_id}")
                raise ExtendedHTTPException(code=404, message=f"Profile not found for user {user_id}")
            return profile

    @staticmethod
    def upsert_profile(
        user_id: str,
        pinned_assistants: list[str] | None,
        favorites: FavoritesData | None,
    ) -> UserPreferences:
        with get_session() as session:
            return user_preferences_repository.upsert(session, user_id, pinned_assistants, favorites)

    # =========================================================================
    # Favorites list endpoints
    # =========================================================================

    @staticmethod
    def get_favorite_assistants(
        user_id: str,
        current_user: User,
        search: str | None = None,
        project: list[str] | None = None,
        categories: list[str] | None = None,
        created_by: str | None = None,
        shared: bool | None = None,
        page: int = 0,
        per_page: int = 12,
    ) -> FavoritesListResult:
        with get_session() as session:
            profile = user_preferences_repository.get_by_user_id(session, user_id)
            favorite_ids = getattr(profile.favorites, "assistants", []) if profile and profile.favorites else []
            if not favorite_ids:
                return UserPreferencesService._empty_result(page, per_page)

            pinned_set = set(profile.pinned_assistants) if profile.pinned_assistants else set()

            query = select(Assistant).where(Assistant.id.in_(favorite_ids))

            if search:
                query = query.where(Assistant.name.ilike(f"%{search}%"))
            if project:
                query = query.where(Assistant.project.in_(project))
            if categories:
                cat_conditions = [cast(Assistant.categories, JSONB).contains([cat]) for cat in categories]
                query = query.where(or_(*cat_conditions))
            if created_by:
                query = query.where(Assistant.created_by["name"].astext == created_by)
            if shared is not None:
                query = query.where(Assistant.shared == shared)

            total = session.exec(select(func.count()).select_from(query.subquery())).one()
            rows = list(
                session.exec(query.order_by(Assistant.update_date.desc()).offset(page * per_page).limit(per_page)).all()
            )

            assistant_ids_on_page = {a.id for a in rows}
            all_reactions = get_assistant_reactions_by_user(user_id)
            reaction_map = {
                r.assistant_id: r.reaction for r in all_reactions if r.assistant_id in assistant_ids_on_page
            }

            ability = Ability(current_user)
            items = [
                FavoriteItem(
                    id=a.id,
                    icon_url=a.icon_url or "",
                    name=a.name,
                    description=a.description or "",
                    type=a.type.value if a.type else None,
                    is_global=a.is_global,
                    shared=a.shared,
                    created_by=a.created_by.model_dump() if a.created_by else None,
                    is_favorited=True,
                    is_pinned=a.id in pinned_set,
                    is_liked=reaction_map.get(a.id) == "like",
                    is_disliked=reaction_map.get(a.id) == "dislike",
                    unique_likes_count=a.unique_likes_count or 0,
                    unique_dislikes_count=a.unique_dislikes_count or 0,
                    user_abilities=[action.value for action in ability.list(a)],
                )
                for a in rows
            ]
            logger.debug(f"Returning {len(rows)} favorite assistants (total={total}) for user {user_id}")
            return UserPreferencesService._paginated_result(items, page, per_page, total)

    @staticmethod
    def get_favorite_skills(
        user_id: str,
        current_user: User,
        search: str | None = None,
        project: list[str] | None = None,
        categories: list[str] | None = None,
        created_by: str | None = None,
        visibility: str | None = None,
        page: int = 0,
        per_page: int = 12,
    ) -> FavoritesListResult:
        with get_session() as session:
            profile = user_preferences_repository.get_by_user_id(session, user_id)
            favorite_ids = getattr(profile.favorites, "skills", []) if profile and profile.favorites else []
            if not favorite_ids:
                return UserPreferencesService._empty_result(page, per_page)

            query = select(Skill).where(Skill.id.in_(favorite_ids))

            if search:
                query = query.where(Skill.name.ilike(f"%{search}%"))
            if project:
                query = query.where(Skill.project.in_(project))
            if categories:
                cat_conditions = [Skill.categories.contains([cat]) for cat in categories]
                query = query.where(or_(*cat_conditions))
            if created_by:
                query = query.where(Skill.created_by["name"].astext == created_by)
            if visibility:
                try:
                    query = query.where(Skill.visibility == SkillVisibility(visibility))
                except ValueError:
                    raise ExtendedHTTPException(
                        code=400,
                        message=f"Invalid visibility value: '{visibility}'",
                        details=f"Allowed values: {[v.value for v in SkillVisibility]}",
                    )

            total = session.exec(select(func.count()).select_from(query.subquery())).one()
            rows = list(
                session.exec(query.order_by(Skill.updated_date.desc()).offset(page * per_page).limit(per_page)).all()
            )

            skill_ids = [s.id for s in rows]
            assistants_count_map = SkillRepository.get_assistants_count_for_skills(session, skill_ids)

            skill_ids_on_page = {s.id for s in rows}
            all_reactions = get_skill_reactions_by_user(user_id)
            reaction_map = {r.skill_id: r.reaction for r in all_reactions if r.skill_id in skill_ids_on_page}

            ability = Ability(current_user)
            items = [
                FavoriteItem(
                    id=s.id,
                    icon_url="",
                    name=s.name,
                    description=s.description or "",
                    visibility=s.visibility.value if s.visibility else None,
                    created_by=s.created_by.model_dump() if s.created_by else None,
                    is_favorited=True,
                    is_liked=reaction_map.get(s.id) == "like",
                    is_disliked=reaction_map.get(s.id) == "dislike",
                    unique_likes_count=s.unique_likes_count or 0,
                    unique_dislikes_count=s.unique_dislikes_count or 0,
                    assistants_count=assistants_count_map.get(s.id, 0),
                    user_abilities=[action.value for action in ability.list(s)],
                )
                for s in rows
            ]
            logger.debug(f"Returning {len(rows)} favorite skills (total={total}) for user {user_id}")
            return UserPreferencesService._paginated_result(items, page, per_page, total)

    @staticmethod
    def get_favorite_workflows(
        user_id: str,
        current_user: User,
        search: str | None = None,
        project: list[str] | None = None,
        created_by: str | None = None,
        shared: bool | None = None,
        page: int = 0,
        per_page: int = 12,
    ) -> FavoritesListResult:
        with get_session() as session:
            profile = user_preferences_repository.get_by_user_id(session, user_id)
            favorite_ids = getattr(profile.favorites, "workflows", []) if profile and profile.favorites else []
            if not favorite_ids:
                return UserPreferencesService._empty_result(page, per_page)

            query = select(WorkflowConfig).where(WorkflowConfig.id.in_(favorite_ids))

            if search:
                query = query.where(WorkflowConfig.name.ilike(f"%{search}%"))
            if project:
                query = query.where(WorkflowConfig.project.in_(project))
            if created_by:
                query = query.where(WorkflowConfig.created_by["name"].astext == created_by)
            if shared is not None:
                query = query.where(WorkflowConfig.shared == shared)

            total = session.exec(select(func.count()).select_from(query.subquery())).one()
            rows = list(
                session.exec(
                    query.order_by(WorkflowConfig.update_date.desc()).offset(page * per_page).limit(per_page)
                ).all()
            )

            ability = Ability(current_user)
            items = [
                FavoriteItem(
                    id=w.id,
                    icon_url=w.icon_url or "",
                    name=w.name,
                    description=w.description or "",
                    shared=w.shared,
                    created_by=w.created_by.model_dump() if w.created_by else None,
                    is_favorited=True,
                    is_global=w.is_global,
                    user_abilities=[action.value for action in ability.list(w)],
                )
                for w in rows
            ]
            logger.debug(f"Returning {len(rows)} favorite workflows (total={total}) for user {user_id}")
            return UserPreferencesService._paginated_result(items, page, per_page, total)

    # =========================================================================
    # Helpers
    # =========================================================================

    @staticmethod
    def _empty_result(page: int, per_page: int) -> FavoritesListResult:
        return FavoritesListResult(data=[], page=page, per_page=per_page, total=0, pages=0)

    @staticmethod
    def _paginated_result(
        items: list[FavoriteItem],
        page: int,
        per_page: int,
        total: int,
    ) -> FavoritesListResult:
        pages = math.ceil(total / per_page) if per_page > 0 else 1
        return FavoritesListResult(data=items, page=page, per_page=per_page, total=total, pages=pages)


user_preferences_service = UserPreferencesService()
