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

import pytest
from unittest.mock import patch, MagicMock

from codemie.core.models import CreatedByUser
from codemie.rest_api.models.permission import ResourceType
from codemie.rest_api.models.index import IndexInfo


class TestResourceType:
    @pytest.fixture
    def mock_index_info(self):
        return IndexInfo(
            project_name="project",
            repo_name="repo",
            embeddings_model="model",
            current_state=10,
            complete_state=10,
            index_type="code",
            created_by=CreatedByUser(username="test_user", id="123"),
        )

    def test_resource_class(self):
        assert ResourceType.DATASOURCE.resource_class == IndexInfo

    def test_for_instance(self, mock_index_info):
        assert ResourceType.type_for_instance(mock_index_info) == ResourceType.DATASOURCE

    def test_for_instance_not_found(self):
        instance = "SomeString"

        with pytest.raises(ValueError):
            ResourceType.type_for_instance(instance)


class Permission:
    @patch("codemie.rest_api.models.permission.Permission.exists_for", return_value=True)
    def test_exists_for_true(self, _mock_exists_for, mock_index_info):
        assert Permission.exists_for(user=mock_index_info.created_by, instance=mock_index_info, action="read") is True

    @patch("codemie.rest_api.models.permission.Permission.exists_for", return_value=False)
    def test_exists_for_false(self, _mock_exists_for, mock_index_info):
        assert Permission.exists_for(user=mock_index_info.created_by, instance=mock_index_info, action="read") is False

    @patch("codemie.rest_api.models.permission.Permission.exists_for", return_value=False)
    def test_exists_for_false_invalid_resource(self, _mock_exists_for, mock_index_info):
        assert Permission.exists_for(user=mock_index_info.created_by, instance=MagicMock(), action="read") is False
