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

from typing import Self, Optional

from codemie.rest_api.models.settings import Settings, SettingsBase, SettingType
from codemie.service.settings.base_settings import SearchFields
from codemie.repository.assistants.assistant_user_mapping_repository import AssistantUserMappingRepositoryImpl
from codemie.service.settings.settings_util import search_settings_by_id, search_assistant, search_assistant_settings


class SettingsHandler:
    _next_handler: Self or None = None

    def set_next(self, handler: Self) -> Self:
        self._next_handler = handler
        return self

    def __or__(self, other: 'SettingsHandler') -> 'SettingsHandler':
        """Enable pipe-like syntax with | operator"""
        self.set_next(other)
        return other

    def handle(self, search_fields: dict, **kwargs) -> Optional[SettingsBase]:
        if self._next_handler:
            return self._next_handler.handle(search_fields, **kwargs)

        return None


class AssistantUserMappingSettingsHandler(SettingsHandler):
    """Search by Assistant -> UserSettings mapping; used when user set settings for markeplace assistants"""

    def handle(self, search_fields: dict, assistant_id: Optional[str] = None, **kwargs) -> Optional[SettingsBase]:
        next_handler = lambda: super(self.__class__, self).handle(search_fields, assistant_id=assistant_id, **kwargs)  # noqa:E731

        if not assistant_id:
            return next_handler()

        assistant = search_assistant(assistant_id)

        if not assistant:
            return next_handler()

        assistant_setting = search_assistant_settings(assistant, search_fields, None)

        if assistant.is_global and assistant_setting:
            # if global assistant and assistant has setting - skip
            return next_handler()

        mapping = AssistantUserMappingRepositoryImpl().get_mapping(
            assistant_id=assistant_id, user_id=search_fields[SearchFields.USER_ID]
        )

        if not mapping:
            return next_handler()

        for config in mapping.tools_config:
            if settings := Settings.get_by_fields(
                {"id": config.integration_id, "credential_type": search_fields[SearchFields.CREDENTIAL_TYPE]}
            ):
                return settings

        return next_handler()


class BySettingIDSettingsHandler(SettingsHandler):
    """Search setting directly by setting ID"""

    def handle(self, search_fields: dict, setting_id: Optional[str] = None, **kwargs) -> Optional[SettingsBase]:
        if setting_id and (settings := search_settings_by_id(setting_id)):
            return settings

        return super().handle(search_fields, setting_id=setting_id, **kwargs)


class GlobalAssistantSettingsHandler(SettingsHandler):
    """If assistant is global - returns assistant settings"""

    def handle(self, search_fields: dict, assistant_id: Optional[str] = None, **kwargs) -> Optional[SettingsBase]:
        if assistant_id:
            assistant = search_assistant(assistant_id)
            assistant_setting = search_assistant_settings(assistant, search_fields, None)

            if assistant.is_global and assistant_setting:
                return assistant_setting

        return super().handle(search_fields, assistant_id=assistant_id, **kwargs)


class AssistantSettingsHandler(SettingsHandler):
    """Search non-global assistant settings"""

    def handle(self, search_fields: dict, assistant_id: Optional[str] = None, **kwargs) -> Optional[SettingsBase]:
        if assistant_id:
            assistant = search_assistant(assistant_id)
            assistant_setting = search_assistant_settings(assistant, search_fields, None)

            if not assistant_setting:
                return super().handle(search_fields, assistant_id=assistant_id, **kwargs)

            match_by_user = (
                assistant_setting.setting_type == SettingType.USER
                and assistant_setting.user_id == search_fields.get(SearchFields.USER_ID)
            )
            match_by_project = assistant_setting.setting_type == SettingType.PROJECT

            if match_by_user or match_by_project:
                return assistant_setting

        return super().handle(search_fields, assistant_id=assistant_id, **kwargs)


class DefaultSettingsHandler(SettingsHandler):
    """Search by seach_fields and default=True. NOTE: might be lagacy"""

    def handle(self, search_fields: dict, **kwargs) -> Optional[SettingsBase]:
        default_search_fields = search_fields.copy()
        default_search_fields[SearchFields.DEFAULT] = True

        if settings := Settings.get_by_fields(default_search_fields):
            return settings

        return super().handle(search_fields, **kwargs)


class UserSettingsHandler(SettingsHandler):
    """Search for user non-global setting"""

    def handle(self, search_fields: dict, **kwargs) -> Optional[SettingsBase]:
        user_search_fields = search_fields.copy()
        user_search_fields[SearchFields.SETTING_TYPE] = SettingType.USER.value
        user_search_fields[SearchFields.IS_GLOBAL] = False

        if settings := Settings.get_by_fields(user_search_fields):
            return settings

        return super().handle(search_fields, **kwargs)


class GlobalUserSettingsHandler(SettingsHandler):
    """Search for user global setting"""

    def handle(self, search_fields: dict, **kwargs) -> Optional[SettingsBase]:
        if not search_fields.get(SearchFields.USER_ID) and search_fields.get(SearchFields.CREDENTIAL_TYPE):
            return super().handle(search_fields)

        global_search_fields = {
            **search_fields,
            SearchFields.IS_GLOBAL: True,
            SearchFields.SETTING_TYPE: SettingType.USER.value,
        }

        # Remove PROJECT_NAME from global search fields if it exists
        global_search_fields.pop(SearchFields.PROJECT_NAME, None)

        if settings := Settings.get_by_fields(global_search_fields):
            return settings

        return super().handle(search_fields, **kwargs)


class ProjectSettingsHandler(SettingsHandler):
    """Search for project setting"""

    def handle(self, search_fields: dict, **kwargs) -> Optional[SettingsBase]:
        project_search_fields = search_fields.copy()
        project_search_fields.pop(SearchFields.USER_ID, None)
        project_search_fields[SearchFields.SETTING_TYPE] = SettingType.PROJECT.value
        settings = Settings.get_by_fields(project_search_fields)

        if settings:
            return settings

        return super().handle(search_fields, **kwargs)


def build_settings_handlers():
    start = AssistantUserMappingSettingsHandler()
    (
        start  # 1. Setting by assistant-usersetting mapping (for global assistant)
        | BySettingIDSettingsHandler()  # 2. By setting_id (if provided)
        | GlobalAssistantSettingsHandler()  # 3. If assistant is global - return assistant setting
        | AssistantSettingsHandler()  # 4. If assistant is not global with match by / user project
        | DefaultSettingsHandler()  # 5. Default setting -> by setting 'default' field (legacy?)
        | UserSettingsHandler()  # 6. By matching user setting (match by user and project)
        | GlobalUserSettingsHandler()  # 7. By matching global user setting (match by user)
        | ProjectSettingsHandler()  # 8. By matching by project
    )

    return start
