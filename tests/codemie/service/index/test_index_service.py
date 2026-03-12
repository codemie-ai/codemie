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
from unittest.mock import MagicMock, patch

from codemie.rest_api.models.index import IndexListItem, IndexInfo
from codemie.service.index.index_service import IndexStatusService
from codemie.service.filter.filter_models import IndexInfoStatus


@pytest.fixture
def mock_admin_user():
    user = MagicMock()
    user.is_admin = True
    return user


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.is_admin = False
    return user


@pytest.fixture
def mock_index_info():
    return IndexInfo(
        id="62028826-c9e5-4f9b-85df-fffccc271ee7",
        project_name="demo",
        description="dreerrerterte",
        repo_name="ddddd",
        index_type="code",
        embeddings_model="ada-002",
        current_state=12,
        complete_state=12,
        current__chunks_state=21,
        processed_files=[],
        error=False,
        completed=True,
        full_name="62028826-c9e5-4f9b-85df-fffccc271ee7-demo-ddddd-code",
        created_by={"id": "dev-codemie-user", "username": "", "name": ""},
        project_space_visible=True,
        branch="master",
        link="https://your-git.example.com/project",
        files_filter=".json",
    )


@pytest.fixture
def mock_provider_index_info():
    return IndexInfo(
        id="62028826-c9e5-4f9b-85df-fffccc271ee7",
        project_name="demo",
        description="dreerrerterte",
        repo_name="ddddd",
        index_type="code",
        embeddings_model="ada-002",
        current_state=12,
        complete_state=12,
        current__chunks_state=21,
        processed_files=[],
        error=False,
        completed=True,
        full_name="62028826-c9e5-4f9b-85df-fffccc271ee7-demo-ddddd-code",
        created_by={"id": "dev-codemie-user", "username": "", "name": ""},
        project_space_visible=True,
        branch="master",
        link="https://your-git.example.com/project",
        files_filter=".json",
        provider_fields={
            "toolkit_id": "9d24ddda-c1e1-43b2-88b1-f5b342a42d39",
            "base_params": {"datasource_id": "80f41e62-2146-4263-89b8-e13f1750cd30"},
            "provider_id": "cabe4c4b-5dfd-4dc1-a3b9-b92fcebda12e",
        },
    )


@patch("codemie.service.index.index_service.get_provider_id")
@patch("codemie.rest_api.models.permission.Permission.exists_for")
@patch("codemie.service.index.index_service.Session")
def test_index_service_run_visible_to_admin_user(
    mock_session_class,
    mock_permission_exists,
    mock_get_provider_id,
    mock_index_info,
    mock_admin_user,
):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_index_info]
    mock_session.exec.return_value.one.return_value = 1
    mock_permission_exists.return_value = False
    mock_get_provider_id.return_value = None

    result = IndexStatusService.get_index_info_list(mock_admin_user)

    assert isinstance(result["data"][0], IndexListItem)
    assert len(result["data"]) == 1
    assert result["pagination"] == {"page": 0, "per_page": 10000, "total": 1, "pages": 1}
    mock_session_class.assert_called_once_with(IndexInfo.get_engine())


@patch("codemie.rest_api.models.permission.Permission.exists_for")
@patch("codemie.service.index.index_service.Session")
@patch("codemie.service.index.index_service.get_provider_id")
def test_index_service_run_with_aice_provider_index(
    mock_provider_id,
    mock_session_class,
    mock_permission_exists,
    mock_index_info,
    mock_provider_index_info,
    mock_admin_user,
):
    mock_session = MagicMock()
    mock_provider_id.return_value = "cabe4c4b-5dfd-4dc1-a3b9-b92fcebda12e"
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_index_info, mock_provider_index_info]
    mock_session.exec.return_value.one.return_value = 2
    mock_permission_exists.return_value = False

    result = IndexStatusService.get_index_info_list(mock_admin_user)

    assert isinstance(result["data"][0], IndexListItem)
    assert len(result["data"]) == 2
    assert result["data"][0].aice_datasource_id is None
    assert result["data"][1].aice_datasource_id == "80f41e62-2146-4263-89b8-e13f1750cd30"


@patch("codemie.rest_api.models.permission.Permission.exists_for")
@patch("codemie.service.index.index_service.Session")
@patch("codemie.service.index.index_service.get_provider_id")
def test_index_service_run_visible_to_regular_user(
    mock_provider_id, mock_session_class, mock_permission_exists, mock_index_info, mock_user
):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_index_info]
    mock_session.exec.return_value.one.return_value = 1
    mock_permission_exists.return_value = False

    result = IndexStatusService.get_index_info_list(mock_user)

    assert isinstance(result["data"][0], IndexListItem)
    assert len(result["data"]) == 1
    assert result["pagination"] == {"page": 0, "per_page": 10000, "total": 1, "pages": 1}
    mock_session_class.assert_called_once_with(IndexInfo.get_engine())


@patch("codemie.rest_api.models.permission.Permission.exists_for")
@patch("codemie.service.index.index_service.Session")
@patch("codemie.service.index.index_service.get_provider_id")
def test_index_service_with_filters(
    mock_provider_id,
    mock_session_class,
    mock_permission_exists,
    mock_index_info,
    mock_admin_user,
):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_index_info]
    mock_session.exec.return_value.one.return_value = 1

    filters = {
        "date_range": {"start_date": "2024-01-01", "end_date": "2024-12-31"},
        "index_status": IndexInfoStatus.COMPLETED.value,
    }

    result = IndexStatusService.get_index_info_list(mock_admin_user, filters=filters)

    assert isinstance(result["data"][0], IndexListItem)
    assert len(result["data"]) == 1
    assert not result["data"][0].error
    assert result["data"][0].completed
    assert result["pagination"] == {"page": 0, "per_page": 10000, "total": 1, "pages": 1}
    mock_session_class.assert_called_once_with(IndexInfo.get_engine())


@patch("codemie.rest_api.models.permission.Permission.exists_for")
@patch("codemie.service.index.index_service.Session")
def test_index_service_full_reponse(mock_session_class, mock_permission_exists, mock_index_info, mock_admin_user):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_index_info]
    mock_session.exec.return_value.one.return_value = 1

    result = IndexStatusService.get_index_info_list(mock_admin_user, filters={}, full_response=True)

    assert isinstance(result["data"][0], IndexInfo)

    assert result["data"][0].branch == "master"
    assert result["data"][0].link == "https://your-git.example.com/project"


@patch("codemie.service.index.index_service.Session")
def test_get_users_as_admin(mock_session_class, mock_admin_user):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [{"id": "user1", "username": "User One", "name": "User One"}]

    result = IndexStatusService.get_users(mock_admin_user)

    assert len(result) == 1
    assert result[0]["id"] == "user1"
    assert result[0]["username"] == "User One"
    mock_session_class.assert_called_once_with(IndexInfo.get_engine())


@patch("codemie.service.index.index_service.Session")
def test_get_users_as_user(mock_session_class, mock_user):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [{"id": "user1", "username": "User One", "name": "User One"}]

    result = IndexStatusService.get_users(mock_user)

    assert len(result) == 1
    assert result[0]["id"] == "user1"
    assert result[0]["username"] == "User One"
    mock_session_class.assert_called_once_with(IndexInfo.get_engine())
