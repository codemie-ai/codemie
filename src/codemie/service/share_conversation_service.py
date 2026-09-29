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

import re

from typing import Optional, Dict, Any
from datetime import datetime
from urllib.parse import quote

from codemie_tools.base.models import Tool
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.conversation import Conversation, AssistantDetails
from codemie.rest_api.models.share.shared_conversation import SharedConversation
from codemie.rest_api.security.user import User
from codemie.configs import logger
from codemie.service.monitoring.conversation_monitoring_service import ConversationMonitoringService


SANDBOX_FILE_PREFIX = "sandbox:/v1/files/"

# Mirrors SANDBOX_FILE_RE in codemie.agents.tools.agent: matches a whole sandbox file URL
# so the share token can be appended to it without disturbing surrounding markdown.
_SANDBOX_FILE_RE = re.compile(r"sandbox:/v1/files/[^\s)\]>\"']+")


def collect_message_file_tokens(message: Any) -> list[str]:
    """Return every encoded file token one history message refers to.

    Two carriers: `file_names`, which holds bare encoded tokens, and inline
    `sandbox:/v1/files/<token>` URLs inside the message text. The share endpoint issues a grant for
    each token this returns and the file endpoint authorizes against the same set, so both sides
    must derive it from this one function — a substring match on the message text would instead
    grant a token that merely prefixes a longer one.
    """
    tokens = list(getattr(message, "file_names", None) or [])

    text = getattr(message, "message", None)
    if isinstance(text, str) and SANDBOX_FILE_PREFIX in text:
        tokens.extend(match[len(SANDBOX_FILE_PREFIX) :] for match in _SANDBOX_FILE_RE.findall(text))

    return tokens


class ShareConversationService:
    """
    Service class for handling conversation sharing functionality.
    """

    @classmethod
    def share_conversation(cls, conversation: Conversation, user: User) -> Dict[str, Any]:
        """
        Create a share for a conversation.
        If a share already exists for this conversation, return that instead.
        """

        # Check if a share already exists
        existing_share = SharedConversation.get_by_fields({"conversation_id.keyword": conversation.id})
        if existing_share:
            return {
                "share_id": existing_share.share_id,
                "token": existing_share.share_token,
                "created_at": existing_share.created_at.isoformat(),
                "access_count": existing_share.access_count,
            }

        # Create new share
        share_token = SharedConversation.generate_share_token()
        share_id = f"share_{conversation.id}"

        shared_conversation = SharedConversation(
            id=share_id,
            share_id=share_id,
            conversation_id=conversation.id,
            shared_by_user_id=user.id,
            shared_by_user_name=user.name,
            created_at=datetime.now(),
            access_count=0,
            share_token=share_token,
        )

        shared_conversation.save(refresh=True)
        ConversationMonitoringService.send_share_conversation_metric(
            user=user,
            conversation_id=conversation.id,
            action="create",
        )
        return {
            "share_id": share_id,
            "token": share_token,
            "created_at": shared_conversation.created_at.isoformat(),
            "access_count": 0,
        }

    @classmethod
    def get_shared_conversation(cls, token: str, user: User) -> Optional[Dict[str, Any]]:
        """
        Retrieve a conversation by its share token.
        """
        shared = SharedConversation.get_by_fields({"share_token.keyword": token})
        if not shared:
            logger.warning(f"Attempted to access non-existent share with token: {token}")
            ConversationMonitoringService.send_share_conversation_metric(
                user=user,
                conversation_id="",
                action="create",
                success=False,
            )
            return None

        conversation = Conversation.find_by_id(shared.conversation_id)
        if not conversation:
            logger.warning(f"Shared conversation not found: {shared.conversation_id}")
            ConversationMonitoringService.send_share_conversation_metric(
                user=user,
                conversation_id="unknown",
                action="create",
                success=False,
            )
            return None

        assistants = Assistant.get_by_ids(ids=conversation.assistant_ids, user=user)

        conversation.assistant_data = [
            AssistantDetails(
                assistant_id=a.id,
                assistant_name=a.name,
                assistant_icon=a.icon_url,
                context=a.context,
                conversation_starters=a.conversation_starters,
                tools=[Tool(name=tool.name, label=tool.label) for toolkit in a.toolkits for tool in toolkit.tools],
            )
            for a in assistants
        ]

        shared_file_urls = cls._build_shared_file_urls(conversation, token)

        # Increment the access counter
        shared.increment_access_count()
        ConversationMonitoringService.send_share_conversation_metric(
            user=user,
            conversation_id=conversation.id,
            action="get",
        )

        # Return the conversation data with sharing metadata
        return {
            "conversation": conversation,
            "shared_by": shared.shared_by_user_name,
            "created_at": shared.created_at,
            "access_count": shared.access_count,
            "share_token": token,
            "shared_file_urls": shared_file_urls,
        }

    @staticmethod
    def _append_share_token(url: str, share_token: str) -> str:
        """Append the share token to a file URL or bare encoded file token."""
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}share_token={quote(share_token, safe='')}"

    @classmethod
    def _build_shared_file_urls(cls, conversation: Conversation, share_token: str) -> Dict[str, str]:
        """Map every file token in the conversation to the same token carrying the share grant.

        The conversation itself is returned untouched: `file_names` and message text keep holding
        bare encoded tokens, which is what `_process_file_names_to_objects`, the export flow and
        API consumers decode. A share recipient renders files through this map instead, appending
        `?share_token=...` so `GET /v1/files/{file_name}` can authorize the download.
        """
        history = getattr(conversation, "history", None)
        if not isinstance(history, list):
            return {}

        return {
            token: cls._append_share_token(token, share_token)
            for message in history
            for token in collect_message_file_tokens(message)
        }
