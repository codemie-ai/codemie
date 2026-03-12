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

from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import HTTPError
from requests.models import Response

from codemie_tools.git.bitbucket.custom_bitbucket_wrapper import Sources, CustomBranches


class TestSources:
    @pytest.fixture
    def sources(self):
        # Mock the BitbucketCloudBase initialization parameters
        url = "https://api.bitbucket.org/2.0/repositories/user/repo/src"
        return Sources(url=url)

    def test_request_success(self, sources):
        # Setup mock response
        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200
        mock_response.reason = "OK"
        mock_response.text = "Mock response text"
        mock_session = MagicMock()
        mock_session.request.return_value = mock_response
        sources._session = mock_session

        # Call the request method
        result = sources.request(method="GET", path="/test-path")

        # Assertions
        mock_session.request.assert_called_once_with(
            method="GET",
            url="https://api.bitbucket.org/2.0/repositories/user/repo/src/test-path",
            headers=sources.default_headers,
            data=None,
            json=None,
            timeout=sources.timeout,
            verify=sources.verify_ssl,
            files=None,
            proxies=sources.proxies,
            cert=sources.cert,
        )
        assert result == mock_response

    def test_request_http_error(self, sources):
        # Setup mock response with HTTP error
        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 404
        mock_response.reason = "Not Found"
        mock_response.text = "Mock error response text"
        mock_session = MagicMock()
        mock_session.request.return_value = mock_response
        sources._session = mock_session

        # Call the request method and expect an HTTPError
        with pytest.raises(HTTPError):
            sources.request(method="GET", path="/test-path")

        # Assertions
        mock_session.request.assert_called_once_with(
            method="GET",
            url="https://api.bitbucket.org/2.0/repositories/user/repo/src/test-path",
            headers=sources.default_headers,
            data=None,
            json=None,
            timeout=sources.timeout,
            verify=sources.verify_ssl,
            files=None,
            proxies=sources.proxies,
            cert=sources.cert,
        )

    @patch.object(Sources, 'post', autospec=True)
    def test_create(self, mock_post, sources):
        # Setup mock response
        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 201
        mock_response.reason = "Created"
        mock_response.text = "Mock create response text"
        mock_post.return_value = mock_response

        # Call the create method
        result = sources.create(
            branch="main",
            commit_message="Test commit",
            file_path_to_create_or_update="test.txt",
            file_content_to_create_or_update="Test content",
        )

        # Assertions
        mock_post.assert_called_once_with(
            sources,
            None,
            data={'message': "Test commit", 'branch': "main", 'test.txt': "Test content"},
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )
        assert result == mock_response

    @patch.object(Sources, 'get', autospec=True)
    def test_read_file_or_directory_contents(self, mock_get, sources):
        # Setup mock response
        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200
        mock_response.reason = "OK"
        mock_response.text = "Mock file content"
        mock_get.return_value = mock_response

        # Call the read_file_or_directory_contents method
        result = sources.read_file_or_directory_contents(commit_hash="abcd1234", file_path="test.txt")

        # Assertions
        mock_get.assert_called_once_with(sources, path="/abcd1234/test.txt", advanced_mode=True)
        assert result == "Mock file content"


class TestCustomBranches:
    @pytest.fixture
    def custom_branches(self):
        # Mock the Branches initialization parameters
        url = "https://api.bitbucket.org/2.0/repositories/user/repo/branches"
        return CustomBranches(url=url)

    @patch('atlassian.bitbucket.cloud.repositories.Branches.create')
    def test_create_success(self, mock_create, custom_branches):
        # Setup mock response
        mock_create.return_value = "Mock branch creation success"

        # Call the create method
        result = custom_branches.create(name="new-branch", commit="commit-hash")

        # Assertions
        mock_create.assert_called_once_with(name="new-branch", commit="commit-hash")
        assert result == "Mock branch creation success"

    @patch('atlassian.bitbucket.cloud.repositories.Branches.create')
    def test_create_branch_already_exists(self, mock_create, custom_branches):
        # Setup mock response for branch already exists
        def side_effect(name, commit):
            if name == "new-branch":
                raise HTTPError(response=MagicMock(status_code=400, text="BRANCH_ALREADY_EXISTS"))
            return f"Mock branch creation success for {name}"

        mock_create.side_effect = side_effect

        # Call the create method
        result = custom_branches.create(name="new-branch", commit="commit-hash")

        # Assertions
        assert mock_create.call_count == 2  # Initial call and one retry
        assert result == "Mock branch creation success for new-branch_v1"

    @patch('atlassian.bitbucket.cloud.repositories.Branches.create')
    def test_create_other_http_error(self, mock_create, custom_branches):
        # Setup mock response for other HTTP error
        mock_create.side_effect = HTTPError(response=MagicMock(status_code=500, text="Internal Server Error"))

        # Call the create method and expect an Exception
        with pytest.raises(Exception, match="Unable to create branch name from proposed_branch_name: new-branch"):
            custom_branches.create(name="new-branch", commit="commit-hash")

        # Assertions
        mock_create.assert_called_once_with(name="new-branch", commit="commit-hash")

    @patch('atlassian.bitbucket.cloud.repositories.Branches.create')
    def test_create_max_retries(self, mock_create, custom_branches):
        # Setup mock response for branch already exists for all retries
        def side_effect(name, commit):
            raise HTTPError(response=MagicMock(status_code=400, text="BRANCH_ALREADY_EXISTS"))

        mock_create.side_effect = side_effect

        # Call the create method and expect an Exception
        with pytest.raises(
            Exception, match="At least 1000 branches exist with named derived from proposed_branch_name: `new-branch`"
        ):
            custom_branches.create(name="new-branch", commit="commit-hash")

        # Assertions
        assert mock_create.call_count == 1000
