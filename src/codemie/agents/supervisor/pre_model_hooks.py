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

from typing import TYPE_CHECKING, Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, ImageContentBlock, ToolMessage

from codemie.agents.image_artifact_hook import accumulate_artifact, make_image_message
from codemie.core.exceptions import StreamAbortedException

if TYPE_CHECKING:
    from codemie.agents.langgraph_agent import LangGraphAgent


def _image_artifact_pre_model_hook(state: dict) -> dict:
    """Inject images from ToolMessage.artifact inline after each tool group that produced them."""
    messages = state.get("messages", [])
    result: list = []
    pending: list[ImageContentBlock] = []
    pending_text: str | None = None

    for msg in messages:
        if isinstance(msg, ToolMessage):
            pending, pending_text = accumulate_artifact(msg, pending, pending_text)
        else:
            if pending:
                result.append(make_image_message(pending, pending_text))
                pending = []
                pending_text = None
        result.append(msg)

    if pending:
        result.append(make_image_message(pending, pending_text))

    if result == list(messages):
        return {"llm_input_messages": messages}
    return {"llm_input_messages": result}


def _find_last_turn_ai_message(messages: list) -> AIMessage | None:
    """Return the most recent AIMessage produced since the current turn's HumanMessage.

    A truncated tool-call response is usually followed by a ToolMessage (the tool still
    ran, possibly with corrupted/garbage args, since LangGraph's default `ToolNode`
    swallows tool errors into a ToolMessage rather than raising) before the node's next
    model call. Checking only the literal last message would miss that truncation
    entirely once a ToolMessage sits on top of it, so this scans back past any
    ToolMessages to the AIMessage that produced them. It stops at the nearest preceding
    HumanMessage so a brand-new turn is never checked against a previous, already
    reported, turn's truncated response.
    """
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return None
        if isinstance(message, AIMessage):
            return message
    return None


def _is_incomplete_tool_call(message: AIMessage) -> bool:
    """Same rule as `LangGraphEventAdapter._finalize_truncated_text`: a text-only truncated
    answer is deliberately kept (the model simply ran out of room mid-sentence but still said
    something usable), so only a truncated *tool call* - cut mid-arguments, or garbled into
    `invalid_tool_calls` by truncation landing mid-JSON - is worth stopping the loop for.

    Gating on `invalid_tool_calls` here, not on `_check_for_truncated_response` independently
    recognizing it: that detector only looks at `response_metadata`, same as
    `_finalize_truncated_text`'s own `if message.invalid_tool_calls:` call (predates this ticket,
    EPMCDME-15787) already relies on. Extending the shared detector to treat `invalid_tool_calls`
    as its own signal is a separate, riskier change - it risks misclassifying a non-truncation
    malformed tool call as a token-limit error - and is out of scope here.
    """
    return bool(message.tool_calls or message.invalid_tool_calls)


def _build_truncation_abort_pre_model_hook(agent: "LangGraphAgent") -> Callable[[dict[str, Any]], None]:
    """Build a pre_model_hook that fails this node's next model call when the owning
    stream was closed/cancelled, when the consumer aborted the run for any other reason, or
    when the current turn's last AIMessage was already truncated mid tool-call.

    Reuses `agent._safe_check_for_truncation` for detection (no third truncation
    detector) and the same open/closed-stream check `_should_stop_streaming` already
    uses. Returning None is a no-op: an open, non-aborted stream and a non-truncated (or
    text-only-truncated) last AIMessage leave today's tool-calling loop unaffected.
    """

    def truncation_abort_pre_model_hook(state: dict[str, Any]) -> None:
        thread_generator = agent.thread_generator
        if thread_generator is not None:
            if thread_generator.is_closed():
                raise StreamAbortedException(f"stream for agent {agent.agent_name} was closed or cancelled")
            # EPMCDME-16233: checked on the shared thread_generator, not a per-instance flag -
            # a sibling sub-agent is its own separate LangGraphAgent and would never see one.
            if thread_generator.is_consumer_aborted():
                raise StreamAbortedException(
                    f"consumer aborted the run while agent {agent.agent_name} was still running"
                )

        last_ai_message = _find_last_turn_ai_message(state.get("messages") or [])
        if last_ai_message is not None and _is_incomplete_tool_call(last_ai_message):
            agent._safe_check_for_truncation(last_ai_message)

        return None

    return truncation_abort_pre_model_hook


def _compose_pre_model_hooks(*hooks) -> Any | None:
    active_hooks = [hook for hook in hooks if hook is not None]
    if not active_hooks:
        return None

    def composed_pre_model_hook(state: dict[str, Any]) -> dict[str, Any]:
        working_state = dict(state)
        combined_updates: dict[str, Any] = {}

        for hook in active_hooks:
            hook_result = hook(working_state)
            if not hook_result:
                continue

            combined_updates.update(hook_result)

            if "llm_input_messages" in hook_result:
                llm_input_messages = hook_result["llm_input_messages"]
                working_state["messages"] = llm_input_messages
                working_state["llm_input_messages"] = llm_input_messages
            else:
                working_state.update(hook_result)

        return combined_updates

    return composed_pre_model_hook
