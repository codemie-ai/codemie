# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException, ModelNotSelectableError
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.routers.utils import check_user_can_access_assistant, raise_forbidden
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service


def validate_model_selection(
    model_name: str | None, *, previous_model_name: str | None = None, allow_deprecated: bool = False
) -> None:
    """Reject a new unavailable selection before inference or streaming starts."""
    if not model_name:
        return
    try:
        llm_service.assert_model_selectable(
            model_name, previous_model_name=previous_model_name, allow_deprecated=allow_deprecated
        )
    except ModelNotSelectableError as exc:
        raise ExtendedHTTPException(code=400, message="Model not selectable", details=str(exc)) from exc


def get_authorized_conversation(conversation_id: str | None, user: User) -> Conversation | None:
    """Read selection context only from a conversation accessible to the caller."""
    conversation = Conversation.find_by_id(conversation_id) if conversation_id else None
    if conversation and not Ability(user).can(Action.READ, conversation):
        raise_forbidden("read")
    return conversation


def validate_chat_model_selection(assistant: Assistant, request: AssistantChatRequest, user: User) -> None:
    """Validate explicit overrides, retaining the authorized chat or assistant selection."""
    if not request.llm_model and not request.image_generation_model:
        return

    check_user_can_access_assistant(user, assistant, "view", Action.READ)
    previous_model = assistant.llm_model_type
    previous_image_model = assistant.image_generation_model
    conversation = get_authorized_conversation(request.conversation_id, user)
    if conversation:
        previous_model = conversation.llm_model or previous_model
        previous_image_model = conversation.image_generation_model or previous_image_model

    validate_model_selection(request.llm_model, previous_model_name=previous_model)
    validate_model_selection(request.image_generation_model, previous_model_name=previous_image_model)
