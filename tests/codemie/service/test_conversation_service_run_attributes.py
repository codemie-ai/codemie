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

"""Run attributes are stored on the conversation by its first turn and label every usage metric after that."""

from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.models import AssistantChatRequest, TokensUsage
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.service.conversation_service import ConversationService, UpsertChatHistoryParams

STORED = {"com.epam.gen_ai.service.work_item.id": "ABC-123"}
CONVERSATION = "codemie.rest_api.models.conversation.Conversation"
METRICS = "codemie.rest_api.models.conversation.ConversationMetrics"


@pytest.fixture
def assistant():
    return Assistant(
        id="assistant-1",
        name="Helper",
        description="",
        project="proj",
        toolkits=[],
        system_prompt="",
        llm_model_type="test_model",
        slug="helper",
    )


@pytest.fixture
def user():
    user = MagicMock(is_admin=True, id="user-1", current_project=None)
    user.name = "User One"
    return user


@contextmanager
def _persistence_patched(existing: Conversation | None):
    with ExitStack() as stack:
        stack.enter_context(patch(f"{CONVERSATION}.find_by_id", return_value=existing))
        stack.enter_context(patch(f"{CONVERSATION}.save", return_value=True))
        stack.enter_context(patch(f"{CONVERSATION}.update", return_value=True))
        stack.enter_context(patch(f"{METRICS}.get_by_conversation_id", side_effect=KeyError("not found")))
        stack.enter_context(patch(f"{METRICS}.save", return_value=True))
        stack.enter_context(patch(f"{METRICS}.calculate_metrics"))
        stack.enter_context(patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files"))
        stack.enter_context(patch("codemie.service.conversation_service.ConversationFolder.touch_folder"))
        yield stack.enter_context(
            patch(
                "codemie.service.monitoring.conversation_monitoring_service."
                "ConversationMonitoringService.send_conversation_metric"
            )
        )


def _upsert(user, assistant, attributes: dict[str, str] | None) -> None:
    ConversationService.upsert_chat_history(
        UpsertChatHistoryParams(
            assistant_response="Hi there!",
            thoughts=[],
            time_elapsed=0,
            tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
            user=user,
            assistant=assistant,
            request=AssistantChatRequest(text="Hello", conversation_id="conv-1", history=[]),
            attributes=attributes,
        )
    )


def _conversation(history: list, attributes: dict[str, str] | None, user_id: str = "user-1") -> Conversation:
    return Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id=user_id,
        assistant_ids=["assistant-1"],
        history=history,
        attributes=attributes,
    )


def _turn() -> list[GeneratedMessage]:
    return [
        GeneratedMessage(history_index=0, message="Hello", role="User"),
        GeneratedMessage(history_index=0, message="Hello back", role="Assistant"),
    ]


def test_new_conversation_stores_attributes_and_labels_the_usage_metric(user, assistant):
    with _persistence_patched(existing=None) as send_metric:
        _upsert(user, assistant, STORED)

    assert send_metric.call_args.kwargs["run_attributes"] == STORED


def test_precreated_empty_conversation_takes_the_first_turn_attributes(user, assistant):
    conversation = _conversation(history=[], attributes=None)

    with _persistence_patched(existing=conversation) as send_metric:
        _upsert(user, assistant, STORED)

    assert conversation.attributes == STORED
    assert send_metric.call_args.kwargs["run_attributes"] == STORED


@pytest.mark.parametrize("sent", [None, {"team": "core"}])
def test_conversation_with_history_keeps_stored_attributes_on_later_turns(user, assistant, sent):
    conversation = _conversation(history=_turn(), attributes=STORED)

    with _persistence_patched(existing=conversation) as send_metric:
        _upsert(user, assistant, sent)

    assert conversation.attributes == STORED
    assert send_metric.call_args.kwargs["run_attributes"] == STORED


def test_conversation_the_caller_cannot_read_does_not_label_the_usage_metric(user, assistant):
    conversation = _conversation(history=_turn(), attributes=STORED, user_id="owner")

    with _persistence_patched(existing=conversation) as send_metric:
        _upsert(user, assistant, None)

    assert conversation.attributes == STORED
    assert send_metric.call_args.kwargs["run_attributes"] is None


def test_conversation_without_attributes_sends_none(user, assistant):
    with _persistence_patched(existing=_conversation(history=_turn(), attributes=None)) as send_metric:
        _upsert(user, assistant, None)

    assert send_metric.call_args.kwargs["run_attributes"] is None
