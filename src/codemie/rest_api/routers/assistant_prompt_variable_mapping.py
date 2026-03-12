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
Router for assistant prompt variable mappings endpoints.
"""

from fastapi import APIRouter, status, Depends

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import BaseResponse
from codemie.rest_api.models.usage.assistant_prompt_variable_mapping import (
    AssistantPromptVariableMappingRequest,
    AssistantPromptVariableMappingResponse,
)
from codemie.rest_api.routers.assistant import _get_assistant_by_id_or_raise, _check_user_can_access_assistant
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.core.ability import Action
from codemie.service.assistant.assistant_prompt_variable_mapping_service import (
    assistant_prompt_variable_mapping_service,
)

router = APIRouter(
    tags=["Assistant Prompt Variable Mappings"],
    prefix="/v1",
    dependencies=[],
)


@router.post(
    "/assistants/{assistant_id}/users/prompt-variables",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def create_or_update_prompt_variable_mapping(
    request: AssistantPromptVariableMappingRequest, assistant_id: str, user: User = Depends(authenticate)
):
    """
    Create or update mappings between an assistant's prompt variables and user-specific values.

    Example request:
    ```json
    {
      "variables_config": [
        {
          "variable_key": "project_name",
          "variable_value": "My Project"
        }
      ]
    }
    ```
    """
    # Verify that the assistant exists and user has access
    assistant = _get_assistant_by_id_or_raise(assistant_id)
    _check_user_can_access_assistant(user, assistant, "view", Action.READ)

    try:
        # Store the variables_config with encryption for sensitive variables
        assistant_prompt_variable_mapping_service.create_or_update_mapping(
            assistant_id=assistant_id,
            user_id=user.id,
            variables_config=request.variables_config,
        )

        return BaseResponse(message="Prompt variable mappings created or updated successfully")
    except Exception as e:
        logger.error(f"Error creating or updating prompt variable mappings: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to create or update prompt variable mappings",
            details=f"An error occurred while trying to store prompt variable mappings: {str(e)}",
            help="Please check your request format and try again. If the issue persists, contact support.",
        ) from e


@router.get(
    "/assistants/{assistant_id}/users/prompt-variables",
    status_code=status.HTTP_200_OK,
    response_model=AssistantPromptVariableMappingResponse,
    response_model_by_alias=True,
)
def get_assistant_prompt_variable_mapping(assistant_id: str, user: User = Depends(authenticate)):
    """
    Get prompt variable mappings for a specific assistant and the current user.
    """
    # Verify that the assistant exists
    assistant = _get_assistant_by_id_or_raise(assistant_id)

    # Verify that the user has access to the assistant
    _check_user_can_access_assistant(user, assistant, "view", Action.READ)

    try:
        # Get the mappings with masked sensitive variables
        # Pass assistant so we can get is_sensitive flag from definition
        mapping = assistant_prompt_variable_mapping_service.get_mapping_with_masked_values(
            assistant_id=assistant_id, user_id=user.id, assistant=assistant
        )

        if not mapping:
            return AssistantPromptVariableMappingResponse(
                id="", variables_config=[], user_id=user.id, assistant_id=assistant_id
            )

        # Convert to response model
        return AssistantPromptVariableMappingResponse.from_db_model(mapping)
    except ExtendedHTTPException as e:
        # Re-raise ExtendedHTTPException as is
        raise e
    except Exception as e:
        logger.error(f"Error getting prompt variable mappings: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to get prompt variable mappings",
            details=f"An error occurred while trying to retrieve prompt variable mappings: {str(e)}",
            help="Please try again later. If the issue persists, contact support.",
        ) from e
