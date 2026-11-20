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

"""``categories_by_id``/``require_existing`` against the real, seeded ``categories`` table."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.service.hosted_applications.categories import INVALID_CATEGORIES, categories_by_id, require_existing
from tests.codemie.service.hosted_applications.conftest import CATEGORY_A, CATEGORY_B, SEEDED_CATEGORIES


@pytest.mark.asyncio
async def test_categories_by_id_maps_seeded_rows_and_skips_missing(database):
    engine, _ = database
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await categories_by_id(session, [CATEGORY_B, "missing", CATEGORY_A])
    assert result == {CATEGORY_B: SEEDED_CATEGORIES[CATEGORY_B], CATEGORY_A: SEEDED_CATEGORIES[CATEGORY_A]}


@pytest.mark.asyncio
async def test_categories_by_id_empty_input_skips_the_query(database):
    engine, _ = database
    async with AsyncSession(engine, expire_on_commit=False) as session:
        assert await categories_by_id(session, []) == {}


@pytest.mark.asyncio
async def test_require_existing_returns_the_mapping_when_all_ids_exist(database):
    engine, _ = database
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await require_existing(session, [CATEGORY_A, CATEGORY_B])
    assert result == {CATEGORY_A: SEEDED_CATEGORIES[CATEGORY_A], CATEGORY_B: SEEDED_CATEGORIES[CATEGORY_B]}


@pytest.mark.asyncio
async def test_require_existing_raises_invalid_categories_naming_the_missing_ids(database):
    engine, _ = database
    async with AsyncSession(engine, expire_on_commit=False) as session:
        with pytest.raises(HostedApplicationError) as excinfo:
            await require_existing(session, [CATEGORY_A, "missing-1", "missing-2"])
    error = excinfo.value
    assert error.code == INVALID_CATEGORIES
    assert error.status == 422
    assert error.message == "Invalid category IDs: ['missing-1', 'missing-2']"
