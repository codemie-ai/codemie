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

"""The tool-call resume API of an assistant handler (rules 4, 5): check the request, consume the pause, hand it over.

``ToolCallResumeMixin.resume_tool_call`` authorises, refuses what it cannot resume (422), then consumes the pending
row in one transaction, validating the decisions inside it (404, 409). Nothing is registered, written or built before
the consume. The consumed row goes to the handler's ``handle_tool_call_resume`` hook once; the standard hook runs it
as an ordinary turn of ``StandardAssistantHandler._handle_stream``, whose resume branch is ``begin_resume``. The
module also holds the turn-start cancel of a waiting pause (rule 7) and the helpers that rebuild the paused turn's
request and root assistant.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, final

from fastapi import Request, status
from starlette.responses import StreamingResponse

from codemie.agents.tool_confirmation.models import (
    AppliedDecision,
    StoredPendingToolCall,
    applied_decisions,
)
from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core import tool_step_states
from codemie.core.models import AssistantChatRequest, ToolCallAction, ToolCallPolicy
from codemie.core.thread import ThreadedGenerator
from codemie.rest_api.handlers.execution_model import RequestExecutionModel
from codemie.rest_api.models.base import ConversationStatus
from codemie.rest_api.models.conversation import Conversation
from codemie.service.assistant.assistant_engine_builder import (
    BuildChannel,
    SubagentBuildContext,
)
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
from codemie.service.request_summary_manager import request_summary_manager

if TYPE_CHECKING:
    from codemie.rest_api.handlers.assistant_handlers import StandardAssistantHandler
    from codemie.rest_api.models.assistant import Assistant, ToolCallResumeRequest

NDJSON_MEDIA_TYPE = "application/x-ndjson"
NO_PENDING_TOOL_CALL_MESSAGE = "No pending tool call found for this conversation."
OTHER_ROOT_ASSISTANT_MESSAGE = "This tool call is waiting for another assistant of the conversation."
DECISIONS_MISMATCH_MESSAGE = (
    "The decisions do not match the tool calls waiting for a decision. Please reload the conversation."
)
UNSUPPORTED_RESUME_MESSAGE = "Tool call confirmation is not supported for this assistant."
NOT_STREAMED_RESUME_MESSAGE = "A tool call resume is always streamed; stream=false is not supported."


class ToolCallResumeMixin:
    """The resume API of ``AssistantRequestHandler``; the host provides ``user``, ``assistant`` and the hook."""

    supports_tool_call_resume: ClassVar[bool] = False

    def handle_tool_call_resume(
        self,
        request: ToolCallResumeRequest,
        conversation: Conversation,
        pending: ConsumedPendingToolCall,
        raw_request: Request,
    ) -> StreamingResponse:
        """Run the resumed turn of the consumed ``pending`` row; called once per resume by ``resume_tool_call``.

        Only a handler with ``supports_tool_call_resume`` reaches it (any other gets 422 before the consume), so a
        handler that supports resume overrides it.
        """
        raise NotImplementedError

    @final
    def resume_tool_call(
        self, request: ToolCallResumeRequest, raw_request: Request
    ) -> tuple[StreamingResponse, Assistant]:
        """Authorise, consume the pause and call the hook once (rules 4, 5.2, 5.3).

        Order: READ on the assistant, WRITE on the conversation and membership; the body shape, resume support and
        ``stream`` (422); the consume, which validates inside its transaction the root assistant and the decision set
        (409, the row stays); no row: 404. Only then the request summary, so a refused resume leaves nothing in
        memory, and the hook. Returns the hook's response and the assistant the turn ran on (the pinned version once
        the resume branch set it).
        """
        conversation = self._authorize_tool_call_resume(request)
        shape_error = request.shape_error()
        if shape_error is not None:
            raise ExtendedHTTPException(code=422, message=shape_error)
        if not self.supports_tool_call_resume:
            raise ExtendedHTTPException(code=422, message=UNSUPPORTED_RESUME_MESSAGE)
        if not request.stream:
            raise ExtendedHTTPException(code=422, message=NOT_STREAMED_RESUME_MESSAGE)
        service = ConversationCheckpointService()
        conversation_id = request.conversation_id
        root_assistant_id = str(getattr(self.assistant, "id", "") or "")  # type: ignore[attr-defined]
        decisions: list[AppliedDecision] = []

        def validate(record: StoredPendingToolCall) -> None:
            if record.root_assistant_id != root_assistant_id:
                raise _conflict(OTHER_ROOT_ASSISTANT_MESSAGE)
            decisions[:] = _applied_or_raise(record, request)

        record = service.consume(conversation_id, validate)
        if record is None:
            _cancel_stale_steps(conversation_id)
            raise ExtendedHTTPException(code=status.HTTP_404_NOT_FOUND, message=NO_PENDING_TOOL_CALL_MESSAGE)
        request_summary_manager.create_request_summary(
            request_id=self.request_uuid,  # type: ignore[attr-defined]
            project_name=self.assistant.project,  # type: ignore[attr-defined]
            user=self.user.as_user_model(),  # type: ignore[attr-defined]
        )
        pending = ConsumedPendingToolCall(record, decisions)
        response = self.handle_tool_call_resume(request, conversation, pending, raw_request)
        return response, self.assistant  # type: ignore[attr-defined]

    def _authorize_tool_call_resume(self, request: ToolCallResumeRequest) -> Conversation:
        from codemie.rest_api.routers.utils import check_user_can_access_assistant

        check_user_can_access_assistant(self.user, self.assistant, "view", Action.READ)
        conversation = Conversation.find_by_conversation_id(request.conversation_id)
        if conversation is None or not Ability(self.user).can(Action.WRITE, conversation):
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Conversation not found or access denied.",
            )
        assistant_id = getattr(self.assistant, "id", None)
        if assistant_id not in (conversation.assistant_ids or []) and assistant_id != conversation.initial_assistant_id:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Conversation not found or access denied.",
            )
        return conversation

    def _end_pending_pause_at_turn_start(self, conversation: Conversation, conversation_id: str) -> None:
        """A new message cancels a waiting pause and stale waiting steps (rule 7, AC12).

        Ending a pause writes the conversation, so it needs WRITE, checked on the conversation the turn start
        already loaded. With WRITE nothing is read first: ``cancel_pause`` runs one ``DELETE ... RETURNING``. Without
        WRITE the pending row is read only to refuse a message that would end someone's pause.
        """
        service = ConversationCheckpointService()
        if not Ability(self.user).can(Action.WRITE, conversation):
            if service.get_pending(conversation_id) is not None:
                self._deny_conversation_access(conversation, conversation_id)
            return
        service.cancel_pause(conversation)


def _cancel_stale_steps(conversation_id: str) -> None:
    """No pause to resume: cancel the waiting steps it left in the last answer (rule 7.2); never changes the 404.

    The conversation is loaded again after the consume, so a resume that won the row and already saved its
    placeholder shows no waiting step, and one that has not yet saved it overwrites the entry afterwards.
    """
    try:
        conversation = Conversation.find_by_conversation_id(conversation_id)
        if conversation is not None:
            ConversationCheckpointService.cancel_stale_steps(conversation)
    except Exception as error:
        logger.error(f"Could not cancel the stale waiting steps of conversation {conversation_id}: {error}")


def resumed_turn_request(conversation_id: str, record: StoredPendingToolCall) -> AssistantChatRequest:
    """The chat request of the paused entry from the row's columns alone; pure, it cannot raise (rule 5.4).

    ``history_index`` and the original user message come from the row; the request is marked as a persisted
    variant, so every save of the resumed turn, main's error save included, replaces the paused variant at
    ``history_index``. ``restore_request`` then adds the stored request fields of ``payload``.
    """
    chat_request = AssistantChatRequest.model_construct(
        conversation_id=conversation_id,
        text=record.original_user_message or "",
        history=[],
        stream=True,
        file_names=[],
        save_history=True,
        history_index=record.history_index,
    )
    chat_request.mark_history_variant_persisted()
    return chat_request


def restore_request(chat_request: AssistantChatRequest, payload: dict[str, Any]) -> None:
    """Set the stored request fields of ``payload`` on ``chat_request``, each exactly as received (rule 5.4).

    ``None`` stays ``None``. ``llm_model`` and ``tool_call_policy`` are set only when the turn's request carried
    them, so the conversation's own values apply on resume exactly as they did on the paused turn. The turn's
    attached files come back with it, so its tools see the same file objects.

    Raises:
        ValueError: a stored ``request_tool_call_policy`` the enum no longer knows
    """
    chat_request.file_names = list(payload.get("file_names") or [])
    chat_request.version = payload.get("root_version")
    chat_request.sub_assistants_versions = payload.get("sub_assistants_versions")
    chat_request.skill_ids = payload.get("skill_ids")
    if payload.get("llm_model") is not None:
        chat_request.llm_model = payload["llm_model"]
    if payload.get("request_tool_call_policy") is not None:
        chat_request.tool_call_policy = ToolCallPolicy(payload["request_tool_call_policy"])


@dataclass
class ConsumedPendingToolCall:
    """A pending row the resume consumed, with every call's action (auto-allowed ones allow)."""

    record: StoredPendingToolCall
    decisions: list[AppliedDecision]


