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

from datetime import datetime
import io
import json
from typing import List, Optional

from codemie_tools.base.models import Tool
from fastapi import APIRouter, Depends, status, UploadFile, Response, Query
from fastapi.encoders import jsonable_encoder
from starlette.responses import StreamingResponse

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    CreateConversationRequest,
    BaseResponse,
    UpdateConversationRequest,
    UpdateAiMessageRequest,
    UpdateConversationFolderRequest,
)
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.base import BaseModelWithSQLSupport
from codemie.core.workflow_models import WorkflowConfig
from codemie.rest_api.models.conversation import (
    Conversation,
    AssistantDetails,
    ConversationHistoryPaginationData,
    ConversationListItem,
    ConversationExportFormat,
    ConversationResponse,
    UpsertHistoryRequest,
    UpsertHistoryResponse,
)
from codemie.rest_api.models.index import SortOrder
from codemie.rest_api.models.conversation_folder import ConversationFolder
from codemie.rest_api.models.share.shared_conversation import SharedConversation
from codemie.rest_api.routers.feedback import CONVERSATION_NOT_FOUND_MESSAGE, CONVERSATION_NOT_FOUND_HELP
from codemie.rest_api.routers.utils import raise_access_denied, remove_nulls
from codemie.utils.datetime_utils import get_timestamp_bounds
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.conversation import MessageExporter, ExportFormat
from codemie.service.conversation_service import ConversationService
from codemie.service.constants import (
    DEFAULT_CONVERSATIONS_PER_PAGE,
    DEFAULT_HISTORY_ITEMS_PER_PAGE,
    MAX_CONVERSATIONS_PER_PAGE,
    MAX_HISTORY_ITEMS_PER_PAGE,
    DEFAULT_PAGE,
)
from codemie.service.monitoring.conversation_monitoring_service import ConversationMonitoringService


router = APIRouter(
    tags=["Conversation"],
    prefix="/v1",
    dependencies=[Depends(authenticate)],
)
conversation_monitoring_service = ConversationMonitoringService()

EXPORT_FORMAT_NOT_SUPPORTED_MESSAGE = "Export format not supported"
EXPORT_FORMAT_NOT_SUPPORTED_DETAILS = "The requested export format is not supported by the system."
EXPORT_FORMAT_NOT_SUPPORTED_HELP = (
    "Please select one of the supported export formats and try again."
    + "For additional supported formats, refer to the documentation or contact support."
)


def _enrich_conv_with_workflow(conversation: Conversation, conversation_id: str) -> Conversation:
    """Populate workflow assistant details for workflow-based conversations.

    Args:
        conversation: Conversation entity to enrich with assistant metadata.
        conversation_id: Conversation identifier.

    Returns:
        The same conversation instance with `assistant_data` populated from workflow
        config, or fallback data when workflow config is missing.
    """
    try:
        workflow = WorkflowConfig.get_by_id(conversation.initial_assistant_id)
        conversation.assistant_data = [
            AssistantDetails(
                assistant_id=workflow.id,
                assistant_name=workflow.name,
                assistant_icon=workflow.icon_url,
                assistant_type=None,
                context=None,
                tools=None,
                conversation_starters=[],
            )
        ]
    except KeyError:
        logger.warning(
            f"Workflow {conversation.initial_assistant_id} not found for conversation {conversation_id}. "
            f"This is expected if the workflow was deleted. Using fallback data from conversation history."
        )
        conversation.assistant_data = [
            AssistantDetails(
                assistant_id=conversation.initial_assistant_id,
                conversation_starters=[],
            )
        ]
    return conversation


