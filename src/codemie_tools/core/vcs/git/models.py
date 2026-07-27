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

from typing import ClassVar

from pydantic import Field

from codemie_tools.base.models import (
    CodeMieToolConfig,
    CredentialTypes,
    RequiredField,
    get_tool_default,
)


class GenericGitConfig(CodeMieToolConfig):
    """Generic Git credential configuration for UI-based credential entry."""

    TOOL_NAME: ClassVar[str] = "git"

    credential_type: CredentialTypes = Field(default=CredentialTypes.GIT, frozen=True, exclude=True)

    auth_type: str = Field(
        default=get_tool_default(TOOL_NAME, "auth_type") or "pat",
        description="Authentication type (pat = Personal Access Token)",
    )

    url: str = RequiredField(
        default=get_tool_default(TOOL_NAME, "url") or "",
        description="Git server URL",
        json_schema_extra={"placeholder": get_tool_default(TOOL_NAME, "url_placeholder") or ""},
    )

    token: str = RequiredField(
        description="Personal Access Token",
        json_schema_extra={"sensitive": True},
    )
