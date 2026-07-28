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
from codemie.core.models import BaseResponse, CreatedByUser
from codemie.rest_api.routers.index import router
from codemie.service.datasource.file_datasource_service import _validate_json_file as validate_json_file
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
@patch("codemie.rest_api.routers.index.AgentMonitoringService.send_count_metric")
async def test_index_deletion(mock_send_metric, mock_remove_guardrails, mock_get_index_info, mock_can, auth_headers):
    mock_remove_guardrails.return_value = None
    mock_send_metric.return_value = None

    mock_index_info = MagicMock()
    mock_index_info.repo_name = "test_index"
    mock_index_info.id = 1
    mock_index_info.error = None
    mock_index_info.completed = True
    mock_index_info.is_fetching = None

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
    mock_index_info.error = None
    mock_index_info.completed = True
    mock_index_info.is_fetching = None
    mock_index_info.delete.side_effect = NotFoundError(body="Index not found", meta={}, message="Index not found")

    mock_get_index_info.return_value = mock_index_info

    mock_can.return_value = True

    with pytest.raises(ExtendedHTTPException) as e:
        client.delete(f"/v1/index/{mock_index_info.id}", headers=auth_headers)

    assert e.value.code == 404
    assert e.value.message == "Index not found"
    assert e.value.details == f"Index {mock_index_info.repo_name} could not be found in the system."


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

    assert e.value.code == 403
    assert e.value.message == "Access denied"
    assert e.value.details == "You do not have the necessary permissions to delete this entity."


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


@patch('codemie.rest_api.routers.index.GitBatchLoader.test_public_access')
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
    mock_test_public_access,
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
    # No git integration is selected in the payload, so the router probes public access
    mock_test_public_access.return_value = None
    expected_response = f"{expected_response} of datasource demo has been started in the background"
    expected_created_status_code = status.HTTP_201_CREATED
    for idx in existing_indexes:
        idx.index_type = "git"
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
        "reset_error": False,
        "project_name": None,
        "guardrail_assignments": None,
        **{index_field_name: field_value},
    }
    expected_created_status_code = status.HTTP_201_CREATED
    m_index_info = MagicMock()
    m_index_info.index_type = "git"
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


@patch('codemie.rest_api.routers.index.SettingsService.get_git_creds')
@patch('codemie.core.ability.Ability.can')
@patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
@patch('codemie.rest_api.routers.index.GitRepo.get_by_app_id')
@pytest.mark.parametrize(
    "absent_repo_field, repo_attr",
    [
        ("branch", "branch"),
        ("link", "link"),
        ("setting_id", "setting_id"),
        ("embeddingsModel", "embeddings_model"),
        ("docsGeneration", "docs_generation"),
        ("projectSpaceVisible", "project_space_visible"),
        ("filesFilter", "files_filter"),
    ],
)
@pytest.mark.asyncio
async def test_update_index_omitted_fields_not_assigned_to_repository(
    mock_get_git_repo: MagicMock,
    mock_filter: MagicMock,
    mock_can: MagicMock,
    mock_get_creds: MagicMock,
    absent_repo_field: str,
    repo_attr: str,
    auth_headers: dict,
    mock_app_repo: MagicMock,
    mock_create_processor: MagicMock,
    authenticated_user: Callable,
) -> None:
    """When a repo field is absent from the PUT payload, it must NOT be written to the repository.

    Uses a MagicMock with a patched __setattr__ on the *instance* (not the class)
    so we can track which repository attributes the handler explicitly assigns.
    """
    mock_get_creds.return_value = MagicMock(token="test_token")
    mock_can.return_value = True

    m_index_info = MagicMock()
    mock_filter.return_value = [m_index_info]

    m_git_repo = MagicMock()
    m_git_repo.name = DEMO
    mock_get_git_repo.return_value = [m_git_repo]

    # Record which repo attributes are explicitly set by the handler.
    # We attach the tracking set to the mock instance itself and intercept
    # setattr via a subclass created per-test (not mutating the shared MagicMock
    # class) so other tests remain unaffected.
    assigned_by_handler: set = set()
    _sentinel = object()

    original_setattr = m_git_repo.__class__.__setattr__

    class _TrackingMock(m_git_repo.__class__):
        def __setattr__(self, name, value):
            if self is m_git_repo:
                assigned_by_handler.add(name)
            original_setattr(self, name, value)

    m_git_repo.__class__ = _TrackingMock
    # Reset tracking: the name assignment above was captured; clear it.
    assigned_by_handler.clear()

    # Send a payload that includes `name` (required to enter the handler) but
    # deliberately omits the field under test.
    payload = {"name": DEMO}
    assert absent_repo_field not in payload, "test setup: field must be absent from payload"

    response = app_client.put(
        f"/v1/application/{DEMO}/index/{DEMO}",
        json=payload,
        headers={**auth_headers, "X-Request-ID": "7ecd6b14-6294-429a-b51a-cab32b344984"},
    )

    # Restore class so other tests that share the MagicMock class are not affected.
    m_git_repo.__class__ = MagicMock

    assert response.status_code == status.HTTP_201_CREATED
    assert repo_attr not in assigned_by_handler, (
        f"repository.{repo_attr} should NOT be assigned when '{absent_repo_field}' "
        f"is absent from the request payload, but it was set to "
        f"{getattr(m_git_repo, repo_attr, '<unset>')!r}"
    )


