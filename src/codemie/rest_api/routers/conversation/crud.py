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

from fastapi import APIRouter, Depends, Query

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    CreateConversationRequest,
    UpdateConversationRequest,
)
from codemie.rest_api.models.conversation import (
    Conversation,
    AssistantDetails,
    ConversationListItem,
)
from codemie.rest_api.routers.utils import raise_access_denied
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.conversation_service import ConversationService
from codemie.service.constants import (
    DEFAULT_CONVERSATIONS_PER_PAGE,
    MAX_CONVERSATIONS_PER_PAGE,
    DEFAULT_PAGE,
)


router = APIRouter()


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
    is_finished: Optional[bool] = Query(None, alias="isFinished"),
) -> list[ConversationListItem]:
    """
    Get a list of all user conversations (both assistant chats and workflow conversations).

    Optional pagination parameters:
    - page: Page number (0-based). If not provided, returns all conversations.
    - per_page: Number of items per page. Default 20 when page is provided.

    Optional isFinished filter narrows the list to finished (true) or unfinished (false)
    conversations only.
    """
    if page is None and per_page is None:
        filters = {"is_finished": is_finished} if is_finished is not None else None
        conversations: list[ConversationListItem] = Conversation.get_user_conversations(
            user_id=user.id, filters=filters
        )
        return conversations

    # Use defaults if only one param is provided
    page_val: int = DEFAULT_PAGE if page is None else page
    per_page_val: int = DEFAULT_CONVERSATIONS_PER_PAGE if per_page is None else per_page

    return ConversationService.get_user_conversations_paginated(
        user_id=user.id,
        page=page_val,
        per_page=per_page_val,
        is_finished=is_finished,
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