def _is_pagination_required(page: int | None, per_page: int | None, sort_order: SortOrder | None) -> bool:
    return page is not None or per_page is not None or sort_order is not None


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
)
def get_conversation_by_id(
    conversation_id: str,
    user: User = Depends(authenticate),
    page: Optional[int] = Query(None, ge=0),
    per_page: Optional[int] = Query(None, ge=1, le=MAX_HISTORY_ITEMS_PER_PAGE),
    sort_order: Optional[SortOrder] = Query(None),
) -> ConversationResponse:
    """
    Get a conversation document by provided conversation id.
    Handles both assistant conversations and workflow conversations.

    Optional pagination/sorting parameters:
    - page: 0-based page index. Default 0 when per_page is provided.
    - per_page: Items per history page. Default 20 when page is provided.
    - sort_order: 'asc' or 'desc' by message date. Omit to keep insertion order.

    When no new params are provided, response is identical to before (backward compatible).
    """
    very_first_msg_at: Optional[datetime] = None
    very_last_msg_at: Optional[datetime] = None

    if _is_pagination_required(page, per_page, sort_order):
        page_val = DEFAULT_PAGE if page is None else page
        per_page_val = DEFAULT_HISTORY_ITEMS_PER_PAGE if per_page is None else per_page
        conversation, total, very_first_msg_at, very_last_msg_at = ConversationService.get_conversation_history_slice(
            conversation_id=conversation_id,
            page=page_val,
            per_page=per_page_val,
            sort_order=sort_order,
        )
        offset = page_val * per_page_val
        pages = (total + per_page_val - 1) // per_page_val if per_page_val > 0 else 0
        pagination_data: ConversationHistoryPaginationData | None = ConversationHistoryPaginationData(
            page=page_val,
            per_page=per_page_val,
            total=total,
            pages=pages,
            has_next=offset + per_page_val < total,
            has_previous=page_val > 0,
        )
    else:
        pagination_data = None
        if conversation := Conversation.find_by_id(conversation_id):
            very_first_msg_at, very_last_msg_at = get_timestamp_bounds(conversation.history)

    if not conversation:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=CONVERSATION_NOT_FOUND_MESSAGE,
            details=f"The conversation with ID [{conversation_id}] could not be found in the system.",
            help=CONVERSATION_NOT_FOUND_HELP,
        )

    if not Ability(user).can(Action.READ, conversation):
        raise_access_denied("view")

    if conversation.is_workflow_conversation:
        conversation = _enrich_conv_with_workflow(conversation, conversation_id)
    else:
        assistants = Assistant.get_by_ids(ids=conversation.assistant_ids, user=user)
        conversation.assistant_data = [
            AssistantDetails(
                assistant_id=a.id,
                assistant_type=a.type,
                assistant_name=a.name,
                assistant_icon=a.icon_url,
                context=a.context,
                conversation_starters=a.conversation_starters,
                tools=[Tool(name=tool.name, label=tool.label) for toolkit in a.toolkits for tool in toolkit.tools],
            )
            for a in assistants
        ]

    conversation.conversation_name = conversation.get_conversation_name()

    response = ConversationResponse.model_validate(conversation)

    response.pagination = pagination_data
    response.very_first_msg_at = very_first_msg_at
    response.very_last_msg_at = very_last_msg_at

    return response


@router.get(
    "/conversations/{conversation_id}/files",
    response_model=List[str],
)
def get_conversation_files(conversation_id: str, user: User = Depends(authenticate)) -> list[str]:
    """
    Get a list of files attached to the conversation.
    """
    conversation = Conversation.get_by_id(conversation_id)

    if not Ability(user).can(Action.READ, conversation):
        raise_access_denied("view")

    if not conversation:
        logger.info(f"Conversation with given id {conversation_id} is not found")
        return []

    files = []
    if conversation.history:
        files = set().union(*(message.file_names or [] for message in conversation.history if message.file_names))

    return files


@router.delete(
    "/conversations",
    response_model=BaseResponse,
)
def delete_conversation_by_user(user: User = Depends(authenticate)) -> BaseResponse:
    """
    Remove all user conversations
    """
    Conversation.delete_by_user(user.id)
    ConversationFolder.delete_by_user(user.id)
    SharedConversation.delete_by_user_who_shared(user.id)

    return BaseResponse(message="Conversation history cleared")


