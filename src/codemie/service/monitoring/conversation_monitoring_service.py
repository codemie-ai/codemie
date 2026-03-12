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

from typing import Optional

from codemie.core.models import TokensUsage
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.base import ConversationStatus
from codemie.rest_api.models.feedback import MarkEnum
from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes


class ConversationMonitoringService(BaseMonitoringService):
    CONVERSATION_BASE_METRIC = "conversation"

    @classmethod
    def send_view_mode_metric(
        cls,
        mode: str,
        user_id: str,
        user_name: str,
        additional_attributes: Optional[dict] = None,
    ):
        attributes = cls._build_conversation_attributes(
            True,
            user_id,
            user_name,
        )
        if additional_attributes:
            attributes.update(additional_attributes)
        cls.send_count_metric(name=f"{cls.CONVERSATION_BASE_METRIC}_{mode}_view_switch", attributes=attributes)

    @classmethod
    def send_conversation_metric(
        cls,
        user: User,
        assistant: Assistant,
        tokens_usage: TokensUsage,
        time_elapsed: float,
        conversation_id: str,
        llm_model: str,
        status: ConversationStatus,
    ):
        attributes = {
            MetricsAttributes.USER_ID: user.id,
            MetricsAttributes.USER_NAME: user.name,
            MetricsAttributes.USER_EMAIL: user.username,
            MetricsAttributes.ASSISTANT_ID: assistant.id,
            MetricsAttributes.ASSISTANT_NAME: assistant.name,
            MetricsAttributes.INPUT_TOKENS: tokens_usage.input_tokens,
            MetricsAttributes.OUTPUT_TOKENS: tokens_usage.output_tokens,
            MetricsAttributes.CACHE_READ_INPUT_TOKENS: tokens_usage.cached_tokens,
            MetricsAttributes.MONEY_SPENT: tokens_usage.money_spent,
            MetricsAttributes.CACHED_TOKENS_MONEY_SPENT: tokens_usage.cached_tokens_money_spent,
            MetricsAttributes.PROJECT: assistant.project,
            MetricsAttributes.EXECUTION_TIME: time_elapsed,
            MetricsAttributes.LLM_MODEL: llm_model,
            MetricsAttributes.CONVERSATION_ID: conversation_id,
            MetricsAttributes.STATUS: status.value,
        }

        cls.send_count_metric(name=cls.CONVERSATION_BASE_METRIC + "_assistant_usage", attributes=attributes)

    @classmethod
    def send_share_conversation_metric(
        cls,
        user: User,
        conversation_id: str,
        action: str,
        success: bool = True,
    ):
        attributes = {
            MetricsAttributes.USER_ID: user.id,
            MetricsAttributes.USER_NAME: user.name,
            MetricsAttributes.USER_EMAIL: user.username,
            MetricsAttributes.CONVERSATION_ID: conversation_id,
            MetricsAttributes.STATUS: "success" if success else "error",
        }
        cls.send_count_metric(name=cls.CONVERSATION_BASE_METRIC + f"_share_{action}", attributes=attributes)

    @classmethod
    def send_feedback_metric(
        cls,
        conversation_id: str,
        assistant_id: str,
        mark: MarkEnum,
        message_index: int,
        feedback_id: Optional[str],
        comments: Optional[str],
        user: User,
        request_type: Optional[str] = None,
        app_name: Optional[str] = None,
        repo_name: Optional[str] = None,
        index_type: Optional[str] = None,
    ):
        """
        Sends metrics when a user provides feedback (mark) on a conversation.

        Args:
            conversation_id: ID of the conversation being evaluated
            assistant_id: ID of the assistant that handled the conversation
            mark: Feedback rating (correct, partially correct, wrong)
            message_index: Index of the message being rated
            feedback_id: UUID of the feedback entry
            comments: Optional text comments provided with feedback
            user: The user who provided the feedback
            request_type: Optional request type
            app_name: Optional application name
            repo_name: Optional repository name
            index_type: Optional datasource index type
        """
        attributes = {
            MetricsAttributes.USER_ID: user.id,
            MetricsAttributes.USER_NAME: user.name,
            MetricsAttributes.USER_EMAIL: user.username,
            MetricsAttributes.CONVERSATION_ID: conversation_id,
            MetricsAttributes.ASSISTANT_ID: assistant_id,
            "mark": mark.value,
            "message_index": message_index,
            "feedback_id": feedback_id,
            "comments": comments,
            "comments_provided": bool(comments),
        }

        if request_type:
            attributes["request_type"] = request_type

        if app_name:
            attributes["app_name"] = app_name

        if repo_name:
            attributes[MetricsAttributes.REPO_NAME] = repo_name

        if index_type:
            attributes[MetricsAttributes.DATASOURCE_TYPE] = index_type
        cls.send_count_metric(name=cls.CONVERSATION_BASE_METRIC + "_feedback", attributes=attributes)

    @classmethod
    def send_feedback_delete_metric(
        cls, conversation_id: str, assistant_id: str, feedback_id: str, message_index: int, user: User
    ):
        """
        Sends metrics when a user deletes feedback for a conversation.

        Args:
            conversation_id: ID of the conversation for which feedback was deleted
            assistant_id: ID of the assistant that handled the conversation
            feedback_id: UUID of the feedback being deleted
            message_index: Index of the message from which feedback was removed
            user: The user who deleted the feedback
        """
        attributes = {
            MetricsAttributes.USER_ID: user.id,
            MetricsAttributes.USER_NAME: user.name,
            MetricsAttributes.CONVERSATION_ID: conversation_id,
            MetricsAttributes.ASSISTANT_ID: assistant_id,
            "feedback_id": feedback_id,
            "message_index": message_index,
            "action": "delete",
        }

        cls.send_count_metric(name=cls.CONVERSATION_BASE_METRIC + "_feedback_delete", attributes=attributes)

    @classmethod
    def _build_conversation_attributes(cls, success: bool, user_id, user_name, error: Optional[str] = None):
        return {
            MetricsAttributes.USER_ID: user_id,
            MetricsAttributes.USER_NAME: user_name,
            MetricsAttributes.STATUS: "success" if success else "error",
            MetricsAttributes.ERROR: "" if success else error,
        }
