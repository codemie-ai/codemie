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

"""Per-call-site real-session SQLite tests for ``Conversation.update(columns=...)`` in the routers.

Each test seeds a real ``Conversation`` row on the shared in-memory SQLite harness
(``conversation_sqlite_engine`` / ``conversation_update_sql`` from tests/codemie/conftest.py),
calls the real route handler function (mocking only auth/unrelated collaborators), and asserts
exactly one UPDATE (no pre-read SELECT) plus the expected persisted columns.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.base import ConversationStatus
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.rest_api.models.standard import AuthorEnum, FinalFeedbackRequest, MarkEnum
from codemie.rest_api.routers.admin import update_conversation_final_feedback
from codemie.rest_api.routers.conversation import abort_conversation_generation
from codemie.rest_api.routers.feedback import final_feedback
from codemie.rest_api.security.user import User

_OLD_DATE = datetime(2020, 1, 1, 12, 0, 0)


def _seed(**fields) -> Conversation:
    conv = Conversation(
        id=fields.pop("id", "conv-1"),
        conversation_id=fields.pop("conversation_id", "conv-1"),
        conversation_name=fields.pop("conversation_name", "original"),
        user_id=fields.pop("user_id", "user-1"),
        user_name=fields.pop("user_name", "Test User"),
        date=_OLD_DATE,
        update_date=_OLD_DATE,
        **fields,
    )
    conv.save()
    return conv


def _kinds(statements: list[str]) -> list[str]:
    return [s.strip().split()[0].upper() for s in statements]


def _mock_user(id_: str = "user-1", name: str = "Test User") -> User:
    return User(id=id_, name=name)


@patch("codemie.rest_api.routers.conversation.GenerationManager")
@patch("codemie.rest_api.routers.conversation.Ability")
def test_abort_marks_last_message_interrupted(
    mock_ability, mock_generation_manager, conversation_sqlite_engine, conversation_update_sql
):
    _seed(history=[GeneratedMessage(history_index=0, message="hi", role="Assistant", in_progress=True)])

    ability_instance = MagicMock()
    ability_instance.can.return_value = True
    mock_ability.return_value = ability_instance
    mock_generation_manager.return_value.abort.return_value = True

    abort_conversation_generation("conv-1", user=_mock_user())

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "history" in statement

    stored = Conversation.find_by_id("conv-1")
    assert stored.history[-1].in_progress is False
    assert stored.history[-1].status == ConversationStatus.INTERRUPTED.value


@patch("codemie.rest_api.routers.feedback.ConversationMonitoringService")
@pytest.mark.asyncio
async def test_final_feedback_persists_final_user_mark(
    mock_monitoring_service, conversation_sqlite_engine, conversation_update_sql
):
    _seed()
    request = FinalFeedbackRequest(author=AuthorEnum.USER, mark=MarkEnum.CORRECT, comments="great")

    await final_feedback("conv-1", request, user=_mock_user())

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "final_user_mark" in statement

    stored = Conversation.find_by_id("conv-1")
    assert stored.final_user_mark.mark == MarkEnum.CORRECT
    assert stored.final_user_mark.comments == "great"
    mock_monitoring_service.send_final_feedback_metric.assert_called_once()


@pytest.mark.asyncio
async def test_update_conversation_final_feedback_persists_final_operator_mark(
    conversation_sqlite_engine, conversation_update_sql
):
    _seed()
    request = FinalFeedbackRequest(author=AuthorEnum.OPERATOR, mark=MarkEnum.WRONG, comments="needs review")
    admin = _mock_user(id_="admin-1", name="Admin User")

    result = await update_conversation_final_feedback("user-1", "conv-1", request, admin=admin)

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "final_operator_mark" in statement

    stored = Conversation.find_by_id("conv-1")
    assert stored.final_operator_mark.mark == MarkEnum.WRONG
    assert stored.final_operator_mark.operator.user_id == "admin-1"
    assert result.final_operator_mark.mark == MarkEnum.WRONG
