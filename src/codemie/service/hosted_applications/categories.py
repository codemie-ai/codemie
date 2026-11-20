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

"""Shared-platform category lookups for hosted applications.

Mirrors ``DatabaseCategoryService.enrich_categories`` (``service/assistant/category_service.py``), but against
the hosted transaction's own async session instead of a sync, per-call session: one ``IN`` query, missing ids
silently skipped. Callers that need stored order read it off the id list they already have, not off the dict.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.rest_api.models.category import Category

INVALID_CATEGORIES = "INVALID_CATEGORIES"
PUBLISHED_NEEDS_ONE = "A published application needs at least one category"


async def categories_by_id(session: AsyncSession, ids: list[str]) -> dict[str, str]:
    """``{id: name}`` for every id in ``ids`` that still exists; missing ids are skipped, not an error."""
    if not ids:
        return {}
    rows = (await session.execute(select(Category).where(Category.id.in_(ids)))).scalars().all()
    return {row.id: row.name for row in rows}


async def require_existing(session: AsyncSession, ids: list[str]) -> dict[str, str]:
    """``{id: name}`` for ``ids``, or ``HostedApplicationError(INVALID_CATEGORIES)`` naming what is missing."""
    names = await categories_by_id(session, ids)
    missing = [category_id for category_id in ids if category_id not in names]
    if missing:
        raise HostedApplicationError(INVALID_CATEGORIES, f"Invalid category IDs: {missing}", 422)
    return names
