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

import threading
import uuid
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.a2a.server.codemie_task_manager import CodemieTaskManager
from codemie.rest_api.a2a.types import (
    Artifact,
    CancelTaskRPCRequest,
    CancelTaskRequest,
    ContentTypeNotSupportedError,
    GetTaskRPCRequest,
    GetTaskRequest,
    InvalidParamsError,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    SendMessageRPCRequest,
    SendStreamingMessageRPCRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    UnsupportedOperationError,
)
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService

_DUMP = {"by_alias": True, "exclude_none": True}


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.id = "user-1"
    return user


@pytest.fixture
def mock_assistant():
    assistant = MagicMock(spec=Assistant)
    assistant.id = "assistant-1"
    return assistant


@pytest.fixture
def task_manager(mock_assistant, mock_user):
    return CodemieTaskManager(assistant=mock_assistant, user=mock_user)


@pytest.fixture
def store():
    """In-memory stand-in for the a2a_tasks table."""
    tasks: dict[str, Task] = {}

    def find(task_id):
        return tasks.get(task_id)

    def save(self, refresh=False):
        tasks[self.id] = self
        return self

    with (
        patch.object(Task, "find_by_id", side_effect=find),
        patch.object(Task, "save", save),
        patch.object(Task, "update", save),
    ):
        yield tasks


def _seed(store, task_id="t-1", state=TaskState.WORKING, assistant_id="assistant-1", user_id="user-1", ctx="ctx-1"):
    message = Message(role=Role.USER, parts=[Part(text="earlier")], contextId=ctx, taskId=task_id)
    task = Task(
        id=task_id,
        contextId=ctx,
        assistant_id=assistant_id,
        user_id=user_id,
        status=TaskStatus(state=state),
        history=[
            message,
            Message(role=Role.AGENT, parts=[Part(text="reply")]),
            Message(role=Role.USER, parts=[Part(text="q3")]),
        ],
    )
    store[task_id] = task
    return task


def _agent(response=None, error=None):
    agent = MagicMock()
    if error is not None:
        agent.invoke_with_a2a_output.side_effect = error
    else:
        agent.invoke_with_a2a_output.return_value = response or {"content": "the answer"}
    return agent


def _send(
    text="hello",
    task_id=None,
    context_id=None,
    modes=None,
    history_length=None,
    immediate=None,
    parts=None,
    cls=SendMessageRPCRequest,
):
    message = Message(role=Role.USER, parts=parts or [Part(text=text)], taskId=task_id, contextId=context_id)
    configuration = None
    if modes is not None or history_length is not None or immediate is not None:
        configuration = SendMessageConfiguration(
            acceptedOutputModes=modes, historyLength=history_length, returnImmediately=immediate
        )
    return cls(id="request-123", params=SendMessageRequest(message=message, configuration=configuration))


async def _collect(manager, request):
    return [frame async for frame in manager.on_send_streaming_message(request)]


class TestInit:
    def test_initialization(self, mock_assistant, mock_user):
        manager = CodemieTaskManager(assistant=mock_assistant, user=mock_user)
        assert manager.assistant == mock_assistant and manager.user == mock_user


# ---- GetTask ----


