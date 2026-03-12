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

"""Utilities for converting between Assistant and A2A types."""

import base64
import re
from typing import Dict, List, Optional, Any

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from fastapi import Request

from codemie.configs import config
from codemie.core.models import AssistantChatRequest, BaseModelResponse, ChatMessage, ChatRole, AuthenticationType
from codemie.rest_api.a2a.types import (
    AgentCard,
    AgentProvider,
    AgentCapabilities,
    AgentAuthentication,
    AgentSkill,
    Message,
    TaskSendParams,
    SendTaskRequest,
    SendTaskStreamingRequest,
    TextPart,
    Task,
)
from codemie.rest_api.models.assistant import Assistant, ToolKitDetails, ToolDetails


def convert_to_task_request(
    chat_request: AssistantChatRequest, raw_request: Request
) -> SendTaskRequest | SendTaskStreamingRequest:
    """
    Convert an AssistantChatRequest to either SendTaskRequest or SendTaskStreamingRequest.

    Args:
        chat_request: The AssistantChatRequest to convert
        raw_request: The raw FastAPI request object

    Returns:
        Either SendTaskRequest or SendTaskStreamingRequest depending on request's stream flag
    """
    # Create Message object from chat request text and content
    message = Message(role="user", parts=[TextPart(text=chat_request.text)])

    # Create TaskSendParams
    params = TaskSendParams(
        id=raw_request.state.uuid,
        sessionId=chat_request.conversation_id,
        message=message,
        historyLength=chat_request.history_index or 0,
    )

    # Choose request type based on request.stream flag
    request_cls = SendTaskStreamingRequest if chat_request.stream else SendTaskRequest
    return request_cls(params=params)


def convert_to_base_model_response(task: Task) -> BaseModelResponse:
    """
    Convert Task to BaseModelResponse.

    Args:
        task: Response from task manager containing result with artifacts

    Returns:
        BaseModelResponse with the task result and metadata
    """
    response_parts = task.artifacts[0].parts if task.artifacts else task.status.message.parts
    return BaseModelResponse(
        generated=response_parts[0].text,
        time_elapsed=0,  # We could add timing if needed
        thoughts=[],  # We could extract thoughts if needed
        task_id=task.id,
    )


def convert_messages_to_chat_messages(messages: List[Message]) -> List[ChatMessage]:
    """
    Convert a list of A2A Message objects to a list of ChatMessage objects.

    Args:
        messages: List of A2A Message objects to convert

    Returns:
        List[ChatMessage]: The converted list of ChatMessage objects
    """
    chat_messages = []

    for message in messages:
        # Convert role from "user"/"agent" to ChatRole.USER/ChatRole.ASSISTANT
        role = ChatRole.USER if message.role == "user" else ChatRole.ASSISTANT

        # Extract text content from parts
        message_text = ""
        for part in message.parts:
            if part.type == "text":
                message_text += part.text
            elif part.type == "file":
                # For file parts, we might want to include a placeholder or reference
                message_text += f"[File: {part.file.name or 'unnamed'}]"
            elif part.type == "data":
                # For data parts, we might want to include a summary or representation
                message_text += "[Data object]"

        # Create ChatMessage object
        chat_message = ChatMessage(role=role, message=message_text)
        chat_messages.append(chat_message)

    return chat_messages


def to_kebab_case(s: str) -> str:
    """
    Convert a string to kebab-case format (lowercase with hyphens between words).

    Args:
        s: The string to convert

    Returns:
        str: The string converted to kebab-case
    """
    # First handle camelCase by inserting hyphens
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1-\2', s)

    # Replace underscores with hyphens
    s = s.replace('_', '-')

    # Replace spaces with hyphens
    s = re.sub(r'\s+', '-', s)

    # Replace multiple hyphens with a single one
    s = re.sub(r'-+', '-', s)

    # Convert to lowercase and trim any leading/trailing hyphens
    return s.lower().strip('-')


def tool_to_agent_skill(tool: ToolDetails, toolkit_label: Optional[str] = None) -> AgentSkill:
    """
    Convert a ToolDetails object to an A2A AgentSkill.

    Args:
        tool: The tool to convert
        toolkit_label: Optional label of the parent toolkit for context

    Returns:
        AgentSkill: The A2A agent skill representing this tool
    """
    # Generate a unique ID for the skill based on the tool name
    skill_id = tool.name.lower().replace(" ", "_")

    # Use the tool label as the name, or the tool name if no label is set
    skill_name = tool.label if tool.label else tool.name

    # Create a description from the tool details
    description = getattr(tool, "user_description", None) or getattr(tool, "description", "")

    # Create examples from the tool if available
    examples = []
    if hasattr(tool, "examples") and tool.examples:
        examples = tool.examples

    # Create tags from tool name parts and convert to kebab-case
    name_parts = tool.name.split('_') if '_' in tool.name else [tool.name]
    tags = [to_kebab_case(part) for part in name_parts]

    # Add toolkit label as a tag if provided
    if toolkit_label:
        tags.append(to_kebab_case(toolkit_label))

    return AgentSkill(
        id=skill_id,
        name=skill_name,
        description=description,
        tags=tags,
        examples=examples if examples else None,
        inputModes=["text"],  # Default to text mode
        outputModes=["text"],  # Default to text mode
    )