@dataclass
class ResumeStart:
    """What the resume branch of ``_handle_stream`` runs on; built inside it, never a request field."""

    decisions: list[AppliedDecision]  # every call; auto-allowed ones allow
    assistant: Assistant  # the restored pinned version


def begin_resume(
    handler: StandardAssistantHandler,
    request: AssistantChatRequest,
    generator: ThreadedGenerator,
    pending: ConsumedPendingToolCall,
    execution_start: float,
    user_message_received_at: datetime | None = None,
) -> ResumeStart:
    """The resume branch of ``_handle_stream``, run inside its ``try`` (rule 5.4).

    ``request`` is ``resumed_turn_request`` of the row: it already names the paused entry. In order: seed the
    generator from the row's snapshot (not streamed), restore the stored request fields onto ``request``, set
    ``handler.assistant`` to the pinned version, save the ``in_progress`` placeholder over the seed. The seed comes
    first because it cannot raise: any later raise lands in ``_handle_stream``'s except, which saves the seed, with
    no waiting step, as the turn's error over the entry (rule 5.5, AC10).

    Raises:
        ValueError: a stored request field the request no longer accepts
        ExtendedHTTPException: the pinned version is gone (an ordinary turn error)
    """
    record = pending.record
    payload = record.payload or {}
    carried_ids = set(payload.get("carried_ids") or [])
    allowed_ids = {decision.item.thought_id for decision in pending.decisions if decision.action == "allow"}
    allowed_ids |= carried_ids
    seed = _seeded_thoughts(record.thoughts, pending.decisions, carried_ids)
    generator.seed(seed, allowed_ids)

    restore_request(request, payload)
    handler.assistant = _restored_assistant(handler, request)
    # The saves and the LLM context record the model of the version that runs, not the master's.
    handler._execution_model = RequestExecutionModel(handler.assistant)
    _save_placeholder(handler, request, generator, execution_start, user_message_received_at)
    return ResumeStart(decisions=pending.decisions, assistant=handler.assistant)


