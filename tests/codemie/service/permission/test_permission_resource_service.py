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

from codemie.rest_api.models.permission import PrincipalType, ResourceType
from codemie.rest_api.security.user import User
from codemie.service.permission.permission_resource_service import PermissionResourceService


def test_run_returns_none_for_current_stub_implementation():
    user = User(id="user-1", username="alice")

    result = PermissionResourceService.run(
        resource_id="resource-1",
        resource_type=ResourceType.DATASOURCE,
        principal_id="alice",
        principal_type=PrincipalType.USER,
        user=user,
    )

    assert result is None
