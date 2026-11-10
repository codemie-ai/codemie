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

"""``create_app`` stores the requested ``shared`` flag; a database-free check (the DB-backed suite covers the rest)."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.rest_api.models.hosted_application_requests import AppCreate
from codemie.service.hosted_applications import catalog as catalog_module
from codemie.service.hosted_applications.catalog import HostedApplicationCatalog


def fake_catalog(monkeypatch):
    added = []
    session = SimpleNamespace(scalar=AsyncMock(return_value=0), add=added.append, flush=AsyncMock())
    tx = SimpleNamespace(
        session=session,
        now=datetime(2026, 11, 7, tzinfo=timezone.utc),
        lock_quota=AsyncMock(),
        operation=AsyncMock(return_value=(SimpleNamespace(app_id=None), None)),
        finish=AsyncMock(side_effect=lambda op, body, status=200: body),
    )

    @asynccontextmanager
    async def transaction(*identity, **options):
        yield tx

    service = HostedApplicationCatalog(SimpleNamespace(transaction=transaction), directory=None)
    platform = SimpleNamespace(id="u-1", user_type="regular", has_access_to_application=lambda project: True)
    monkeypatch.setattr(service, "_platform_user", AsyncMock(return_value=platform))
    monkeypatch.setattr(service, "_project", AsyncMock(side_effect=lambda tx, app, user: {"shared": app.shared}))
    monkeypatch.setattr(catalog_module, "is_eligible", lambda user: True)
    return service, added


class Actor(tuple):
    """``HostedActor`` is unpacked into ``transaction(*identity)``; a tuple with the attributes ``create_app`` reads."""

    def __new__(cls):
        user = MagicMock(id="u-1", username="creator")
        user.name = "Creator"
        value = super().__new__(cls, (user, None))
        value.user, value.principal_id = user, user.id
        return value


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", [True, False])
async def test_create_stores_the_requested_shared_flag(monkeypatch, shared):
    service, added = fake_catalog(monkeypatch)
    request = AppCreate(name="Visual", description="Text", project="team-a", shared=shared)
    created = await service.create_app(Actor(), request, "key-1")
    assert created == {"shared": shared}
    assert [(app.shared, app.is_global) for app in added] == [(shared, False)]


@pytest.mark.asyncio
async def test_create_without_shared_stays_private(monkeypatch):
    service, added = fake_catalog(monkeypatch)
    await service.create_app(Actor(), AppCreate(name="Visual", description="Text", project="team-a"), "k")
    assert [app.shared for app in added] == [False]