def build_resumed_agent(
    handler: StandardAssistantHandler,
    request: AssistantChatRequest,
    generator: ThreadedGenerator,
    start: ResumeStart,
) -> Any:
    """The resumed turn's agent: built on ``RESUME`` from the pinned version and the restored request."""
    return handler._build_agent(
        assistant=start.assistant,
        request=request,
        user=handler.user,
        request_uuid=handler.request_uuid,
        thread_generator=generator,
        channel=BuildChannel.RESUME,
        build_context=SubagentBuildContext.root(BuildChannel.RESUME),
    )


def _seeded_thoughts(
    thoughts: list[dict[str, Any]], decisions: list[AppliedDecision], carried_ids: set[str]
) -> list[dict[str, Any]]:
    """The snapshot with the decided states: allowed, denied, and ``in_progress`` for carried steps.

    Every other step, such as a delegation container, is kept as the snapshot holds it.
    """
    states = {
        decision.item.thought_id: tool_step_states.allowed if decision.action == "allow" else tool_step_states.denied
        for decision in decisions
    }

    def seeded(step: dict[str, Any]) -> dict[str, Any]:
        step = {**step, "children": [seeded(child) for child in step.get("children") or []]}
        if step.get("id") in carried_ids:
            step["in_progress"] = True
        state = states.get(step.get("id"))
        return state(step) if state is not None else step

    return [seeded(step) for step in deepcopy(thoughts)]


def _save_placeholder(
    handler: StandardAssistantHandler,
    request: AssistantChatRequest,
    generator: ThreadedGenerator,
    execution_start: float,
    user_message_received_at: datetime | None,
) -> None:
    """Main's ``in_progress`` placeholder, with the seed instead of ``[]``; a failure is logged as on main."""
    from codemie.rest_api.handlers.assistant_handlers import ChatHistoryData

    try:
        handler.save_chat_history(
            ChatHistoryData(
                execution_start=execution_start,
                request=request,
                response="",
                thoughts=generator.thoughts,
                status=ConversationStatus.SUCCESS,
                user_message_received_at=user_message_received_at,
                in_progress=True,
            )
        )
    except Exception as persist_exc:
        logger.error(f"Error persisting initial prompt in_progress: {str(persist_exc)}")


def _restored_assistant(handler: StandardAssistantHandler, chat_request: AssistantChatRequest) -> Assistant:
    """The root as the paused turn ran it: its pinned version, skills and model, as the ask path applies them.

    Resolved once per resume, inside the resume branch (rule 5.1); a failure is an ordinary turn error.
    """
    from codemie.rest_api.routers.assistant._ask import _prepare_assistant_for_execution

    return _prepare_assistant_for_execution(handler.assistant, chat_request)


def _applied_or_raise(record: StoredPendingToolCall, request: ToolCallResumeRequest) -> list[AppliedDecision]:
    """Every call's action, when the request decides exactly the shown calls (rule 1); 409 otherwise.

    ``decisions`` must name each shown call once (duplicates count). ``{action}`` needs exactly one shown call;
    a top-level ``thought_id`` must be that call.
    """
    shown = [item.thought_id for item in record.shown_items()]
    if request.decisions is not None:
        requested_ids = [decision.thought_id for decision in request.decisions]
        if Counter(requested_ids) != Counter(shown):
            raise _conflict(DECISIONS_MISMATCH_MESSAGE)
        requested = {decision.thought_id: ToolCallAction(decision.action).value for decision in request.decisions}
    else:
        if len(shown) != 1 or request.thought_id not in (None, shown[0]):
            raise _conflict(DECISIONS_MISMATCH_MESSAGE)
        requested = {shown[0]: ToolCallAction(request.action).value}
    return applied_decisions(record.items, requested)


def _conflict(message: str) -> ExtendedHTTPException:
    return ExtendedHTTPException(code=status.HTTP_409_CONFLICT, message=message)
