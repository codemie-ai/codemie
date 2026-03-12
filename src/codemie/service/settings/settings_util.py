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

from codemie.configs import logger
from codemie.rest_api.models.assistant import Assistant
from codemie.service.assistant import VirtualAssistantService, VIRTUAL_ASSISTANT_PREFIX
from codemie.rest_api.models.settings import Settings
from codemie.service.settings.base_settings import SearchFields


def search_settings_by_id(setting_id):
    try:
        settings = Settings.get_by_id(setting_id)
        return settings
    except Exception as e:
        logger.info(f"Failed to retrieve specified assistant settings: {e}, settings_id: {setting_id}")


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
