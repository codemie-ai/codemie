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

from typing import Any, Dict

from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.routers.feedback import CONVERSATION_NOT_FOUND_HELP, CONVERSATION_NOT_FOUND_MESSAGE
from codemie.rest_api.routers.utils import raise_access_denied
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.share_conversation_service import ShareConversationService
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel


class ShareConversationRequest(BaseModel):
    chat_id: str


router = APIRouter(
    tags=["Share"],
    prefix="/v1",
)


@router.post("/share/conversations", status_code=status.HTTP_201_CREATED, dependencies=[Depends(authenticate)])
async def create_shared_conversation(
    request: ShareConversationRequest, user: User = Depends(authenticate)
) -> Dict[str, Any]:
    """
    Create a shareable link for a conversation.
    """
    conversation = Conversation.find_by_id(request.chat_id)

    if not conversation:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=CONVERSATION_NOT_FOUND_MESSAGE,
            details=f"The conversation with ID [{request.chat_id}] could not be found in the system.",
            help=CONVERSATION_NOT_FOUND_HELP,
        )

    if not Ability(user).can(Action.READ, conversation):
        raise_access_denied("share")

    try:
        return ShareConversationService.share_conversation(conversation, user)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/share/conversations/{token}", status_code=status.HTTP_200_OK, dependencies=[Depends(authenticate)])
async def access_shared_conversation(token: str, user: User = Depends(authenticate)) -> Dict[str, Any]:
    """
    Access a shared conversation using the share token.
    """
    shared_data = ShareConversationService.get_shared_conversation(token, user)

    if not shared_data:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Shared conversation not found",
            details="The shared conversation you're trying to access doesn't exist or has been removed.",
            help="Please check the URL and try again.",
        )

    # Return the conversation data
    return shared_data
