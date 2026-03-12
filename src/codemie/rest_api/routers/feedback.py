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
from typing import List
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.conversation_service import ConversationService
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.models.feedback import FeedbackEntry, FeedbackRequest, FeedbackDeleteRequest
from codemie.rest_api.models.conversation import Conversation, UserMark
from codemie.rest_api.models.standard import PostResponse, FinalFeedbackRequest
from codemie.rest_api.security.user import User
from codemie.core.models import BaseResponse
from codemie.service.monitoring.conversation_monitoring_service import ConversationMonitoringService

CONVERSATION_NOT_FOUND_MESSAGE = "Conversation not found"
CONVERSATION_NOT_FOUND_HELP = (
    "Please verify the conversation ID and try again. If you believe this is an error, contact support."
)

router = APIRouter(
    tags=["Feedback"],
    prefix="/v1",
    dependencies=[Depends(authenticate)],
)


@router.get("/feedback", response_model=List[FeedbackEntry])
async def index() -> List[FeedbackEntry]:
    """
    Get all feedback documents
    """
    return FeedbackEntry.get_all()


@router.get("/feedback/{document_id}", response_model=FeedbackEntry)
async def show(document_id: str) -> FeedbackEntry:
    """
    Get a feedback document by id
    """
    return FeedbackEntry.get_by_id(document_id)


@router.post("/feedback", response_model=PostResponse, response_model_exclude_none=True)
async def create(request: FeedbackRequest, user: User = Depends(authenticate)) -> PostResponse:
    """
    Create a new feedback document
    """
    conversation = Conversation.get_by_id(request.conversation_id)

    if not conversation:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=CONVERSATION_NOT_FOUND_MESSAGE,
            details=f"The conversation with ID [{request.conversation_id}] could not be found in the system.",
            help=CONVERSATION_NOT_FOUND_HELP,
        )

    feedback = FeedbackEntry(
        request=request.request,
        response=request.response,
        date=datetime.now(),
        conversationId=request.conversation_id,
        mark=request.mark,
        comments=request.comments,
        history=request.history,
        appName=request.appName,
        repoName=request.repoName,
        indexType=request.indexType,
    )
    stored_feedback = feedback.save()
    request.feedback_id = stored_feedback.id_
    ConversationService.add_feedback(request, user=user)
    return stored_feedback


@router.delete("/feedback", response_model=BaseResponse, response_model_exclude_none=True)
async def delete(request: FeedbackDeleteRequest, user: User = Depends(authenticate)) -> BaseResponse:
    """
    Delete a feedback document
    """
    conversation = Conversation.get_by_id(request.conversation_id)
    if not conversation:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=CONVERSATION_NOT_FOUND_MESSAGE,
            details=f"The conversation with ID [{request.conversation_id}] could not be found in the system.",
            help=CONVERSATION_NOT_FOUND_HELP,
        )

    try:
        ConversationService.remove_feedback(request, user=user)
    except KeyError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=CONVERSATION_NOT_FOUND_MESSAGE,
            details=f"The conversation with ID [{request.conversation_id}] could not be found in the system.",
            help=CONVERSATION_NOT_FOUND_HELP,
        ) from e
    FeedbackEntry.delete_feedback(id=request.feedback_id)
    return BaseResponse(message="Specified feedback entry removed")


@router.post("/conversations/{conversation_id}/feedback")
async def final_feedback(
    conversation_id: str,
    request: FinalFeedbackRequest,
    user: User = Depends(authenticate),
) -> JSONResponse:
    """
    Update chat log with final chat feedback
    """
    chat = Conversation.get_by_fields({"user_id": user.id, "conversation_id": conversation_id})

    if not chat:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=CONVERSATION_NOT_FOUND_MESSAGE,
            details=f"The conversation with ID [{conversation_id}] could not be found in the system.",
            help=CONVERSATION_NOT_FOUND_HELP,
        )

    mark = UserMark(**request.dict())
    mark.date = datetime.now()

    chat.final_user_mark = mark
    chat.update()

    # Send metric for final feedback
    ConversationMonitoringService.send_final_feedback_metric(conversation_id, request, user)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"user_id": user.id, "conversation_id": conversation_id},
    )
