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

"""Authorization for the per-user OAuth connection endpoints.

The connect / connection / disconnect endpoints all take a caller-supplied `setting_id`.
Authenticating the caller is not enough: without an ownership check any authenticated user
who learns a setting UUID could drive a flow against an integration belonging to a project
they are not a member of, which would disclose that integration's OAuth app configuration
through the generated authorize URL.

Access is resolved through the same `Ability` model the settings routers use, so a member of
the owning project may connect, while a non-member is refused.
"""

from fastapi import status

from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import ProjectSetting, Settings, SettingType, UserSetting
from codemie.rest_api.security.user import User
from codemie.service.oauth.folded_credentials import oauth_provider


def _as_ability_setting(setting: Settings):
    """Wrap a setting in the ownership model matching its scope."""
    if setting.setting_type == SettingType.PROJECT:
        return ProjectSetting(setting)
    return UserSetting(setting)


def authorize_setting_access(setting: Settings, user: User) -> None:
    """Raise 403 unless `user` may use `setting`.

    PROJECT integrations are usable by any member of the owning project; USER integrations
    only by their owner. Admins and maintainers pass through, as elsewhere in the settings API.
    """
    if Ability(user).can(Action.READ, _as_ability_setting(setting)):
        return
    raise ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message="Access denied",
        details=f"You do not have access to integration '{setting.id}'.",
        help="Ask a project administrator to grant you access to the project that owns this integration.",
    )


def load_authorized_setting(setting_id: str, user: User, expected_provider: str, provider: str) -> Settings:
    """Load an OAuth integration the caller is allowed to use, or raise.

    Returns 404 when the integration does not exist, 403 when the caller has no access to it,
    and 400 when it is not an integration of the expected OAuth provider. EPMCDME-14586/14587: the
    provider is resolved from the folded credential type (base type + auth_type=oauth marker), not a
    standalone *OAuth credential type.
    """
    setting = Settings.find_by_id(setting_id)
    if setting is None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Integration not found",
            details=f"Integration '{setting_id}' not found.",
            help="Verify the integration id and that it has not been removed.",
        )
    authorize_setting_access(setting, user)
    if oauth_provider(setting) != expected_provider:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Unsupported integration type",
            details=f"Integration '{setting_id}' is not a {provider} OAuth integration.",
            help=f"Select a {provider} OAuth integration and try again.",
        )
    return setting