@router.delete(
    "/conversations/{conversation_id}",
    response_model=BaseResponse,
)
def delete_conversation_by_id(conversation_id: str, user: User = Depends(authenticate)) -> BaseResponse:
    """
    Remove a conversation document by provided conversation id.
    Handles both assistant conversations and workflow conversations.

    Note: Workflow executions are NOT deleted when conversation is deleted - they remain
    as historical records, similar to how assistants are not deleted when their conversations are deleted.
    """
    conversation = Conversation.get_by_id(conversation_id)

    if not Ability(user).can(Action.DELETE, conversation):
        raise_access_denied("delete")

    if not conversation:
        logger.info(f"Conversation with given id {conversation_id} is not found")

    # Delete the conversation (workflow executions remain as historical records)
    Conversation.delete_by_id(conversation_id)
    SharedConversation.delete_by_conversation(conversation_id)

    return BaseResponse(message="Specified conversation removed")


@router.delete(
    "/conversations/{conversation_id}/history/{history_index}",
    response_model=Conversation,
)
def delete_conversation_history_by_index(
    conversation_id: str, history_index: int, user: User = Depends(authenticate)
) -> Conversation:
    """
    Remove a conversation history item by provided history index
    """
    conversation = Conversation.get_by_id(conversation_id)

    if not Ability(user).can(Action.WRITE, conversation):
        raise_access_denied("write")

    if not conversation:
        logger.info(f"Conversation with given id {conversation_id} is not found")

    updated_conversation = ConversationService.remove_conversation_history_index(conversation, history_index)

    return updated_conversation


@router.delete(
    "/conversations/{conversation_id}/history",
    response_model=Conversation,
)
def delete_conversation_history(conversation_id: str, user: User = Depends(authenticate)) -> Conversation:
    """
    Clear conversation history
    """
    conversation = Conversation.get_by_id(conversation_id)

    if not Ability(user).can(Action.WRITE, conversation):
        raise_access_denied("write")

    if not conversation:
        logger.info(f"Conversation with given id {conversation_id} is not found")

    updated_conversation = ConversationService.clear_conversation_history(conversation)

    return updated_conversation


@router.put(
    "/conversations/{conversation_id}/history/{history_index}",
    response_model=Conversation,
)
def update_conversation_history_by_index(
    conversation_id: str, history_index: int, request: UpdateAiMessageRequest, user: User = Depends(authenticate)
) -> Conversation:
    """
    Update a conversation history ai message item by provided history index
    """
    conversation = Conversation.get_by_id(conversation_id)

    if not Ability(user).can(Action.WRITE, conversation):
        raise_access_denied("write")

    if not conversation:
        logger.info(f"Conversation with given id {conversation_id} is not found")

    updated_conversation = ConversationService.update_conversation_ai_message(conversation, history_index, request)

    return updated_conversation


@router.put(
    "/conversations/{conversation_id}/history",
    response_model=UpsertHistoryResponse,
    responses={
        200: {"description": "Conversation updated with new messages"},
        201: {"description": "Conversation created with history"},
    },
)
def upsert_conversation_history(
    conversation_id: str,
    request: UpsertHistoryRequest,
    response: Response,
    user: User = Depends(authenticate),
) -> UpsertHistoryResponse:
    """
    Create or update conversation with history (idempotent upsert).

    Behavior:
    - If conversation doesn't exist: Create with custom ID and provided history
    - If conversation exists: Append only NEW messages (not already present)
    - Uses timestamp comparison to detect new messages

    This enables incremental updates for bulk history imports from any client.

    Returns:
        UpsertHistoryResponse with metadata:
        - conversation_id: str
        - new_messages: int (number of messages added)
        - total_messages: int (total messages in conversation)
        - created: bool (whether conversation was newly created)
    """
    # Validate request
    if not request.history:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="History cannot be empty",
            details="The request must contain at least one message in the history.",
            help="Please provide a valid history array with at least one message.",
        )

    # Authorization check for existing conversations
    existing_conversation = Conversation.find_by_id(conversation_id)
    if existing_conversation and not Ability(user).can(Action.WRITE, existing_conversation):
        raise_access_denied("write")

    # Delegate to service layer for business logic
    logger.info(
        f"Upserting conversation history for conversation_id={conversation_id}, user_id={user.id}, "
        f"messages_in_request={len(request.history)}"
    )

    result = ConversationService.upsert_conversation_with_history(
        conversation_id=conversation_id, request=request, user=user
    )

    # Set appropriate status code based on whether conversation was created
    response.status_code = status.HTTP_201_CREATED if result["created"] else status.HTTP_200_OK

    return UpsertHistoryResponse(**result)