@patch('codemie.rest_api.routers.index.SettingsService.get_git_creds')
@patch('codemie.core.ability.Ability.can')
@patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
@patch('codemie.rest_api.routers.index.GitRepo.get_by_app_id')
@pytest.mark.parametrize(
    "absent_index_field",
    [
        "prompt",
        "docsGeneration",
        "filesFilter",
        "projectSpaceVisible",
        "embeddingsModel",
        "description",
    ],
)
@pytest.mark.asyncio
async def test_update_index_omitted_fields_not_passed_to_update_index(
    mock_get_git_repo: MagicMock,
    mock_filter: MagicMock,
    mock_can: MagicMock,
    mock_get_creds: MagicMock,
    absent_index_field: str,
    auth_headers: dict,
    mock_app_repo: MagicMock,
    mock_create_processor: MagicMock,
    authenticated_user: Callable,
) -> None:
    """When an index field is absent from the PUT payload, it must NOT be passed to update_index.

    Ensures the partial-update fix covers IndexInfo as well as GitRepo.
    """
    mock_get_creds.return_value = MagicMock(token="test_token")
    mock_can.return_value = True

    m_index_info = MagicMock()
    mock_filter.return_value = [m_index_info]

    m_git_repo = MagicMock()
    m_git_repo.name = DEMO
    mock_get_git_repo.return_value = [m_git_repo]

    payload = {"name": DEMO}
    assert absent_index_field not in payload

    response = app_client.put(
        f"/v1/application/{DEMO}/index/{DEMO}",
        json=payload,
        headers={**auth_headers, "X-Request-ID": "7ecd6b14-6294-429a-b51a-cab32b344984"},
    )

    assert response.status_code == status.HTTP_201_CREATED

    call_kwargs = m_index_info.update_index.call_args[1]
    from codemie.rest_api.routers.index import _INDEX_FIELD_MAP

    index_kwarg = _INDEX_FIELD_MAP.get(absent_index_field)
    assert index_kwarg not in call_kwargs, (
        f"update_index should NOT receive '{index_kwarg}' when '{absent_index_field}' "
        f"is absent from the request payload"
    )


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


# ---------------------------------------------------------------------------
# Tests for _parse_jwt_exp (JWT expiry extraction helper)
# ---------------------------------------------------------------------------