class TestGetTask:
    @pytest.mark.asyncio
    async def test_not_found(self, task_manager, store):
        response = await task_manager.on_get_task(GetTaskRPCRequest(id="r1", params=GetTaskRequest(id="missing")))
        assert isinstance(response.error, TaskNotFoundError) and response.result is None and response.id == "r1"
        assert response.error.data[0]["metadata"] == {"taskId": "missing"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "attr,value", [("user_id", "someone-else"), ("assistant_id", "assistant-2"), ("user_id", None)]
    )
    async def test_non_owner_and_legacy_unowned_are_not_found(self, task_manager, store, attr, value):
        task = _seed(store)
        setattr(task, attr, value)
        if value is None:
            task.assistant_id = None
        response = await task_manager.on_get_task(GetTaskRPCRequest(id="r1", params=GetTaskRequest(id="t-1")))
        assert isinstance(response.error, TaskNotFoundError)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("history_length,expected", [(None, 3), (0, None), (2, 2), (10, 3)])
    async def test_history_length(self, task_manager, store, history_length, expected):
        _seed(store)
        request = GetTaskRPCRequest(id="r1", params=GetTaskRequest(id="t-1", historyLength=history_length))
        response = await task_manager.on_get_task(request)
        assert response.error is None
        assert response.result.id == "t-1"
        assert (len(response.result.history) if response.result.history is not None else None) == expected
        if history_length == 2:
            assert response.result.history[-1].parts[0].text == "q3"
        # the stored task is not truncated
        assert len(store["t-1"].history) == 3

    @pytest.mark.asyncio
    async def test_result_dump_uses_task_id_field_and_no_removed_keys(self, task_manager, store):
        _seed(store)
        response = await task_manager.on_get_task(GetTaskRPCRequest(id="r1", params=GetTaskRequest(id="t-1")))
        dumped = response.model_dump(**_DUMP)["result"]
        assert dumped["id"] == "t-1" and dumped["contextId"] == "ctx-1"
        assert dumped["status"]["state"] == "TASK_STATE_WORKING"
        assert not {"taskId", "sessionId", "assistant_id", "user_id", "date", "update_date"} & set(dumped)


# ---- CancelTask ----


class TestCancelTask:
    @pytest.mark.asyncio
    async def test_transitions_working_to_canceled(self, task_manager, store):
        _seed(store)
        response = await task_manager.on_cancel_task(CancelTaskRPCRequest(id="r1", params=CancelTaskRequest(id="t-1")))
        assert response.error is None
        assert response.result.status.state == TaskState.CANCELED
        assert store["t-1"].status.state == TaskState.CANCELED

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED])
    async def test_rejects_terminal_task(self, task_manager, store, state):
        _seed(store, state=state)
        response = await task_manager.on_cancel_task(CancelTaskRPCRequest(id="r1", params=CancelTaskRequest(id="t-1")))
        assert isinstance(response.error, TaskNotCancelableError)
        assert store["t-1"].status.state == state

    @pytest.mark.asyncio
    async def test_unknown_and_foreign_are_not_found(self, task_manager, store):
        _seed(store, user_id="someone-else")
        for task_id in ("missing", "t-1"):
            response = await task_manager.on_cancel_task(
                CancelTaskRPCRequest(id="r1", params=CancelTaskRequest(id=task_id))
            )
            assert isinstance(response.error, TaskNotFoundError)
        assert store["t-1"].status.state == TaskState.WORKING

    @pytest.mark.asyncio
    async def test_cancel_wins_over_late_agent_result(self, task_manager, store):
        _seed(store)
        await task_manager.on_cancel_task(CancelTaskRPCRequest(id="r1", params=CancelTaskRequest(id="t-1")))
        result = CodemieTaskManager.update_store(
            "t-1", TaskStatus(state=TaskState.COMPLETED), [Artifact(parts=[Part(text="late")])]
        )
        assert result.status.state == TaskState.CANCELED
        assert not store["t-1"].artifacts


