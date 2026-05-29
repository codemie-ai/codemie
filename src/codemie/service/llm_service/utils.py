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

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import status

from codemie.configs import config, logger
from codemie.core.dependecies import set_dial_credentials, set_litellm_context
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import AssistantBase
from codemie.rest_api.models.settings import LiteLLMContext
from codemie.rest_api.security.user import User
from codemie.service.settings.settings import SettingsService

if TYPE_CHECKING:
    from codemie.core.workflow_models.workflow_config import WorkflowConfigBase
    from codemie.rest_api.models.index import IndexInfo


def _resolve_effective_project(
    asset: AssistantBase | WorkflowConfigBase | IndexInfo | None,
    fallback_project_name: str | None,
    user: User,
) -> str | None:
    if asset is None:
        return fallback_project_name

    project = getattr(asset, 'project', None) or getattr(asset, 'project_name', None)
    if not project:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Asset project is not set",
            details=f"Asset {getattr(asset, 'id', None)} has no project assigned.",
        )

    if getattr(asset, 'is_global', False):
        user_projects = set(user.project_names or []) | set(user.admin_project_names or [])
        if project in user_projects or not user.email:
            return project
        return user.email

    return project


def set_llm_context(
    asset: AssistantBase | WorkflowConfigBase | IndexInfo | None,
    fallback_project_name: str | None,
    user: User,
):
    from codemie.rest_api.models.settings import SettingType
    from codemie.service.settings.base_settings import SearchFields
    from codemie_tools.base.models import CredentialTypes

    effective_project = _resolve_effective_project(asset, fallback_project_name, user)

    try:
        litellm_creds = SettingsService.get_litellm_creds(project_name=effective_project, user_id=user.id)
        if litellm_creds:
            setting = SettingsService.retrieve_setting(
                {
                    SearchFields.CREDENTIAL_TYPE: CredentialTypes.LITE_LLM,
                    SearchFields.PROJECT_NAME: effective_project,
                    SearchFields.USER_ID: user.id,
                }
            )
            # Project-scoped keys (e.g. platform/CLI budget keys) must not set litellm_context.credentials —
            # doing so would trigger USER_CREDENTIALS_BYPASS mode in llm_factory and skip override customer injection.
            if getattr(setting, "setting_type", None) == SettingType.PROJECT.value:
                litellm_creds = None
            elif asset is not None and config.LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED:
                is_shared = getattr(asset, 'shared', None)
                if is_shared is None:
                    is_shared = getattr(asset, 'project_space_visible', True)
                if is_shared:
                    litellm_creds = None  # shared asset → force project budget even when user has personal key
        litellm_context = LiteLLMContext(credentials=litellm_creds, current_project=effective_project)
        set_litellm_context(litellm_context)
        dial_creds = SettingsService.get_dial_creds(effective_project)
        set_dial_credentials(dial_creds)
    except Exception as e:
        logger.warning(
            f"set_llm_context failed for project={effective_project!r} user={user.username!r} ({user.id}): {e} — "
            f"LLM calls will fall back to platform budget without project attribution"
        )
