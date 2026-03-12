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

from enum import Enum
from typing import Optional

from codemie_tools.base.models import CodeMieToolConfig, RequiredField, CredentialTypes

from pydantic import Field


class EmailAuthType(str, Enum):
    """Email authentication types."""

    BASIC = "basic"  # Basic SMTP authentication (username/password)
    OAUTH_AZURE = "oauth_azure"  # OAuth via Microsoft Entra ID Application


class EmailToolConfig(CodeMieToolConfig):
    credential_type: CredentialTypes = Field(default=CredentialTypes.EMAIL, exclude=True, frozen=True)

    # Common field
    url: str = RequiredField(
        description="SMTP server URL including port, e.g. smtp.gmail.com:587 or smtp.office365.com:587",
        json_schema_extra={"placeholder": "smtp.gmail.com:587"},
    )

    auth_type: EmailAuthType = Field(
        default=EmailAuthType.BASIC,
        description="Authentication type: basic (basic auth) or oauth_azure (Microsoft Entra ID OAuth)",
    )

    # Basic authentication fields (required for auth_type=BASIC)
    smtp_username: Optional[str] = Field(
        default=None,
        description="SMTP server username/email (required for basic auth, also used as FROM address)",
        json_schema_extra={"placeholder": "user@example.com"},
    )
    smtp_password: Optional[str] = Field(
        default=None,
        description="SMTP server password or app-specific password (required for basic auth)",
        json_schema_extra={"placeholder": "password", "sensitive": True},
    )

    # Microsoft Entra ID OAuth fields (required for auth_type=OAUTH_AZURE)
    oauth_from_email: Optional[str] = Field(
        default=None,
        description="Email address to send from (required for OAuth authentication)",
        json_schema_extra={"placeholder": "sender@example.com"},
    )
    oauth_client_id: Optional[str] = Field(
        default=None,
        description="OAuth Client ID - Microsoft Entra ID Application (Client) ID (required for Microsoft Entra ID OAuth)",
        json_schema_extra={"placeholder": "12345678-1234-1234-1234-123456789012"},
    )
    oauth_client_secret: Optional[str] = Field(
        default=None,
        description="OAuth Client Secret - Microsoft Entra ID Application Secret (required for Microsoft Entra ID OAuth)",
        json_schema_extra={"placeholder": "your_client_secret", "sensitive": True},
    )
    oauth_tenant_id: Optional[str] = Field(
        default=None,
        description="OAuth Tenant ID - Microsoft Entra ID Tenant ID (required for Microsoft Entra ID OAuth)",
        json_schema_extra={"placeholder": "12345678-1234-1234-1234-123456789012"},
    )
    oauth_authority: Optional[str] = Field(
        default="https://login.microsoftonline.com",
        description="OAuth authority base URL without tenant_id (optional, defaults to https://login.microsoftonline.com)",
        json_schema_extra={"placeholder": "https://login.microsoftonline.com"},
    )
    oauth_scope: Optional[str] = Field(
        default="https://outlook.office365.com/.default",
        description="OAuth scope for token acquisition (optional, defaults to https://outlook.office365.com/.default)",
        json_schema_extra={"placeholder": "https://outlook.office365.com/.default"},
    )