# ---- SendMessage ----


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_new_task_server_generates_ids_and_completes_with_artifact(self, task_manager, store):
        with patch.object(AssistantService, "build_agent", return_value=_agent()):
            response = await task_manager.on_send_task(_send("hi"))
        assert response.error is None and response.id == "request-123"
        task = response.result.task
        assert task.id and task.contextId
        assert task.status.state == TaskState.COMPLETED
        assert len(task.artifacts) == 1
        assert task.artifacts[0].artifactId and task.artifacts[0].parts[0].text == "the answer"
        assert store[task.id].assistant_id == "assistant-1" and store[task.id].user_id == "user-1"
        stamped = task.history[0]
        assert stamped.taskId == task.id and stamped.contextId == task.contextId and stamped.parts[0].text == "hi"

    @pytest.mark.asyncio
    async def test_supplied_context_id_is_kept_and_used_as_conversation_id(self, task_manager, store):
        with patch.object(AssistantService, "build_agent", return_value=_agent()) as build:
            response = await task_manager.on_send_task(_send("hi", context_id="conv-9"))
        assert response.result.task.contextId == "conv-9"
        assert build.call_args.kwargs["request"].conversation_id == "conv-9"
        assert build.call_args.kwargs["request"].text == "hi"

    @pytest.mark.asyncio
    async def test_result_dump_is_v1_shape(self, task_manager, store):
        with patch.object(AssistantService, "build_agent", return_value=_agent()):
            response = await task_manager.on_send_task(_send("hi"))
        dumped = response.model_dump(**_DUMP)["result"]["task"]
        assert set(dumped) <= {"id", "contextId", "status", "artifacts", "history"}
        assert dumped["status"]["state"] == "TASK_STATE_COMPLETED"
        assert set(dumped["artifacts"][0]) <= {"artifactId", "name", "description", "parts"}
        assert dumped["artifacts"][0]["parts"] == [{"text": "the answer"}]

    @pytest.mark.asyncio
    async def test_input_required_carries_status_message(self, task_manager, store):
        agent = _agent({"content": "which city?", "require_user_input": True})
        with patch.object(AssistantService, "build_agent", return_value=agent):
            response = await task_manager.on_send_task(_send("weather"))
        task = response.result.task
        assert task.status.state == TaskState.INPUT_REQUIRED
        assert task.status.message.role == Role.AGENT and task.status.message.parts[0].text == "which city?"
        assert not task.artifacts

    @pytest.mark.asyncio
    async def test_continue_input_required_task_reuses_ids_and_appends_history(self, task_manager, store):
        _seed(store, state=TaskState.INPUT_REQUIRED)
        with patch.object(AssistantService, "build_agent", return_value=_agent()):
            response = await task_manager.on_send_task(_send("Paris", task_id="t-1"))
        task = response.result.task
        assert task.id == "t-1" and task.contextId == "ctx-1"
        assert task.status.state == TaskState.COMPLETED
        assert [m.parts[0].text for m in task.history][-1] == "Paris"
        assert len(store) == 1

    @pytest.mark.asyncio
    async def test_continuation_with_matching_context_id_is_accepted(self, task_manager, store):
        _seed(store, state=TaskState.INPUT_REQUIRED)
        with patch.object(AssistantService, "build_agent", return_value=_agent()):
            response = await task_manager.on_send_task(_send("Paris", task_id="t-1", context_id="ctx-1"))
        assert response.error is None

    @pytest.mark.asyncio
    async def test_context_id_mismatch_is_invalid_params(self, task_manager, store):
        _seed(store, state=TaskState.INPUT_REQUIRED)
        response = await task_manager.on_send_task(_send("Paris", task_id="t-1", context_id="other"))
        assert isinstance(response.error, InvalidParamsError)
        assert response.error.data[0]["@type"].endswith("google.rpc.BadRequest")
        assert response.error.data[0]["fieldViolations"][0]["field"] == "message.contextId"

    @pytest.mark.asyncio
    async def test_foreign_and_unknown_task_ids_are_not_found(self, task_manager, store):
        _seed(store, user_id="someone-else")
        for task_id in ("t-1", "missing"):
            response = await task_manager.on_send_task(_send("x", task_id=task_id))
            assert isinstance(response.error, TaskNotFoundError)
        assert len(store) == 1 and len(store["t-1"].history) == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED])
    async def test_terminal_task_is_unsupported_operation(self, task_manager, store, state):
        _seed(store, state=state)
        response = await task_manager.on_send_task(_send("x", task_id="t-1"))
        assert isinstance(response.error, UnsupportedOperationError)
        assert len(store["t-1"].history) == 3

    @pytest.mark.asyncio
    async def test_return_immediately_is_unsupported(self, task_manager, store):
        with patch.object(AssistantService, "build_agent") as build:
            response = await task_manager.on_send_task(_send("x", immediate=True))
        assert isinstance(response.error, UnsupportedOperationError)
        build.assert_not_called()
        assert not store

    @pytest.mark.asyncio
    async def test_return_immediately_false_is_accepted(self, task_manager, store):
        with patch.object(AssistantService, "build_agent", return_value=_agent()):
            response = await task_manager.on_send_task(_send("x", immediate=False))
        assert response.error is None

    @pytest.mark.asyncio
    async def test_disjoint_accepted_output_modes_is_content_type_error(self, task_manager, store):
        with patch.object(AssistantService, "build_agent") as build:
            response = await task_manager.on_send_task(_send("x", modes=["image/png"]))
        assert isinstance(response.error, ContentTypeNotSupportedError) and response.error.code == -32005
        build.assert_not_called()
        assert not store

    @pytest.mark.asyncio
    @pytest.mark.parametrize("modes", [["text/plain"], ["image/png", "text/plain"], [], None])
    async def test_compatible_accepted_output_modes(self, task_manager, store, modes):
        with patch.object(AssistantService, "build_agent", return_value=_agent()):
            response = await task_manager.on_send_task(_send("x", modes=modes, history_length=5))
        assert response.error is None

    @pytest.mark.asyncio
    async def test_non_text_parts_are_invalid_params(self, task_manager, store):
        with patch.object(AssistantService, "build_agent") as build:
            response = await task_manager.on_send_task(_send(parts=[Part(raw="aGk=", filename="a.txt")]))
        assert isinstance(response.error, InvalidParamsError)
        assert "Only text parts are supported" in response.error.data[0]["fieldViolations"][0]["description"]
        build.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("history_length,expected", [(None, 3), (0, None), (1, 1)])
    async def test_history_length_shapes_returned_history(self, task_manager, store, history_length, expected):
        _seed(store, state=TaskState.INPUT_REQUIRED)
        with patch.object(AssistantService, "build_agent", return_value=_agent()):
            response = await task_manager.on_send_task(_send("x", task_id="t-1", history_length=history_length))
        history = response.result.task.history
        assert (len(history) if history is not None else None) == (expected + 1 if expected == 3 else expected)

    @pytest.mark.asyncio
    async def test_agent_failure_ends_failed_with_error_message(self, task_manager, store):
        with patch.object(AssistantService, "build_agent", return_value=_agent(error=Exception("boom"))):
            response = await task_manager.on_send_task(_send("x"))
        task = response.result.task
        assert task.status.state == TaskState.FAILED
        assert "boom" in task.status.message.parts[0].text

    @pytest.mark.asyncio
    async def test_submitted_then_working_are_persisted_before_agent_setup(self, task_manager, store):
        seen = []

        def build_agent(**kwargs):
            (only,) = store.values()
            seen.append(only.status.state)
            return _agent()

        with patch.object(AssistantService, "build_agent", side_effect=build_agent):
            await task_manager.on_send_task(_send("x"))
        assert seen == [TaskState.WORKING]

    @pytest.mark.asyncio
    async def test_setup_failure_ends_failed(self, task_manager, store):
        with patch.object(AssistantService, "build_agent", side_effect=RuntimeError("no agent")):
            response = await task_manager.on_send_task(_send("x"))
        assert response.result.task.status.state == TaskState.FAILED
        assert "no agent" in response.result.task.status.message.parts[0].text


