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

import json
from uuid import uuid4

import pytest
from sqlalchemy.orm.exc import StaleDataError
from unittest.mock import MagicMock, patch

from codemie.rest_api.models.index import IndexInfo, IndexDeletedException
from codemie.rest_api.security.user import User

NULLABLE_DEFAULT_FIELDS = (
    "embeddings_model",
    "summarization_model",
    "created_by",
    "branch",
    "link",
    "user_abilities",
    "confluence",
    "jira",
    "setting_id",
    "tokens_usage",
    "prompt",
)


@pytest.fixture
def mock_update_path():
    return 'codemie.rest_api.models.base.BaseModelWithSQLSupport.update'


@pytest.fixture
def index_info(request):
    index_obj = IndexInfo(
        project_name="Test Project",
        description="Test Project Description",
        repo_name="Test Repo",
        index_type="Test index type",
        id="1234",
        error=True,
    )

    return index_obj


@patch('codemie.rest_api.routers.index.IndexStatusService.get_index_info_list')
@pytest.mark.asyncio
async def test_index_indexes_progress_no_filters(mock_get_index_info):
    mock_get_index_info.return_value = {}
    mock_get_index_info(user=None, filters=None, per_page=10, page=0)
    mock_get_index_info.assert_called_once()


@patch('codemie.rest_api.routers.index.IndexStatusService.get_index_info_list')
@pytest.mark.asyncio
async def test_index_indexes_progress_with_filters(mock_get_index_info):
    filters = json.dumps({"key": "value"})
    mock_get_index_info.return_value = {}
    mock_get_index_info(user=None, filters=filters, per_page=10, page=0)
    mock_get_index_info.assert_called_once_with(user=None, filters='{"key": "value"}', per_page=10, page=0)


def test_update_index_fields_should_be_updated(index_info, mock_update_path):
    with patch(mock_update_path, return_value=True) as mock_update:
        index_info.update_index(
            user=MagicMock(spec=User),
            project_space_visible=True,
            description="New Project Description",
            docs_generation=True,
            branch="New Branch",
            link="New Link",
            files_filter="*.py\n*.txt",
            embeddings_model="New model",
            reset_error=True,
        )

        mock_update.assert_called_once()

        assert index_info.description == "New Project Description"
        assert index_info.docs_generation
        assert index_info.branch == "New Branch"
        assert index_info.link == "New Link"
        assert index_info.files_filter == "*.py\n*.txt"
        assert index_info.embeddings_model == "New model"
        assert not index_info.error


def test_update_index_fields_default_set(index_info, mock_update_path):
    with patch(mock_update_path, return_value=True) as mock_update:
        index_info.update_index(
            user=MagicMock(spec=User),
            project_space_visible=True,
            description="New Project Description",
            docs_generation=True,
            branch="New Branch",
            link="New Link",
            embeddings_model="New model",
            reset_error=True,
            setting_id="test-id",
        )

        mock_update.assert_called_once()

        assert index_info.description == "New Project Description"
        assert index_info.docs_generation
        assert index_info.branch == "New Branch"
        assert index_info.link == "New Link"
        assert index_info.files_filter is None
        assert index_info.embeddings_model == "New model"
        assert not index_info.error
        assert index_info.setting_id == "test-id"


def test_test_update_index_does_not_reset_error(index_info, mock_update_path):
    with patch(mock_update_path, return_value=True) as mock_update:
        index_info.error = True

        index_info.update_index(user=MagicMock(spec=User), description="New Project Description", reset_error=False)

        mock_update.assert_called_once()

        assert index_info.description == "New Project Description"
        assert index_info.error


def test_update_index_not_found(mocker, index_info):
    mocker.patch(
        'codemie.rest_api.models.base.BaseModelWithSQLSupport.update',
        side_effect=StaleDataError,
    )

    with pytest.raises(IndexDeletedException):
        index_info.update()


def test_start_fetching(mocker, index_info):
    mocker.patch('codemie.rest_api.models.base.BaseModelWithSQLSupport.update')

    index_info.start_fetching()

    assert index_info.is_fetching
    assert not index_info.error
    assert not index_info.completed
    assert not index_info.completed
    assert index_info.current_state == 0
    assert index_info.complete_state == 0
    assert index_info.current__chunks_state == 0
    assert index_info.processed_files == []


