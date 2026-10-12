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

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from codemie_tools.base.models import (
    CodeMieToolConfig,
    RequiredField,
    CredentialTypes,
)


class ExchangeAuthType(str, Enum):
    """MS Exchange authentication types."""

    OAUTH_AZURE = "oauth_azure"  # OAuth via Microsoft Entra ID Application (Graph)
    NTLM = "ntlm"  # NTLM service account authentication (EWS)


_PATH_FIELDS = {
    ExchangeAuthType.OAUTH_AZURE: ("oauth_tenant_id", "oauth_client_id", "oauth_client_secret"),
    ExchangeAuthType.NTLM: ("ews_url", "service_account_username", "service_account_password"),
}


class ExchangeToolConfig(CodeMieToolConfig):
    credential_type: CredentialTypes = Field(default=CredentialTypes.MS_EXCHANGE, exclude=True, frozen=True)

    auth_type: ExchangeAuthType | None = Field(default=None, description="oauth_azure (Graph) or ntlm (EWS)")

    mailbox: str = RequiredField(description="Target mailbox address")

    # Microsoft Entra ID OAuth fields (required for auth_type=OAUTH_AZURE)
    oauth_tenant_id: str | None = Field(default=None)
    oauth_client_id: str | None = Field(default=None)
    oauth_client_secret: str | None = Field(default=None, json_schema_extra={"sensitive": True})

    # NTLM service account fields (required for auth_type=NTLM)
    ews_url: str | None = Field(default=None)
    service_account_username: str | None = Field(default=None)
    service_account_password: str | None = Field(default=None, json_schema_extra={"sensitive": True})

    @model_validator(mode="after")
    def _validate_selected_path(self) -> ExchangeToolConfig:
        if self.auth_type is None:
            raise ValueError("auth_type is required (oauth_azure or ntlm)")
        if not (self.mailbox or "").strip():
            raise ValueError("mailbox is required")
        for name in _PATH_FIELDS[self.auth_type]:
            if not (getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required when auth_type={self.auth_type.value}")
        return self
