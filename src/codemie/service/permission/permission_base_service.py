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

from abc import ABC, abstractmethod

from codemie.rest_api.models.permission import Permission, ResourceType
from codemie.rest_api.security.user import User


class PermissionBaseService(ABC):
    @abstractmethod
    def run(cls, *args, **kwargs):
        pass

    @staticmethod
    def _find_permission(resource_id: str, resource_type: ResourceType, principal_id: str, user: User) -> Permission:
        """Find the existing permission for the given request"""
        return Permission.get_by_fields(
            fields={
                "resource_type": resource_type,
                "resource_id": resource_id,
                "principal_id": principal_id,
            }
        )