# ---- SendStreamingMessage ----


def _results(frames):
    return [f.result for f in frames]


class TestStreaming:
    @pytest.mark.asyncio
    async def test_completed_order_task_working_artifact_terminal(self, task_manager, store):
        with patch.object(AssistantService, "build_agent", return_value=_agent()):
            frames = await _collect(task_manager, _send("hi", cls=SendStreamingMessageRPCRequest))
        results = _results(frames)
        assert all(f.error is None and f.id == "request-123" for f in frames)
        assert results[0].task is not None and results[0].task.status.state == TaskState.SUBMITTED
        assert results[1].statusUpdate.status.state == TaskState.WORKING
        assert results[2].artifactUpdate.artifact.parts[0].text == "the answer"
        assert results[2].artifactUpdate.artifact.artifactId
        assert results[3].statusUpdate.status.state == TaskState.COMPLETED
        assert len(results) == 4
        task_id = results[0].task.id
        assert all((r.task.id if r.task else (r.statusUpdate or r.artifactUpdate).taskId) == task_id for r in results)
        assert results[1].statusUpdate.contextId == results[0].task.contextId

    @pytest.mark.asyncio
    async def test_frames_carry_no_final_flag(self, task_manager, store):
        with patch.object(AssistantService, "build_agent", return_value=_agent()):
            frames = await _collect(task_manager, _send("hi", cls=SendStreamingMessageRPCRequest))
        for frame in frames:
            assert "final" not in str(frame.model_dump(**_DUMP))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("request_cls", [SendMessageRPCRequest, SendStreamingMessageRPCRequest])
    async def test_agent_runs_off_the_event_loop_thread(self, task_manager, store, request_cls):
        loop_thread = threading.get_ident()
        agent_threads = []
        agent = _agent()

        def invoke(text):
            agent_threads.append(threading.get_ident())
            return {"content": "the answer"}

        agent.invoke_with_a2a_output.side_effect = invoke
        request = _send("hi", cls=request_cls)
        with patch.object(AssistantService, "build_agent", return_value=agent):
            if request_cls is SendMessageRPCRequest:
                await task_manager.on_send_task(request)
            else:
                await _collect(task_manager, request)

        assert agent_threads and agent_threads[0] != loop_thread

    @pytest.mark.asyncio
    async def test_input_required_ends_stream_with_status_message_and_no_artifact(self, task_manager, store):
        agent = _agent({"content": "which city?", "require_user_input": True})
        with patch.object(AssistantService, "build_agent", return_value=agent):
            frames = await _collect(task_manager, _send("w", cls=SendStreamingMessageRPCRequest))
        results = _results(frames)
        assert not any(r.artifactUpdate for r in results)
        last = results[-1].statusUpdate
        assert last.status.state == TaskState.INPUT_REQUIRED and last.status.message.parts[0].text == "which city?"

    @pytest.mark.asyncio
    async def test_agent_failure_ends_with_failed_frame(self, task_manager, store):
        with patch.object(AssistantService, "build_agent", return_value=_agent(error=Exception("boom"))):
            frames = await _collect(task_manager, _send("x", cls=SendStreamingMessageRPCRequest))
        last = _results(frames)[-1].statusUpdate
        assert last.status.state == TaskState.FAILED and "boom" in last.status.message.parts[0].text

    @pytest.mark.asyncio
    async def test_setup_failure_ends_with_failed_frame(self, task_manager, store):
        with patch.object(AssistantService, "build_agent", side_effect=RuntimeError("no agent")):
            frames = await _collect(task_manager, _send("x", cls=SendStreamingMessageRPCRequest))
        results = _results(frames)
        assert results[1].statusUpdate.status.state == TaskState.WORKING
        assert results[-1].statusUpdate.status.state == TaskState.FAILED
        (only,) = store.values()
        assert only.status.state == TaskState.FAILED

    @pytest.mark.asyncio
    async def test_cancel_wins_race_yields_canceled_and_no_artifact(self, task_manager, store):
        def build_agent(**kwargs):
            agent = MagicMock()

            def invoke(_text):
                (only,) = store.values()
                only.status = TaskStatus(state=TaskState.CANCELED)
                return {"content": "late"}

            agent.invoke_with_a2a_output.side_effect = invoke
            return agent

        with patch.object(AssistantService, "build_agent", side_effect=build_agent):
            frames = await _collect(task_manager, _send("x", cls=SendStreamingMessageRPCRequest))
        results = _results(frames)
        assert results[-1].statusUpdate.status.state == TaskState.CANCELED
        assert not any(r.artifactUpdate for r in results)

    @pytest.mark.asyncio
    async def test_continuation_appends_message_to_existing_task(self, task_manager, store):
        _seed(store, state=TaskState.INPUT_REQUIRED)
        with patch.object(AssistantService, "build_agent", return_value=_agent()):
            frames = await _collect(task_manager, _send("Paris", task_id="t-1", cls=SendStreamingMessageRPCRequest))
        assert _results(frames)[0].task.id == "t-1"
        assert store["t-1"].history[3].parts[0].text == "Paris"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kwargs,error_type",
        [
            ({"modes": ["image/png"]}, ContentTypeNotSupportedError),
            ({"immediate": True}, UnsupportedOperationError),
            ({"task_id": "missing"}, TaskNotFoundError),
            ({"parts": [Part(url="https://x/f.png")]}, InvalidParamsError),
        ],
    )
    async def test_pre_stream_errors_are_a_single_error_frame(self, task_manager, store, kwargs, error_type):
        with patch.object(AssistantService, "build_agent") as build:
            frames = await _collect(task_manager, _send("x", cls=SendStreamingMessageRPCRequest, **kwargs))
        build.assert_not_called()
        assert len(frames) == 1 and isinstance(frames[0].error, error_type) and frames[0].result is None

    @pytest.mark.asyncio
    async def test_terminal_and_foreign_tasks_are_single_error_frames(self, task_manager, store):
        _seed(store, task_id="done", state=TaskState.COMPLETED)
        _seed(store, task_id="theirs", user_id="someone-else")
        done = await _collect(task_manager, _send("x", task_id="done", cls=SendStreamingMessageRPCRequest))
        theirs = await _collect(task_manager, _send("x", task_id="theirs", cls=SendStreamingMessageRPCRequest))
        assert isinstance(done[0].error, UnsupportedOperationError) and len(done) == 1
        assert isinstance(theirs[0].error, TaskNotFoundError) and len(theirs) == 1


