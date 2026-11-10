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


from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from codemie.core.ability import Action
from codemie.rest_api.models import permission as permission_module
from codemie.rest_api.models.permission import (
    Permission,
    PrincipalType,
    ResourceType,
    grant_exists,
    levels_covering,
    live_grants,
)
from codemie.rest_api.models.user_management import UserDB, eligible_internal_user


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def _params(statement) -> list:
    return list(statement.compile(dialect=postgresql.dialect()).params.values())


class TestLevelsCovering:
    def test_read_is_covered_by_every_level(self):
        assert levels_covering(Action.READ) == {Action.READ, Action.WRITE, Action.DELETE}

    def test_write_excludes_read(self):
        assert levels_covering(Action.WRITE) == {Action.WRITE, Action.DELETE}

    def test_delete_is_covered_only_by_delete(self):
        assert levels_covering(Action.DELETE) == {Action.DELETE}


class TestEligibleInternalUser:
    def test_active_not_deleted_not_external(self):
        sql = _sql(sa.select(UserDB.id).where(eligible_internal_user(UserDB)))
        assert "users.is_active" in sql
        assert "users.deleted_at IS NULL" in sql
        assert "users.user_type !=" in sql

    def test_binds_the_configured_external_user_type(self):
        from codemie.configs import config

        with patch.object(config, "EXTERNAL_USER_TYPE", "contractor"):
            statement = sa.select(UserDB.id).where(eligible_internal_user(UserDB))
        assert "contractor" in _params(statement)
        assert "external" not in _params(statement)


class TestLiveGrants:
    def test_joins_users_and_gates_internal_user(self):
        with patch.object(permission_module.config, "ENABLE_USER_MANAGEMENT", True):
            statement = live_grants(ResourceType.HOSTED_APPLICATION, sa.literal("u-1"), eligible_internal_user)
        sql = _sql(statement)
        assert "JOIN users ON users.id = permissions.principal_id" in sql
        assert "permissions.principal_type" in sql
        assert "users.is_active" in sql and "users.deleted_at IS NULL" in sql and "users.user_type" in sql
        assert PrincipalType.USER in _params(statement)
        for forbidden in ("WITH ", "DISTINCT", "GROUP BY", "CAST("):
            assert forbidden not in sql

    def test_selects_resource_id_and_level(self):
        with patch.object(permission_module.config, "ENABLE_USER_MANAGEMENT", True):
            statement = live_grants(ResourceType.DATASOURCE, sa.literal("u-1"), eligible_internal_user)
        assert [c.name for c in statement.selected_columns] == ["resource_id", "permission_level"]

    def test_user_management_off_is_always_false(self):
        with patch.object(permission_module.config, "ENABLE_USER_MANAGEMENT", False):
            statement = live_grants(ResourceType.HOSTED_APPLICATION, sa.literal("u-1"), eligible_internal_user)
        sql = _sql(statement)
        assert "false" in sql.lower() or "1 != 1" in sql
        assert "JOIN" not in sql

    def test_gate_is_read_at_call_time(self):
        with patch.object(permission_module.config, "ENABLE_USER_MANAGEMENT", False):
            off = _sql(live_grants(ResourceType.HOSTED_APPLICATION, sa.literal("u-1"), eligible_internal_user))
        with patch.object(permission_module.config, "ENABLE_USER_MANAGEMENT", True):
            on = _sql(live_grants(ResourceType.HOSTED_APPLICATION, sa.literal("u-1"), eligible_internal_user))
        assert off != on


class TestGrantExists:
    def test_compares_resource_id_without_cast_and_covers_action(self):
        with patch.object(permission_module.config, "ENABLE_USER_MANAGEMENT", True):
            statement = sa.select(sa.literal(1)).where(
                grant_exists(
                    ResourceType.HOSTED_APPLICATION,
                    Permission.resource_id,
                    sa.literal("u-1"),
                    Action.WRITE,
                    recipient=eligible_internal_user,
                )
            )
        sql = _sql(statement)
        assert "EXISTS" in sql
        assert "CAST(" not in sql
        assert "permissions.resource_id = permissions.resource_id" in sql
        assert "permissions.permission_level IN" in sql
        assert {Action.WRITE, Action.DELETE} <= set(_flatten(_params(statement)))
        assert Action.READ not in set(_flatten(_params(statement)))


class TestRecipientPolicy:
    def test_live_grants_apply_the_policy_they_are_given(self):
        """The internal-only policy is no longer baked into the generic statement (ALMA SC-9)."""
        with patch.object(permission_module.config, "ENABLE_USER_MANAGEMENT", True):
            sql = _sql(live_grants(ResourceType.HOSTED_APPLICATION, sa.literal("u-1"), lambda users: users.is_auditor))
        assert "users.is_auditor" in sql and "users.user_type" not in sql

    def test_grant_exists_compares_through_the_resource_key(self):
        with patch.object(permission_module.config, "ENABLE_USER_MANAGEMENT", True):
            statement = sa.select(sa.literal(1)).where(
                grant_exists(
                    ResourceType.ASSISTANT,
                    sa.literal("a-1"),
                    sa.literal("u-1"),
                    Action.READ,
                    recipient=eligible_internal_user,
                    resource_key=lambda column: sa.cast(column, sa.Text),
                )
            )
        assert "CAST(permissions.resource_id AS TEXT) =" in _sql(statement)


def _flatten(values):
    for value in values:
        if isinstance(value, (list, tuple, set)):
            yield from value
        else:
            yield value


class _FakeSession:
    statements: list = []

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def exec(self, statement):
        _FakeSession.statements.append(statement)
        return SimpleNamespace(first=lambda: "row")


class TestGetFor:
    def test_reads_application_rows_only_at_covering_levels(self):
        _FakeSession.statements = []
        instance = SimpleNamespace(id=uuid4())
        user = SimpleNamespace(username="app-name", id="u-1")
        with (
            patch.object(permission_module, "Session", _FakeSession),
            patch.object(Permission, "get_engine", return_value=None),
            patch.object(ResourceType, "type_for_instance", return_value=ResourceType.DATASOURCE),
        ):
            assert Permission.get_for(user, instance, Action.READ) == "row"
        statement = _FakeSession.statements[0]
        sql = _sql(statement)
        assert "permissions.principal_type" in sql and "permissions.principal_id" in sql
        assert "permissions.permission_level IN" in sql
        params = list(_flatten(_params(statement)))
        assert PrincipalType.APPLICATION in params
        assert PrincipalType.USER not in params
        assert "app-name" in params
        assert {Action.READ, Action.WRITE, Action.DELETE} <= set(params)

    def test_write_action_excludes_read_level(self):
        _FakeSession.statements = []
        instance = SimpleNamespace(id=uuid4())
        user = SimpleNamespace(username="app-name", id="u-1")
        with (
            patch.object(permission_module, "Session", _FakeSession),
            patch.object(Permission, "get_engine", return_value=None),
            patch.object(ResourceType, "type_for_instance", return_value=ResourceType.DATASOURCE),
        ):
            Permission.get_for(user, instance, Action.WRITE)
        assert Action.READ not in set(_flatten(_params(_FakeSession.statements[0])))