class TestParseJwtExp:
    """Tests for the _parse_jwt_exp helper in index router."""

    def test_valid_jwt_returns_exp(self):
        """Returns exp claim from a real JWT payload."""
        import base64
        import json
        from codemie.rest_api.routers.index import _parse_jwt_exp

        header = base64.b64encode(b'{"alg":"RS256"}').decode()
        payload = base64.b64encode(json.dumps({"exp": 9999999999}).encode()).decode()
        token = f"{header}.{payload}.sig"

        result = _parse_jwt_exp(token)

        assert result == 9999999999

    def test_jwt_without_exp_falls_back_to_now_plus_hour(self):
        """Falls back to approx. current time + 3600 when exp is missing."""
        import base64
        import json
        import time
        from codemie.rest_api.routers.index import _parse_jwt_exp

        header = base64.b64encode(b'{"alg":"RS256"}').decode()
        payload = base64.b64encode(json.dumps({"sub": "user"}).encode()).decode()
        token = f"{header}.{payload}.sig"

        before = int(time.time()) + 3600
        result = _parse_jwt_exp(token)
        after = int(time.time()) + 3600

        assert before <= result <= after + 5  # 5 second tolerance

    def test_invalid_token_falls_back(self):
        """Malformed token falls back to now + 3600."""
        import time
        from codemie.rest_api.routers.index import _parse_jwt_exp

        result = _parse_jwt_exp("not-a-jwt")

        assert result >= int(time.time()) + 3590  # within tolerance

    def test_empty_token_falls_back(self):
        """Empty token string falls back to now + 3600."""
        import time
        from codemie.rest_api.routers.index import _parse_jwt_exp

        result = _parse_jwt_exp("")

        assert result >= int(time.time()) + 3590


@patch('codemie.rest_api.routers.index.SettingsService.get_jira_creds')
@patch('codemie.rest_api.routers.index.JiraDatasourceProcessor')
@patch('codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo')
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
@pytest.mark.asyncio
async def test_reindex_jira_uses_stored_setting_id_when_request_setting_id_is_none(
    mock_get_guardrails, mock_filter, mock_worker, mock_creds, auth_headers
):
    kb_index_mock = MagicMock()
    kb_index_mock.setting_id = "stored-jira-id"
    mock_filter.return_value = [kb_index_mock]
    mock_worker.return_value = MagicMock()
    mock_get_guardrails.return_value = []

    response = app_client.put(
        "/v1/index/knowledge_base/jira?full_reindex=true",
        json={"name": "test_index", "project_name": "test_project", "jql": "project=TEST", "description": ""},
        headers=auth_headers,
    )

    assert response.status_code == 200
    mock_creds.assert_called_once_with(
        user_id="test_user",
        project_name="test_project",
        setting_id="stored-jira-id",
    )


