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

from typing import Optional

from codemie.configs import logger
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant import VirtualAssistantService, VIRTUAL_ASSISTANT_PREFIX
from codemie.rest_api.models.settings import Settings, SettingsBase, SettingType
from codemie.service.settings.base_settings import SearchFields


def user_can_access_setting(
    setting: Optional[SettingsBase],
    user: User,
    assistant_project: Optional[str],
    marketplace: bool = False,
) -> bool:
    """Return True if ``user`` may use ``setting`` as an integration for ``assistant_project``.

    Access rule (conditional on assistant type):
    - USER settings: only the owner of the setting. Applies to both assistant types, so
      another user's personal integration is never accessible.
    - PROJECT settings:
      - project-shared assistants (``marketplace=False``): strictly the assistant's own
        project — the setting's ``project_name`` must equal ``assistant_project`` AND the
        user must have access to that project (via ``User.has_access_to_application``).
        Integrations from any other project (even a project the user is also a member of)
        are rejected.
      - marketplace assistants (``marketplace=True``): the integration is not tied to the
        assistant's own project, so any project may match, but the user must still have
        access to *that* integration's own project (via ``User.has_access_to_application``).
        This still lets a marketplace assistant reference a different project than its own,
        it just never opens a project the requesting user cannot otherwise reach. The USER
        owner-only rule is unchanged, so this only ever concerns PROJECT credentials, never
        other users' personal ones.

    Used to gate per-user tool mappings both on save and defensively at runtime, so a stale
    or forged mapping can never surface credentials the current user has no access to. Fails
    closed on a missing setting.
    """
    if setting is None:
        return False

    if setting.setting_type == SettingType.PROJECT:
        if marketplace:
            return user.has_access_to_application(setting.project_name)
        return (
            bool(assistant_project)
            and setting.project_name == assistant_project
            and user.has_access_to_application(assistant_project)
        )

    return bool(setting.user_id) and setting.user_id == user.id


def search_settings_by_id(setting_id: str) -> Settings | None:
    try:
        settings = Settings.get_by_id(setting_id)
        return settings
    except Exception as e:
        logger.info(f"Failed to retrieve specified assistant settings: {e}, settings_id: {setting_id}")
        return None


def search_assistant(assistant_id) -> Assistant:
    is_virtual_assistant = assistant_id.startswith(VIRTUAL_ASSISTANT_PREFIX)

    if is_virtual_assistant:
        return VirtualAssistantService.get(assistant_id)
    else:
        return Assistant.get_by_id(assistant_id)


def get_assistant_settings_id(assistant, credential_type):
    for toolkit in assistant.toolkits:
        if toolkit.settings and (toolkit.settings.credential_type == credential_type):
            logger.debug(f"Found assistant Toolkit settings for {credential_type}")
            return toolkit.settings.id
        for tool in toolkit.tools:
            if credential_type.value.lower() in tool.name.lower() and tool.settings:
                logger.debug(f"Found assistant Tool settings for {credential_type}")
                return tool.settings.id
            if tool.settings and tool.settings.credential_type == credential_type:
                logger.debug(f"Found assistant Tool settings for {credential_type}")
                return tool.settings.id
    return None


def search_assistant_settings(assistant: Assistant, search_fields: dict, settings):
    settings_id = get_assistant_settings_id(assistant, search_fields.get(SearchFields.CREDENTIAL_TYPE))
    if settings_id:
        settings = search_settings_by_id(setting_id=settings_id)

    return settings
