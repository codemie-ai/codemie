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
from codemie.rest_api.models.conversation import SearchResultItem, ConversationSearchResponse


def test_search_result_item_chat():
    """Test SearchResultItem for chat type"""
    item = SearchResultItem(
        id='chat-123', name='Admin Dashboard', updated_at=datetime(2026, 4, 30, 12, 0, 0), type='chat', folder='Work'
    )

    assert item.id == 'chat-123'
    assert item.name == 'Admin Dashboard'
    assert item.type == 'chat'
    assert item.folder == 'Work'


def test_search_result_item_folder():
    """Test SearchResultItem for folder type"""
    item = SearchResultItem(id='folder-1', name='Projects', updated_at=datetime(2026, 4, 30, 12, 0, 0), type='folder')

    assert item.id == 'folder-1'
    assert item.name == 'Projects'
    assert item.type == 'folder'
    assert item.folder is None


def test_conversation_search_response():
    """Test ConversationSearchResponse with multiple items"""
    item1 = SearchResultItem(id='chat-1', name='Chat 1', updated_at=datetime(2026, 4, 30, 12, 0, 0), type='chat')
    item2 = SearchResultItem(id='folder-1', name='Folder 1', updated_at=datetime(2026, 4, 30, 11, 0, 0), type='folder')

    response = ConversationSearchResponse(items=[item1, item2])

    assert len(response.items) == 2
    assert response.items[0].type == 'chat'
    assert response.items[1].type == 'folder'