def tools_from_toolkits_to_agent_skills(toolkits: List[ToolKitDetails]) -> List[AgentSkill]:
    """
    Convert all tools from a list of ToolKitDetails objects to A2A AgentSkills.

    Args:
        toolkits: The list of toolkits whose tools will be converted

    Returns:
        List[AgentSkill]: The list of A2A agent skills representing the individual tools
    """
    skills = []
    for toolkit in toolkits:
        toolkit_label = toolkit.label if toolkit.label else toolkit.toolkit
        for tool in toolkit.tools:
            skills.append(tool_to_agent_skill(tool, toolkit_label))
    return skills


def assistant_to_agent_card(
    assistant: Assistant,
    request: Request,
) -> AgentCard:
    """
    Convert an Assistant to an A2A AgentCard.

    Args:
        :param assistant: The assistant to convert
        :param request: Http request

    Returns:
        AgentCard: The A2A agent card representing this assistant
    """
    # Map assistant tools to A2A skills format
    skills = []

    # Convert tools from toolkits to agent skills if available
    if assistant.toolkits:
        skills = tools_from_toolkits_to_agent_skills(assistant.toolkits)

    # If no skills are defined, add a default conversational skill
    if not skills:
        skills = [
            AgentSkill(
                id="conversation",
                name="Conversation",
                description="General conversation ability",
                inputModes=["text"],
                outputModes=["text"],
            )
        ]

    # Create the agent card
    return AgentCard(
        name=assistant.name,
        description=assistant.description,
        url=f"{request.base_url.scheme}://{request.base_url.netloc}{config.API_ROOT_PATH}/v1/a2a/assistants/{assistant.id}",
        version="1.0.0",  # Default version
        provider=AgentProvider(organization=config.A2A_PROVIDER_ORGANIZATION, url=config.A2A_PROVIDER_URL),
        capabilities=AgentCapabilities(streaming=False, pushNotifications=False, stateTransitionHistory=True),
        authentication=_get_agent_authentication(),
        skills=skills,
    )


def _get_agent_authentication():
    credentials = None
    if config.is_local:
        credentials = "dev-codemie-user"
    # Need to add support for integrations to get for given user and pass in header
    return AgentAuthentication(schemes=["Bearer"], credentials=credentials)


def get_auth_header(
    creds: Dict[str, Any], method: str = "GET", url: Optional[str] = None, body: Optional[bytes] = None
) -> Dict[str, str]:
    """
    Generates authentication headers based on the A2A credentials.
    Always returns an {"Authorization": xxx} dictionary or appropriate header for API keys.

    Args:
        creds: Dictionary containing authentication credentials with auth_type and other required fields
        method: HTTP method for the request (used for AWS Signature)
        url: URL for the request (used for AWS Signature)
        body: Request body (used for AWS Signature)

    Returns:
        dict: A dictionary containing the appropriate authorization header

    Raises:
        ValueError: When credentials are missing or incomplete
    """
    if not creds:
        return {}

    auth_type = AuthenticationType.from_string(creds.get("auth_type"))

    if auth_type == AuthenticationType.BASIC:
        return _basic_auth(auth_type, creds)

    if auth_type == AuthenticationType.APIKEY:
        return _apikey_auth(creds)

    if auth_type == AuthenticationType.AWS_SIGNATURE:
        return _aws_signature_auth(creds, method, url, body)

    if auth_type == AuthenticationType.BEARER:
        return _bearer_auth(auth_type, creds)

    raise ValueError(f"Unsupported authentication type: {auth_type}")


def _basic_auth(auth_type: AuthenticationType, creds: Dict[str, Any]) -> Dict[str, str]:
    """Handle Basic authentication (username:password encoded in base64)"""
    username = creds.get("username", "")
    password = creds.get("password", "")
    if not (username and password):
        raise ValueError("Basic authentication requires both username and password")
    auth_value = base64.b64encode(f"{username}:{password}".encode()).decode('utf-8')
    return {"Authorization": f"{auth_type.display_value} {auth_value}"}


def _apikey_auth(creds: Dict[str, Any]) -> Dict[str, str]:
    """Handle API key authentication, use the specified header name"""
    header_name = creds.get("header_name", "X-API-Key")
    auth_value = creds.get("auth_value", "")
    if not auth_value:
        raise ValueError("API key authentication requires an auth_value")
    # Return the API key with its custom header name
    return {header_name: auth_value}


def _aws_signature_auth(
    creds: Dict[str, Any], method: str = "GET", url: Optional[str] = None, body: Optional[bytes] = None
) -> Dict[str, str]:
    """Handle AWS signature authentication"""
    aws_region = creds.get("aws_region", "")
    aws_service_name = creds.get("aws_service_name", "")
    aws_secret_access_key = creds.get("aws_secret_access_key", "")
    aws_access_key_id = creds.get("aws_access_key_id", "")

    if not all([url, aws_region, aws_service_name, aws_secret_access_key, aws_access_key_id]):
        raise ValueError(
            "AWS Signature authentication requires url, aws_region, aws_service_name, "
            "aws_access_key_id, and aws_secret_access_key"
        )

    request = AWSRequest(
        method=method,
        url=url,
        headers={"Content-Type": "application/json"},
        data=body,
    )

    SigV4Auth(
        Credentials(aws_access_key_id, aws_secret_access_key, creds.get("aws_session_token")),
        aws_service_name,
        aws_region,
    ).add_auth(request)

    return dict(request.headers)


def _bearer_auth(auth_type: AuthenticationType, creds: Dict[str, Any]) -> Dict[str, str]:
    """Handle Bearer authentication"""
    auth_value = creds.get("auth_value", "")
    if not auth_value:
        raise ValueError("Bearer token authentication requires an auth_value")
    return {"Authorization": f"{auth_type.display_value} {auth_value}"}
