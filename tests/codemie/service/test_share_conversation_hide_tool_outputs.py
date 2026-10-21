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
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from codemie.chains.base import Thought
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.rest_api.models.share.shared_conversation import SharedConversation
from codemie.rest_api.security.user import User
from codemie.service.share_conversation_service import ShareConversationService

HIDDEN = "assistant-hidden"
VIS = "codemie.service.assistant.tool_output_visibility.Assistant.get_by_ids_no_permission_check"
SVC = "codemie.service.share_conversation_service"


def test_shared_conversation_strips_for_viewer_without_read_access():
    viewer = User(id="viewer", username="v", name="V")
    conversation = Conversation(
        id="c1",
        conversation_id="c1",
        name="Conv",
        initial_assistant_id=HIDDEN,
        assistant_ids=[HIDDEN],
        history=[
            GeneratedMessage(
                message="answer",
                role="Assistant",
                assistant_id=HIDDEN,
                thoughts=[Thought(id="t1", author_name="Tool", message="secret")],
            )
        ],
    )
    shared = MagicMock(
        spec=SharedConversation,
        conversation_id="c1",
        shared_by_user_name="Owner",
        created_at=datetime(2026, 1, 1),
        access_count=0,
    )
    # Viewer has no READ on the assistant -> permission-filtered lookup returns nothing,
    # but the unfiltered policy lookup still sees the flag.
    with (
        patch(f"{SVC}.SharedConversation.get_by_fields", return_value=shared),
        patch(f"{SVC}.Conversation.find_by_id", return_value=conversation),
        patch(f"{SVC}.Assistant.get_by_ids", return_value=[]),
        patch(VIS, return_value=[SimpleNamespace(id=HIDDEN, hide_tool_outputs=True)]),
        patch(f"{SVC}.ConversationMonitoringService.send_share_conversation_metric"),
    ):
        result = ShareConversationService.get_shared_conversation("tok", viewer)

    assert not result["conversation"].history[0].thoughts
    assert conversation.history[0].thoughts  # loaded object not mutated


def test_shared_conversation_exposes_flag_in_assistant_data():
    viewer = User(id="viewer", username="v", name="V")
    conversation = Conversation(id="c1", conversation_id="c1", name="Conv", assistant_ids=[HIDDEN], history=[])
    shared = MagicMock(
        spec=SharedConversation,
        conversation_id="c1",
        shared_by_user_name="Owner",
        created_at=datetime(2026, 1, 1),
        access_count=0,
    )
    assistant = MagicMock(
        id=HIDDEN, icon_url=None, context=[], conversation_starters=[], toolkits=[], hide_tool_outputs=True
    )
    assistant.name = "A"
    with (
        patch(f"{SVC}.SharedConversation.get_by_fields", return_value=shared),
        patch(f"{SVC}.Conversation.find_by_id", return_value=conversation),
        patch(f"{SVC}.Assistant.get_by_ids", return_value=[assistant]),
        patch(f"{SVC}.ConversationMonitoringService.send_share_conversation_metric"),
    ):
        result = ShareConversationService.get_shared_conversation("tok", viewer)

    assert result["conversation"].assistant_data[0].hide_tool_outputs is True
