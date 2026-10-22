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

import asyncio
import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import BaseModelResponse
from codemie.rest_api.a2a.client.remote_agent_connection import RemoteAgentConnections
from codemie.rest_api.a2a.types import (
    A2AClientHTTPError,
    A2AProtocolVersionError,
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Artifact,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from codemie.rest_api.handlers.assistant_handlers import A2AAssistantHandler
from codemie.rest_api.models.assistant import AssistantChatRequest


def _card(streaming: bool = False) -> AgentCard:
    return AgentCard(
        name="Remote",
        description="A remote agent",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=streaming),
        skills=[],
        supportedInterfaces=[AgentInterface(url="https://remote.example.com/rpc", protocolBinding="JSONRPC")],
    )


def _handler(streaming: bool = False) -> A2AAssistantHandler:
    assistant = MagicMock()
    assistant.agent_card = _card(streaming)
    user = MagicMock()
    user.id = "user-1"
    with patch("codemie.rest_api.handlers.assistant_handlers.RemoteAgentConnections"):
        return A2AAssistantHandler(assistant=assistant, user=user, request_uuid="u")


def test_process_request_does_not_need_card_url():
    handler = _handler()
    sentinel = object()
    with (
        patch.object(handler, "_guard_conversation_not_finished"),
        patch.object(handler, "_sync_uploaded_files_to_workspace"),
        patch.object(handler, "_validate_a2ui_action"),
        patch.object(handler, "_populate_conversation_history"),
        patch("codemie.rest_api.handlers.assistant_handlers.convert_to_task_request", return_value=MagicMock()),
        patch.object(handler, "_handle_a2a_sync", return_value=sentinel),
    ):
        result = handler.process_request(AssistantChatRequest(text="hi"), MagicMock(), MagicMock())
    assert result is sentinel


