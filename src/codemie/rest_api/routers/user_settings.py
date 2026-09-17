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
from typing import List, Optional
from fastapi import APIRouter, status, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from codemie.configs.customer_config import customer_config
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import BaseResponse
from codemie.rest_api.routers.utils import raise_access_denied, raise_forbidden
from codemie.rest_api.security.authentication import project_access_check
from codemie.service.aws_bedrock.bedrock_orchestration_service import BedrockOrchestratorService
from codemie.service.settings.settings import SettingsService
from codemie.service.settings.settings_tester import SettingsTester
from codemie.service.settings.settings_index_service import SettingsIndexService
from codemie.rest_api.security.authentication import authenticate, User
from codemie.rest_api.models.settings import (
    SettingRequest,
    Settings,
    SettingType,
    TestSettingRequest,
    UserSetting,
    ProjectSetting,
)
from codemie_tools.base.models import CredentialTypes
from codemie.service.settings.settings_request_validator import (
    validate_credential_type_not_deprecated,
    validate_git_request,
    validate_litellm_request,
    validate_ms_teams_request,
    validate_scheduler_request,
    validate_webhook_request,
)

router = APIRouter(
    tags=["User Settings"],
    prefix="/v1",
    dependencies=[],
)

INVALID_SETTING_DATA_MSG = (
    "Invalid setting data. Please provide a non-empty, unique alias for the setting "
    "and ensure all required fields are filled correctly. If the problem persists, contact support."
)
PERSONAL_LITELLM_FEATURE = "personalLiteLLMIntegrations"
PERSONAL_LITELLM_DISABLED_DETAILS = "Personal LiteLLM integrations are not enabled for this customer."
PERSONAL_LITELLM_DISABLED_HELP = "Please contact your system administrator to enable personal LiteLLM integrations."


def _map_integrity_error(request: SettingRequest) -> ExtendedHTTPException:
    """Map a persistence-layer IntegrityError to an API error, scoped by credential_type.

    The ms_teams singleton-per-user constraint is the only known cause of an
    IntegrityError on this path today; every other credential type falls back to
    the generic 422 handling used for unexpected persistence failures elsewhere in
    this router.
    """
    if request.credential_type == CredentialTypes.MS_TEAMS:
        return ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message="ms_teams integration already exists",
            details="A concurrent request already created an ms_teams integration for your account.",
            help="Update or delete your existing ms_teams integration instead of creating a new one.",
        )
    return ExtendedHTTPException(
        code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message="Cannot save specified setting",
        details="A concurrent or conflicting persistence error occurred while trying to save the setting.",
        help=INVALID_SETTING_DATA_MSG,
    )


def _validate_litellm_user_setting_access(user: User) -> None:
    if user.is_admin_or_maintainer:
        return

    if customer_config.is_feature_enabled(PERSONAL_LITELLM_FEATURE):
        return

    raise ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message="Access denied",
        details=PERSONAL_LITELLM_DISABLED_DETAILS,
        help=PERSONAL_LITELLM_DISABLED_HELP,
    )


@router.get(
    "/settings/user",
    status_code=status.HTTP_200_OK,
)
def index_user_settings(
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
        settings_type=SettingType.USER, user=user, page=page, per_page=per_page, filters=parsed_filters
    )


@router.get(
    "/settings/user/available",
    status_code=status.HTTP_200_OK,
    response_model=List[Settings],
    response_model_by_alias=True,
)
def index_settings(request: Request, user: User = Depends(authenticate)):
    """
    Returns all saved credentials available for user.

    PROJECT integrations are always limited to projects the user can access — broader
    visibility is granted only by role (admin/maintainer sees every project; an applications
    admin sees their admin projects), never by a caller-supplied scope. Only the current
    user's own USER integrations are returned, so another user's personal credentials are
    never listed.
    """
    if user.is_admin_or_maintainer:
        project_settings = SettingsService.get_all_settings(settings_type=SettingType.PROJECT)
    elif user.is_applications_admin:
        project_settings = SettingsService.get_settings(
            project_names=user.admin_project_names, settings_type=SettingType.PROJECT
        )
    else:
        project_settings = SettingsService.get_settings(
            project_names=user.project_names, settings_type=SettingType.PROJECT
        )
    return SettingsService.get_settings(user_id=user.id) + project_settings


