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

from codemie.rest_api.security.user import User
from codemie.rest_api.models.permission import ResourceType, PrincipalType

from .permission_base_service import PermissionBaseService


class PermissionResourceService(PermissionBaseService):
    """Get a resource details for a permission"""

    @classmethod
    def run(
        cls, resource_id: str, resource_type: ResourceType, principal_id: str, principal_type: PrincipalType, user: User
    ):
        pass

    #