def test_process_request_without_jsonrpc_interface_raises_400():
    assistant = MagicMock()
    card = _card()
    card.supportedInterfaces = [AgentInterface(url="https://remote.example.com/grpc", protocolBinding="GRPC")]
    assistant.agent_card = card
    with patch("codemie.rest_api.handlers.assistant_handlers.RemoteAgentConnections"):
        handler = A2AAssistantHandler(assistant=assistant, user=MagicMock(), request_uuid="u")
    with (
        patch.object(handler, "_guard_conversation_not_finished"),
        patch.object(handler, "_sync_uploaded_files_to_workspace"),
        patch.object(handler, "_validate_a2ui_action"),
        patch.object(handler, "_populate_conversation_history"),
        patch("codemie.rest_api.handlers.assistant_handlers.convert_to_task_request", return_value=MagicMock()),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            handler.process_request(AssistantChatRequest(text="hi"), MagicMock(), MagicMock())
    assert exc_info.value.code == 400


def test_constructor_without_jsonrpc_interface_raises_400_unpatched():
    assistant = MagicMock()
    card = _card()
    card.supportedInterfaces = [AgentInterface(url="https://remote.example.com/grpc", protocolBinding="GRPC")]
    assistant.agent_card = card
    with pytest.raises(ExtendedHTTPException) as exc_info:
        A2AAssistantHandler(assistant=assistant, user=MagicMock(), request_uuid="u")
    assert exc_info.value.code == 400


# ---- sync path ----


def _run_sync(handler, send_task):
    handler.remote_connection.send_task = send_task
    with patch.object(handler, "save_chat_history") as save:
        response = handler._handle_a2a_sync(AssistantChatRequest(text="hi"), MagicMock())
    return response, save


def test_sync_task_result_becomes_chat_response():
    handler = _handler()
    task = Task(
        id="t1",
        status=TaskStatus(state=TaskState.COMPLETED),
        artifacts=[Artifact(parts=[Part(text="the answer")])],
    )

    async def send_task(params, callback):
        return task

    response, save = _run_sync(handler, send_task)

    assert isinstance(response, BaseModelResponse) and response.generated == "the answer"
    assert save.call_args[0][0].response == "the answer"


def test_sync_message_result_becomes_chat_response():
    handler = _handler()

    async def send_task(params, callback):
        return Message(role=Role.AGENT, parts=[Part(text="direct reply")], taskId="t2")

    response, _ = _run_sync(handler, send_task)

    assert response.generated == "direct reply" and response.task_id == "t2"


def test_sync_empty_result_is_a_502_not_an_attribute_error():
    handler = _handler()

    async def send_task(params, callback):
        return None

    with pytest.raises(ExtendedHTTPException) as exc_info:
        _run_sync(handler, send_task)
    assert exc_info.value.code == 502


def test_sync_protocol_version_error_surfaces_as_400_with_clear_message():
    handler = _handler()

    async def send_task(params, callback):
        raise A2AProtocolVersionError("Remote agent does not support A2A protocol 1.0")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        _run_sync(handler, send_task)
    assert exc_info.value.code == 400
    assert "does not support A2A protocol 1.0" in (exc_info.value.details or exc_info.value.message)


def test_sync_other_client_errors_are_400_class_too():
    handler = _handler()

    async def send_task(params, callback):
        raise A2AClientHTTPError(503, "upstream down")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        _run_sync(handler, send_task)
    assert exc_info.value.code == 400
    assert "upstream down" in (exc_info.value.details or "")


# ---- streaming path ----


def _status(state, text=None):
    message = Message(role=Role.AGENT, parts=[Part(text=text)]) if text else None
    return TaskStatusUpdateEvent(taskId="t", contextId="c", status=TaskStatus(state=state, message=message))


def _artifact(text):
    return TaskArtifactUpdateEvent(taskId="t", contextId="c", artifact=Artifact(parts=[Part(text=text)]))


def _run_stream(updates=(), error=None):
    """Drive _handle_a2a_stream with a fake remote connection; returns (parsed chunks, save_chat_history mock)."""
    handler = _handler(streaming=True)

    async def send_task(params, callback):
        for update in updates:
            callback(update, handler.agent_card)
        if error:
            raise error

    handler.remote_connection.send_task = send_task
    chunks = []

    async def drain(body_iterator):
        async for chunk in body_iterator:
            chunks.append(json.loads(chunk))

    with patch.object(handler, "save_chat_history") as save:
        response = handler._handle_a2a_stream(AssistantChatRequest(text="run", stream=True), MagicMock())
        asyncio.run(drain(response.body_iterator))
    return chunks, save


def test_stream_completes_on_terminal_status_update_without_final_flag():
    chunks, save = _run_stream(
        [_status(TaskState.WORKING, "thinking"), _artifact("the answer"), _status(TaskState.COMPLETED)]
    )

    assert chunks[0]["thought"]["message"] == "thinking"
    assert chunks[-1]["generated"] == "the answer" and chunks[-1]["last"] is True
    assert save.call_args[0][0].response == "the answer"


def test_stream_ends_on_input_required_and_returns_agent_question_as_answer():
    chunks, save = _run_stream([_status(TaskState.WORKING), _status(TaskState.INPUT_REQUIRED, "Which city?")])

    assert chunks[-1]["generated"] == "Which city?" and chunks[-1]["last"] is True
    assert save.call_args[0][0].response == "Which city?"


def test_stream_message_result_is_the_answer():
    chunks, save = _run_stream([Message(role=Role.AGENT, parts=[Part(text="direct reply")])])

    assert [c["generated"] for c in chunks if c.get("generated")] == ["direct reply"]
    assert save.call_args[0][0].response == "direct reply"


def test_stream_task_snapshot_with_artifact_and_terminal_state_is_the_answer():
    task = Task(id="t", status=TaskStatus(state=TaskState.COMPLETED), artifacts=[Artifact(parts=[Part(text="final")])])
    chunks, _ = _run_stream([task])
    assert chunks[-1]["generated"] == "final"


def _answer_chunks(chunks):
    return [c for c in chunks if c.get("generated_chunk")]


def test_stream_artifacts_are_incremental_chunks_and_only_one_chunk_is_last():
    chunks, save = _run_stream(
        [
            _status(TaskState.WORKING, "thinking"),
            _artifact("1. Unbox"),
            _artifact("2. Connect"),
            _artifact("3. Power on"),
            _status(TaskState.COMPLETED, "Guide complete."),
        ]
    )

    steps = _answer_chunks(chunks)
    assert [c["generated_chunk"] for c in steps] == ["1. Unbox", "\n\n2. Connect", "\n\n3. Power on"]
    assert all(c["last"] is False for c in steps)
    assert [c["last"] for c in chunks].count(True) == 1
    assert chunks[-1]["last"] is True and chunks[-1]["generated"] == "1. Unbox\n\n2. Connect\n\n3. Power on"
    assert save.call_args[0][0].response == "1. Unbox\n\n2. Connect\n\n3. Power on"


def test_stream_appended_artifact_chunks_are_joined_without_separator():
    appended = TaskArtifactUpdateEvent(
        taskId="t", contextId="c", artifact=Artifact(parts=[Part(text="lo")]), append=True
    )
    chunks, _ = _run_stream([_artifact("Hel"), appended, _status(TaskState.COMPLETED)])

    assert [c["generated_chunk"] for c in _answer_chunks(chunks)] == ["Hel", "lo"]
    assert chunks[-1]["generated"] == "Hello"


def test_stream_final_task_snapshot_does_not_repeat_already_streamed_artifacts():
    snapshot = Task(
        id="t",
        status=TaskStatus(state=TaskState.COMPLETED),
        artifacts=[Artifact(parts=[Part(text="step")])],
    )
    chunks, save = _run_stream([_artifact("step"), snapshot])

    assert [c["generated_chunk"] for c in _answer_chunks(chunks)] == ["step"]
    assert chunks[-1]["generated"] == "step"
    assert save.call_args[0][0].response == "step"


def test_stream_ends_with_last_chunk_when_remote_stream_ends_after_answer_without_final_update():
    chunks, save = _run_stream([_artifact("partial answer")])

    assert chunks[-1]["last"] is True and chunks[-1]["generated"] == "partial answer"
    assert save.call_args[0][0].response == "partial answer"


def test_stream_closes_even_if_remote_stream_ends_without_final_update():
    chunks, save = _run_stream([_status(TaskState.WORKING, "thinking")])

    assert chunks[0]["thought"]["message"] == "thinking"
    save.assert_called_once()


def test_stream_protocol_version_error_before_the_first_frame_is_a_400():
    with pytest.raises(ExtendedHTTPException) as exc_info:
        _run_stream(error=A2AProtocolVersionError("Remote agent does not support A2A protocol 1.0"))

    assert exc_info.value.code == 400
    assert "does not support A2A protocol 1.0" in exc_info.value.details


def test_stream_protocol_version_error_after_the_first_frame_is_a_final_chunk():
    chunks, save = _run_stream(
        [_status(TaskState.WORKING, "thinking")],
        error=A2AProtocolVersionError("Remote agent does not support A2A protocol 1.0"),
    )

    assert "does not support A2A protocol 1.0" in chunks[-1]["generated"]
    assert chunks[-1]["last"] is True
    save.assert_called_once()


def test_stream_version_error_from_the_real_connection_is_a_400():
    """The connection's local SUBMITTED placeholder must not count as a remote frame."""
    handler = _handler(streaming=True)

    async def rejected(payload):
        raise A2AProtocolVersionError("Remote agent does not support A2A protocol 1.0")
        yield

    with patch("codemie.rest_api.a2a.client.remote_agent_connection.A2AClient") as client_cls:
        client_cls.return_value.send_task_streaming = rejected
        handler.remote_connection = RemoteAgentConnections(handler.agent_card)
    request = SendMessageRequest(message=Message(role=Role.USER, parts=[Part(text="hi")], taskId="t", contextId="c"))

    with pytest.raises(ExtendedHTTPException) as exc_info:
        handler._handle_a2a_stream(AssistantChatRequest(text="hi", stream=True), MagicMock(params=request))

    assert exc_info.value.code == 400


def test_stream_delivers_chunks_while_the_remote_call_is_still_running():
    handler = _handler(streaming=True)
    first_chunk_seen = threading.Event()
    remote_timed_out = []

    async def send_task(params, callback):
        callback(_artifact("part one"), handler.agent_card)
        remote_timed_out.append(not await asyncio.to_thread(first_chunk_seen.wait, 5))
        callback(_status(TaskState.COMPLETED), handler.agent_card)

    handler.remote_connection.send_task = send_task

    async def drain(body_iterator):
        async for chunk in body_iterator:
            if json.loads(chunk).get("generated_chunk"):
                first_chunk_seen.set()

    with patch.object(handler, "save_chat_history"):
        response = handler._handle_a2a_stream(AssistantChatRequest(text="run", stream=True), MagicMock())
        asyncio.run(drain(response.body_iterator))

    assert remote_timed_out == [False]