@patch('codemie.rest_api.routers.index.SettingsService.get_confluence_creds')
@patch('codemie.rest_api.routers.index.ConfluenceDatasourceProcessor')
@patch('codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo')
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
@pytest.mark.asyncio
async def test_reindex_confluence_uses_stored_setting_id_when_request_setting_id_is_none(
    mock_get_guardrails, mock_filter, mock_worker, mock_creds, auth_headers
):
    kb_index_mock = MagicMock()
    kb_index_mock.setting_id = "stored-conf-id"
    mock_filter.return_value = [kb_index_mock]
    mock_worker.return_value = MagicMock()
    mock_get_guardrails.return_value = []

    response = app_client.put(
        "/v1/index/knowledge_base/confluence?full_reindex=true",
        json={"name": "test_index", "project_name": "test_project"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    mock_creds.assert_called_once_with(
        user_id="test_user",
        project_name="test_project",
        setting_id="stored-conf-id",
    )


@patch('codemie.rest_api.routers.index.SettingsService.get_xray_creds')
@patch('codemie.rest_api.routers.index.XrayDatasourceProcessor')
@patch('codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo')
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
@pytest.mark.asyncio
async def test_reindex_xray_uses_stored_setting_id_when_request_setting_id_is_none(
    mock_get_guardrails, mock_filter, mock_worker, mock_creds, auth_headers
):
    kb_index_mock = MagicMock()
    kb_index_mock.setting_id = "stored-xray-id"
    mock_filter.return_value = [kb_index_mock]
    mock_worker.return_value = MagicMock()
    mock_get_guardrails.return_value = []

    response = app_client.put(
        "/v1/index/knowledge_base/xray?full_reindex=true",
        json={"name": "test_index", "project_name": "test_project", "jql": "project=TEST"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    mock_creds.assert_called_once_with(
        user_id="test_user",
        project_name="test_project",
        setting_id="stored-xray-id",
    )


@patch('codemie.rest_api.routers.index.SettingsService.get_azure_devops_creds')
@patch('codemie.rest_api.routers.index.AzureDevOpsWikiDatasourceProcessor')
@patch('codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo')
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
@pytest.mark.asyncio
async def test_reindex_azure_devops_wiki_uses_stored_setting_id_when_request_setting_id_is_none(
    mock_get_guardrails, mock_filter, mock_worker, mock_creds, auth_headers
):
    kb_index_mock = MagicMock()
    kb_index_mock.setting_id = "stored-ado-id"
    mock_filter.return_value = [kb_index_mock]
    mock_worker.return_value = MagicMock()
    mock_get_guardrails.return_value = []

    response = app_client.put(
        "/v1/index/knowledge_base/azure_devops_wiki?full_reindex=true",
        json={"name": "test_index", "project_name": "test_project"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    mock_creds.assert_called_once_with(
        user_id="test_user",
        project_name="test_project",
        setting_id="stored-ado-id",
    )


@patch('codemie.rest_api.routers.index.SettingsService.get_azure_devops_creds')
@patch('codemie.rest_api.routers.index.AzureDevOpsWorkItemDatasourceProcessor')
@patch('codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo')
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
@pytest.mark.asyncio
async def test_reindex_azure_devops_work_item_uses_stored_setting_id_when_request_setting_id_is_none(
    mock_get_guardrails, mock_filter, mock_worker, mock_creds, auth_headers
):
    kb_index_mock = MagicMock()
    kb_index_mock.setting_id = "stored-ado-wi-id"
    mock_filter.return_value = [kb_index_mock]
    mock_worker.return_value = MagicMock()
    mock_get_guardrails.return_value = []

    response = app_client.put(
        "/v1/index/knowledge_base/azure_devops_work_item?full_reindex=true",
        json={"name": "test_index", "project_name": "test_project"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    mock_creds.assert_called_once_with(
        user_id="test_user",
        project_name="test_project",
        setting_id="stored-ado-wi-id",
    )


@patch('codemie.rest_api.routers.index.SettingsService.get_jira_creds')
@patch('codemie.rest_api.routers.index.JiraDatasourceProcessor')
@patch('codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo')
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
@pytest.mark.asyncio
async def test_reindex_jira_explicit_setting_id_takes_precedence_over_stored(
    mock_get_guardrails, mock_filter, mock_worker, mock_creds, auth_headers
):
    kb_index_mock = MagicMock()
    kb_index_mock.setting_id = "stored-id"
    mock_filter.return_value = [kb_index_mock]
    mock_worker.return_value = MagicMock()
    mock_get_guardrails.return_value = []

    response = app_client.put(
        "/v1/index/knowledge_base/jira?full_reindex=true",
        json={
            "name": "test_index",
            "project_name": "test_project",
            "jql": "project=TEST",
            "description": "",
            "setting_id": "explicit-id",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    mock_creds.assert_called_once_with(
        user_id="test_user",
        project_name="test_project",
        setting_id="explicit-id",
    )


# ---------------------------------------------------------------------------
# Azure DevOps Work Item PUT endpoint — wiql_query fallback logic
# ---------------------------------------------------------------------------

_ADO_WORK_ITEM_PUT_URL = "/v1/index/knowledge_base/azure_devops_work_item"
_ADO_UPDATE_REQUEST = {
    "name": "work-item-ds",
    "project_name": "test-project",
    "wiql_query": "",
}
_STORED_WIQL = "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'"
_DEFAULT_WIQL = "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project"


def _make_kb_index(wiql_query: str) -> MagicMock:
    kb = MagicMock()
    kb.azure_devops_work_item = MagicMock()
    kb.azure_devops_work_item.wiql_query = wiql_query
    kb.project_space_visible = False
    return kb


@patch("codemie.rest_api.routers.index.AzureDevOpsWorkItemDatasourceProcessor")
@patch("codemie.rest_api.routers.index.SettingsService.get_azure_devops_creds")
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
@patch("codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo")
@pytest.mark.asyncio
async def test_put_reindex_uses_stored_wiql_when_request_is_empty(
    mock_filter, mock_guardrails, mock_creds, mock_processor_cls, auth_headers
):
    kb_index = _make_kb_index(_STORED_WIQL)
    mock_filter.return_value = [kb_index]
    mock_guardrails.return_value = []

    mock_processor = MagicMock()
    mock_processor.started_message = "Indexing started"
    mock_processor_cls.return_value = mock_processor

    response = app_client.put(
        f"{_ADO_WORK_ITEM_PUT_URL}?full_reindex=true",
        json=_ADO_UPDATE_REQUEST,
        headers={**auth_headers, "X-Request-ID": "test-uuid"},
    )

    assert response.status_code == 200
    _, kwargs = mock_processor_cls.call_args
    assert kwargs["wiql_query"] == _STORED_WIQL


@patch("codemie.rest_api.routers.index.AzureDevOpsWorkItemDatasourceProcessor")
@patch("codemie.rest_api.routers.index.SettingsService.get_azure_devops_creds")
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
@patch("codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo")
@pytest.mark.asyncio
async def test_put_reindex_uses_request_wiql_when_provided(
    mock_filter, mock_guardrails, mock_creds, mock_processor_cls, auth_headers
):
    kb_index = _make_kb_index(_STORED_WIQL)
    mock_filter.return_value = [kb_index]
    mock_guardrails.return_value = []

    mock_processor = MagicMock()
    mock_processor.started_message = "Indexing started"
    mock_processor_cls.return_value = mock_processor

    custom_wiql = "SELECT [System.Id] FROM WorkItems WHERE [System.AreaPath] = 'Team'"
    request_with_custom_wiql = {**_ADO_UPDATE_REQUEST, "wiql_query": custom_wiql}

    response = app_client.put(
        f"{_ADO_WORK_ITEM_PUT_URL}?full_reindex=true",
        json=request_with_custom_wiql,
        headers={**auth_headers, "X-Request-ID": "test-uuid"},
    )

    assert response.status_code == 200
    _, kwargs = mock_processor_cls.call_args
    assert kwargs["wiql_query"] == custom_wiql


@patch("codemie.rest_api.routers.index.AzureDevOpsWorkItemDatasourceProcessor")
@patch("codemie.rest_api.routers.index.SettingsService.get_azure_devops_creds")
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
@patch("codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo")
@pytest.mark.asyncio
async def test_put_reindex_uses_none_when_no_stored_wiql(
    mock_filter, mock_guardrails, mock_creds, mock_processor_cls, auth_headers
):
    kb_index = MagicMock()
    kb_index.azure_devops_work_item = None
    kb_index.project_space_visible = False
    mock_filter.return_value = [kb_index]
    mock_guardrails.return_value = []

    mock_processor = MagicMock()
    mock_processor.started_message = "Indexing started"
    mock_processor_cls.return_value = mock_processor

    response = app_client.put(
        f"{_ADO_WORK_ITEM_PUT_URL}?full_reindex=true",
        json=_ADO_UPDATE_REQUEST,
        headers={**auth_headers, "X-Request-ID": "test-uuid"},
    )

    assert response.status_code == 200
    _, kwargs = mock_processor_cls.call_args
    # None — processor's own default will apply
    assert kwargs["wiql_query"] is None


# ---------------------------------------------------------------------------
# Constants shared by file-datasource tests
# ---------------------------------------------------------------------------

_FILE_DS_NAME = "file-ds"
_FILE_DS_PROJECT = "test_project"
_FILE_DS_DESCRIPTION = "test description"
_FILE_POST_URL = "/v1/index/knowledge_base/file"
_FILE_PUT_URL = "/v1/index/knowledge_base/file"


# ---------------------------------------------------------------------------
# Tests for POST /index/knowledge_base/file
# ---------------------------------------------------------------------------


@patch('codemie.rest_api.routers.index._kb_demo_user_check')
@patch('codemie.rest_api.routers.index._index_unique_check')
@patch('codemie.rest_api.routers.index.FileRepositoryFactory.get_current_repository')
@patch('codemie.rest_api.routers.index.FileDatasourceProcessor')
def test_post_file_datasource_success(
    mock_processor_cls, mock_file_repo_factory, mock_unique_check, mock_demo_check, auth_headers
):
    """Happy path: files uploaded, processor scheduled, 200 with started message."""
    mock_file_object = MagicMock()
    mock_file_object.name = "uploaded_test.txt"
    mock_file_object.owner = "test_user"
    mock_file_repo_factory.return_value.write_file.return_value = mock_file_object

    mock_processor = MagicMock()
    mock_processor.started_message = f"Indexing of {_FILE_DS_NAME} has started in the background"
    mock_processor_cls.return_value = mock_processor

    response = app_client.post(
        _FILE_POST_URL,
        params={"name": _FILE_DS_NAME, "project_name": _FILE_DS_PROJECT, "description": _FILE_DS_DESCRIPTION},
        files=[("files", ("test.txt", b"hello world", "text/plain"))],
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"message": f"Indexing of {_FILE_DS_NAME} has started in the background"}
    mock_unique_check.assert_called_once_with(_FILE_DS_PROJECT, _FILE_DS_NAME)
    mock_processor.schedule.assert_called_once()


@patch('codemie.rest_api.routers.index._index_unique_check')
def test_post_file_datasource_duplicate_raises_409(mock_unique_check, auth_headers):
    """_index_unique_check raises 409 when the datasource already exists."""
    mock_unique_check.side_effect = ExtendedHTTPException(
        code=status.HTTP_409_CONFLICT,
        message="Index already exists",
        details=f"An index with name '{_FILE_DS_NAME}' already exists.",
        help="Choose a different name.",
    )

    response = app_client.post(
        _FILE_POST_URL,
        params={"name": _FILE_DS_NAME, "project_name": _FILE_DS_PROJECT, "description": _FILE_DS_DESCRIPTION},
        files=[("files", ("test.txt", b"hello", "text/plain"))],
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert response.json()['error']['message'] == "Index already exists"


@patch('codemie.rest_api.routers.index._kb_demo_user_check')
@patch('codemie.rest_api.routers.index._index_unique_check')
def test_post_file_datasource_demo_user_blocked_raises_403(mock_unique_check, mock_demo_check, auth_headers):
    """_kb_demo_user_check raises 403 for demo users who reached the repository limit."""
    mock_demo_check.side_effect = ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message="Repository limit reached",
        details="Demo users can index only one repository.",
        help="Upgrade your account.",
    )

    response = app_client.post(
        _FILE_POST_URL,
        params={"name": _FILE_DS_NAME, "project_name": _FILE_DS_PROJECT, "description": _FILE_DS_DESCRIPTION},
        files=[("files", ("test.txt", b"hello", "text/plain"))],
        headers=auth_headers,
    )

    assert response.status_code == 403
    assert response.json()['error']['message'] == "Repository limit reached"


@patch('codemie.rest_api.routers.index._kb_demo_user_check')
@patch('codemie.rest_api.routers.index._index_unique_check')
def test_post_file_datasource_invalid_guardrail_assignments_raises_400(
    mock_unique_check, mock_demo_check, auth_headers
):
    """Invalid JSON in guardrail_assignments query param → 400 response."""
    response = app_client.post(
        _FILE_POST_URL,
        params={
            "name": _FILE_DS_NAME,
            "project_name": _FILE_DS_PROJECT,
            "description": _FILE_DS_DESCRIPTION,
            "guardrail_assignments": "not-valid-json",
        },
        files=[("files", ("test.txt", b"hello", "text/plain"))],
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert response.json()['error']['message'] == "Invalid guardrail_assignments parameter"


# ---------------------------------------------------------------------------
# Tests for PUT /index/knowledge_base/file
# ---------------------------------------------------------------------------


@patch('codemie.rest_api.routers.index.UpdateFileDatasourceUseCase')
def test_put_file_datasource_metadata_only_returns_edit_successful(mock_use_case_cls, auth_headers):
    """No file changes: use case returns 'Edit successful'."""
    mock_use_case_cls.return_value.execute.return_value = BaseResponse(message="Edit successful")

    response = app_client.put(
        _FILE_PUT_URL,
        params={"name": _FILE_DS_NAME, "project_name": _FILE_DS_PROJECT},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Edit successful"}
    mock_use_case_cls.return_value.execute.assert_called_once()


@patch('codemie.rest_api.routers.index.UpdateFileDatasourceUseCase')
def test_put_file_datasource_file_changes_returns_started_message(mock_use_case_cls, auth_headers):
    """File changes present: use case returns the processor started message."""
    started_msg = f"Indexing of {_FILE_DS_NAME} has started in the background"
    mock_use_case_cls.return_value.execute.return_value = BaseResponse(message=started_msg)

    response = app_client.put(
        _FILE_PUT_URL,
        params={"name": _FILE_DS_NAME, "project_name": _FILE_DS_PROJECT},
        files=[("files", ("new.txt", b"new content", "text/plain"))],
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"message": started_msg}


@patch('codemie.rest_api.routers.index.UpdateFileDatasourceUseCase')
def test_put_file_datasource_not_found_returns_404(mock_use_case_cls, auth_headers):
    """Index not found: use case raises 404 and the handler returns 404."""
    mock_use_case_cls.return_value.execute.side_effect = ExtendedHTTPException(
        code=status.HTTP_404_NOT_FOUND,
        message="Index not found",
        details="The index could not be found.",
        help="Verify the index name.",
    )

    response = app_client.put(
        _FILE_PUT_URL,
        params={"name": _FILE_DS_NAME, "project_name": _FILE_DS_PROJECT},
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert response.json()['error']['message'] == "Index not found"


@patch('codemie.rest_api.routers.index.UpdateFileDatasourceUseCase')
def test_put_file_datasource_blocked_while_indexing_returns_409(mock_use_case_cls, auth_headers):
    """Indexing in progress: use case raises 409 and the handler returns 409."""
    mock_use_case_cls.return_value.execute.side_effect = ExtendedHTTPException(
        code=status.HTTP_409_CONFLICT,
        message="Indexing or fetching is in progress.",
        details="Updates are not allowed while indexing is in progress.",
        help="Retry once indexing has finished.",
    )

    response = app_client.put(
        _FILE_PUT_URL,
        params={"name": _FILE_DS_NAME, "project_name": _FILE_DS_PROJECT},
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert response.json()['error']['message'] == "Indexing or fetching is in progress."


# ===================== _update_datasource_scheduler timezone tests =====================


@patch("codemie.service.settings.scheduler_settings_service.SchedulerSettingsService.handle_schedule")
def test_update_datasource_scheduler_passes_timezone(mock_handle):
    from codemie.rest_api.routers.index import _update_datasource_scheduler
    from codemie.rest_api.models.index import IndexInfo

    index_info = MagicMock(spec=IndexInfo)
    index_info.project_name = "proj"
    index_info.id = "abc-123"
    index_info.repo_name = "my-repo"

    _update_datasource_scheduler("user-1", index_info, "0 9 * * *", timezone="America/New_York")

    mock_handle.assert_called_once()
    _, kwargs = mock_handle.call_args
    assert kwargs["timezone"] == "America/New_York"


@patch("codemie.service.settings.scheduler_settings_service.SchedulerSettingsService.handle_schedule")
def test_update_datasource_scheduler_no_timezone_passes_none(mock_handle):
    from codemie.rest_api.routers.index import _update_datasource_scheduler
    from codemie.rest_api.models.index import IndexInfo

    index_info = MagicMock(spec=IndexInfo)
    index_info.project_name = "proj"
    index_info.id = "abc-123"
    index_info.repo_name = "my-repo"

    _update_datasource_scheduler("user-1", index_info, "0 9 * * *")

    mock_handle.assert_called_once()
    _, kwargs = mock_handle.call_args
    assert kwargs["timezone"] is None
