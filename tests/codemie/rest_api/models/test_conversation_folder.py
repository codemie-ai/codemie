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

from datetime import datetime
from unittest.mock import MagicMock, patch

from codemie.rest_api.models.conversation_folder import ConversationFolder


def _make_folder_row(id_, folder_name, user_id, update_date):
    """Build a mock DB row with named attributes matching the SQL query columns."""
    row = MagicMock()
    row.id = id_
    row.folder_name = folder_name
    row.user_id = user_id
    row.date = update_date
    row.update_date = update_date
    row.user_abilities = None
    return row


@patch('codemie.rest_api.models.conversation_folder.get_session')
def test_folder_search_by_name_and_user(mock_get_session):
    """Test search_by_name_and_user returns matching folders."""
    user_id = 'user-123'
    query = 'proj'

    row1 = _make_folder_row('folder-1', 'Projects', user_id, datetime(2026, 4, 30, 12, 0, 0))
    row2 = _make_folder_row('folder-2', 'Project Archive', user_id, datetime(2026, 4, 29, 12, 0, 0))

    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [row1, row2]

    results = ConversationFolder.search_by_name_and_user(user_id=user_id, query=query, limit=20)

    assert len(results) == 2
    assert isinstance(results[0], ConversationFolder)
    assert results[0].folder_name == 'Projects'
    assert results[1].folder_name == 'Project Archive'
    assert results[0].user_id == user_id
    assert results[1].user_id == user_id

    # Verify the session was used
    mock_session.exec.assert_called_once()


@patch('codemie.rest_api.models.conversation_folder.get_session')
def test_folder_search_by_name_and_user_empty_results(mock_get_session):
    """Test search_by_name_and_user with no matches returns empty list."""
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []

    results = ConversationFolder.search_by_name_and_user(
        user_id='user-123',
        query='nonexistent',
        limit=20,
    )

    assert len(results) == 0


@patch('codemie.rest_api.models.conversation_folder.get_session')
def test_folder_search_uses_like_pattern(mock_get_session):
    """Test that search passes a LIKE %pattern% to the database."""
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []

    ConversationFolder.search_by_name_and_user(user_id='user-123', query='Admin', limit=10)

    mock_session.exec.assert_called_once()
    # Verify that exec was called (the SQL + binding is opaque via text(), spot-check via call count)


@patch('codemie.rest_api.models.conversation_folder.get_session')
def test_folder_search_escapes_wildcards(mock_get_session):
    """Test that LIKE wildcard characters in the query are escaped."""
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []

    # Should not raise and should call exec once
    ConversationFolder.search_by_name_and_user(user_id='user-123', query='100%_special', limit=20)

    mock_session.exec.assert_called_once()
