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

from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from codemie.rest_api.models.feedback import FeedbackEntry

USER_ID = "owner-user-id"


def _compile(statement):
    compiled = statement.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


@patch("codemie.rest_api.models.feedback.Session")
@patch("codemie.clients.postgres.PostgresClient.get_engine")
def test_get_all_for_user_filters_by_conversation_owner(mock_get_engine, mock_session_class):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    entries = [MagicMock(), MagicMock()]
    mock_session.exec.return_value.all.return_value = entries

    result = FeedbackEntry.get_all_for_user(USER_ID)

    sql, params = _compile(mock_session.exec.call_args[0][0])
    assert result == entries
    assert "JOIN conversations" in sql
    assert "CAST(feedback.conversation_id AS VARCHAR) = conversations.id" in sql
    assert "conversations.user_id = " in sql
    assert "ORDER BY feedback.update_date DESC" in sql
    assert "LIMIT" in sql
    assert USER_ID in params.values()
    assert 10_000 in params.values()


@patch("codemie.rest_api.models.feedback.Session")
@patch("codemie.clients.postgres.PostgresClient.get_engine")
def test_get_all_for_user_does_not_cast_conversation_id_to_uuid(mock_get_engine, mock_session_class):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []

    FeedbackEntry.get_all_for_user(USER_ID)

    sql, _ = _compile(mock_session.exec.call_args[0][0])
    assert "CAST(conversations.id" not in sql


@patch("codemie.rest_api.models.feedback.Session")
@patch("codemie.clients.postgres.PostgresClient.get_engine")
def test_find_by_id_for_user_applies_owner_and_id_filters(mock_get_engine, mock_session_class):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    entry = MagicMock()
    mock_session.exec.return_value.first.return_value = entry

    result = FeedbackEntry.find_by_id_for_user("feedback-id", USER_ID)

    sql, params = _compile(mock_session.exec.call_args[0][0])
    assert result is entry
    assert "JOIN conversations" in sql
    assert "CAST(feedback.conversation_id AS VARCHAR) = conversations.id" in sql
    assert "conversations.user_id = " in sql
    assert "feedback.id = " in sql
    assert USER_ID in params.values()
    assert "feedback-id" in params.values()


@patch("codemie.rest_api.models.feedback.Session")
@patch("codemie.clients.postgres.PostgresClient.get_engine")
def test_find_by_id_for_user_returns_none_when_not_found(mock_get_engine, mock_session_class):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.first.return_value = None

    assert FeedbackEntry.find_by_id_for_user("feedback-id", USER_ID) is None
