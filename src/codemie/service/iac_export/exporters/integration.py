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


"""Integration exporter: every credential becomes an env reference entry, never a stored value."""

from typing import Any

from fastapi import status
from pydantic import BaseModel, Field

from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import Settings, SettingType, UserSetting
from codemie.rest_api.routers.project_settings import _check_permission
from codemie.rest_api.routers.utils import raise_forbidden
from codemie.service.iac_export.credential_ref import env_ref
from codemie.service.iac_export.document import ExportDocument, ExportError, ExportMeta
from codemie.service.settings.settings import SettingsService


class CredentialRefEntry(BaseModel):
    key: str
    ref: str  # "env:{ALIAS}_{KEY}"


class IntegrationSpec(BaseModel):
    project_name: str
    alias: str
    default: bool = False
    is_global: bool = False
    credential_type: str
    credential_values: list[CredentialRefEntry] = Field(default_factory=list)
    setting_type: str


def load_setting(id: str) -> Settings:
    try:
        return Settings.get_by_id(id)
    except KeyError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Integration not found",
            details=f"The integration with ID '{id}' could not be found.",
        ) from e


def check_access(setting: Settings, user: Any) -> None:
    if setting.setting_type == SettingType.PROJECT:
        _check_permission(user, setting.project_name)
        if SettingsService.is_budget_integration_hidden_from(setting, user):
            raise_forbidden("export")
    elif not Ability(user).can(Action.READ, UserSetting(setting)):
        raise_forbidden("export")


def check_usable(setting: Settings, user: Any) -> None:
    """Read rule for an integration that another document only names (an alias, never its credentials).

    A project member, or anyone for a global integration, may name a project integration; the project-admin
    rule of `check_access` guards exporting the integration document itself.
    """
    if setting.setting_type == SettingType.PROJECT:
        if not (setting.is_global or user.has_access_to_application(setting.project_name)):
            raise_forbidden("export")
        if SettingsService.is_budget_integration_hidden_from(setting, user):
            raise_forbidden("export")
    elif not Ability(user).can(Action.READ, UserSetting(setting)):
        raise_forbidden("export")


def _credential_refs(setting: Settings) -> list[CredentialRefEntry]:
    # Iterate keys only: a stored value is never read. The envelope has no inline value slot, so even the OpenAPI
    # document is a reference (the operator supplies it through the environment on apply).
    keys = sorted({cred.key for cred in setting.credential_values})
    return [CredentialRefEntry(key=key, ref=env_ref(setting.alias, key)) for key in keys]


def _build_spec(setting: Settings) -> IntegrationSpec:
    if not (setting.alias or "").strip():
        raise ExportError("Integration has an empty alias; it cannot be exported by name")
    if setting.credential_type is None:
        raise ExportError(f"Integration '{setting.alias}' has no credential type")
    if not setting.project_name:
        raise ExportError(f"Integration '{setting.alias}' has no project; the document format requires one")
    return IntegrationSpec(
        project_name=setting.project_name,
        alias=setting.alias,
        default=bool(setting.default),
        is_global=bool(setting.is_global),
        credential_type=setting.credential_type.value,
        credential_values=_credential_refs(setting),
        setting_type=(setting.setting_type or SettingType.USER).value,
    )


class IntegrationExporter:
    def export(self, id: str, user: Any) -> ExportDocument:
        setting = load_setting(id)
        check_access(setting, user)
        return ExportDocument(meta=ExportMeta(kind="integration", name=setting.alias or ""), spec=_build_spec(setting))
