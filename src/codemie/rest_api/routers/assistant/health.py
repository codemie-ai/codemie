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


from fastapi import APIRouter, status, Request, Depends

from codemie.configs import logger
from codemie.core.ability import Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import (
    AssistantHealthCheckRequest,
    AssistantHealthCheckResponse,
)
from codemie.rest_api.routers.utils import check_user_can_access_assistant, get_assistant_by_id_or_raise
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_health_check_service import AssistantHealthCheckService


router = APIRouter()


@router.post(
    "/assistants/{assistant_id}/health",
    status_code=status.HTTP_200_OK,
    response_model=AssistantHealthCheckResponse,
)
def health_check_assistant(
    assistant_id: str,
    raw_request: Request,
    request: AssistantHealthCheckRequest,
    user: User = Depends(authenticate),
):
    """
    Health check endpoint for assistants.

    Similar to the /model endpoint, this validates the assistant exists and is accessible
    before performing the health check.

    Performs a comprehensive health check on an assistant to verify it's properly configured
    and fully functional. The health check validates:

    1. **Configuration**: Checks that the assistant exists and has valid configuration (LLM model, tools, context)
    2. **Functionality**: Actually calls the assistant with a test message (non-streaming) to verify it works end-to-end

    This endpoint is useful for:
    - Validating assistant configuration after creation or updates
    - Troubleshooting assistant issues
    - Monitoring assistant availability
    - Pre-deployment verification
    - Ensuring LLM connectivity and tool functionality

    **Important**: This health check actually calls the LLM with a test message to validate
    the assistant is fully functional. This ensures not just configuration validity but also
    that the assistant can successfully generate responses.

    **Path Parameters:**
    - assistant_id: ID of the assistant to check (required)

    **Request Body (optional):**
    - version: Optional specific version to test

    **Note**: The health check always uses the test message "show tools" and the assistant's
    configured LLM model to validate functionality.

    **Response:**
    - is_healthy: Overall health status (true if all checks pass)
    - configuration_valid: Whether configuration is valid
    - execution_successful: Whether the assistant successfully executed and generated a response
    - tools_available: Number of tools configured for the assistant
    - error: Error details if health check failed

    **Example Usage:**
    ```bash
    POST /v1/assistants/123e4567-e89b-12d3-a456-426614174000/health
    ```
    ```json
    {}
    ```

    Or to test a specific version:
    ```json
    {
        "version": 2
    }
    ```

    **Note**: This endpoint executes a real LLM call, so it will consume tokens and may take
    a few seconds to complete depending on the LLM response time.
    """
    # Check if assistant exists and user has access (similar to /model endpoint)
    assistant = get_assistant_by_id_or_raise(assistant_id)
    check_user_can_access_assistant(user, assistant, "view", Action.READ)

    try:
        return AssistantHealthCheckService.health_check_assistant(
            assistant=assistant, request=request, user=user, raw_request=raw_request
        )
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot perform health check on specified assistant",
            details=f"An error occurred while trying to check the assistant: {str(e)}",
            help="Please check the assistant configuration or contact an administrator for assistance.",
        ) from e
