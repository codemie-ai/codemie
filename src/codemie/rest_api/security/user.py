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

from pydantic import BaseModel, Field, computed_field, model_validator

from codemie.core.constants import Environment, DEMO_PROJECT
from codemie.core.models import UserEntity
from codemie.configs import config
from codemie.rest_api.security.permissions import is_admin_or_maintainer

DEMO_USER_ROLE = "demo_user"

USER_ID_HEADER = "user-id"
AUTHORIZATION_HEADER = "Authorization"


class User(BaseModel):
    """User model for authentication context

    This model is populated from:
    - IDP JWT claims when ENABLE_USER_MANAGEMENT=False
    - Database tables when ENABLE_USER_MANAGEMENT=True
    """

    id: str
    username: str = ""
    name: str = ""
    email: str = ""
    roles: list = Field(default_factory=list)
    project_names: list[str] = Field(default_factory=lambda: ['demo'])
    admin_project_names: list[str] = Field(default_factory=list)
    picture: str = ""
    knowledge_bases: list = Field(default_factory=list)
    user_type: str | None = 'regular'
    is_admin: bool = Field(default=False)
    is_maintainer: bool = Field(default=False)
    project_limit: int | None = Field(default=None)  # NULL = unlimited (admins); set from DB when flag ON
    auth_token: str | None = Field(None, exclude=True)

    @model_validator(mode='after')
    def resolve_is_admin(self) -> 'User':
        """Resolve is_admin at construction time.

        - ENV=local: Always True (dev override)
        - ENABLE_USER_MANAGEMENT=False: Legacy IDP role-based
        - ENABLE_USER_MANAGEMENT=True: Value passed from DB at construction
        """
        if Environment.LOCAL.value == config.ENV:
            self.is_admin = True
        elif not config.ENABLE_USER_MANAGEMENT:
            self.is_admin = (bool(config.ADMIN_USER_ID) and self.id == config.ADMIN_USER_ID) or (
                config.ADMIN_ROLE_NAME in self.roles
            )

        if self.is_maintainer:
            self.is_admin = True

        return self

    @computed_field
    @property
    def applications(self) -> list[str]:
        return self.project_names

    @computed_field
    @property
    def applications_admin(self) -> list[str]:
        return self.admin_project_names

    @property
    def full_name(self):
        return self.username or self.name or self.id

    @property
    def is_admin_or_maintainer(self) -> bool:
        return is_admin_or_maintainer(self)

    @property
    def is_applications_admin(self) -> bool:
        return len(self.admin_project_names) > 0 or self.is_admin_or_maintainer

    def is_application_admin(self, app_name: str) -> bool:
        return app_name in self.admin_project_names

    @property
    def is_demo_user(self) -> bool:
        return DEMO_USER_ROLE in self.roles

    @property
    def is_external_user(self) -> bool:
        """Check if user is external (temporary marketplace user)"""
        return self.user_type == config.EXTERNAL_USER_TYPE

    @property
    def current_project(self) -> str:
        apps = self.project_names if self.project_names else [DEMO_PROJECT]
        return apps[0]

    def has_access_to_application(self, app_name: str) -> bool:
        return self.is_admin_or_maintainer or (app_name in self.project_names) or self.is_application_admin(app_name)

    def has_access_to_kb(self, name: str) -> bool:
        return self.is_admin_or_maintainer or (name in self.knowledge_bases)

    def as_user_model(self) -> UserEntity:
        return UserEntity(user_id=self.id, name=self.name, username=self.username)
