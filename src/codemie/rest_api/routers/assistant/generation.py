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

from typing import Optional

from pydantic import BaseModel

from fastapi import APIRouter, status, Request, Depends
from fastapi.responses import JSONResponse

from codemie.configs.logger import set_logging_info
from codemie.core.exceptions import ExtendedHTTPException, MCPAuthenticationRequiredException
from codemie.rest_api.models.assistant import (
    MCPServerCheckRequest,
)
from codemie.rest_api.models.assistant_generator import (
    AssistantGeneratorRequest,
    AssistantGeneratorResponse,
    PromptGeneratorResponse,
    PromptGeneratorRequest,
    RefineRequest,
    RefineGeneratorResponse,
    RefineRequestBody,
)
from codemie.service.assistant_generator_service import AssistantGeneratorService, AssistantToolkit, RefinePromptDetails
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService
from codemie.service.mcp.mcp_tester import MCPServerTester
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException


router = APIRouter()


class SystemPromptValidationRequest(BaseModel):
    """Request model for system prompt validation"""

    system_prompt_template: str
    prompt_vars: dict[str, str]
    assistant_id: Optional[str] = None


class SystemPromptValidationResponse(BaseModel):
    """Response model for system prompt validation"""

    rendered_prompt: str
    is_valid: bool = True
    message: str = "System prompt rendered successfully"


@router.post(
    "/assistants/generate",
    status_code=status.HTTP_200_OK,
    response_model=AssistantGeneratorResponse,
)
def generate_assistant(raw_request: Request, request: AssistantGeneratorRequest, user: User = Depends(authenticate)):
    """
    Generate assistant details from user input text.
    Returns assistant name, description, conversation starters, and comprehensive system prompt.
    """
    try:
        request_id = raw_request.state.uuid
        set_logging_info(uuid=request_id, user_id=user.id, user_email=user.username)

        # Set LiteLLM context with user's credentials
        from codemie.service.llm_service.utils import set_llm_context

        set_llm_context(None, user.current_project, user)

        result = AssistantGeneratorService.generate_assistant_details(
            text=request.text,
            user=user,
            llm_model=request.llm_model,
            include_tools=request.include_tools,
            request_id=request_id,
        )
        return result
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to generate assistant details",
            details=f"An error occurred while generating assistant details: {str(e)}",
            help="Try refining your input text or using a different model.",
        )


@router.post(
    "/assistants/prompt/generate",
    status_code=status.HTTP_200_OK,
    response_model=PromptGeneratorResponse,
)
def generate_assistant_prompt(
    raw_request: Request, request: PromptGeneratorRequest, user: User = Depends(authenticate)
):
    """
    Generate assistant details from user input text.
    Returns assistant name, description, conversation starters, and comprehensive system prompt.
    Automatically includes available datasources from the user's project.
    """
    try:
        request_id = raw_request.state.uuid
        set_logging_info(uuid=request_id, user_id=user.id, user_email=user.username)

        # Set LiteLLM context with user's credentials
        from codemie.service.llm_service.utils import set_llm_context

        set_llm_context(None, user.current_project, user)

        result = AssistantGeneratorService.generate_assistant_prompt(
            user=user,
            text=request.text,
            existing_prompt=request.system_prompt,
            project=user.current_project,
            llm_model=request.llm_model,
            request_id=request_id,
        )
        return result
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to generate assistant details",
            details=f"An error occurred while generating assistant details: {str(e)}",
            help="Try refining your input text or using a different model.",
        )


@router.post(
    "/assistants/refine",
    status_code=status.HTTP_200_OK,
    response_model=RefineGeneratorResponse,
)
def refine_with_ai(
    raw_request: Request,
    request: RefineRequest = RefineRequestBody,
    user: User = Depends(authenticate),
):
    """
    Accepts assistant draft fields and returns LLM recommendations and datasource assessments.
    """
    try:
        request_id = raw_request.state.uuid
        set_logging_info(uuid=request_id, user_id=user.id, user_email=user.username)

        # Set LiteLLM context with user's credentials
        from codemie.service.llm_service.utils import set_llm_context

        set_llm_context(None, request.project or user.current_project, user)

        refine_details = RefinePromptDetails(
            name=request.name,
            description=request.description,
            categories=request.categories,
            system_prompt=request.system_prompt,
            conversation_starters=request.conversation_starters,
            toolkits=[AssistantToolkit.model_validate(t) for t in request.toolkits] if request.toolkits else None,
            context=request.context,
        )

        result = AssistantGeneratorService.generate_refine_prompt(
            refine_details=refine_details,
            user=user,
            request_id=request_id,
            refine_prompt=request.refine_prompt,
            project=request.project,
            _include_tools=request.include_tools,
            include_context=request.include_context,
            include_categories=request.include_categories,
            llm_model=request.llm_model,
        )

        return result
    except ExtendedHTTPException:
        raise
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to refine assistant with AI",
            details=f"An error occurred while refining assistant: {str(e)}",
            help="Try again later or check the assistant draft fields.",
        )


@router.post(
    "/assistants/mcp/test",
    status_code=status.HTTP_200_OK,
)
def check_mcp_server(request: MCPServerCheckRequest, user: User = Depends(authenticate)):
    """
    Test if MCP server config is valid
    """
    try:
        success, message = MCPServerTester(request, user).test()

        return JSONResponse(status_code=status.HTTP_200_OK, content={"success": success, "message": message})
    except BrokerAuthRequiredException:
        raise
    except MCPAuthenticationRequiredException:
        raise
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot test specified mcp server",
            details=f"An error occurred while trying to test the mcp server: {str(e)}",
        ) from e


@router.post(
    "/assistants/system-prompt/validate",
    status_code=status.HTTP_200_OK,
    response_model=SystemPromptValidationResponse,
)
def validate_system_prompt(request: SystemPromptValidationRequest, user: User = Depends(authenticate)):
    """
    Validate and render a system prompt template with provided variables.

    This endpoint allows you to test system prompt templates before using them,
    ensuring that variable names are valid and the template renders correctly.
    It uses sandboxed Jinja2 rendering to prevent SSTI attacks.

    Variable names must be alphanumeric with underscores only (e.g., 'project_name', 'user_123').
    Spaces and special characters are not allowed in variable names.
    """
    try:
        rendered_prompt = AssistantService.render_system_prompt_with_vars(
            system_prompt_template=request.system_prompt_template,
            prompt_vars=request.prompt_vars,
            assistant_id=request.assistant_id,
            user_id=user.id,
        )

        return SystemPromptValidationResponse(
            rendered_prompt=rendered_prompt,
            is_valid=True,
            message="System prompt rendered successfully",
        )

    except Exception as e:
        # Import at function level to avoid circular imports
        from codemie.core.template_security import TemplateSecurityError

        if isinstance(e, TemplateSecurityError):
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="System prompt validation failed",
                details=f"Security violation detected: {str(e)}",
                help="Please review your template for invalid variable names or security issues. "
                "Variable names must be alphanumeric with underscores only (e.g., 'project_name', 'user_123'). "
                "Spaces and special characters are not allowed in variable names.",
            )
        else:
            raise ExtendedHTTPException(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message="Failed to validate system prompt",
                details=f"An error occurred while validating the system prompt: {str(e)}",
                help="Please check your template syntax and try again.",
            )
