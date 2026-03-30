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

from fastapi import status

from codemie.configs import logger
from codemie.core.dependecies import set_dial_credentials, set_litellm_context
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import AssistantBase
from codemie.rest_api.models.settings import LiteLLMContext
from codemie.rest_api.security.user import User
from codemie.service.settings.settings import SettingsService


def _resolve_effective_project(
    assistant: AssistantBase | None, fallback_project_name: str | None, user: User
) -> str | None:
    if assistant is None:
        return fallback_project_name

    if not assistant.project:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Assistant project is not set",
            details=f"Assistant {assistant.id} has no project assigned.",
        )

    if not assistant.is_global:
        return assistant.project

    user_projects = set(user.project_names or []) | set(user.admin_project_names or [])
    if assistant.project in user_projects or not user.email:
        return assistant.project

    return user.email


def set_llm_context(assistant: AssistantBase | None, fallback_project_name: str | None, user: User):
    effective_project = _resolve_effective_project(assistant, fallback_project_name, user)

    try:
        litellm_creds = SettingsService.get_litellm_creds(project_name=effective_project, user_id=user.id)
        litellm_context = LiteLLMContext(credentials=litellm_creds, current_project=effective_project)
        set_litellm_context(litellm_context)
        dial_creds = SettingsService.get_dial_creds(effective_project)
        set_dial_credentials(dial_creds)
    except Exception as e:
        logger.error(
            f"Cannot get/set current llm credentials for project: {effective_project}, user: {user.id} due to: {str(e)}"
        )
