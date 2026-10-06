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

"""Tests for get_indexed_repo and get_repo_from_fields in dependecies.py."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.constants import CodeIndexType
from codemie.core.dependecies import enforce_allowed_model, get_indexed_repo, get_repo_from_fields
from codemie.core.exceptions import ModelNotWhitelistedException
from codemie.core.models import CodeFields
from codemie.service.llm.model_availability_service import ProjectModelConfig


@pytest.fixture
def git_fields():
    return CodeFields(app_name="my-app", repo_name="my-repo", index_type=CodeIndexType.CODE, repo_type="git")


@pytest.fixture
def svn_fields():
    return CodeFields(app_name="my-app", repo_name="my-repo", index_type=CodeIndexType.CODE, repo_type="svn")


@pytest.fixture
def git_repo():
    repo = MagicMock()
    repo.name = "my-repo"
    repo.index_type = CodeIndexType.CODE
    repo.get_identifier.return_value = "my-app-my-repo-code"
    return repo


@pytest.fixture
def svn_repo():
    repo = MagicMock()
    repo.name = "my-repo"
    repo.index_type = CodeIndexType.CODE
    repo.get_identifier.return_value = "my-app-my-repo-svn-code"
    return repo


# --- get_indexed_repo ---


class TestGetIndexedRepo:
    def test_git_repo_type_returns_git_repo(self, git_fields, git_repo):
        with (
            patch("codemie.core.dependecies.GitRepo.find_by_id", return_value=git_repo),
            patch("codemie.core.dependecies.SVNRepo.find_by_id") as svn_find,
        ):
            result = get_indexed_repo(git_fields)

        assert result is git_repo
        svn_find.assert_not_called()

    def test_svn_repo_type_returns_svn_repo(self, svn_fields, svn_repo):
        with (
            patch("codemie.core.dependecies.SVNRepo.find_by_id", return_value=svn_repo),
            patch("codemie.core.dependecies.GitRepo.find_by_id") as git_find,
        ):
            result = get_indexed_repo(svn_fields)

        assert result is svn_repo
        git_find.assert_not_called()

    def test_git_repo_type_raises_when_not_found(self, git_fields):
        with (
            patch("codemie.core.dependecies.GitRepo.find_by_id", return_value=None),
            pytest.raises(KeyError, match="my-repo"),
        ):
            get_indexed_repo(git_fields)

    def test_svn_repo_type_raises_when_not_found(self, svn_fields):
        with (
            patch("codemie.core.dependecies.SVNRepo.find_by_id", return_value=None),
            pytest.raises(KeyError, match="my-repo"),
        ):
            get_indexed_repo(svn_fields)

    def test_git_identifier_used_for_git_lookup(self, git_fields, git_repo):
        with (
            patch("codemie.core.dependecies.GitRepo.identifier_from_fields", return_value="git-id") as git_id,
            patch("codemie.core.dependecies.GitRepo.find_by_id", return_value=git_repo),
        ):
            get_indexed_repo(git_fields)

        git_id.assert_called_once_with(app_id="my-app", name="my-repo", index_type=CodeIndexType.CODE)

    def test_svn_identifier_used_for_svn_lookup(self, svn_fields, svn_repo):
        with (
            patch("codemie.core.dependecies.SVNRepo.identifier_from_fields", return_value="svn-id") as svn_id,
            patch("codemie.core.dependecies.SVNRepo.find_by_id", return_value=svn_repo),
        ):
            get_indexed_repo(svn_fields)

        svn_id.assert_called_once_with(app_id="my-app", name="my-repo", index_type=CodeIndexType.CODE)

    def test_default_repo_type_is_git(self):
        fields = CodeFields(app_name="a", repo_name="r", index_type=CodeIndexType.CODE)
        assert fields.repo_type == "git"


# --- get_repo_from_fields ---


class TestGetRepoFromFields:
    def _make_application(self, name="my-app"):
        app = MagicMock()
        app.name = name
        return app

    def test_git_repo_type_returns_matching_git_repo(self, git_fields, git_repo):
        with (
            patch("codemie.core.dependecies.Application.get_by_id", return_value=self._make_application()),
            patch("codemie.core.dependecies.GitRepo.get_by_app_id", return_value=[git_repo]),
            patch("codemie.core.dependecies.SVNRepo.get_by_app_id") as svn_get,
        ):
            result = get_repo_from_fields(git_fields)

        assert result is git_repo
        svn_get.assert_not_called()

    def test_svn_repo_type_returns_matching_svn_repo(self, svn_fields, svn_repo):
        with (
            patch("codemie.core.dependecies.Application.get_by_id", return_value=self._make_application()),
            patch("codemie.core.dependecies.SVNRepo.get_by_app_id", return_value=[svn_repo]),
            patch("codemie.core.dependecies.GitRepo.get_by_app_id") as git_get,
        ):
            result = get_repo_from_fields(svn_fields)

        assert result is svn_repo
        git_get.assert_not_called()

    def test_git_repo_filtered_by_index_type(self, git_fields, git_repo):
        git_repo.index_type = CodeIndexType.SUMMARY  # mismatch

        with (
            patch("codemie.core.dependecies.Application.get_by_id", return_value=self._make_application()),
            patch("codemie.core.dependecies.GitRepo.get_by_app_id", return_value=[git_repo]),
        ):
            result = get_repo_from_fields(git_fields)

        assert result is None

    def test_svn_repo_filtered_by_index_type(self, svn_fields, svn_repo):
        svn_repo.index_type = CodeIndexType.SUMMARY  # mismatch

        with (
            patch("codemie.core.dependecies.Application.get_by_id", return_value=self._make_application()),
            patch("codemie.core.dependecies.SVNRepo.get_by_app_id", return_value=[svn_repo]),
        ):
            result = get_repo_from_fields(svn_fields)

        assert result is None

    def test_svn_repo_filtered_by_name(self, svn_fields):
        other_svn = MagicMock()
        other_svn.name = "other-repo"
        other_svn.index_type = CodeIndexType.CODE

        with (
            patch("codemie.core.dependecies.Application.get_by_id", return_value=self._make_application()),
            patch("codemie.core.dependecies.SVNRepo.get_by_app_id", return_value=[other_svn]),
        ):
            result = get_repo_from_fields(svn_fields)

        assert result is None


# --- enforce_allowed_model (CR-005) ---


class TestEnforceAllowedModel:
    def _project(self):
        project = MagicMock()
        project.id = "my-project"
        return project

    def _enabled(self):
        return patch(
            "codemie.core.dependecies.customer_config",
            is_feature_enabled=MagicMock(return_value=True),
        )

    def test_no_project_returns_requested_model_unchanged(self):
        assert enforce_allowed_model("gpt-4", None) == "gpt-4"

    def test_feature_disabled_returns_requested_model_unchanged(self):
        with patch(
            "codemie.core.dependecies.customer_config",
            is_feature_enabled=MagicMock(return_value=False),
        ):
            assert enforce_allowed_model("gpt-4", self._project()) == "gpt-4"

    def test_requested_model_in_whitelist_passes_through(self):
        config = ProjectModelConfig(allowed_models=["gpt-4", "claude"], default_model="gpt-4")
        with (
            self._enabled(),
            patch("codemie.core.dependecies.ModelAvailabilityService.get_project_models", return_value=config),
        ):
            assert enforce_allowed_model("claude", self._project()) == "claude"

    def test_disallowed_model_falls_back_to_default(self):
        config = ProjectModelConfig(allowed_models=["gpt-4", "claude"], default_model="gpt-4")
        with (
            self._enabled(),
            patch("codemie.core.dependecies.ModelAvailabilityService.get_project_models", return_value=config),
        ):
            assert enforce_allowed_model("disallowed-model", self._project()) == "gpt-4"

    def test_default_missing_from_whitelist_falls_back_to_first_allowed(self):
        config = ProjectModelConfig(allowed_models=["claude", "gpt-4"], default_model="stale-default")
        with (
            self._enabled(),
            patch("codemie.core.dependecies.ModelAvailabilityService.get_project_models", return_value=config),
        ):
            assert enforce_allowed_model("disallowed-model", self._project()) == "claude"

    def test_deny_all_whitelist_raises_instead_of_bypassing(self):
        """Regression for CR-005: an empty allowed_models list used to fall through to
        `return requested_model`, silently bypassing a project explicitly configured to
        block every model."""
        config = ProjectModelConfig(allowed_models=[], default_model="gpt-4")
        with (
            self._enabled(),
            patch("codemie.core.dependecies.ModelAvailabilityService.get_project_models", return_value=config),
        ):
            with pytest.raises(ModelNotWhitelistedException):
                enforce_allowed_model("gpt-4", self._project())

    def test_deny_all_whitelist_raises_even_with_blank_requested_model(self):
        config = ProjectModelConfig(allowed_models=[], default_model="gpt-4")
        with (
            self._enabled(),
            patch("codemie.core.dependecies.ModelAvailabilityService.get_project_models", return_value=config),
        ):
            with pytest.raises(ModelNotWhitelistedException):
                enforce_allowed_model("", self._project())

    def test_unconfigured_project_does_not_raise(self):
        """Regression: get_project_models() normalizes allowed_models=None (unconfigured,
        not restricted) into allowed_models=[], which is indistinguishable from a project
        explicitly configured to deny all models. enforce_allowed_model must check the raw
        Application.allowed_models field first so unconfigured projects aren't blocked."""
        project = self._project()
        project.allowed_models = None
        project.default_model = ""
        with (
            self._enabled(),
            patch("codemie.core.dependecies.ModelAvailabilityService.get_project_models") as mock_get,
        ):
            assert enforce_allowed_model("gpt-4", project) == "gpt-4"
            mock_get.assert_not_called()

    def test_unconfigured_project_with_blank_requested_model_falls_back_to_project_default(self):
        project = self._project()
        project.allowed_models = None
        project.default_model = "gpt-4"
        with self._enabled():
            assert enforce_allowed_model("", project) == "gpt-4"
