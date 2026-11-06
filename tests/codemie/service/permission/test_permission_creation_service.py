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

from unittest.mock import patch

import pytest

from codemie.core.ability import Action
from codemie.rest_api.models.permission import PermissionCreateRequest, PrincipalType, ResourceType
from codemie.rest_api.security.user import User
from codemie.service.permission import PermissionAccessDenied
from codemie.service.permission.permission_creation_service import PermissionCreationService


def test_generic_create_rejects_assistant_resources():
    request = PermissionCreateRequest(
        resource_id="asst-1",
        resource_type=ResourceType.ASSISTANT,
        principal_type=PrincipalType.USER,
        principal_id="u2",
        permission_level=Action.READ,
    )
    with (
        patch.object(PermissionCreationService, "_check_ability") as check,
        patch("codemie.service.permission.permission_creation_service.Permission.save") as save,
        pytest.raises(PermissionAccessDenied),
    ):
        PermissionCreationService.run(request, User(id="owner", username="owner"))
    check.assert_not_called()
    save.assert_not_called()
