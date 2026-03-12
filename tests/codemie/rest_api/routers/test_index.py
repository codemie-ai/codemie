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

from fastapi import status, FastAPI, Request
from fastapi.testclient import TestClient
from elasticsearch.exceptions import NotFoundError
from unittest.mock import patch, MagicMock
from typing import Callable, Generator

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import app
from codemie.rest_api.models.index import IndexKnowledgeBaseJIRARequest
from codemie.core.models import CreatedByUser
from codemie.rest_api.routers.index import router
from codemie.rest_api.routers.index import validate_json_file
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

DEMO = "demo"

app_client = TestClient(app)
app_for_client = FastAPI()
app_for_client.include_router(router)
client = TestClient(app_for_client)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def authenticated_user():
    # Create a single user instance to ensure equality checks pass
    user = User(id='test_user', username='test_user', name='test_user')

    def mock_authenticate(request: Request = None):
        if request:
            request.state.user = user
        return user

    # Override for app level client
    app.dependency_overrides[authenticate] = mock_authenticate

    # Override for router level client
    client.app.dependency_overrides[authenticate] = mock_authenticate

    yield mock_authenticate

    app.dependency_overrides = {}
    client.app.dependency_overrides = {}


@pytest.fixture
def auth_headers(authenticated_user) -> dict:
    return {"user-id": authenticated_user().id}


@pytest.fixture
def mock_jira_request():
    return IndexKnowledgeBaseJIRARequest(
        project_name="test_project",
        name="test_index",
        description="test_description",
        project_space_visible=True,
        jql="test_jql",
    )


@patch('codemie.rest_api.routers.index._index_unique_check')
@patch('codemie.rest_api.routers.index.SettingsService.get_jira_creds')
@patch('codemie.rest_api.routers.index.JiraDatasourceProcessor')
@pytest.mark.asyncio
async def test_index_knowledge_base_jira(mock_worker, mock_creds, mock_unique_check, mock_jira_request, auth_headers):
    mock_worker_instance = MagicMock()
    mock_worker_instance.started_message = "OK"
    mock_worker.return_value = mock_worker_instance

    mock_unique_check.return_value = True

    response = app_client.post("/v1/index/knowledge_base/jira", json=mock_jira_request.dict(), headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "message": f"Indexing of datasource {mock_jira_request.name} has been started in the background"
    }


@patch('codemie.rest_api.routers.index.SettingsService.get_jira_creds')
@patch('codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo')
@pytest.mark.asyncio
async def test_update_index_knowledge_base_jira(mock_filter, mock_creds, mock_jira_request, auth_headers):
    mock_filter.return_value = [MagicMock()]

    response = client.put("/v1/index/knowledge_base/jira", json=mock_jira_request.dict(), headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"message": "Edit successful"}