@router.get("/conversations/{conversation_id}/history/{history_index}/{message_index}/export")
def export_conversation_message(
    conversation_id: str,
    history_index: int,
    message_index: int,
    export_format: str,
    user: User = Depends(authenticate),
) -> StreamingResponse:
    """
    Export a specific assistant message from the conversation history as PDF or DOCX file.
    """
    conversation = Conversation.get_by_id(conversation_id)

    if not conversation:
        logger.info(f"Conversation with given id {conversation_id} is not found")
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Conversation not found",
            details=f"The conversation with ID [{conversation_id}] could not be found in the system.",
        )

    if not Ability(user).can(Action.WRITE, conversation):
        raise_access_denied("write")

    try:
        service = MessageExporter(
            conversation=conversation,
            export_format=export_format,
            history_index=history_index,
            message_index=message_index,
        )
        return StreamingResponse(
            service.export_single_message(),
            media_type=service.content_type,
            headers={"Content-Disposition": f"attachment; filename={service.filename}"},
        )
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Error occured while exporting the message",
            details=f"An error occurred while trying to export the message: {str(e) or e.__class__.__name__}",
        ) from e


@router.put(
    "/conversations/{conversation_id}",
    response_model=Conversation,
)
def update_conversation(
    conversation_id: str,
    request: UpdateConversationRequest,
    user: User = Depends(authenticate),
) -> Conversation:
    """
    Create a conversation document
    """
    conversation = Conversation.get_by_id(conversation_id)

    if not Ability(user).can(Action.WRITE, conversation):
        raise_access_denied("write")

    conversation = ConversationService.update_conversation(
        request=request,
        conversation=conversation,
    )

    if not conversation:
        logger.info("Conversation could not be updated")

    return conversation


@router.get(
    "/conversations",
    response_model=list[ConversationListItem],
)
def get_conversation_list(
    user: User = Depends(authenticate),
    page: Optional[int] = Query(None, ge=DEFAULT_PAGE),
    per_page: Optional[int] = Query(None, ge=1, le=MAX_CONVERSATIONS_PER_PAGE),
) -> list[ConversationListItem]:
    """
    Get a list of all user conversations (both assistant chats and workflow conversations).

    Optional pagination parameters:
    - page: Page number (0-based). If not provided, returns all conversations.
    - per_page: Number of items per page. Default 20 when page is provided.
    """
    if page is None and per_page is None:
        conversations: list[ConversationListItem] = Conversation.get_user_conversations(user_id=user.id)
        return conversations

    # Use defaults if only one param is provided
    page_val: int = DEFAULT_PAGE if page is None else page
    per_page_val: int = DEFAULT_CONVERSATIONS_PER_PAGE if per_page is None else per_page

    return ConversationService.get_user_conversations_paginated(
        user_id=user.id,
        page=page_val,
        per_page=per_page_val,
    )


@router.post(
    "/conversations",
    response_model=Conversation,
)
def create_conversation(
    request: CreateConversationRequest,
    user: User = Depends(authenticate),
) -> Conversation:
    """
    Create a conversation document.
    Handles both assistant conversations and workflow conversations.
    """
    from codemie.core.workflow_models import WorkflowConfig

    conversation = ConversationService.create_conversation(
        user=user,
        initial_assistant_id=request.initial_assistant_id,
        folder=request.folder,
        mcp_server_single_usage=request.mcp_server_single_usage,
        is_workflow_conversation=request.is_workflow,
    )

    if not conversation:
        logger.info("Conversation could not be created")
        return conversation

    # Populate assistant_data based on whether this is a workflow or assistant conversation
    if request.initial_assistant_id and request.is_workflow:
        # Try to get as workflow
        workflow = WorkflowConfig.get_by_id(request.initial_assistant_id)
        if not workflow:
            raise ExtendedHTTPException(404, f"Requested workflow with id {request.initial_assistant_id} not found")
        # This is a workflow conversation
        conversation.assistant_data = [
            AssistantDetails(
                assistant_id=workflow.id,
                assistant_name=workflow.name,
                assistant_icon=workflow.icon_url,
                assistant_type=None,
                context=None,
                tools=None,
                conversation_starters=[],
            )
        ]

    return conversation


