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

from typing import List, Optional

from codemie_tools.base.models import Tool
from fastapi import APIRouter, Depends, status, Query

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import Assistant
from codemie.core.workflow_models import WorkflowConfig
from codemie.rest_api.models.conversation import (
    Conversation,
    AssistantDetails,
    ConversationHistoryPaginationData,
    ConversationResponse,
    ConversationSearchResponse,
)
from codemie.rest_api.models.index import SortOrder
from codemie.rest_api.routers.feedback import CONVERSATION_NOT_FOUND_MESSAGE, CONVERSATION_NOT_FOUND_HELP
from codemie.rest_api.routers.utils import raise_access_denied
from codemie.utils.datetime_utils import get_timestamp_bounds
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.assistant.tool_output_visibility import strip_history_for_conversation
from codemie.service.conversation_service import ConversationService
from codemie.service.constants import (
    DEFAULT_HISTORY_ITEMS_PER_PAGE,
    MAX_HISTORY_ITEMS_PER_PAGE,
    DEFAULT_PAGE,
)


router = APIRouter()


def _use_global_models_for_conversation(conversation: Conversation, user: User) -> bool:
    """A marketplace asset's owning project does not constrain the chat model picker."""
    asset_id = conversation.initial_assistant_id
    if not asset_id:
        return False
    if conversation.is_workflow_conversation:
        try:
            return bool(WorkflowConfig.get_by_id(asset_id).is_global)
        except KeyError:
            return False
    assistants = Assistant.get_by_ids(ids=[asset_id], user=user)
    return bool(assistants and assistants[0].is_global)


@router.get('/conversations/search', response_model=ConversationSearchResponse)
def search_conversations(
    query: str = Query(..., min_length=3, max_length=100),
    user: User = Depends(authenticate),
) -> ConversationSearchResponse:
    """
    Search assistants visible to the user plus the user's conversations and folders by name.

    Case-insensitive partial matching on name/folder_name fields.
    Limit: 20 results per entity type.
    """
    try:
        return ConversationService.search_conversations(user, query)

    except ExtendedHTTPException:
        raise
    except Exception as e:
        logger.error(f'Search error: {e}')
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message='Search service unavailable',
            details='An error occurred while searching conversations.',
            help='Please try again later.',
        )


def _enrich_conv_with_workflow(
    conversation: Conversation, conversation_id: str
) -> tuple[Conversation, Optional[WorkflowConfig]]:
    """Populate workflow assistant details for workflow-based conversations.

    Args:
        conversation: Conversation entity to enrich with assistant metadata.
        conversation_id: Conversation identifier.

    Returns:
        The same conversation instance with `assistant_data` populated from workflow
        config, or fallback data when workflow config is missing, alongside the
        fetched WorkflowConfig (or None if it no longer exists) so callers don't
        need to fetch it again.
    """
    try:
        workflow = WorkflowConfig.get_by_id(conversation.initial_assistant_id)
        conversation.assistant_data = [
            AssistantDetails(
                assistant_id=workflow.id,
                assistant_name=workflow.name,
                assistant_icon=workflow.icon_url,
                assistant_type=None,
                context=None,
                tools=None,
                conversation_starters=[],
            )
        ]
    except KeyError:
        logger.warning(
            f"Workflow {conversation.initial_assistant_id} not found for conversation {conversation_id}. "
            f"This is expected if the workflow was deleted. Using fallback data from conversation history."
        )
        conversation.assistant_data = [
            AssistantDetails(
                assistant_id=conversation.initial_assistant_id,
                conversation_starters=[],
            )
        ]
        workflow = None
    return conversation, workflow


def _is_pagination_required(page: int | None, per_page: int | None, sort_order: SortOrder | None) -> bool:
    return page is not None or per_page is not None or sort_order is not None


@router.get(
    "/conversations/new",
    response_model=ConversationResponse,
)
def get_conversation_template(
    initial_assistant_id: Optional[str] = Query(None),
    is_workflow: Optional[bool] = Query(False),
    folder: Optional[str] = Query(None),
    user: User = Depends(authenticate),
) -> ConversationResponse:
    """Get a new conversation payload.

    Args:
        initial_assistant_id: Assistant/workflow id to generate conversation data. Optional.
        is_workflow: If true, treat initial_assistant_id as a WorkflowConfig id. Optional.
        folder: Chat folder name. Optional.
    """

    template = ConversationService.build_new_conversation(
        user=user,
        initial_assistant_id=initial_assistant_id,
        is_workflow=bool(is_workflow),
        folder=folder,
    )

    response = ConversationResponse.model_validate(template)
    if _use_global_models_for_conversation(template, user):
        response.project = None
    return response