@patch('codemie.rest_api.routers.index.SettingsService.get_jira_creds')
@patch('codemie.rest_api.routers.index.JiraDatasourceProcessor')
@patch('codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo')
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
@pytest.mark.asyncio
async def test_full_reindex_knowledge_base_jira(
    mock_get_guardrails, mock_filter, mock_worker, mock_creds, mock_jira_request, auth_headers
):
    mock_filter.return_value = [MagicMock()]

    mock_worker_instance = MagicMock()
    mock_worker_instance.started_message = "Started"
    mock_worker.return_value = mock_worker_instance

    mock_get_guardrails.return_value = []  # No guardrails configured

    response = app_client.put(
        "/v1/index/knowledge_base/jira?full_reindex=true", json=mock_jira_request.dict(), headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json() == {
        "message": f"Indexing of datasource {mock_jira_request.name} has been started in the background"
    }


@pytest.mark.asyncio
@patch('codemie.core.ability.Ability.can')
@patch('codemie.rest_api.models.index.IndexInfo.get_by_id')
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_entity")
async def test_index_deletion(mock_remove_guardrails, mock_get_index_info, mock_can, auth_headers):
    mock_remove_guardrails.return_value = None

    mock_index_info = MagicMock()
    mock_index_info.repo_name = "test_index"
    mock_index_info.id = 1

    mock_can.return_value = True

    mock_get_index_info.return_value = mock_index_info

    expected_message = f"'Index {mock_index_info.repo_name} was deleted successfully"

    response = client.delete(f"/v1/index/{mock_index_info.id}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"message": expected_message}


@pytest.mark.asyncio
@patch('codemie.core.ability.Ability.can')
@patch('codemie.rest_api.models.index.IndexInfo.get_by_id')
async def test_index_deletion_not_found(mock_get_index_info, mock_can, auth_headers):
    mock_index_info = MagicMock()
    mock_index_info.repo_name = "test_index"
    mock_index_info.id = 1
    mock_index_info.delete.side_effect = NotFoundError(body="Index not found", meta={}, message="Index not found")

    mock_get_index_info.return_value = mock_index_info

    mock_can.return_value = True

    with pytest.raises(ExtendedHTTPException) as e:
        client.delete(f"/v1/index/{mock_index_info.id}", headers=auth_headers)

        assert e.code == 404
        assert e.message == "Index not found"
        assert e.details == f"ndex {mock_index_info.repo_name} could not be found in the system."


@pytest.mark.asyncio
@patch('codemie.core.ability.Ability.can')
@patch('codemie.rest_api.models.index.IndexInfo.get_by_id')
async def test_index_deletion_no_permissions(mock_get_index_info, mock_can, auth_headers):
    mock_can.return_value = False

    mock_index_info = MagicMock()
    mock_index_info.repo_name = "test_index"
    mock_get_index_info.return_value = mock_index_info

    with pytest.raises(ExtendedHTTPException) as e:
        client.delete("/v1/index/1", headers=auth_headers)

        assert e.code == 404
        assert e.message == "Access denied"
        assert e.details == f"You don't have permission to delete the index with ID '{mock_index_info.repo_name}'."


@pytest.mark.asyncio
@patch('codemie.service.index.index_service.IndexStatusService.get_users')
async def test_index_users(mock_get_users, auth_headers):
    mock_get_users.return_value = {"users": [CreatedByUser(id="user1", username="User One", name="User One")]}

    response = client.get("/v1/index/users", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"users": [{"id": "user1", "username": "User One", "name": "User One"}]}


@pytest.fixture
def mock_app_repo() -> Generator[MagicMock, None, None]:
    mock_application = MagicMock()
    mock_application.name = "demo"
    with (
        patch("codemie.rest_api.routers.index.Application.get_by_id", return_value=mock_application),
        patch("codemie.rest_api.routers.index.ensure_application_exists"),
    ):
        yield mock_application


@pytest.fixture
def mock_create_processor() -> Generator[MagicMock, None, None]:
    mock_processor_instance = MagicMock()
    with patch("codemie.datasource.code.code_datasource_processor.CodeDatasourceProcessor") as mocked_processor_cls:
        mocked_processor_cls.create_processor.return_value = mock_processor_instance
        mock_processor_instance.process = MagicMock()
        mock_processor_instance.reprocess = MagicMock()
        yield mocked_processor_cls


@patch('codemie.rest_api.routers.index.SettingsService.get_git_creds')
@patch('codemie.core.ability.Ability.can')
@patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
@patch('codemie.rest_api.routers.index.GitRepo.get_by_app_id')
@patch('codemie.rest_api.routers.index.index_code_datasource_in_background')
@patch('codemie.rest_api.routers.index.update_code_datasource_in_background')
@pytest.mark.parametrize(
    "client_method, endpoint_format, existing_indexes, expected_response",
    [
        (app_client.post, "/v1/application/{app_name}/index", [], 'Indexing'),
        (app_client.put, "/v1/application/{app_name}/index/{repo_name}", [MagicMock()], 'Incremental reindexing'),
    ],
    ids=("post", "put"),
)
@pytest.mark.parametrize(
    "link",
    ["   https://github.com/test/demo   ", "https://github.com/test/demo   ", "   https://github.com/test/demo"],
    ids=("both_sides_spaces", "trailing_spaces", "leading_spaces"),
)
@pytest.mark.asyncio
async def test_index_application_link_with_spaces(
    mock_update_background_processing,
    mock_background_processing,
    mock_get_git_repo,
    mock_filter,
    mock_can,
    mock_get_creds,
    client_method: Callable,
    endpoint_format: str,
    existing_indexes: list,
    expected_response: str,
    link: str,
    auth_headers: dict,
    mock_app_repo: MagicMock,
) -> None:
    # Mock git credentials validation
    mock_get_creds.return_value = MagicMock(token="test_token")
    expected_response = f"{expected_response} of datasource demo has been started in the background"
    expected_created_status_code = status.HTTP_201_CREATED
    mock_filter.return_value = existing_indexes
    m_git_repo = MagicMock()
    m_git_repo.name = DEMO
    mock_get_git_repo.return_value = [m_git_repo]
    mock_can.return_value = True
    git_repo = {
        "name": DEMO,
        "description": "example_2test",
        "link": link,
        "branch": "main",
        "token": "test_token",
        "embeddingsModel": "ada-002",
        "summarizationModel": "gpt-35-turbo",
        "projectSpaceVisible": True,
        "reindexOnEdit": "",
        "indexType": "code",
    }
    trimmed_link = "https://github.com/test/demo"
    endpoint = endpoint_format.format(app_name=DEMO, repo_name=DEMO)

    response = client_method(
        endpoint, json=git_repo, headers={**auth_headers, "X-Request-ID": "7ecd6b14-6294-429a-b51a-cab32b344984"}
    )

    assert response.status_code == expected_created_status_code
    assert expected_response == response.json()["message"]
    if client_method == app_client.post:
        mock_background_processing.assert_called_once()
        assert mock_background_processing.call_args_list[0][1]["git_repo"].link == trimmed_link
    elif client_method == app_client.put:
        mock_update_background_processing.assert_called_once()
        assert mock_update_background_processing.call_args_list[0][1]["git_repo"].link == trimmed_link


@patch('codemie.rest_api.routers.index.SettingsService.get_git_creds')
@patch('codemie.core.ability.Ability.can')
@patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
@patch('codemie.rest_api.routers.index.GitRepo.get_by_app_id')
@pytest.mark.parametrize(
    "field_name, field_value, index_field_name",
    [
        ("description", "updated test file patterns", "description"),
        ("link", "https://updated-link.com", "link"),
        ("branch", "develop", "branch"),
        ("docsGeneration", True, "docs_generation"),
        ("projectSpaceVisible", False, "project_space_visible"),
        ("embeddingsModel", "none", "embeddings_model"),
        ("setting_id", "updated-d33b38e4-f057-4e61-983d-564930ca8bad", "setting_id"),
        ("prompt", None, "prompt"),
        ("filesFilter", "**/updated_src/**/*.vue\n**/!updated_src/pages/", "files_filter"),
    ],
)
@pytest.mark.asyncio
async def test_update_index_application(
    mock_get_git_repo: MagicMock,
    mock_filter: MagicMock,
    mock_can: MagicMock,
    mock_get_creds: MagicMock,
    mock_create_processor: MagicMock,
    field_name: str,
    field_value: str,
    index_field_name: str,
    auth_headers: dict,
    mock_app_repo: MagicMock,
    authenticated_user: Callable,
) -> None:
    # Mock git credentials validation
    mock_get_creds.return_value = MagicMock(token="test_token")
    expected_index_update_args = {
        "user": authenticated_user(),
        "description": "",
        "prompt": None,
        "project_space_visible": True,
        "docs_generation": False,
        "embeddings_model": None,
        "files_filter": "",
        "branch": None,
        "link": None,
        "reset_error": False,
        "setting_id": None,
        "project_name": None,
        "guardrail_assignments": None,
        **{index_field_name: field_value},
    }
    expected_created_status_code = status.HTTP_201_CREATED
    m_index_info = MagicMock()
    mock_filter.return_value = [m_index_info]
    m_git_repo = MagicMock()
    m_git_repo.name = DEMO
    mock_get_git_repo.return_value = [m_git_repo]

    mock_can.return_value = True
    git_repo = {"name": DEMO, field_name: field_value}

    response = app_client.put(
        f"/v1/application/{DEMO}/index/{DEMO}",
        json=git_repo,
        headers={**auth_headers, "X-Request-ID": "7ecd6b14-6294-429a-b51a-cab32b344984"},
    )

    assert response.status_code == expected_created_status_code
    m_index_info.update_index.assert_called_once_with(**expected_index_update_args)


def test_valid_json():
    filename = "valid.json"
    content = b'[{"content": "example", "metadata": "data"}]'

    try:
        validate_json_file(filename, content)
    except ExtendedHTTPException:
        pytest.fail("validate_json_file() raised ExtendedHTTPException unexpectedly!")


def test_missing_content_key():
    filename = "missing_content.json"
    content = b'[{"metadata": "data"}]'

    with pytest.raises(ExtendedHTTPException) as excinfo:
        validate_json_file(filename, content)

    assert excinfo.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "missing 'content' key" in excinfo.value.details


def test_missing_metadata_key():
    filename = "missing_metadata.json"
    content = b'[{"content": "example"}]'

    with pytest.raises(ExtendedHTTPException) as excinfo:
        validate_json_file(filename, content)

    assert excinfo.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "missing 'metadata' key" in excinfo.value.details


def test_invalid_json_format():
    filename = "invalid.json"
    content = b'{"content": "example", "metadata": "data"}'  # Not a list format

    with pytest.raises(ExtendedHTTPException) as excinfo:
        validate_json_file(filename, content)

    assert excinfo.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_empty_json_list():
    filename = "empty.json"
    content = b'[]'

    try:
        validate_json_file(filename, content)
    except ExtendedHTTPException:
        pytest.fail("validate_json_file() raised ExtendedHTTPException unexpectedly!")


GET_INDEX_INFO_ID_PATH = "/v1/index/find_id?name=test&index_type=test"


@patch('codemie.core.ability.Ability.can')
@patch("codemie.rest_api.models.index.IndexInfo.find_by_name_and_type")
def test_get_index_info_id_success(mock_find_by_name_and_type, mock_can, auth_headers):
    mock_find_by_name_and_type.return_value = MagicMock(id="test")
    mock_can.return_value = True

    response = app_client.get(
        GET_INDEX_INFO_ID_PATH,
        headers=auth_headers,
    )

    assert response.json()["id"] == "test"
    assert response.status_code == 200


@patch('codemie.core.ability.Ability.can')
@patch("codemie.rest_api.models.index.IndexInfo.find_by_name_and_type")
def test_get_index_info_id_not_found(mock_find_by_name_and_type, mock_can, auth_headers):
    mock_find_by_name_and_type.return_value = None
    mock_can.return_value = True

    response = app_client.get(
        GET_INDEX_INFO_ID_PATH,
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert response.json()['error']['message'] == "Datasource not found"


@patch('codemie.core.ability.Ability.can')
@patch("codemie.rest_api.models.index.IndexInfo.find_by_name_and_type")
def test_get_index_info_id_denied(mock_find_by_name_and_type, mock_can, auth_headers):
    mock_find_by_name_and_type.return_value = MagicMock()
    mock_can.return_value = False

    response = app_client.get(
        GET_INDEX_INFO_ID_PATH,
        headers=auth_headers,
    )

    assert response.status_code == 403
    assert response.json()['error']['message'] == "Access denied"


@patch('codemie.rest_api.routers.index._get_elasticsearch_stats')
@patch('codemie.core.ability.Ability.can')
@patch("codemie.rest_api.models.index.IndexInfo.find_by_id")
def test_get_index_elasticsearch_stats_success(mock_find_by_id, mock_can, mock_get_stats, auth_headers):
    """Test successful retrieval of Elasticsearch statistics"""
    from codemie.rest_api.models.index import ElasticsearchStatsResponse

    mock_index = MagicMock()
    mock_index.id = "test_index_id"
    mock_index.index_type = "datasource"
    mock_find_by_id.return_value = mock_index
    mock_can.return_value = True
    mock_get_stats.return_value = ElasticsearchStatsResponse(
        index_name='test_project_test_index', size_in_bytes=1048576
    )

    response = app_client.get(
        "/v1/index/test_index_id/elasticsearch",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {'index_name': 'test_project_test_index', 'size_in_bytes': 1048576}
    mock_find_by_id.assert_called_once_with("test_index_id")
    mock_can.assert_called_once()
    mock_get_stats.assert_called_once_with(mock_index)


@patch("codemie.rest_api.models.index.IndexInfo.find_by_id")
def test_get_index_elasticsearch_stats_not_found(mock_find_by_id, auth_headers):
    """Test when index is not found"""
    mock_find_by_id.return_value = None

    response = app_client.get(
        "/v1/index/nonexistent_id/elasticsearch",
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert "cannot find datasource" in response.json()['error']['message'].lower()


@patch('codemie.core.ability.Ability.can')
@patch("codemie.rest_api.models.index.IndexInfo.find_by_id")
def test_get_index_elasticsearch_stats_access_denied(mock_find_by_id, mock_can, auth_headers):
    """Test access denied when user doesn't have permissions"""
    mock_index = MagicMock()
    mock_index.index_type = "datasource"
    mock_find_by_id.return_value = mock_index
    mock_can.return_value = False

    response = app_client.get(
        "/v1/index/test_index_id/elasticsearch",
        headers=auth_headers,
    )

    assert response.status_code == 403
    assert response.json()['error']['message'] == "Access denied"


@patch('codemie.rest_api.routers.index._get_elasticsearch_stats')
@patch('codemie.core.ability.Ability.can')
@patch("codemie.rest_api.models.index.IndexInfo.find_by_id")
def test_get_index_elasticsearch_stats_not_available(mock_find_by_id, mock_can, mock_get_stats, auth_headers):
    """Test when Elasticsearch statistics are not available"""
    mock_index = MagicMock()
    mock_index.index_type = "datasource"
    mock_find_by_id.return_value = mock_index
    mock_can.return_value = True
    mock_get_stats.return_value = None

    response = app_client.get(
        "/v1/index/test_index_id/elasticsearch",
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert "not available" in response.json()['error']['message'].lower()
