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

import ast
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from codemie.rest_api.models.permission import ResourceType
from codemie.service.permission import named_grant_types
from codemie.service.permission.named_grant_types import NAMED_GRANT_TYPES, descriptor_for

SRC = Path(__file__).resolve().parents[4] / "src" / "codemie"


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def test_every_resource_type_is_in_exactly_one_registry():
    lookup = set(ResourceType._lookup)
    named = set(NAMED_GRANT_TYPES)
    for member in ResourceType:
        assert (member in lookup) != (member in named), member


def test_descriptor_import_is_lazy():
    code = (
        "import sys\n"
        "from codemie.service.permission import named_grant_types as n\n"
        "assert 'codemie.service.hosted_applications.grant_descriptor' not in sys.modules\n"
        "from codemie.rest_api.models.permission import ResourceType\n"
        "n.descriptor_for(ResourceType.HOSTED_APPLICATION)\n"
        "assert 'codemie.service.hosted_applications.grant_descriptor' in sys.modules\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_unknown_resource_type_raises_key_error():
    with pytest.raises(KeyError):
        descriptor_for(ResourceType.DATASOURCE)


def test_core_and_models_do_not_import_named_grant_types():
    offenders = []
    for folder in (SRC / "core", SRC / "rest_api" / "models"):
        for path in folder.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = []
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [alias.name for alias in node.names]
                elif isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                if any("named_grant_types" in name for name in names):
                    offenders.append(str(path))
    assert offenders == []


def test_hosted_descriptor_fields():
    from codemie.service.activity.activity_models import ActivityDomain, ActivityEntityType

    descriptor = descriptor_for(ResourceType.HOSTED_APPLICATION)
    assert descriptor.owned_class_name == "HostedApplications"
    assert descriptor.activity_domain is ActivityDomain.HOSTED_APPLICATION
    assert descriptor.activity_entity_type is ActivityEntityType.HOSTED_APPLICATION
    assert descriptor.event_added == "hosted_application.grant.added"
    assert descriptor.event_removed == "hosted_application.grant.removed"


def test_resolve_is_one_statement_of_scalar_subqueries():
    from codemie.service.hosted_applications import grant_descriptor

    sql = _sql(grant_descriptor.build_resolve_statement())
    assert sql.count("SELECT") > 3
    assert sql.startswith("SELECT (SELECT hosted_applications.project")
    assert "hosted_applications.id = %(rid)s" in sql
    assert "created_by ->> %(created_by_1)s" in sql or "created_by" in sql
    assert "may_remove" in sql
    assert "is_maintainer" in sql
    assert "FROM hosted_applications" in sql
    assert "JOIN" not in sql.split("FROM hosted_applications")[0]


def test_resolve_orphan_branch_has_no_app_creator_term():
    from codemie.service.hosted_applications import grant_descriptor

    sql = _sql(grant_descriptor.build_resolve_statement())
    # the admin-only branch sits outside the app-scoped EXISTS
    assert " OR (EXISTS (SELECT hosted_applications.id" in sql or "OR EXISTS (SELECT hosted_applications.id" in sql


def test_preload_lives_once_in_the_service_not_in_a_descriptor():
    from dataclasses import fields

    assert "preload" not in {f.name for f in fields(named_grant_types.NamedGrantDescriptor)}


def test_assistant_is_a_named_grant_type_and_left_the_resource_lookup():
    assert ResourceType.ASSISTANT in NAMED_GRANT_TYPES
    assert ResourceType.ASSISTANT not in ResourceType._lookup
    assert descriptor_for(ResourceType.ASSISTANT).owned_class_name == "Assistant"


def test_descriptor_keeps_no_level_order_of_its_own():
    from codemie.service.hosted_applications import grant_descriptor

    assert not hasattr(grant_descriptor, "_LEVEL_ORDER")


def test_hosted_owner_is_the_eligible_creator():
    from codemie.rest_api.security.user import User

    descriptor = descriptor_for(ResourceType.HOSTED_APPLICATION)
    app = type("A", (), {"created_by": type("C", (), {"id": "u1"})()})()
    assert descriptor.is_owner(app, User(id="u1", username="u1"))
    assert not descriptor.is_owner(app, User(id="u2", username="u2"))
    assert not descriptor.is_owner(type("A", (), {"created_by": None})(), User(id="u1", username="u1"))


def test_before_grant_refuses_suspended_app():
    from codemie.core.hosted_applications.errors import HostedApplicationError

    descriptor = descriptor_for(ResourceType.HOSTED_APPLICATION)
    descriptor.before_grant(type("A", (), {"security_state": "clear"})())
    with pytest.raises(HostedApplicationError):
        descriptor.before_grant(type("A", (), {"security_state": "suspended"})())


def test_module_exposes_descriptor_type():
    assert named_grant_types.NamedGrantDescriptor is not None


def test_each_type_states_its_recipient_policy_and_both_are_internal_only_today():
    """SC-9: the recipient policy is per type (descriptor), with the same internal-only behaviour for both types."""
    from codemie.core.hosted_applications.access import grant_recipient
    from codemie.rest_api.models.user_management import UserDB, eligible_internal_user

    hosted = descriptor_for(ResourceType.HOSTED_APPLICATION)
    assistant = descriptor_for(ResourceType.ASSISTANT)
    assert hosted.recipient is grant_recipient and assistant.recipient is eligible_internal_user
    expected = _sql(sa.select(UserDB.id).where(eligible_internal_user(UserDB)))
    for descriptor in (hosted, assistant):
        assert _sql(sa.select(UserDB.id).where(descriptor.recipient(UserDB))) == expected


def test_the_generic_service_reads_the_recipient_policy_from_the_descriptor(monkeypatch):
    from dataclasses import replace

    from codemie.configs import config
    from codemie.rest_api.models.user_management import UserDB
    from codemie.service.permission import named_grant_service, named_grant_types

    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)
    custom = replace(descriptor_for(ResourceType.HOSTED_APPLICATION), recipient=lambda users: users.is_auditor)
    monkeypatch.setitem(named_grant_types._cache, ResourceType.HOSTED_APPLICATION, custom)
    rid = "11111111-1111-1111-1111-111111111111"
    for statement in (
        named_grant_service.preload_statement(ResourceType.HOSTED_APPLICATION, "u-1"),
        named_grant_service.validation_statement(ResourceType.HOSTED_APPLICATION, rid, ["u-1"]),
        sa.select(sa.literal(1)).where(
            named_grant_service.grant_exists_term(
                ResourceType.HOSTED_APPLICATION, sa.literal(rid), UserDB.id, named_grant_service.Action.READ
            )
        ),
    ):
        text = _sql(statement)
        assert "users.is_auditor" in text and "users.user_type" not in text


def test_grant_exists_term_compares_through_the_type_key(monkeypatch):
    from codemie.configs import config
    from codemie.service.permission import named_grant_service

    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)

    def term(resource_type):
        return _sql(
            sa.select(sa.literal(1)).where(
                named_grant_service.grant_exists_term(
                    resource_type, sa.literal("x"), sa.literal("u-1"), named_grant_service.Action.READ
                )
            )
        )

    assert "CAST(permissions.resource_id AS TEXT)" in term(ResourceType.ASSISTANT)
    assert "CAST(" not in term(ResourceType.HOSTED_APPLICATION)
