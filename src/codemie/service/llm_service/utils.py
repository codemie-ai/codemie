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
from codemie.core.dependecies import set_dial_credentials, set_litellm_context
from codemie.rest_api.models.settings import LiteLLMContext
from codemie.service.settings.settings import SettingsService


def set_llm_context(project_name: str, user_id: str):
    try:
        litellm_creds = SettingsService.get_litellm_creds(project_name=project_name, user_id=user_id)
        litellm_context = LiteLLMContext(credentials=litellm_creds, current_project=project_name)
        set_litellm_context(litellm_context)
        dial_creds = SettingsService.get_dial_creds(project_name)
        set_dial_credentials(dial_creds)
    except Exception as e:
        logger.error(
            f"Cannot get/set current llm credentials for project: {project_name}, user: {user_id} due to: {str(e)}"
        )
