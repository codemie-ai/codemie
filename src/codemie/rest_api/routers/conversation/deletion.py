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

from fastapi import APIRouter, Depends

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.models import (
    BaseResponse,
)
from codemie.rest_api.models.conversation import (
    Conversation,
)
from codemie.rest_api.models.conversation_folder import ConversationFolder
from codemie.rest_api.models.assistant_folder import (
    AssistantFolderDeleteResponse,
    AssistantFolderListItem,
)
from codemie.rest_api.models.share.shared_conversation import SharedConversation
from codemie.rest_api.routers.utils import raise_access_denied
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.conversation_service import ConversationService


router = APIRouter()


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


@router.get(
    "/assistant-folders",
    response_model=list[AssistantFolderListItem],
)
def get_assistant_folders(user: User = Depends(authenticate)) -> list[AssistantFolderListItem]:
    return ConversationService.get_assistant_folders(user)


@router.delete(
    "/assistant-folders/{assistant_id}",
    response_model=AssistantFolderDeleteResponse,
)
def delete_assistant_folder(
    assistant_id: str,
    remove_conversations: bool = False,
    user: User = Depends(authenticate),
) -> AssistantFolderDeleteResponse:
    return ConversationService.delete_assistant_folder(
        user=user,
        assistant_id=assistant_id,
        remove_conversations=remove_conversations,
    )


@router.delete(
    "/conversations/{conversation_id}",
    response_model=BaseResponse,
)
def delete_conversation_by_id(conversation_id: str, user: User = Depends(authenticate)) -> BaseResponse:
    """
    Remove a conversation document by provided conversation id.
    Handles both assistant conversations and workflow conversations.

    Workflow executions linked to this conversation are cascade-deleted in the
    same transaction so they no longer appear in Workflow Execution History.
    """
    conversation = Conversation.get_by_id(conversation_id)

    if not Ability(user).can(Action.DELETE, conversation):
        raise_access_denied("delete")

    if not conversation:
        logger.info(f"Conversation with given id {conversation_id} is not found")

    # Cascade-delete linked workflow executions, then the conversation itself.
    Conversation.delete_by_id(conversation_id)
    SharedConversation.delete_by_conversation(conversation_id)

    return BaseResponse(message="Specified conversation removed")