def test_start_fetching_incremental(mocker, index_info):
    mocker.patch('codemie.rest_api.models.base.BaseModelWithSQLSupport.update')
    index_info.complete_state = 10
    index_info.current__chunks_state = 20
    index_info.processed_files = ['file1', 'file2']
    index_info.current_state = 5

    index_info.start_fetching(is_incremental=True)
    assert index_info.is_fetching
    assert not index_info.error
    assert not index_info.completed
    assert index_info.current_state == 5
    assert index_info.complete_state == 10
    assert index_info.current__chunks_state == 20
    assert index_info.processed_files == ['file1', 'file2']


def test_start_progress(mocker, index_info):
    mocker.patch('codemie.rest_api.models.base.BaseModelWithSQLSupport.update')

    index_info.start_progress(
        complete_state=10,
    )

    assert not index_info.is_fetching
    assert not index_info.error
    assert not index_info.completed
    assert not index_info.completed
    assert not index_info.completed
    assert index_info.current_state == 0
    assert index_info.complete_state == 10
    assert index_info.current__chunks_state == 0
    assert index_info.processed_files == []


def test_start_progress_incremental(mocker, index_info):
    mocker.patch('codemie.rest_api.models.base.BaseModelWithSQLSupport.update')
    index_info.complete_state = 10
    index_info.current__chunks_state = 20
    index_info.processed_files = ['file1', 'file2']
    index_info.current_state = 10

    index_info.start_progress(
        complete_state=10,
        is_incremental=True,
    )

    assert not index_info.completed
    assert not index_info.is_fetching
    assert index_info.complete_state == 20
    assert index_info.current__chunks_state == 20
    assert index_info.processed_files == ['file1', 'file2']
    assert index_info.current_state == 10


def test_move_progress(mocker, index_info):
    mocker.patch('codemie.rest_api.models.base.BaseModelWithSQLSupport.update')
    index_info.processed_files = ['file1']

    index_info.move_progress(count=2, chunks_count=10, complete_state=10, processed_file='file2')

    assert index_info.current_state == 2
    assert index_info.complete_state == 10
    assert index_info.processed_files == ['file1', 'file2']
    assert not index_info.completed


def test_decrease_progress(mocker, index_info):
    """Test decrease_progress decrements counters and removes processed file."""
    mocker.patch('codemie.rest_api.models.base.BaseModelWithSQLSupport.update')
    index_info.current_state = 10
    index_info.current__chunks_state = 20
    index_info.complete_state = 10
    index_info.processed_files = ['file1', 'file2', 'file3']

    index_info.decrease_progress(count=2, chunks_count=5, processed_file='file2')

    assert index_info.current_state == 8
    assert index_info.current__chunks_state == 15
    assert index_info.complete_state == 8
    assert index_info.processed_files == ['file1', 'file3']
    assert not index_info.is_fetching
    assert not index_info.completed


def test_decrease_progress_with_explicit_complete_state(mocker, index_info):
    """Test decrease_progress with explicit complete_state parameter."""
    mocker.patch('codemie.rest_api.models.base.BaseModelWithSQLSupport.update')
    index_info.current_state = 10
    index_info.current__chunks_state = 20
    index_info.complete_state = 10

    index_info.decrease_progress(count=2, chunks_count=5, complete_state=15)

    assert index_info.current_state == 8
    assert index_info.current__chunks_state == 15
    assert index_info.complete_state == 15


def test_decrease_progress_does_not_go_negative(mocker, index_info):
    """Test decrease_progress does not allow negative values."""
    mocker.patch('codemie.rest_api.models.base.BaseModelWithSQLSupport.update')
    index_info.current_state = 2
    index_info.current__chunks_state = 3
    index_info.complete_state = 2

    index_info.decrease_progress(count=5, chunks_count=10)

    assert index_info.current_state == 0
    assert index_info.current__chunks_state == 0
    assert index_info.complete_state == 0


def test_decrease_progress_without_processed_file(mocker, index_info):
    """Test decrease_progress works without processed_file parameter."""
    mocker.patch('codemie.rest_api.models.base.BaseModelWithSQLSupport.update')
    index_info.current_state = 10
    index_info.current__chunks_state = 20
    index_info.complete_state = 10
    index_info.processed_files = ['file1', 'file2']

    index_info.decrease_progress(count=1, chunks_count=2)

    assert index_info.current_state == 9
    assert index_info.current__chunks_state == 18
    assert index_info.complete_state == 9
    assert index_info.processed_files == ['file1', 'file2']