@router.get(
    "/conversations/folders/list",
    response_model=List[ConversationFolder],
)
def get_conversation_folder_list(user: User = Depends(authenticate)) -> List[BaseModelWithSQLSupport]:
    """
    Get a list if all user folders
    """
    return ConversationFolder.get_all_by_fields({"user_id.keyword": user.id})


@router.delete(
    "/conversations/folder/{folder:path}",
    response_model=BaseResponse,
)
def remove_folder(
    folder: str,
    remove_conversations: bool = False,
    user: User = Depends(authenticate),
) -> BaseResponse:
    """
    Reset folder for user's conversations
    """
    ConversationService.delete_conversation_folder(
        user=user,
        folder=folder,
        remove_conversations=remove_conversations,
    )

    return BaseResponse(message="Folder removed")


@router.put(
    "/conversations/folder/{folder:path}",
    response_model=BaseResponse,
)
def update_folder(
    folder: str,
    request: UpdateConversationFolderRequest,
    user: User = Depends(authenticate),
) -> BaseResponse:
    """
    Rename folder for user's conversations
    """
    try:
        ConversationService.update_conversation_folder(
            user=user,
            folder=folder,
            new_folder=request.folder,
        )
    except ValueError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Folder name already exists",
            details="Folder name should be unique",
        )

    return BaseResponse(message="Folder name updated")


@router.post(
    "/conversations/folder",
    response_model=BaseResponse,
)
def create_folder(
    request: UpdateConversationFolderRequest,
    user: User = Depends(authenticate),
) -> BaseResponse:
    """
    Create folder(folder) for user's conversations
    """
    try:
        ConversationFolder.create_folder(
            folder_name=request.folder,
            user_id=user.id,
        )
    except ValueError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Folder name already exists",
            details="Folder name should be unique",
            help="",
        )

    return BaseResponse(message="Folder created")


def _validate_conversation_access_and_get_assistants(
    user: User, conversation_id: str, conversation: Optional[Conversation]
) -> tuple[Conversation, Optional[Assistant], List[Assistant]]:
    """
    Validate access rights for a conversation and retrieve associated assistants.

    Args:
        user: The authenticated user requesting access.
        conversation_id: The ID of the conversation.
        conversation: The conversation object (or None if not found).

    Returns:
        tuple: A tuple containing:
            - conversation: The validated conversation object.
            - assistant: The primary assistant (currently always None).
            - assistants: A list of Assistant objects associated with the conversation.

    Raises:
        ExtendedHTTPException: If conversation is not found (404).
        ExtendedHTTPException: If user does not have read access (403).
    """
    if not conversation:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=CONVERSATION_NOT_FOUND_MESSAGE,
            details=f"The conversation with ID [{conversation_id}] could not be found in the system.",
            help=CONVERSATION_NOT_FOUND_HELP,
        )

    if not Ability(user).can(Action.READ, conversation):
        raise_access_denied("view")

    assistants = Assistant.get_by_ids(ids=conversation.assistant_ids, user=user)
    assistant = None
    for assistant in assistants:
        assistant.system_prompt_history = None

    return conversation, assistant, assistants


