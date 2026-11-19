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

"""Request and response models of the IdP-sync management API (EPMCDME-16895)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ID_EXAMPLE = "8f14e45f-ceea-467a-9575-1c5e1f2b3d4a"

# Mirrors the length of the users.picture column (see the user management migration).
PICTURE_MAX_LENGTH = 1024


class IdpUserUpsertRequest(BaseModel):
    """Desired state of one user in the platform, as the customer IdP knows it."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(description="User id in the IdP (UUID, the token subject).", examples=[_ID_EXAMPLE])
    email: str = Field(min_length=3, max_length=255, description="User email.", examples=["jane.doe@example.com"])
    username: str = Field(min_length=1, max_length=255, description="Login name.", examples=["jane.doe"])
    name: str = Field(min_length=1, max_length=255, description="Display name.", examples=["Jane Doe"])
    is_admin: bool = Field(
        description="Platform administrator flag as the IdP sees it. Advisory: it is applied here, but the "
        "last active administrator can never be demoted.",
        examples=[False],
    )
    picture: str | None = Field(
        default=None,
        max_length=PICTURE_MAX_LENGTH,
        description="Avatar URL. Omitted or empty leaves the stored picture alone; sync never clears it.",
        examples=["https://example.com/avatars/jane.png"],
    )
    user_type: Literal["regular", "external"] | None = Field(
        default=None,
        description="User type: regular or external. Omitted leaves the stored type alone (a new user is regular). "
        "service_account is refused (422).",
        examples=["regular"],
    )
    is_maintainer: bool | None = Field(
        default=None,
        description="Platform maintainer flag as the IdP sees it. Omitted leaves the stored flag alone. When sent, "
        "it decides: true requires is_admin true (otherwise 422). A user who ends up without is_admin also ends up "
        "without the maintainer role. Removing the last active maintainer is refused with 409.",
        examples=[False],
    )
    is_auditor: bool | None = Field(
        default=None,
        description="Platform auditor flag as the IdP sees it. Omitted leaves the stored flag alone.",
        examples=[False],
    )

    @field_validator("id")
    @classmethod
    def _id_is_uuid(cls, value: str) -> str:
        UUID(value)
        return value

    @model_validator(mode="after")
    def _maintainer_is_an_admin(self) -> IdpUserUpsertRequest:
        if self.is_maintainer and not self.is_admin:
            raise ValueError("is_maintainer requires is_admin: a maintainer is always an administrator")
        return self


class IdpAssignRequest(BaseModel):
    """Membership of a user in a project."""

    model_config = ConfigDict(str_strip_whitespace=True)

    project_name: str = Field(min_length=1, max_length=255, description="Project name.", examples=["team-alpha"])
    is_project_admin: bool = Field(
        default=False, description="Whether the user administers the project.", examples=[False]
    )


class IdpMembership(BaseModel):
    project_name: str = Field(description="Project name.", examples=["team-alpha"])
    is_project_admin: bool = Field(description="Whether the user administers the project.", examples=[False])
    is_personal: bool = Field(description="Whether this is the user's personal project.", examples=[False])


class IdpUserResponse(BaseModel):
    id: str = Field(description="User id in the IdP (UUID).", examples=[_ID_EXAMPLE])
    is_active: bool = Field(description="False for a deactivated user.", examples=[True])
    email: str = Field(description="User email.", examples=["jane.doe@example.com"])
    username: str = Field(description="Login name.", examples=["jane.doe"])
    name: str | None = Field(description="Display name.", examples=["Jane Doe"])
    is_admin: bool = Field(description="Platform administrator flag (advisory for the IdP side).", examples=[False])
    picture: str | None = Field(default=None, description="Avatar URL, when the user has one.", examples=[None])
    user_type: str = Field(
        default="regular", description="User type: regular, external or service_account.", examples=["regular"]
    )
    is_maintainer: bool = Field(
        default=False,
        description="Platform maintainer flag. A maintainer always reports is_admin true.",
        examples=[False],
    )
    is_auditor: bool = Field(default=False, description="Platform auditor flag (read-only access).", examples=[False])
    memberships: list[IdpMembership] = Field(description="Project memberships of the user.", examples=[[]])


class IdpUserPage(BaseModel):
    items: list[IdpUserResponse] = Field(description="Users of the page, deactivated included, ordered by id.")
    total: int = Field(description="Total number of users.", examples=[60000])
    page: int = Field(description="Page number, starting from 0.", examples=[0])
    per_page: int = Field(description="Page size.", examples=[100])
