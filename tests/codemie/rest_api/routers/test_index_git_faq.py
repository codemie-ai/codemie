# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from codemie.rest_api.models.index import IndexKnowledgeBaseGitFaqRequest, UpdateKnowledgeBaseGitFaqRequest
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from tests.codemie.rest_api.routers.test_index import app


app_client = TestClient(app)


@pytest.fixture
def authenticated_user():
    user = User(id="test_user", username="test_user", name="test_user")

    def mock_authenticate(request: Request = None):
        if request:
            request.state.user = user
        return user

    app.dependency_overrides[authenticate] = mock_authenticate
    yield mock_authenticate
    app.dependency_overrides = {}


@pytest.fixture
def auth_headers(authenticated_user) -> dict:
    return {"user-id": authenticated_user().id}


@pytest.fixture
def create_request():
    return IndexKnowledgeBaseGitFaqRequest(
        project_name="test_project",
        name="test_faq_index",
        description="test faq description",
        project_space_visible=True,
        link="https://git.example.com/team/docs.git",
        branch="main",
        setting_id=None,
    )


@pytest.fixture
def update_request():
    return UpdateKnowledgeBaseGitFaqRequest(
        name="test_faq_index",
        project_name="test_project",
        description="updated description",
    )


@patch("codemie.rest_api.routers.index._kb_demo_user_check")
@patch("codemie.rest_api.routers.index._index_unique_check")
@patch("codemie.rest_api.routers.index._validate_git_credentials")
@patch("codemie.rest_api.routers.index.GitFaqDatasourceProcessor")
@pytest.mark.asyncio
async def test_create_faq_datasource_schedules_indexing(
    mock_processor,
    mock_validate,
    mock_unique,
    mock_demo,
    create_request,
    auth_headers,  # noqa: F811
):
    mock_unique.return_value = True
    instance = MagicMock()
    mock_processor.return_value = instance

    response = app_client.post(
        "/v1/index/knowledge_base/git_faq", json=create_request.model_dump(), headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Indexing of datasource test_faq_index has been started in the background"}
    mock_validate.assert_called_once_with(
        user_id=auth_headers["user-id"],
        project_name="test_project",
        repo_link="https://git.example.com/team/docs.git",
        setting_id=None,
    )
    _, kwargs = mock_processor.call_args
    assert kwargs["git_config"].repo_link == "https://git.example.com/team/docs.git"
    assert kwargs["git_config"].branch == "main"
    instance.schedule.assert_called_once()


@patch("codemie.rest_api.routers.index._kb_demo_user_check")
@patch("codemie.rest_api.routers.index._index_unique_check")
@pytest.mark.asyncio
async def test_create_faq_duplicate_name_conflicts(mock_unique, mock_demo, create_request, auth_headers):  # noqa: F811
    from fastapi import HTTPException

    mock_unique.side_effect = HTTPException(status_code=409, detail="Index already exists")

    response = app_client.post(
        "/v1/index/knowledge_base/git_faq", json=create_request.model_dump(), headers=auth_headers
    )

    assert response.status_code == 409


@patch("codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo")
@pytest.mark.asyncio
async def test_update_faq_not_found(mock_filter, update_request, auth_headers):  # noqa: F811
    mock_filter.return_value = []

    response = app_client.put(
        "/v1/index/knowledge_base/git_faq", json=update_request.model_dump(), headers=auth_headers
    )

    assert response.status_code == 404


@patch("codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo")
@pytest.mark.asyncio
async def test_update_faq_metadata_only(mock_filter, update_request, auth_headers):  # noqa: F811
    index_mock = MagicMock()
    mock_filter.return_value = [index_mock]

    response = app_client.put(
        "/v1/index/knowledge_base/git_faq", json=update_request.model_dump(), headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Edit successful"}
    _, kwargs = index_mock.update_index.call_args
    assert kwargs["description"] == "updated description"


@patch("codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo")
@pytest.mark.asyncio
async def test_update_faq_source_fields_require_full_reindex(mock_filter, auth_headers):  # noqa: F811
    mock_filter.return_value = [MagicMock()]
    request = UpdateKnowledgeBaseGitFaqRequest(
        name="test_faq_index",
        project_name="test_project",
        files_filter="docs/**/*.md",
    )

    response = app_client.put("/v1/index/knowledge_base/git_faq", json=request.model_dump(), headers=auth_headers)

    assert response.status_code == 422
    assert "full_reindex" in response.json()["error"]["details"]
    assert response.json()["error"]["message"] == "FAQ source fields require a full reindex"


@patch("codemie.rest_api.routers.index._validate_git_credentials")
@patch("codemie.rest_api.routers.index._build_git_faq_processor_from_index")
@patch("codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo")
@pytest.mark.asyncio
async def test_update_faq_clears_integration_with_empty_string(
    mock_filter,
    mock_build,
    mock_validate,
    auth_headers,  # noqa: F811
):
    """The UI sends setting_id='' when the integration cross is clicked — that must
    clear the stored integration (None), not be ignored like an absent field."""
    index_mock = MagicMock()
    index_mock.setting_id = "old-integration-id"
    mock_filter.return_value = [index_mock]
    mock_build.return_value = MagicMock()
    request = UpdateKnowledgeBaseGitFaqRequest(
        name="test_faq_index",
        project_name="test_project",
        setting_id="",
    )

    response = app_client.put(
        "/v1/index/knowledge_base/git_faq?full_reindex=true",
        json=request.model_dump(),
        headers=auth_headers,
    )

    assert response.status_code == 200
    _, kwargs = index_mock.update_index.call_args
    assert kwargs["setting_id"] is None


@patch("codemie.rest_api.routers.index._validate_git_credentials")
@patch("codemie.rest_api.routers.index._build_git_faq_processor_from_index")
@patch("codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo")
@pytest.mark.asyncio
async def test_update_faq_source_fields_with_full_reindex_reindexes(
    mock_filter,
    mock_build,
    mock_validate,
    auth_headers,  # noqa: F811
):
    index_mock = MagicMock()
    index_mock.setting_id = None
    mock_filter.return_value = [index_mock]
    instance = MagicMock()
    mock_build.return_value = instance
    request = UpdateKnowledgeBaseGitFaqRequest(
        name="test_faq_index",
        project_name="test_project",
        files_filter="docs/**/*.md",
    )

    response = app_client.put(
        "/v1/index/knowledge_base/git_faq?full_reindex=true",
        json=request.model_dump(),
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert "has been started in the background" in response.json()["message"]
    _, kwargs = index_mock.update_index.call_args
    assert kwargs["files_filter"] == "docs/**/*.md"
    mock_validate.assert_not_called()  # public datasource (no setting_id)
    instance.schedule.assert_called_once()
    assert instance.schedule.call_args.args[1] is instance.reprocess


@patch("codemie.rest_api.routers.index._validate_project_change")
@patch("codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo")
@pytest.mark.asyncio
async def test_update_faq_project_change_requires_full_reindex(mock_filter, mock_project_change, auth_headers):  # noqa: F811
    mock_filter.return_value = [MagicMock()]
    request = UpdateKnowledgeBaseGitFaqRequest(
        name="test_faq_index",
        project_name="test_project",
        new_project_name="other_project",
    )

    response = app_client.put("/v1/index/knowledge_base/git_faq", json=request.model_dump(), headers=auth_headers)

    assert response.status_code == 422
    assert "new_project_name" in response.json()["error"]["details"]


@patch("codemie.rest_api.routers.index._validate_git_credentials")
@patch("codemie.rest_api.routers.index._build_git_faq_processor_from_index")
@patch("codemie.rest_api.routers.index.KnowledgeBaseIndexInfo.filter_by_project_and_repo")
@pytest.mark.asyncio
async def test_reindex_git_faq_route_schedules_reprocess(
    mock_filter,
    mock_build,
    mock_validate,
    auth_headers,  # noqa: F811
):
    index_mock = MagicMock()
    index_mock.setting_id = None
    mock_filter.return_value = [index_mock]
    instance = MagicMock()
    mock_build.return_value = instance

    response = app_client.put(
        "/v1/index/knowledge_base/git_faq/reindex",
        json={"name": "test_faq_index", "project_name": "test_project"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    instance.schedule.assert_called_once()
    assert instance.schedule.call_args.args[1] is instance.reprocess


def test_build_git_faq_processor_from_index_passes_setting_id():
    from codemie.rest_api.routers.index import _build_git_faq_processor_from_index
    from codemie.rest_api.security.user import User

    index_mock = MagicMock()
    index_mock.setting_id = "git-integration"
    index_mock.embeddings_model = "ada"
    index_mock.branch = "main"
    index_mock.link = "https://x.git"
    index_mock.repo_name = "ds"
    index_mock.project_name = "proj"

    processor = _build_git_faq_processor_from_index(index_mock, User(id="u", username="u", name="u"), "req-uuid")

    assert processor.setting_id == "git-integration"
    assert processor.embedding_model == "ada"
