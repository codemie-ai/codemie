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
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, status, UploadFile, Query, Form, Request, Response
from fastapi.encoders import jsonable_encoder
from starlette.responses import StreamingResponse

from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException, UnknownSttModelError
from codemie.core.models import (
    BaseResponse,
    BaseResponseWithData,
)
from codemie.rest_api.models.assistant import Assistant
from codemie.core.workflow_models.workflow_execution import UpdateWorkflowExecutionOutputRequest
from codemie.rest_api.models.conversation import (
    Conversation,
    ConversationExportFormat,
)
from codemie.rest_api.routers.feedback import CONVERSATION_NOT_FOUND_MESSAGE, CONVERSATION_NOT_FOUND_HELP
from codemie.rest_api.routers.utils import raise_access_denied, remove_nulls
from codemie.utils.datetime_utils import get_timestamp_bounds
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.routers.conversation._common import _with_stripped_history
from codemie.rest_api.security.user import User
from codemie.service.assistant.tool_output_visibility import strip_history_for_conversation
from codemie.service.conversation import MessageExporter, ExportFormat
from codemie.service.conversation_service import ConversationService
from codemie.service.constants import (
    DEFAULT_HISTORY_ITEMS_PER_PAGE,
    MAX_HISTORY_ITEMS_PER_PAGE,
    DEFAULT_PAGE,
)


router = APIRouter()


EXPORT_FORMAT_NOT_SUPPORTED_MESSAGE = "Export format not supported"
EXPORT_FORMAT_NOT_SUPPORTED_DETAILS = "The requested export format is not supported by the system."


EXPORT_FORMAT_NOT_SUPPORTED_HELP = (
    "Please select one of the supported export formats and try again."
    + "For additional supported formats, refer to the documentation or contact support."
)


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
    include_tool_outputs: bool = False,
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
        include_tool_outputs=include_tool_outputs,
    )


def _get_streaming_response(
    conversation_id: str,
    export_format: ConversationExportFormat,
    assistants: List[Assistant],
    conversation: Conversation,
    assistant: Optional[Assistant],
    very_first_msg_at: Optional[datetime] = None,
    very_last_msg_at: Optional[datetime] = None,
    include_tool_outputs: bool = False,
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
        include_tool_outputs: Whether the PDF/DOCX export should include thought/tool-output
            content. Ignored by the JSON branch. Defaults to False (concise export).

    Returns:
        StreamingResponse: A streaming response containing the exported file.

    Raises:
        ExtendedHTTPException: If the export format is not supported (501).
    """
    file_name = f"{conversation_id}"
    if export_format == "json":
        result = {
            "assistants": assistants,
            "history": strip_history_for_conversation(conversation),
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
            conversation=_with_stripped_history(conversation),
            export_format=ExportFormat(export_format),
            assistant=assistant,
            include_tool_outputs=include_tool_outputs,
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


@router.put(
    "/conversations/{conversation_id}/messages/{history_index}/output",
    response_model=BaseResponse,
    summary="Edit the output of the last interrupted workflow state in a conversation",
)
def update_conversation_message_output(
    conversation_id: str,
    history_index: int,
    body: UpdateWorkflowExecutionOutputRequest,
    user: User = Depends(authenticate),
) -> BaseResponse:
    from codemie.core.workflow_models import WorkflowExecutionStatusEnum
    from codemie.service.workflow_execution.workflow_update_output_service import WorkflowUpdateOutputService
    from codemie.service.workflow_service import WorkflowService

    conversation = Conversation.find_by_id(conversation_id)

    if not conversation:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=CONVERSATION_NOT_FOUND_MESSAGE,
            details=f"The conversation with ID [{conversation_id}] could not be found in the system.",
            help=CONVERSATION_NOT_FOUND_HELP,
        )

    if not Ability(user).can(Action.READ, conversation):
        raise_access_denied("view")

    message = next(
        (m for m in (conversation.history or []) if m.history_index == history_index and m.workflow_execution_ref),
        None,
    )
    if not message or not message.execution_id:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Workflow execution message not found",
            details=f"No workflow execution reference at index [{history_index}] in conversation [{conversation_id}].",
            help="Ensure the history index points to a workflow execution message.",
        )

    execution = WorkflowService.find_workflow_execution_by_id(message.execution_id)
    if not execution:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Workflow execution not found",
            details=f"Workflow execution [{message.execution_id}] could not be found.",
            help="The execution may have been deleted.",
        )

    if execution.overall_status != WorkflowExecutionStatusEnum.INTERRUPTED:
        raise ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message="Workflow execution is not interrupted",
            details=f"Execution [{message.execution_id}] has status [{execution.overall_status}] and cannot be edited.",
            help="Only executions with status INTERRUPTED support output editing.",
        )

    WorkflowUpdateOutputService.run(
        execution_id=message.execution_id,
        state_id=body.state_id,
        new_output=body.output,
    )

    return BaseResponse(message="Conversation message output updated successfully")


_SUPPORTED_AUDIO_EXTENSIONS = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"}
_AUDIO_EXTENSION_BY_CONTENT_TYPE = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/flac": ".flac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def _resolve_audio_filename(filename: str | None, content_type: str | None) -> str:
    """Name the upload so the STT backend detects the real audio format; default to wav."""
    if filename and os.path.splitext(filename)[1].lower() in _SUPPORTED_AUDIO_EXTENSIONS:
        return filename
    mime = (content_type or "").split(";")[0].strip().lower()
    return f"audio{_AUDIO_EXTENSION_BY_CONTENT_TYPE.get(mime, '.wav')}"


@router.post("/speech-recognition", response_model=BaseResponseWithData)
def recognize_speech(
    request: Request,
    response: Response,
    file: UploadFile,
    model: str | None = Form(default=None),
    language: str | None = Form(default=None),
    conversation_id: str | None = Form(default=None),
    user: User = Depends(authenticate),
) -> BaseResponse:
    """
    Speech to text from audio file.

    `model` is the base_name of an STT model from GET /v1/audio_transcription_models (default: the first allowed model).
    `language` is an optional ISO-639-1 hint (default: the `default_language` of the model's `stt_config`, if any).
    `conversation_id` is optional; it groups the STT trace with the other traces of that conversation.

    The resolved model's `stt_config` (in the LLM config) controls which request
    headers reach the provider, and which provider response headers/fields come back as
    response headers and in `data`.
    """
    content = file.file.read()
    audio_file = io.BytesIO(content)
    audio_file.name = _resolve_audio_filename(file.filename, file.content_type)
    try:
        result = ConversationService.recognize_speech(
            audio_file,
            user=user,
            model=model,
            language=language,
            forward_headers=request.headers,
            conversation_id=conversation_id,
        )
    except UnknownSttModelError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Couldn't recognize speech",
            details=str(e),
            help="Pick a model from GET /v1/audio_transcription_models.",
        )

    for name, value in result.headers.items():
        response.headers[name] = value
    return BaseResponseWithData(message=result.text, data=result.metadata or None)
