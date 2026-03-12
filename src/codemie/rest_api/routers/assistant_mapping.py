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

from fastapi import APIRouter, status, Depends

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import BaseResponse
from codemie.rest_api.models.usage.assistant_user_mapping import (
    AssistantMappingRequest,
    AssistantMappingResponse,
)
from codemie.rest_api.routers.assistant import _get_assistant_by_id_or_raise
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_user_mapping_service import assistant_user_mapping_service

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
    _get_assistant_by_id_or_raise(assistant_id)

    try:
        assistant_user_mapping_service.create_or_update_mapping(
            assistant_id=assistant_id, user_id=user.id, tools_config=request.tools_config
        )

        return BaseResponse(message="Mappings created or updated successfully")
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
def get_assistant_mapping(assistant_id: str, user: User = Depends(authenticate)):
    """
    Get mappings for a specific assistant and the current user.
    Allows retrieving mappings for both published and unpublished assistants.
    """
    # Verify that the assistant exists
    _get_assistant_by_id_or_raise(assistant_id)

    try:
        # Get the mappings
        mapping = assistant_user_mapping_service.get_mapping(assistant_id=assistant_id, user_id=user.id)

        if not mapping:
            return AssistantMappingResponse(id="", tools_config=[], user_id=user.id, assistant_id=assistant_id)

        # Convert to response model
        return AssistantMappingResponse.from_db_model(mapping)
    except ExtendedHTTPException as e:
        # Re-raise ExtendedHTTPException as is
        raise e
    except Exception as e:
        logger.error(f"Error getting mappings: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to get mappings",
            details=f"An error occurred while trying to retrieve mappings: {str(e)}",
            help="Please try again later. If the issue persists, contact support.",
        ) from e
