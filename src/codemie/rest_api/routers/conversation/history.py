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
from typing import Optional

from fastapi import APIRouter, Depends, status, Response, Query, Request
from starlette.responses import StreamingResponse

from codemie.configs import config, logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    UpdateAiMessageRequest,
)
from codemie.rest_api.models.base import PaginatedListResponse, PaginationData
from codemie.rest_api.models.conversation import (
    Conversation,
    ConversationListItem,
    ConversationFinishBulkRequest,
    ConversationFinishBulkResponse,
    ConversationFinishResponse,
    UpsertHistoryRequest,
    UpsertHistoryResponse,
)
from codemie.rest_api.routers.conversation._common import CONVERSATION_NOT_FOUND_MSG, _with_stripped_history
from codemie.rest_api.routers.feedback import CONVERSATION_NOT_FOUND_MESSAGE, CONVERSATION_NOT_FOUND_HELP
from codemie.rest_api.routers.utils import raise_access_denied, raise_forbidden
from codemie.rest_api.security.authentication import admin_access_only, authenticate
from codemie.rest_api.security.user import User
from codemie.service.conversation import MessageExporter
from codemie.service.conversation_service import ConversationService
from codemie.service.constants import (
    DEFAULT_CONVERSATIONS_PER_PAGE,
    MAX_CONVERSATIONS_PER_PAGE,
    DEFAULT_PAGE,
)
from codemie.core.constants import HEADER_CODEMIE_CLI, HEADER_CODEMIE_CLIENT


router = APIRouter()


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

    return _with_stripped_history(updated_conversation)


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

    return _with_stripped_history(updated_conversation)


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

    return _with_stripped_history(updated_conversation)


@router.post(
    "/conversations/{conversation_id}/finish",
    response_model=ConversationFinishResponse,
)
def finish_conversation(conversation_id: str, user: User = Depends(authenticate)) -> ConversationFinishResponse:
    """
    Mark a conversation as finished (owner-only).

    Already-finished conversations return HTTP 409. Further message-append/edit
    operations on a finished conversation also return HTTP 409.
    """
    conversation = Conversation.find_by_id(conversation_id)
    if not conversation:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=CONVERSATION_NOT_FOUND_MESSAGE,
            details=f"The conversation with ID [{conversation_id}] could not be found in the system.",
            help=CONVERSATION_NOT_FOUND_HELP,
        )
    if not Ability(user).can(Action.WRITE, conversation):
        raise_forbidden("write")
    return ConversationService.finish_conversation(conversation_id)


@router.post(
    "/admin/conversations/finish-bulk",
    response_model=ConversationFinishBulkResponse,
    dependencies=[Depends(admin_access_only)],
)
def admin_finish_conversations_bulk(payload: ConversationFinishBulkRequest) -> ConversationFinishBulkResponse:
    """Finish multiple conversations in one request, with per-ID success/error reporting."""
    results = ConversationService.finish_conversations_bulk(payload.conversation_ids)
    return ConversationFinishBulkResponse(total=len(results), results=results)


@router.get(
    "/admin/conversations",
    response_model=PaginatedListResponse[ConversationListItem],
    dependencies=[Depends(admin_access_only)],
)
def list_admin_conversations(
    is_finished: Optional[bool] = Query(None, alias="isFinished"),
    started_after: Optional[datetime] = Query(None, alias="startedAt"),
    project: Optional[str] = Query(None),
    page: int = Query(DEFAULT_PAGE, ge=0),
    per_page: int = Query(DEFAULT_CONVERSATIONS_PER_PAGE, ge=1, le=MAX_CONVERSATIONS_PER_PAGE, alias="perPage"),
) -> PaginatedListResponse[ConversationListItem]:
    """
    Paginated, cross-user conversation list for admin/scheduler use. Filter by
    isFinished and optional project so callers do not paginate the full table
    (EPMCDME-11067).
    """
    items, total = Conversation.get_all_conversations_admin(
        is_finished=is_finished,
        started_after=started_after,
        project=project,
        page=page,
        per_page=per_page,
    )
    pages = (total + per_page - 1) // per_page if per_page > 0 else 0
    return PaginatedListResponse(
        data=items,
        pagination=PaginationData(page=page, per_page=per_page, total=total, pages=pages),
    )


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
    http_request: Request,
    user: User = Depends(authenticate),
) -> UpsertHistoryResponse:
    """
    Create or update conversation with history (idempotent upsert).

    Behavior:
    - If conversation doesn't exist: Create with custom ID and provided history
    - If conversation exists: Append only NEW messages (not already present)
    - Uses timestamp comparison to detect new messages

    This enables incremental updates for bulk history imports from any client.

    While CONVERSATION_HISTORY_UPSERT_NOOP_ENABLED is on (off by default), the request is
    acknowledged with 200 and nothing is read or written.

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

    if config.CONVERSATION_HISTORY_UPSERT_NOOP_ENABLED:
        response.status_code = status.HTTP_200_OK
        return UpsertHistoryResponse(
            conversation_id=conversation_id,
            new_messages=0,
            total_messages=len(request.history),
            created=False,
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

    import_source = ConversationService.resolve_chat_import_source(
        http_request.headers.get(HEADER_CODEMIE_CLIENT),
        http_request.headers.get(HEADER_CODEMIE_CLI),
    )
    result = ConversationService.upsert_conversation_with_history(
        conversation_id=conversation_id, request=request, user=user, import_source=import_source
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
            message=CONVERSATION_NOT_FOUND_MSG,
            details=f"The conversation with ID [{conversation_id}] could not be found in the system.",
        )

    if not Ability(user).can(Action.WRITE, conversation):
        raise_access_denied("write")

    try:
        service = MessageExporter(
            conversation=_with_stripped_history(conversation),
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
