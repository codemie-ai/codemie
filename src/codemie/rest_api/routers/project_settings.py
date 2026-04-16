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

import json
from typing import Optional

from fastapi import APIRouter, status, Request, Depends

from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import BaseResponse, CreatedByUser
from codemie.rest_api.models.settings import Settings, SettingRequest, SettingType
from codemie_tools.base.models import CredentialTypes
from codemie.rest_api.routers.utils import raise_access_denied
from codemie.rest_api.security.authentication import authenticate, User
from codemie.service.aws_bedrock.bedrock_orchestration_service import BedrockOrchestratorService
from codemie.service.settings.settings import SettingsService
from codemie.service.settings.settings_index_service import SettingsIndexService
from codemie.service.settings.settings_request_validator import (
    validate_git_request,
    validate_litellm_request,
    validate_scheduler_request,
    validate_webhook_request,
)

router = APIRouter(
    tags=["Project Settings"],
    prefix="/v1",
    dependencies=[],
)


@router.get(
    "/settings/project/users",
    status_code=status.HTTP_200_OK,
    response_model=list[CreatedByUser],
)
def get_project_settings_users(
    user: User = Depends(authenticate),
) -> list[CreatedByUser]:
    """
    Returns list of users who created project settings
    """
    result = SettingsIndexService.get_users(user=user, settings_type=SettingType.PROJECT)
    return result


@router.get(
    "/settings/project",
    status_code=status.HTTP_200_OK,
)
def index_project_settings(
    request: Request,
    user: User = Depends(authenticate),
    filters: Optional[str] = None,
    page: int = 0,
    per_page: int = 10,
):
    """
    Returns all saved user-specific credentials
    """
    parsed_filters = json.loads(filters) if filters else {}

    return SettingsIndexService.run(
        settings_type=SettingType.PROJECT, user=user, page=page, per_page=per_page, filters=parsed_filters
    )


@router.post(
    "/settings/project",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def create_project_setting(request: SettingRequest, user: User = Depends(authenticate)):
    """
    Create project-specific setting
    """
    if request.credential_type == CredentialTypes.SCHEDULER:
        validate_scheduler_request(request)
    elif request.credential_type == CredentialTypes.LITE_LLM:
        if not user.is_admin_or_maintainer:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Access denied",
                details="LiteLLM integrations can only be created by admin users.",
                help="Please contact your system administrator to configure LiteLLM integrations.",
            )
        # Check if LiteLLM enterprise is available before allowing credential creation
        from codemie.enterprise.litellm import require_litellm_enabled

        require_litellm_enabled()
        validate_litellm_request(request)
    elif request.credential_type == CredentialTypes.GIT:
        validate_git_request(request)
    elif request.credential_type == CredentialTypes.WEBHOOK:
        validate_webhook_request(request)

    _check_permission(user, request.project_name)

    try:
        SettingsService.create_setting(user_id=user.id, request=request, settings_type=SettingType.PROJECT, user=user)
        return BaseResponse(message="Specified credentials saved")
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot create specified setting",
            details=f"An error occurred while trying to create the setting: {str(e)}",
            help="Invalid setting data. Please provide a non-empty, unique alias for the setting "
            "and ensure all required fields are filled correctly. If the problem persists, contact support.",
        ) from e


@router.put(
    "/settings/project/{setting_id}",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def update_project_setting(request: SettingRequest, setting_id: str, user: User = Depends(authenticate)):
    """
    Update project setting
    """
    if request.credential_type == CredentialTypes.SCHEDULER:
        validate_scheduler_request(request)
    elif request.credential_type == CredentialTypes.LITE_LLM:
        if not user.is_admin_or_maintainer:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Access denied",
                details="LiteLLM integrations can only be updated by admin users.",
                help="Please contact your system administrator to configure LiteLLM integrations.",
            )
        # Check if LiteLLM enterprise is available before allowing credential update
        from codemie.enterprise.litellm import require_litellm_enabled

        require_litellm_enabled()
        validate_litellm_request(request)
    elif request.credential_type == CredentialTypes.GIT:
        validate_git_request(request)
    elif request.credential_type == CredentialTypes.WEBHOOK:
        validate_webhook_request(request)

    try:
        setting_ability = SettingsService.get_setting_ability(
            credential_id=setting_id,
            settings_type=SettingType.PROJECT,
        )

        if not Ability(user).can(Action.WRITE, setting_ability):
            raise_access_denied("write")
        SettingsService.update_settings(credential_id=setting_id, request=request, settings_type=SettingType.PROJECT)
    except ExtendedHTTPException:
        raise
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot update specified setting",
            details=f"An error occurred while trying to update the setting: {str(e)}",
            help="Invalid setting data. Please provide a non-empty, unique alias for the setting"
            " and ensure all required fields are filled correctly. If the problem persists, contact support.",
        ) from e
    return BaseResponse(message="Specified credentials updated")


@router.delete(
    "/settings/project/{setting_id}",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def delete_project_setting(setting_id: str, user: User = Depends(authenticate)):
    """
    Remove project setting
    """
    setting = Settings.get_by_id(setting_id)
    _check_permission(user, setting.project_name)

    try:
        BedrockOrchestratorService.delete_all_entities(setting_id)
        Settings.delete_setting(setting_id)
    except KeyError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Credential not found",
            details=f"The credential with ID '{setting_id}' could not be found in the system.",
            help="Please verify the credential ID and ensure it exists. If you believe this "
            "is an error, check your project settings or contact support.",
        ) from e

    return BaseResponse(message="Specified credential removed")


def _check_permission(user: User, project_name: str):
    if user.is_admin_or_maintainer:
        return True

    if not user.is_application_admin(project_name):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Access denied",
            details=f"You do not have sufficient permissions to access the project '{project_name}'.",
            help="This action requires application admin privileges. If you believe you should have access, "
            "please contact your system administrator or the project owner.",
        )