@router.get(
    "/conversations/{conversation_id}/export",
    status_code=status.HTTP_200_OK,
)
def export_conversation(
    conversation_id: str,
    user: User = Depends(authenticate),
    export_format: ConversationExportFormat = ConversationExportFormat.JSON,
    page: Optional[int] = Query(None, ge=DEFAULT_PAGE),
    per_page: Optional[int] = Query(None, ge=1, le=MAX_HISTORY_ITEMS_PER_PAGE),
):
    # Pagination: if params provided, use DB-level slicing
    if page is not None or per_page is not None:
        page_val: int = DEFAULT_PAGE if page is None else page
        per_page_val: int = DEFAULT_HISTORY_ITEMS_PER_PAGE if per_page is None else per_page

        conversation, _, very_first_msg_at, very_last_msg_at = ConversationService.get_conversation_history_slice(
            conversation_id=conversation_id,
            page=page_val,
            per_page=per_page_val,
        )
        conversation, assistant, assistants = _validate_conversation_access_and_get_assistants(
            user, conversation_id, conversation
        )

    # Without pagination
    else:
        conversation = Conversation.find_by_id(conversation_id)
        conversation, assistant, assistants = _validate_conversation_access_and_get_assistants(
            user, conversation_id, conversation
        )
        very_first_msg_at, very_last_msg_at = get_timestamp_bounds(conversation.history)

    return _get_streaming_response(
        conversation_id,
        export_format,
        assistants,
        conversation,
        assistant,
        very_first_msg_at=very_first_msg_at,
        very_last_msg_at=very_last_msg_at,
    )


def _get_streaming_response(
    conversation_id: str,
    export_format: ConversationExportFormat,
    assistants: List[Assistant],
    conversation: Conversation,
    assistant: Optional[Assistant],
    very_first_msg_at: Optional[datetime] = None,
    very_last_msg_at: Optional[datetime] = None,
) -> StreamingResponse:
    """
    Generate a streaming response for conversation export.

    Args:
        conversation_id: The ID of the conversation.
        export_format: The desired export format (JSON, PDF, DOCX).
        assistants: List of assistants involved in the conversation.
        conversation: The conversation object to export.
        assistant: The primary assistant (optional, used for PDF/DOCX export).
        very_first_msg_at: First message timestamp (JSON export only; aligns with GET conversation).
        very_last_msg_at: Last message timestamp (JSON export only; aligns with GET conversation).

    Returns:
        StreamingResponse: A streaming response containing the exported file.

    Raises:
        ExtendedHTTPException: If the export format is not supported (501).
    """
    file_name = f"{conversation_id}"
    if export_format == "json":
        result = {
            "assistants": assistants,
            "history": conversation.history,
            "very_first_msg_at": very_first_msg_at,
            "very_last_msg_at": very_last_msg_at,
        }
        json_result = jsonable_encoder(result)
        json_result = remove_nulls(json_result)
        json_bytes = io.BytesIO(json.dumps(json_result).encode("utf-8"))

        return StreamingResponse(
            content=json_bytes,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={file_name}.json"},
        )

    elif export_format in [ConversationExportFormat.PDF, ConversationExportFormat.DOCX]:
        service = MessageExporter(
            conversation=conversation,
            export_format=ExportFormat(export_format),
            assistant=assistant,
        )
        return StreamingResponse(
            content=service.run(),
            media_type=service.content_type,
            headers={"Content-Disposition": f"attachment; filename={file_name}.{export_format}"},
        )

    raise ExtendedHTTPException(
        code=status.HTTP_501_NOT_IMPLEMENTED,
        message=EXPORT_FORMAT_NOT_SUPPORTED_MESSAGE,
        details=EXPORT_FORMAT_NOT_SUPPORTED_DETAILS,
        help=EXPORT_FORMAT_NOT_SUPPORTED_HELP,
    )


@router.post("/speech-recognition", response_model=BaseResponse, dependencies=[Depends(authenticate)])
def recognize_speech(
    file: UploadFile,
) -> BaseResponse:
    """
    Speech to text from audio file
    """
    content = file.file.read()
    audio_file = io.BytesIO(content)
    audio_file.name = 'audio.wav'
    try:
        result = ConversationService.recognize_speech(audio_file)
    except ValueError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Couldn't recognize speech",
            details="",
            help="",
        )

    return BaseResponse(message=result)
