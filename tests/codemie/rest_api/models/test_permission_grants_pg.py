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


"""PostgreSQL-gated check: a datasource APPLICATION grant at any level passes READ checks.

Skipped unless HOSTED_TEST_DATABASE_URL points at an isolated PostgreSQL database
that already has the application schema migrated.
"""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session

from codemie.core.ability import Action
from codemie.rest_api.models.permission import Permission, PrincipalType, ResourceType


@pytest.fixture
def pg_engine():
    url = os.environ.get("HOSTED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set HOSTED_TEST_DATABASE_URL to an isolated PostgreSQL database")
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.mark.parametrize("level", [Action.READ, Action.WRITE, Action.DELETE])
def test_application_grant_at_any_level_passes_read_checks(pg_engine, monkeypatch, level):
    resource_id = uuid4()
    app_name = f"app-{uuid4().hex[:8]}"
    monkeypatch.setattr(Permission, "get_engine", classmethod(lambda cls: pg_engine))
    with Session(pg_engine) as session:
        row = Permission(
            resource_type=ResourceType.DATASOURCE,
            resource_id=resource_id,
            principal_type=PrincipalType.APPLICATION,
            principal_id=app_name,
            permission_level=level,
        )
        session.add(row)
        session.commit()
    try:
        instance = SimpleNamespace(id=resource_id)
        user = SimpleNamespace(username=app_name)
        monkeypatch.setattr(ResourceType, "type_for_instance", classmethod(lambda cls, _i: ResourceType.DATASOURCE))
        # IndexEncryptedSettingsService._check_permissions and Ability (ability.py:185) read through get_for.
        assert Permission.get_for(user, instance, Action.READ) is not None
        assert Permission.exists_for(user, instance, Action.READ) is True
    finally:
        with Session(pg_engine) as session:
            session.exec(Permission.__table__.delete().where(Permission.principal_id == app_name))
            session.commit()