@router.post(
    "/settings/user",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def create_user_setting(request: SettingRequest, user: User = Depends(authenticate)):
    """
    Save user-specific settings to DB
    """
    validate_credential_type_not_deprecated(request)

    if request.credential_type == CredentialTypes.SCHEDULER:
        validate_scheduler_request(request)
    elif request.credential_type == CredentialTypes.LITE_LLM:
        _validate_litellm_user_setting_access(user)
        # Check if LiteLLM enterprise is available before allowing credential creation
        from codemie.enterprise.litellm import require_litellm_enabled

        require_litellm_enabled()
        validate_litellm_request(request)
    elif request.credential_type == CredentialTypes.GIT:
        validate_git_request(request)
    elif request.credential_type == CredentialTypes.WEBHOOK:
        validate_webhook_request(request)
    elif request.credential_type == CredentialTypes.MS_TEAMS:
        validate_ms_teams_request(request, SettingType.USER, user_id=user.id)

    if request.project_name:
        project_access_check(user, request.project_name)
    try:
        SettingsService.create_setting(user_id=user.id, request=request, user=user)

        return BaseResponse(message="Specified credentials saved")
    except IntegrityError as e:
        raise _map_integrity_error(request) from e
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot create specified setting",
            details=f"An error occurred while trying to create the setting: {str(e)}",
            help=INVALID_SETTING_DATA_MSG,
        ) from e


@router.put(
    "/settings/user/{setting_id}",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def update_user_setting(request: SettingRequest, setting_id: str, user: User = Depends(authenticate)):
    """
    Update user-specific settings
    """
    validate_credential_type_not_deprecated(request)

    if request.credential_type == CredentialTypes.SCHEDULER:
        validate_scheduler_request(request)
    elif request.credential_type == CredentialTypes.LITE_LLM:
        _validate_litellm_user_setting_access(user)
        # Check if LiteLLM enterprise is available before allowing credential update
        from codemie.enterprise.litellm import require_litellm_enabled

        require_litellm_enabled()
        validate_litellm_request(request)
    elif request.credential_type == CredentialTypes.GIT:
        validate_git_request(request)
    elif request.credential_type == CredentialTypes.WEBHOOK:
        validate_webhook_request(request)
    elif request.credential_type == CredentialTypes.MS_TEAMS:
        validate_ms_teams_request(request, SettingType.USER, setting_id=setting_id, user_id=user.id)

    if request.project_name:
        project_access_check(user, request.project_name)
    try:
        setting_ability = SettingsService.get_setting_ability(
            credential_id=setting_id,
            settings_type=SettingType.USER,
        )

        if not Ability(user).can(Action.WRITE, setting_ability):
            raise_access_denied("write")

        SettingsService.update_settings(credential_id=setting_id, request=request, user_id=user.id)
    except ExtendedHTTPException:
        raise
    except IntegrityError as e:
        raise _map_integrity_error(request) from e
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot update specified setting",
            details=f"An error occurred while trying to create the setting: {str(e)}",
            help=INVALID_SETTING_DATA_MSG,
        ) from e
    return BaseResponse(message="Specified credentials updated")


@router.delete(
    "/settings/user/{setting_id}",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def delete_user_setting(setting_id: str, user: User = Depends(authenticate)):
    """
    Removes user-specific credentials by given id
    """
    try:
        setting_ability = SettingsService.get_setting_ability(
            credential_id=setting_id,
            settings_type=SettingType.USER,
        )

        if not Ability(user).can(Action.DELETE, setting_ability):
            raise_access_denied("delete")

        BedrockOrchestratorService.delete_all_entities(setting_id)
        SettingsService.delete_setting(credential_id=setting_id, user_id=user.id)
    except ExtendedHTTPException:
        raise
    except KeyError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Credential not found",
            details=f"The credential with ID '{setting_id}' could not be found in the system.",
            help="Please verify the credential ID and ensure it exists. If you believe this is an error, "
            "check your project settings or contact support.",
        ) from e
    return BaseResponse(message="Specified credential removed")


@router.post(
    "/settings/test/",
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def test_setting(request: TestSettingRequest, user: User = Depends(authenticate)):
    """
    Test if setting credentials are valid.

    When a stored ``setting_id`` is supplied the caller must be authorized to
    read that setting (owner for USER settings; any project member for PROJECT
    settings), and the requested credential type must match the stored one,
    before any credentials are decrypted or test-executed. Inline requests
    (no ``setting_id``) require no ownership check.
    """
    if request.setting_id:
        setting = Settings.get_by_id(request.setting_id)
        if not setting:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Credential not found",
                details=f"The credential with ID '{request.setting_id}' could not be found in the system.",
                help="Please verify the credential ID and ensure it exists.",
            )

        settings_type = setting.setting_type or SettingType.USER
        setting_ability = ProjectSetting(setting) if settings_type == SettingType.PROJECT else UserSetting(setting)
        if not Ability(user).can(Action.READ, setting_ability):
            raise_forbidden("test")

        if request.credential_type != setting.credential_type:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Cannot test specified setting",
                details="The requested credential type does not match the stored setting's credential type.",
                help=INVALID_SETTING_DATA_MSG,
            )

    try:
        success, message = SettingsTester(request).test()
    except ExtendedHTTPException:
        raise
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot test specified setting",
            details=f"An error occurred while trying to test the setting: {str(e)}",
            help=INVALID_SETTING_DATA_MSG,
        ) from e

    if not success:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Integration test failed",
            details=message,
            help="",
        )

    return JSONResponse(status_code=status.HTTP_200_OK, content={"success": success, "message": message})