def _resolve_conversation_page(
    conversation_id: str,
    page: Optional[int],
    per_page: Optional[int],
    sort_order: Optional[SortOrder],
):
    """Resolve the conversation (with optional history pagination) and its timestamp bounds."""
    if not _is_pagination_required(page, per_page, sort_order):
        conversation = Conversation.find_by_id(conversation_id)
        very_first_msg_at, very_last_msg_at = (
            get_timestamp_bounds(conversation.history) if conversation else (None, None)
        )
        return conversation, None, very_first_msg_at, very_last_msg_at

    page_val = DEFAULT_PAGE if page is None else page
    per_page_val = DEFAULT_HISTORY_ITEMS_PER_PAGE if per_page is None else per_page
    conversation, total, very_first_msg_at, very_last_msg_at = ConversationService.get_conversation_history_slice(
        conversation_id=conversation_id,
        page=page_val,
        per_page=per_page_val,
        sort_order=sort_order,
    )
    offset = page_val * per_page_val
    pages = (total + per_page_val - 1) // per_page_val if per_page_val > 0 else 0
    pagination_data = ConversationHistoryPaginationData(
        page=page_val,
        per_page=per_page_val,
        total=total,
        pages=pages,
        has_next=offset + per_page_val < total,
        has_previous=page_val > 0,
    )
    return conversation, pagination_data, very_first_msg_at, very_last_msg_at


def _enrich_conversation_with_assistant_or_workflow_data(
    conversation: Conversation, conversation_id: str, user: User
) -> Optional[WorkflowConfig]:
    """Populate assistant/workflow-derived fields on the conversation. Returns the workflow, if any."""
    if conversation.is_workflow_conversation:
        _, workflow = _enrich_conv_with_workflow(conversation, conversation_id)
        return workflow

    assistants = Assistant.get_by_ids(ids=conversation.assistant_ids, user=user)
    conversation.assistant_data = [
        AssistantDetails(
            assistant_id=a.id,
            assistant_type=a.type,
            assistant_name=a.name,
            assistant_icon=a.icon_url,
            context=a.context,
            conversation_starters=a.conversation_starters,
            hide_tool_outputs=a.hide_tool_outputs,
            tools=[Tool(name=tool.name, label=tool.label) for toolkit in a.toolkits for tool in toolkit.tools],
        )
        for a in assistants
    ]
    return None


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
)
def get_conversation_by_id(
    conversation_id: str,
    user: User = Depends(authenticate),
    page: Optional[int] = Query(None, ge=0),
    per_page: Optional[int] = Query(None, ge=1, le=MAX_HISTORY_ITEMS_PER_PAGE),
    sort_order: Optional[SortOrder] = Query(None),
) -> ConversationResponse:
    """
    Get a conversation document by provided conversation id.
    Handles both assistant conversations and workflow conversations.

    Optional pagination/sorting parameters:
    - page: 0-based page index. Default 0 when per_page is provided.
    - per_page: Items per history page. Default 20 when page is provided.
    - sort_order: 'asc' or 'desc' by message date. Omit to keep insertion order.

    When no new params are provided, response is identical to before (backward compatible).
    """
    conversation, pagination_data, very_first_msg_at, very_last_msg_at = _resolve_conversation_page(
        conversation_id, page, per_page, sort_order
    )

    if not conversation:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=CONVERSATION_NOT_FOUND_MESSAGE,
            details=f"The conversation with ID [{conversation_id}] could not be found in the system.",
            help=CONVERSATION_NOT_FOUND_HELP,
        )

    if not Ability(user).can(Action.READ, conversation):
        raise_access_denied("view")

    workflow = _enrich_conversation_with_assistant_or_workflow_data(conversation, conversation_id, user)

    conversation.conversation_name = conversation.get_conversation_name()

    response = ConversationResponse.model_validate(conversation)
    if conversation.is_workflow_conversation:
        is_global = bool(workflow and workflow.is_global)
    else:
        is_global = _use_global_models_for_conversation(conversation, user)
    if is_global:
        response.project = None
    response.history = strip_history_for_conversation(conversation)

    response.pagination = pagination_data
    response.very_first_msg_at = very_first_msg_at
    response.very_last_msg_at = very_last_msg_at

    return response


@router.get(
    "/conversations/{conversation_id}/files",
    response_model=List[str],
)
def get_conversation_files(conversation_id: str, user: User = Depends(authenticate)) -> list[str]:
    """
    Get a list of files attached to the conversation.
    """
    conversation = Conversation.get_by_id(conversation_id)

    if not Ability(user).can(Action.READ, conversation):
        raise_access_denied("view")

    if not conversation:
        logger.info(f"Conversation with given id {conversation_id} is not found")
        return []

    files = []
    if conversation.history:
        files = set().union(*(message.file_names or [] for message in conversation.history if message.file_names))

    return files