# ---- helpers ----


class TestHelpers:
    def test_update_store_task_not_found(self, store):
        with pytest.raises(ValueError, match="Task test-task-id not found"):
            CodemieTaskManager.update_store("test-task-id", TaskStatus(state=TaskState.WORKING), [])

    def test_update_store_appends_artifacts_and_status_message(self, store):
        _seed(store)
        message = Message(role=Role.AGENT, parts=[Part(text="Test message")])
        artifact = Artifact(parts=[Part(text="Test artifact")])
        task = CodemieTaskManager.update_store("t-1", TaskStatus(state=TaskState.WORKING, message=message), [artifact])
        assert task.artifacts == [artifact] and task.history[-1] == message
        assert task.update_date is not None

    def test_update_store_does_not_overwrite_terminal_task(self, store):
        _seed(store, state=TaskState.CANCELED)
        result = CodemieTaskManager.update_store(
            "t-1", TaskStatus(state=TaskState.COMPLETED), [Artifact(parts=[Part(text="late")])]
        )
        assert result.status.state == TaskState.CANCELED and not result.artifacts

    @pytest.mark.parametrize("length,expected", [(None, 3), (0, None), (-1, None), (2, 2), (99, 3)])
    def test_append_task_history(self, store, length, expected):
        task = _seed(store)
        shaped = CodemieTaskManager._append_task_history(task, length)
        assert (len(shaped.history) if shaped.history is not None else None) == expected
        assert len(task.history) == 3

    def test_get_user_query_joins_text_parts(self):
        message = Message(role=Role.USER, parts=[Part(text="a"), Part(text="b")])
        assert CodemieTaskManager._get_user_query(message) == "a\nb"

    def test_get_user_query_rejects_non_text_parts(self):
        message = Message(role=Role.USER, parts=[Part(data={"k": "v"})])
        with pytest.raises(ValueError, match="Only text parts are supported"):
            CodemieTaskManager._get_user_query(message)

    def test_create_response_parts_returns_text_part(self, task_manager):
        assert task_manager._create_response_parts("hi") == [Part(text="hi")]
        assert task_manager._create_response_parts(None) == [Part(text="")]

    def test_setup_agent_builds_request_from_task_context(self, task_manager, store):
        task = _seed(store)
        request = _send("Hello there")
        agent_mock = MagicMock()
        with (
            patch.object(AssistantService, "build_agent", return_value=agent_mock) as build,
            patch(
                "codemie.rest_api.a2a.server.codemie_task_manager.uuid4",
                return_value=MagicMock(spec=uuid.UUID, __str__=lambda _: "test-uuid"),
            ),
        ):
            agent, assistant_request = task_manager._setup_agent(request, task)
        assert agent is agent_mock
        assert isinstance(assistant_request, AssistantChatRequest)
        assert assistant_request.text == "Hello there" and assistant_request.conversation_id == "ctx-1"
        build.assert_called_once_with(
            assistant=task_manager.assistant,
            request=assistant_request,
            user=task_manager.user,
            request_uuid="test-uuid",
        )

    def test_status_update_event_type(self, task_manager, store):
        task = _seed(store)
        frame = task_manager._status_frame("r", task, TaskState.WORKING)
        assert isinstance(frame.result.statusUpdate, TaskStatusUpdateEvent)
        assert isinstance(
            task_manager._artifact_frame("r", task, Artifact(parts=[Part(text="x")])).result.artifactUpdate,
            TaskArtifactUpdateEvent,
        )
