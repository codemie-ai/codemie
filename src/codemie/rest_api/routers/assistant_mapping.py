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

"""
Router for assistant mappings endpoints.
"""

from typing import Optional

from fastapi import APIRouter, status, Depends

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import BaseResponse
from codemie.rest_api.models.usage.assistant_user_mapping import (
    ASSISTANT_SCOPE,
    AssistantMappingRequest,
    AssistantMappingResponse,
)
from codemie.rest_api.routers.utils import get_assistant_by_id_or_raise
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_user_mapping_service import assistant_user_mapping_service
from codemie.service.settings.base_settings import SearchFields
from codemie.service.settings.settings import SettingsService
from codemie.service.settings.settings_util import search_settings_by_id, user_can_access_setting
from codemie_tools.base.models import CredentialTypes


def _validate_mapping_access(
    tools_config: list[dict], user: User, assistant_project: str, marketplace: bool = False
) -> None:
    """Reject mappings that reference an integration the user cannot access.

    A user may only map their own USER integration or an accessible PROJECT integration.
    For project-shared assistants (``marketplace=False``) the PROJECT integration must
    belong to the assistant's own project and the user must have access to it. For
    marketplace assistants (``marketplace=True``) any PROJECT integration is accepted,
    matching the relaxed cross-project scope offered in the selection UI. The USER
    owner-only rule is unchanged in both cases, so a crafted request can never bind another
    user's personal integration. An empty ``integration_id`` carries no credentials, so it is
    accepted as-is; what it then means depends on the slot — for an MCP server the author's base
    config, for a regular tool the user's explicit "no integration".
    """
    for tool_config in tools_config:
        integration_id = tool_config.get("integration_id")
        if not integration_id:
            continue

        setting = search_settings_by_id(integration_id)
        if not user_can_access_setting(setting, user, assistant_project, marketplace=marketplace):
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Integration is not accessible",
                details="The selected integration is not available to your account.",
                help="Choose one of your own integrations or an integration of this assistant's project.",
            )


def _resolve_auto_lookup(credential_types: str, user: User, assistant) -> list[dict]:
    """Report what the resolution chain would pick for the given credential types.

    Reuses the very chain that runs at execution time (``SettingsService.retrieve_setting``), so the
    panel pre-selects exactly what a run would use instead of duplicating the lookup rules on the
    client. Each candidate passes the same access rule as an explicit save, so a setting the user
    may not use is never reported — reporting it would repeat the metadata leak that was removed
    from the earlier "resolved default" implementation.
    """
    by_value = {credential_type.value.lower(): credential_type for credential_type in CredentialTypes}
    resolved: list[dict] = []

    for requested in credential_types.split(","):
        requested = requested.strip()
        credential_type = by_value.get(requested.lower())
        if not credential_type:
            continue

        search_fields = {
            SearchFields.CREDENTIAL_TYPE: credential_type,
            SearchFields.USER_ID: user.id,
            SearchFields.PROJECT_NAME: assistant.project,
        }
        setting = SettingsService.retrieve_setting(search_fields, assistant.id)

        if not setting or not user_can_access_setting(
            setting, user, assistant.project, marketplace=bool(assistant.is_global)
        ):
            continue

        resolved.append({"credential_type": requested, "integration_id": setting.id})

    return resolved


router = APIRouter(
    tags=["Assistant Mappings"],
    prefix="/v1",
    dependencies=[],
)


@router.post(
    "/assistants/{assistant_id}/users/mapping",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def create_or_update_mapping(request: AssistantMappingRequest, assistant_id: str, user: User = Depends(authenticate)):
    """
    Create or update mappings between an assistant and tools/settings.

    Example request:
    ```json
    {
      "tools_config": [
        {
          "name": "Git",
          "integration_id": "12312312"
        }
      ]
    }
    ```
    """
    assistant = get_assistant_by_id_or_raise(assistant_id)

    _validate_mapping_access(request.tools_config, user, assistant.project, marketplace=bool(assistant.is_global))

    try:
        assistant_user_mapping_service.create_or_update_mapping(
            assistant_id=assistant_id,
            user_id=user.id,
            tools_config=request.tools_config,
            workflow_id=request.workflow_id or ASSISTANT_SCOPE,
            apply_to_assistant=request.apply_to_assistant,
        )

        return BaseResponse(message="Mappings created or updated successfully")
    except ExtendedHTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating or updating mappings: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to create or update mappings",
            details=f"An error occurred while trying to store mappings: {str(e)}",
            help="Please check your request format and try again. If the issue persists, contact support.",
        ) from e


@router.get(
    "/assistants/{assistant_id}/users/mapping",
    status_code=status.HTTP_200_OK,
    response_model=AssistantMappingResponse,
    response_model_by_alias=True,
)
def get_assistant_mapping(
    assistant_id: str,
    workflow_id: Optional[str] = None,
    credential_types: Optional[str] = None,
    user: User = Depends(authenticate),
):
    """
    Get mappings for a specific assistant and the current user.
    Allows retrieving mappings for both published and unpublished assistants.

    With ``workflow_id`` the response carries the selection effective inside that workflow —
    the assistant-wide slots overridden by the workflow-scoped ones — plus a flag telling the
    client whether an assistant-wide selection exists at all. Without it the response is the
    assistant-scoped mapping, unchanged.

    ``credential_types`` is a comma-separated list of credential types the client displays. For each
    of them the response reports what the resolution chain would pick right now, so slots the user
    never chose explicitly can be pre-selected with the integration a run would actually use.
    """
    assistant = get_assistant_by_id_or_raise(assistant_id)

    try:
        # Inside the try: resolving walks the settings chain and touches the store, so a transient
        # failure must surface as this endpoint's error rather than an unhandled 500.
        auto_resolved = _resolve_auto_lookup(credential_types, user, assistant) if credential_types else []

        if workflow_id:
            tools_config, has_assistant_scope_selection = assistant_user_mapping_service.get_effective_tools_config(
                assistant_id=assistant_id, user_id=user.id, workflow_id=workflow_id
            )
            response = AssistantMappingResponse.from_effective_config(
                assistant_id=assistant_id,
                user_id=user.id,
                workflow_id=workflow_id,
                tools_config=tools_config,
                has_assistant_scope_selection=has_assistant_scope_selection,
            )
            response.auto_resolved = auto_resolved
            return response

        mapping = assistant_user_mapping_service.get_mapping(assistant_id=assistant_id, user_id=user.id)

        if not mapping:
            return AssistantMappingResponse(
                id="",
                tools_config=[],
                user_id=user.id,
                assistant_id=assistant_id,
                auto_resolved=auto_resolved,
            )

        response = AssistantMappingResponse.from_db_model(mapping)
        response.auto_resolved = auto_resolved
        # An empty leftover row (every slot reset to "None") is not a selection.
        response.has_assistant_scope_selection = bool(mapping.tools_config)
        return response
    except ExtendedHTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error getting mappings: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to get mappings",
            details=f"An error occurred while trying to retrieve mappings: {str(e)}",
            help="Please try again later. If the issue persists, contact support.",
        ) from e
