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

import queue

from fastapi import APIRouter, Depends, status
from starlette.responses import StreamingResponse

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    BaseResponse,
)
from codemie.rest_api.models.conversation import (
    Conversation,
)
from codemie.rest_api.routers.conversation._common import CONVERSATION_NOT_FOUND_MSG
from codemie.rest_api.routers.utils import raise_access_denied, NDJSON_MEDIA_TYPE
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.generation_manager import GenerationManager


router = APIRouter()


CONVERSATION_ABORTED_SUCCESS_MSG = "Conversation generation aborted successfully"
LIVE_STREAM_QUEUE_TIMEOUT_SECONDS = 60.0


@router.post(
    "/conversations/{conversation_id}/abort",
    response_model=BaseResponse,
)
def abort_conversation_generation(conversation_id: str, user: User = Depends(authenticate)) -> BaseResponse:
    """
    Abort conversation generation by ID.
    """
    conversation = Conversation.find_by_id(conversation_id)
    if not conversation:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=CONVERSATION_NOT_FOUND_MSG,
            details=f"Conversation {conversation_id} was not found.",
        )

    if not Ability(user).can(Action.WRITE, conversation):
        raise_access_denied("edit")

    from codemie.service.generation_manager import GenerationManager

    aborted = GenerationManager().abort(conversation_id)
    logger.info(f"Explicitly aborted conversation generation for id={conversation_id}, aborted={aborted}")

    try:
        from codemie.rest_api.models.base import ConversationStatus

        if conversation.history:
            last_msg = conversation.history[-1]
            if last_msg.in_progress:
                last_msg.in_progress = False
                last_msg.status = ConversationStatus.INTERRUPTED.value
                conversation.update()
                logger.info(f"Marked conversation {conversation_id} last message as INTERRUPTED in DB.")
    except Exception as update_exc:
        logger.error(f"Error marking conversation as aborted in DB: {str(update_exc)}")

    return BaseResponse(message=CONVERSATION_ABORTED_SUCCESS_MSG)


def empty_stream():
    yield from ()


def _format_chunk(chunk: str) -> str:
    return chunk if chunk.endswith("\n") else f"{chunk}\n"


def _drain_live_stream(gen, sub_queue, conversation_id):
    while True:
        try:
            item = sub_queue.get(timeout=LIVE_STREAM_QUEUE_TIMEOUT_SECONDS)
            if item is StopIteration:
                break
            if isinstance(item, BaseException):
                logger.error(f"Stream error for conversation {conversation_id}: {item}")
                break
            yield _format_chunk(item)
        except queue.Empty:
            if gen.is_closed():
                break


def stream_generator(gen, history_snapshot, sub_queue, is_closed, conversation_id):
    try:
        for chunk in history_snapshot:
            yield _format_chunk(chunk)

        if not is_closed:
            yield from _drain_live_stream(gen, sub_queue, conversation_id)
    finally:
        gen.unsubscribe(sub_queue)


@router.get(
    "/conversations/{conversation_id}/stream",
    response_class=StreamingResponse,
)
def get_conversation_stream(
    conversation_id: str,
    user: User = Depends(authenticate),
) -> StreamingResponse:
    """
    Subscribe to the live streaming response of an ongoing generation for a conversation.
    Replays all chunks generated so far, then yields live chunks as they arrive.
    """
    conversation = Conversation.find_by_id(conversation_id)
    if not conversation:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=CONVERSATION_NOT_FOUND_MSG,
            details=f"Conversation {conversation_id} was not found.",
        )

    if not Ability(user).can(Action.READ, conversation):
        raise_access_denied("view")

    gen = GenerationManager().get_generator(conversation_id)
    if not gen:
        return StreamingResponse(empty_stream(), media_type=NDJSON_MEDIA_TYPE)

    history_snapshot, sub_queue, is_closed = gen.subscribe()
    return StreamingResponse(
        stream_generator(gen, history_snapshot, sub_queue, is_closed, conversation_id),
        media_type=NDJSON_MEDIA_TYPE,
    )
