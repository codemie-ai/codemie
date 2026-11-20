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

import contextvars
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.constants import CodeIndexType
from codemie.configs.logger import logging_user_id
from codemie.core.dependecies import (
    enforce_allowed_model,
    get_indexed_repo,
    get_llm_by_credentials,
    get_repo_from_fields,
)
from codemie.core.exceptions import ModelNotWhitelistedException
from codemie.core.models import CodeFields
from codemie.service.llm.model_availability_service import PolicyMode, ProjectModelPolicy


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

    def _policy(self, mode, allowed=None, default=None):
        policy = ProjectModelPolicy(
            mode=mode, allowed_models=allowed or [], default_model=default, project_name="my-project"
        )
        return patch(
            "codemie.service.llm.model_availability_service.ProjectModelPolicy.for_project", return_value=policy
        )

    def _whitelist(self, allowed, default):
        return self._policy(PolicyMode.WHITELIST, allowed, default)

    def test_no_project_returns_requested_model_unchanged(self):
        assert enforce_allowed_model("gpt-4", None) == "gpt-4"

    def test_unrestricted_policy_returns_requested_model_unchanged(self):
        with self._policy(PolicyMode.UNRESTRICTED):
            assert enforce_allowed_model("gpt-4", self._project()) == "gpt-4"

    def test_requested_model_in_whitelist_passes_through(self):
        with self._whitelist(["gpt-4", "claude"], "gpt-4"):
            assert enforce_allowed_model("claude", self._project()) == "claude"

    def test_disallowed_model_falls_back_to_default(self):
        with self._whitelist(["gpt-4", "claude"], "gpt-4"):
            assert enforce_allowed_model("disallowed-model", self._project()) == "gpt-4"

    def test_default_missing_from_whitelist_falls_back_to_first_allowed(self):
        with self._whitelist(["claude", "gpt-4"], "stale-default"):
            assert enforce_allowed_model("disallowed-model", self._project()) == "claude"

    def test_deny_all_whitelist_raises_instead_of_bypassing(self):
        """Regression for CR-005: an empty allowed_models list must not bypass the check."""
        with self._policy(PolicyMode.DENY_ALL, default="gpt-4"):
            with pytest.raises(ModelNotWhitelistedException):
                enforce_allowed_model("gpt-4", self._project())

    def test_deny_all_whitelist_raises_even_with_blank_requested_model(self):
        with self._policy(PolicyMode.DENY_ALL, default="gpt-4"):
            with pytest.raises(ModelNotWhitelistedException):
                enforce_allowed_model("", self._project())

    def test_unconfigured_project_does_not_raise(self):
        with self._policy(PolicyMode.UNRESTRICTED, default=""):
            assert enforce_allowed_model("gpt-4", self._project()) == "gpt-4"

    def test_unconfigured_project_with_blank_requested_model_falls_back_to_project_default(self):
        with self._policy(PolicyMode.UNRESTRICTED, default="gpt-4"):
            assert enforce_allowed_model("", self._project()) == "gpt-4"

    def test_flag_off_returns_requested_model_through_real_policy(self):
        project = self._project()
        project.allowed_models = []
        with patch(
            "codemie.service.llm.model_availability_service.customer_config",
            is_feature_enabled=MagicMock(return_value=False),
        ):
            assert enforce_allowed_model("gpt-4", project) == "gpt-4"

    _OVERRIDE = "codemie.core.dependecies.ModelAvailabilityService.resolve_user_override_model"

    def test_user_override_model_wins_over_the_project_whitelist(self):
        """A model the override grants outside the whitelist is kept, not replaced by the project default."""
        with self._whitelist(["gpt-4"], "gpt-4"), patch(self._OVERRIDE, return_value="o3-mini") as override:
            assert enforce_allowed_model("o3-mini", self._project(), "u1") == "o3-mini"

        override.assert_called_once_with("o3-mini", "u1", "my-project")

    def test_override_fallback_is_returned_instead_of_the_project_default(self):
        with self._whitelist(["gpt-4"], "gpt-4"), patch(self._OVERRIDE, return_value="claude"):
            assert enforce_allowed_model("o3-mini", self._project(), "u1") == "claude"

    def test_project_policy_applies_when_the_user_has_no_override(self):
        with self._whitelist(["gpt-4"], "gpt-4"), patch(self._OVERRIDE, return_value=None):
            assert enforce_allowed_model("o3-mini", self._project(), "u1") == "gpt-4"

    def test_project_deny_all_still_rejects_when_the_user_has_no_override(self):
        with self._policy(PolicyMode.DENY_ALL, default="gpt-4"), patch(self._OVERRIDE, return_value=None):
            with pytest.raises(ModelNotWhitelistedException):
                enforce_allowed_model("gpt-4", self._project(), "u1")

    def test_override_is_not_looked_up_without_a_user(self):
        with self._whitelist(["gpt-4"], "gpt-4"), patch(self._OVERRIDE) as override:
            assert enforce_allowed_model("o3-mini", self._project()) == "gpt-4"
            assert enforce_allowed_model("o3-mini", self._project(), None) == "gpt-4"

        override.assert_not_called()

    def test_override_is_not_looked_up_without_a_project(self):
        with patch(self._OVERRIDE) as override:
            assert enforce_allowed_model("o3-mini", None, "u1") == "o3-mini"

        override.assert_not_called()

    def test_get_llm_by_credentials_passes_the_request_user(self):
        """The user comes from the request context, so no caller has to thread it through."""
        project = self._project()
        token = logging_user_id.set("u1")
        try:
            with patch("codemie.core.dependecies.enforce_allowed_model", side_effect=RuntimeError("stop")) as enforce:
                with pytest.raises(RuntimeError, match="stop"):
                    get_llm_by_credentials("gpt-4", project=project)
        finally:
            logging_user_id.reset(token)

        enforce.assert_called_once_with("gpt-4", project, "u1")

    def test_get_llm_by_credentials_passes_no_user_outside_a_request(self):
        """Run in an empty context: other tests leave logging_user_id set, and a context variable cannot be unset."""
        project = self._project()

        def call():
            with patch("codemie.core.dependecies.enforce_allowed_model", side_effect=RuntimeError("stop")) as enforce:
                with pytest.raises(RuntimeError, match="stop"):
                    get_llm_by_credentials("gpt-4", project=project)
            return enforce

        enforce = contextvars.Context().run(call)

        enforce.assert_called_once_with("gpt-4", project, None)


class TestNativeEmbeddingsRequestTimeout:
    def test_native_azure_embeddings_receive_configured_request_timeout(self):
        from codemie.configs.llm_config import LLMProvider
        from codemie.core.dependecies import get_embeddings_model

        model_details = MagicMock()
        model_details.provider = LLMProvider.AZURE_OPENAI
        model_details.api_version = None

        with (
            patch("codemie.core.dependecies.llm_service.get_model_details", return_value=model_details),
            patch("codemie.enterprise.litellm.get_litellm_embedding_model", return_value=None),
            patch("codemie.core.dependecies.AzureOpenAIEmbeddings") as azure_embeddings,
            patch("codemie.core.dependecies.config") as mock_config,
        ):
            mock_config.DATASOURCE_REQUEST_TIMEOUT_SECONDS = 321

            get_embeddings_model("text-embedding-ada-002")

        azure_embeddings.assert_called_once()
        kwargs = azure_embeddings.call_args.kwargs
        assert kwargs["request_timeout"] == 321
        assert kwargs["max_retries"] == 10
