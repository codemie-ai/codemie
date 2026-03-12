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
from pydantic import ValidationError

from codemie.core.models import BaseGitRepo, CodeIndexType
from codemie.service.llm_service.llm_service import llm_service


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

    repo = BaseGitRepo(
        name="test-repo", description="Test repository", link=link, branch="main", index_type=CodeIndexType.CODE
    )

    assert repo.link == expected_trimmed_link


def test_invalid_description_length() -> None:
    expected_error = "String should have at most 500 characters"

    with pytest.raises(ValidationError) as error:
        BaseGitRepo(
            name="test-repo",
            description=(501 * "a"),
            link="https://example.com/repo.git",
            branch="main",
            index_type=CodeIndexType.CODE,
        )

    assert expected_error in str(error.value)


def test_optional_fields() -> None:
    expected_nullable_default_fields = ("last_indexed_commit", "embeddings_model", "setting_id")
    expected_empty_default_fields = ("files_filter",)
    expected_false_default_fields = ("docs_generation", "project_space_visible")
    expected_summarization_model = llm_service.default_llm_model

    repo = BaseGitRepo(
        name="test-repo",
        link="https://example.com/repo.git",
        branch="main",
        index_type=CodeIndexType.CODE,
        description="description",
    )

    assert repo.prompt is None
    assert repo.summarization_model == expected_summarization_model
    for field in expected_false_default_fields:
        assert getattr(repo, field) is False, field
    for field in expected_empty_default_fields:
        assert getattr(repo, field) == "", field
    for field in expected_nullable_default_fields:
        assert getattr(repo, field) is None, field


@pytest.mark.parametrize(
    "invalid_name",
    [
        "_test-repo",
        "-test-repo",
        "test@repo",
        "test#repo",
        "",
        "tes",
        "a" * 51,
    ],
)
def test_invalid_names(invalid_name: str) -> None:
    with pytest.raises(ValidationError):
        BaseGitRepo(
            name=invalid_name,
            description="anything",
            link="https://example.com/repo.git",
            branch="main",
            index_type=CodeIndexType.CODE,
        )


@pytest.mark.parametrize(
    "valid_name", ["test-repo", "test_repo", "testRepo123", "123test", "a" * 25, "test-repo_123", "abcdefghijklmnop"]
)
def test_valid_names(valid_name: str) -> None:
    BaseGitRepo(
        name=valid_name,
        description="anything",
        link="https://example.com/repo.git",
        branch="main",
        index_type=CodeIndexType.CODE,
    )


@pytest.mark.parametrize(
    "invalid_link",
    [
        "",
        "not_a_url",
        "ftp://example.com",
        "http://.com",
        "http://example",
        "http://test_repo.com",
        "https://my_domain.com/path",
        "http://-example.com",
        "http://example-.com",
        "a" * 1001,
        "https://",
        "http://localhost:65536",
        "https://example.com:-1",
    ],
)
def test_invalid_links(invalid_link: str) -> None:
    with pytest.raises(ValidationError):
        BaseGitRepo(
            name="test-repo", description="anything", link=invalid_link, branch="main", index_type=CodeIndexType.CODE
        )


@pytest.mark.parametrize(
    "valid_link",
    [
        "http://example.com",
        " http://example.com ",
        "https://github.com/user/repo",
        "https://gitlab.com/user/repo",
        "https://dev-azure.com/org/project",
        "http://test-repo.com",
        "https://my-domain.com/path_with_underscore",
        "https://bitbucket.org/user/repo",
        "https://sub.example.com/repo",
        "https://example.com/" + "a" * 980,
        "http://3.239.165.190:9999",
        "http://localhost:65535",
        "http://127.0.0.1:80",
    ],
)
def test_valid_links(valid_link: str) -> None:
    BaseGitRepo(name="test-repo", description="anything", link=valid_link, branch="main", index_type=CodeIndexType.CODE)


@pytest.mark.parametrize(
    "invalid_branch",
    [
        "",
        "_main",
        "-main",
        "branch@name",
        "branch#name",
        "a" * 1001,
    ],
)
def test_invalid_branches(invalid_branch: str) -> None:
    with pytest.raises(ValidationError):
        BaseGitRepo(
            name="test-repo",
            description="anything",
            link="https://example.com/repo.git",
            branch=invalid_branch,
            index_type=CodeIndexType.CODE,
        )


@pytest.mark.parametrize(
    "valid_branch",
    [
        "main",
        "master",
        "develop",
        "feature-branch",
        "release_1.0",
        "branch123",
        "123branch",
        "development-v1.0.0",
        "release.1.0.0",
        "feature.branch-1",
        "feature-branch_123",
        "v1.0.0",
        "2.0.0-alpha",
        "release_1.0.0-beta",
        "hotfix/name",
        "a" * 1000,
    ],
)
def test_valid_branches(valid_branch: str) -> None:
    BaseGitRepo(
        name="test-repo",
        description="anything",
        link="https://example.com/repo.git",
        branch=valid_branch,
        index_type=CodeIndexType.CODE,
    )
