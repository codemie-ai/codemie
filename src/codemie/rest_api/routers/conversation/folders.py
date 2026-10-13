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

from typing import List

from fastapi import APIRouter, Depends, status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    BaseResponse,
    UpdateConversationFolderRequest,
    MoveConversationsToFolderRequest,
)
from codemie.rest_api.models.base import BaseModelWithSQLSupport
from codemie.rest_api.models.conversation_folder import ConversationFolder
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.conversation_service import ConversationService


router = APIRouter()


@router.get(
    "/conversations/folders/list",
    response_model=List[ConversationFolder],
)
def get_conversation_folder_list(user: User = Depends(authenticate)) -> List[BaseModelWithSQLSupport]:
    """
    Get a list if all user folders
    """
    return ConversationFolder.get_all_by_fields({"user_id.keyword": user.id})


@router.put(
    "/conversations/folders/move",
    response_model=BaseResponse,
)
def move_conversations_to_folder(
    request: MoveConversationsToFolderRequest,
    user: User = Depends(authenticate),
) -> BaseResponse:
    """Atomically move existing conversations into one user-owned Custom Folder."""
    try:
        moved_count = ConversationService.move_conversations_to_folder(
            user=user,
            conversation_ids=request.conversation_ids,
            target_folder=request.target_folder,
        )
    except ValueError as error:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=str(error),
        )

    return BaseResponse(message=f"Moved {moved_count} conversations")


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