def test_decrease_progress_file_not_in_list(mocker, index_info):
    """Test decrease_progress handles file not in processed_files gracefully."""
    mocker.patch('codemie.rest_api.models.base.BaseModelWithSQLSupport.update')
    index_info.current_state = 10
    index_info.processed_files = ['file1', 'file2']

    index_info.decrease_progress(count=1, chunks_count=1, processed_file='file3')

    assert index_info.current_state == 9
    assert index_info.processed_files == ['file1', 'file2']


def test_complete_progress(mocker, index_info):
    mocker.patch('codemie.rest_api.models.base.BaseModelWithSQLSupport.update')

    index_info.complete_progress()

    assert index_info.completed
    assert not index_info.is_fetching


def test_error(mocker, index_info):
    mocker.patch('codemie.rest_api.models.base.BaseModelWithSQLSupport.update')

    index_info.set_error(message='Error')

    assert index_info.error
    assert index_info.text == 'Error'
    assert not index_info.is_fetching
    assert not index_info.completed


@pytest.mark.parametrize(
    "link",
    (
        "   https://example.com/repo.git",
        "https://example.com/repo.git   ",
        "   https://example.com/repo.git   ",
    ),
    ids=("both_sides_spaces", "trailing_spaces", "leading_spaces"),
)
def test_trim_link(link: str) -> None:
    expected_trimmed_link = "https://example.com/repo.git"

    index_info = IndexInfo(
        project_name="test-project", description="Test description", repo_name="test-repo", index_type="code", link=link
    )

    assert index_info.link == expected_trimmed_link


@pytest.mark.parametrize("optional_nullable_explicit_set", ({}, dict.fromkeys(NULLABLE_DEFAULT_FIELDS)))
def test_optional_fields(optional_nullable_explicit_set: dict) -> None:
    index_type = "code"
    repo_name = "test-repo"
    description = "Test description"
    project_name = "test-project"
    expected_empty_str_default_fields = ("text", "files_filter", "google_doc_link")
    expected_false_default_fields = ("error", "completed", "docs_generation", "is_fetching")
    expected_empty_list_default_field = "processed_files"
    expected_zero_default_fields = ("current_state", "complete_state", "current__chunks_state")
    identifier = str(uuid4())
    expected_full_name = f"{identifier}-{project_name}-{repo_name}-{index_type}"

    index_info = IndexInfo(
        id=identifier,
        project_name=project_name,
        description=description,
        repo_name=repo_name,
        index_type=index_type,
        **optional_nullable_explicit_set,
    )

    assert index_info.full_name == expected_full_name, "full_name"
    assert index_info.project_space_visible is True, "project_space_visible"
    assert index_info.processing_info == {}, "processing_info"
    for field in expected_zero_default_fields:
        assert getattr(index_info, field) == 0, field
    for field in expected_false_default_fields:
        assert getattr(index_info, field) is False, field
    for field in expected_empty_str_default_fields:
        assert getattr(index_info, field) == "", field
    for field in NULLABLE_DEFAULT_FIELDS:
        assert getattr(index_info, field) is None, field

    assert getattr(index_info, expected_empty_list_default_field) == []


class TestIndexInfoGetIndexIdentifier:
    """Tests for IndexInfo.get_index_identifier() ES index name sanitization (EPMCDME-11324)."""

    def _make_code_index(self, project_name: str, repo_name: str) -> IndexInfo:
        return IndexInfo(
            project_name=project_name,
            description="desc",
            repo_name=repo_name,
            index_type="code",
        )

    def test_code_index_lowercased(self):
        index = self._make_code_index("MyProject", "my-repo")
        assert index.get_index_identifier() == "myproject-my-repo-code"

    def test_code_index_email_like_project_name_at_sign_preserved(self):
        # @ is valid in Elasticsearch index names and is intentionally kept as-is
        index = self._make_code_index("Kostiantyn.Khomenko@medecision.com", "awf-rules")
        assert index.get_index_identifier() == "kostiantyn.khomenko@medecision.com-awf-rules-code"

    def test_code_index_already_lowercase_unchanged(self):
        index = self._make_code_index("myapp", "my-repo")
        assert index.get_index_identifier() == "myapp-my-repo-code"

    def test_code_index_space_in_project_name_replaced(self):
        index = self._make_code_index("My Project", "repo")
        assert index.get_index_identifier() == "my_project-repo-code"
