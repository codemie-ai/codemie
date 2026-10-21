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

from codemie.rest_api.models.conversation import Conversation
from codemie.service.assistant.tool_output_visibility import strip_history_for_conversation
from codemie.service.monitoring.conversation_monitoring_service import ConversationMonitoringService


conversation_monitoring_service = ConversationMonitoringService()


CONVERSATION_NOT_FOUND_MSG = "Conversation not found"


def _with_stripped_history(conversation: Conversation) -> Conversation:
    """Copy of the conversation with hidden-assistant thoughts removed. Never persist the copy."""
    if not conversation:
        return conversation
    return conversation.model_copy(update={"history": strip_history_for_conversation(conversation)})
