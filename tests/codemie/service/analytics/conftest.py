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

"""Shared fixtures of the analytics service tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from codemie.service.analytics.dashboard_cache import reset_dashboard_cache

_PIPELINE_MODULE = "codemie.service.analytics.query_pipeline"
_INSIGHTS_HANDLER_MODULE = "codemie.service.analytics.handlers.cli.insights_handler"


@pytest.fixture(autouse=True)
def _empty_dashboard_caches():
    """The answers and the users ranking are cached per pod; a test must not see another test's."""
    reset_dashboard_cache()
    yield
    reset_dashboard_cache()


@asynccontextmanager
async def _fake_session():
    yield AsyncMock()


async def _resolve_each_to_itself(_session, identifiers):
    return {i: SimpleNamespace(id=i, username=None, email=None, name=None) for i in identifiers}


@pytest.fixture(autouse=True)
def _known_user_identifiers():
    """User lookups hit Postgres; by default every identifier is a known user that maps only to itself.

    Filters therefore pass through unchanged and user-detail lookups find the user. Patches the
    `user_repository` name in the consuming modules rather than the shared singleton's method,
    so tests of other `user_repository` consumers are unaffected.
    """
    with (
        patch(f"{_PIPELINE_MODULE}.get_async_session", side_effect=_fake_session),
        patch(f"{_PIPELINE_MODULE}.user_repository") as pipeline_user_repository,
        patch(f"{_INSIGHTS_HANDLER_MODULE}.get_async_session", side_effect=_fake_session),
        patch(f"{_INSIGHTS_HANDLER_MODULE}.user_repository") as handler_user_repository,
    ):
        pipeline_user_repository.afind_users_by_identifiers = AsyncMock(side_effect=_resolve_each_to_itself)
        handler_user_repository.afind_users_by_identifiers = AsyncMock(side_effect=_resolve_each_to_itself)
        yield
