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
import sqlalchemy as sa

from codemie.rest_api.models.permission import Permission as PermissionModel, ResourceType
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


def test_resource_type_assistant_value():
    assert ResourceType.ASSISTANT == "assistant"


def test_type_for_instance_refuses_an_assistant(mocker):
    """ASSISTANT is a named-grant type now: it has no APPLICATION permissions, so the lookup does not list it."""
    import pytest

    from codemie.rest_api.models.assistant import Assistant

    assistant = mocker.MagicMock(spec=Assistant)
    with pytest.raises(ValueError):
        ResourceType.type_for_instance(assistant)


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


class TestHostedApplicationStorage:
    def test_resource_type_member(self):
        assert ResourceType.HOSTED_APPLICATION == "hosted_application"

    def test_source_columns_nullable_uuid(self):
        columns = PermissionModel.__table__.c
        assert columns.grant_source.nullable is True
        assert columns.source_link_id.nullable is True
        assert isinstance(columns.source_link_id.type, sa.Uuid)

    def test_grant_source_check_constraint(self):
        checks = [c for c in PermissionModel.__table__.constraints if isinstance(c, sa.CheckConstraint)]
        texts = [str(c.sqltext) for c in checks]
        match = [t for t in texts if "grant_source" in t and "source_link_id" in t]
        assert match, texts
        assert "'name'" in match[0] and "'link'" in match[0]

    def test_enum_columns_use_codemie_schema(self):
        columns = PermissionModel.__table__.c
        for name in ("resource_type", "principal_type", "permission_level"):
            assert isinstance(columns[name].type, sa.Enum)
            assert columns[name].type.schema == "codemie"
