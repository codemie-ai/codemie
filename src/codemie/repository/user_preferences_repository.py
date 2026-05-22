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

from sqlmodel import Session

from codemie.rest_api.models.user_preferences import FavoritesData, UserPreferences


class UserPreferencesRepository:
    """Repository for user preferences (favorites + pinned assistants) data operations."""

    @staticmethod
    def get_by_user_id(session: Session, user_id: str) -> UserPreferences | None:
        return session.get(UserPreferences, user_id)

    @staticmethod
    def upsert(
        session: Session,
        user_id: str,
        pinned_assistants: list[str] | None = None,
        favorites: FavoritesData | None = None,
    ) -> UserPreferences:
        existing = session.get(UserPreferences, user_id)
        if existing is None:
            profile = UserPreferences(
                user_id=user_id,
                pinned_assistants=pinned_assistants if pinned_assistants is not None else [],
                favorites=favorites if favorites is not None else FavoritesData(),
            )
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return profile

        if pinned_assistants is not None:
            existing.pinned_assistants = pinned_assistants
        if favorites is not None:
            existing.favorites = favorites
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing


user_preferences_repository = UserPreferencesRepository()
