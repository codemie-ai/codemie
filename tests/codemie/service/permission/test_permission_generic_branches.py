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

"""Generic named-grant branches of the permissions create/delete services (mocks only, no database)."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.ability import Action
from codemie.rest_api.models.permission import PermissionCreateRequest, PrincipalType, ResourceType
from codemie.rest_api.security.user import User
from codemie.service.permission import PermissionAccessDenied, PermissionCreationService, PermissionDeletionService
from codemie.service.permission import permission_deletion_service as deletion_module
from codemie.service.permission.named_grant_types import ResolvedResource

RID = "11111111-1111-1111-1111-111111111111"


def _admin() -> User:
    return User(id="admin-1", username="admin", name="Admin", roles=["admin"], is_admin=True)


def _permission(resource_type):
    permission = MagicMock()
    permission.id = "perm-1"
    permission.resource_type = resource_type
    permission.resource_id = RID
    permission.principal_type = (
        PrincipalType.APPLICATION if resource_type == ResourceType.DATASOURCE else PrincipalType.USER
    )
    return permission


def test_create_refuses_named_grant_type_even_for_admin():
    request = PermissionCreateRequest(
        resource_id=RID,
        resource_type=ResourceType.HOSTED_APPLICATION,
        principal_id="app",
        principal_type=PrincipalType.APPLICATION,
        permission_level=Action.READ,
    )
    with (
        patch.object(PermissionCreationService, "_check_ability") as check_ability,
        patch.object(PermissionCreationService, "_check_principal") as check_principal,
    ):
        with pytest.raises(PermissionAccessDenied):
            PermissionCreationService.run(request, _admin())

    check_ability.assert_not_called()
    check_principal.assert_not_called()


@pytest.fixture
def delete_env():
    descriptor = MagicMock()
    calls = MagicMock()
    calls.attach_mock(descriptor.resolve, "resolve")
    session = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__.return_value = session
    with (
        patch.object(deletion_module, "descriptor_for", return_value=descriptor),
        patch.object(deletion_module, "Session", return_value=session_cm),
        patch.object(deletion_module.Permission, "get_engine", return_value=MagicMock()),
        patch.object(deletion_module, "revoke_permission") as revoke,
        patch.object(deletion_module, "Ability") as ability,
    ):
        calls.attach_mock(ability, "ability")
        calls.attach_mock(revoke, "revoke")
        yield descriptor, session, revoke, ability, calls


def test_delete_hosted_row_resolves_before_ability_then_revokes_and_commits(delete_env):
    descriptor, session, revoke, ability, calls = delete_env
    permission = _permission(ResourceType.HOSTED_APPLICATION)
    descriptor.resolve.return_value = ResolvedResource(project="proj", creator_id="c", may_remove=True)
    user = _admin()

    with patch.object(deletion_module.Permission, "get_by_id", return_value=permission):
        PermissionDeletionService.run("perm-1", user)

    descriptor.resolve.assert_called_once_with(session, RID, user)
    revoke.assert_called_once_with(user, permission, session, project="proj")
    ability.assert_not_called()
    session.commit.assert_called_once()
    names = [c[0] for c in calls.mock_calls if c[0] in ("resolve", "revoke")]
    assert names == ["resolve", "revoke"]


def test_delete_hosted_row_refused_when_resolve_denies(delete_env):
    descriptor, session, revoke, ability, _ = delete_env
    permission = _permission(ResourceType.HOSTED_APPLICATION)
    descriptor.resolve.return_value = ResolvedResource(project="proj", creator_id="c", may_remove=False)

    with patch.object(deletion_module.Permission, "get_by_id", return_value=permission):
        with pytest.raises(PermissionAccessDenied):
            PermissionDeletionService.run("perm-1", _admin())

    revoke.assert_not_called()
    ability.assert_not_called()
    session.commit.assert_not_called()


def test_delete_refuses_type_in_neither_map(delete_env):
    _, session, revoke, ability, _ = delete_env
    permission = _permission("unknown_type")

    with patch.object(deletion_module.Permission, "get_by_id", return_value=permission):
        with pytest.raises(PermissionAccessDenied):
            PermissionDeletionService.run("perm-1", _admin())

    revoke.assert_not_called()
    ability.assert_not_called()
    permission.delete.assert_not_called()


def test_delete_datasource_path_unchanged(delete_env):
    _, session, revoke, ability, _ = delete_env
    permission = _permission(ResourceType.DATASOURCE)
    ability.return_value.can.return_value = True
    user = _admin()

    with patch.object(deletion_module.Permission, "get_by_id", return_value=permission):
        PermissionDeletionService.run("perm-1", user)

    ability.assert_called_once_with(user)
    ability.return_value.can.assert_called_once_with(Action.WRITE, permission)
    permission.delete.assert_called_once()
    revoke.assert_not_called()
    session.commit.assert_not_called()


def test_delete_datasource_denied_without_write():
    permission = _permission(ResourceType.DATASOURCE)
    with (
        patch.object(deletion_module.Permission, "get_by_id", return_value=permission),
        patch.object(deletion_module, "Ability") as ability,
    ):
        ability.return_value.can.return_value = False
        with pytest.raises(PermissionAccessDenied):
            PermissionDeletionService.run("perm-1", _admin())
    permission.delete.assert_not_called()


# ---- routing by (resource_type, principal_type): AR-10, ALMA SC-2 ----------------------------------------------------


def _with_principal(resource_type, principal_type):
    permission = _permission(resource_type)
    permission.principal_type = principal_type
    return permission


def test_named_grant_routing_needs_both_a_registered_type_and_a_user_principal():
    from codemie.service.permission.named_grant_types import is_named_grant

    assert is_named_grant(ResourceType.HOSTED_APPLICATION, PrincipalType.USER)
    assert is_named_grant(ResourceType.ASSISTANT, PrincipalType.USER)
    assert not is_named_grant(ResourceType.HOSTED_APPLICATION, PrincipalType.APPLICATION)
    assert not is_named_grant(ResourceType.DATASOURCE, PrincipalType.USER)


@pytest.fixture
def datasource_takes_named_grants(monkeypatch):
    """A type with both APPLICATION (project) and USER grants, as datasources would be after joining the registry."""
    from codemie.service.permission import named_grant_types

    registry = {**named_grant_types.NAMED_GRANT_TYPES, ResourceType.DATASOURCE: "unused:DESCRIPTOR"}
    monkeypatch.setattr(named_grant_types, "NAMED_GRANT_TYPES", registry)


def test_a_mixed_type_deletes_its_application_grant_through_the_generic_path(delete_env, datasource_takes_named_grants):
    descriptor, _, revoke, ability, _ = delete_env
    permission = _with_principal(ResourceType.DATASOURCE, PrincipalType.APPLICATION)
    ability.return_value.can.return_value = True
    with patch.object(deletion_module.Permission, "get_by_id", return_value=permission):
        PermissionDeletionService.run("perm-1", _admin())
    permission.delete.assert_called_once()
    descriptor.resolve.assert_not_called()
    revoke.assert_not_called()


def test_a_mixed_type_deletes_its_user_grant_through_the_descriptor(delete_env, datasource_takes_named_grants):
    descriptor, session, revoke, ability, _ = delete_env
    permission = _with_principal(ResourceType.DATASOURCE, PrincipalType.USER)
    descriptor.resolve.return_value = ResolvedResource(project="p", creator_id="c", may_remove=True)
    with patch.object(deletion_module.Permission, "get_by_id", return_value=permission):
        PermissionDeletionService.run("perm-1", _admin())
    revoke.assert_called_once()
    ability.assert_not_called()


def test_a_mixed_type_creates_application_grants_and_refuses_user_grants(datasource_takes_named_grants):
    def request(principal_type):
        return PermissionCreateRequest(
            resource_id=RID,
            resource_type=ResourceType.DATASOURCE,
            principal_id="app",
            principal_type=principal_type,
            permission_level=Action.READ,
        )

    with (
        patch.object(PermissionCreationService, "_check_ability") as check_ability,
        patch.object(PermissionCreationService, "_check_principal"),
        patch("codemie.service.permission.permission_creation_service.Permission") as permission,
    ):
        with pytest.raises(PermissionAccessDenied):
            PermissionCreationService.run(request(PrincipalType.USER), _admin())
        check_ability.assert_not_called()
        PermissionCreationService.run(request(PrincipalType.APPLICATION), _admin())
    check_ability.assert_called_once()
    permission.return_value.save.assert_called_once()


def test_a_named_type_row_with_an_application_principal_is_refused_as_before(delete_env):
    """Hosted and assistant rows hold USER principals only; any other row of those types is refused, as before."""
    descriptor, _, revoke, ability, _ = delete_env
    permission = _with_principal(ResourceType.HOSTED_APPLICATION, PrincipalType.APPLICATION)
    with patch.object(deletion_module.Permission, "get_by_id", return_value=permission):
        with pytest.raises(PermissionAccessDenied):
            PermissionDeletionService.run("perm-1", _admin())
    descriptor.resolve.assert_not_called()
    revoke.assert_not_called()
    permission.delete.assert_not_called()
