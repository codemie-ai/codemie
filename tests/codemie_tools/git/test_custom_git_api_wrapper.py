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
from unittest.mock import patch, Mock, MagicMock

import pytest
from gitlab.exceptions import GitlabCreateError

from codemie_tools.git.github.custom_github_api_wrapper import CustomGitHubAPIWrapper
from codemie_tools.git.gitlab.custom_gitlab_api_wrapper import CustomGitLabAPIWrapper


class TestGitLabApiWrapper:
    @pytest.fixture
    @patch('gitlab.Gitlab')
    def api_wrapper(self, mock_gitlab):
        mock_gitlab.auth.return_value = True

        api_wrapper = CustomGitLabAPIWrapper(
            gitlab_base_url='https://example.gitlab.com',
            gitlab_repository='example/repository',
            gitlab_personal_access_token='token',
            gitlab_branch='main',
            gitlab_base_branch='main',
        )

        repo_instance_mock = MagicMock()

        mock_branch = Mock()
        mock_branch.name = 'new_branch'
        repo_instance_mock.branches.list.return_value = [mock_branch]
        repo_instance_mock.branches.create.return_value = True
        repo_instance_mock.commits.create.return_value = True
        repo_instance_mock.files.get.return_value = ['file1']
        repo_instance_mock.files.create.return_value = True
        repo_instance_mock.files.delete.return_value = True

        api_wrapper.gitlab_repo_instance = repo_instance_mock

        return api_wrapper

    @patch('gitlab.Gitlab')
    def test_validate_environment_success(self, mock_gitlab):
        mock_gitlab.auth.return_value = True

        values = {
            'gitlab_base_url': 'https://example.gitlab.com',
            'gitlab_repository': 'example/repository',
            'gitlab_personal_access_token': 'token',
            'gitlab_branch': 'main',
            'gitlab_base_branch': 'main',
        }
        result = CustomGitLabAPIWrapper.validate_environment(values)

        assert result['gitlab_base_url'] == 'https://example.gitlab.com'
        assert result['gitlab_repository'] == 'example/repository'
        assert result['gitlab_personal_access_token'] == 'token'
        assert result['gitlab_branch'] == 'main'
        assert result['gitlab_base_branch'] == 'main'

    def test_create_branch_success(self, api_wrapper):
        result = api_wrapper.create_branch('new_branch')
        assert result == 'Branch \'new_branch\' created successfully, and set as current active branch.'

    def test_create_branch_failure(self, api_wrapper):
        with patch.object(api_wrapper.gitlab_repo_instance.branches, 'create', side_effect=GitlabCreateError):
            with pytest.raises(GitlabCreateError):
                api_wrapper.create_branch('new_branch')

    def test_set_active_branch_success(self, api_wrapper):
        result = api_wrapper.set_active_branch('new_branch')
        assert result == 'Switched to branch `new_branch`'

    def test_set_active_branch_failur(self, api_wrapper):
        result = api_wrapper.set_active_branch('does_not_exist')
        assert result == 'Error does_not_exist does not exist,in repo with current branches: [\'new_branch\']'

    def test_list_branches_in_repo_success(self, api_wrapper):
        result = api_wrapper.list_branches_in_repo()
        assert result == 'Found 1 branches in the repository:\nnew_branch'

        with patch.object(api_wrapper.gitlab_repo_instance.branches, 'list', return_values=[]):
            result = api_wrapper.list_branches_in_repo()
            assert result == 'No branches found in the repository'

    def test_list_branches_in_repo_exception(self, api_wrapper):
        with patch.object(api_wrapper.gitlab_repo_instance.branches, 'list', side_effect=Exception('Error')):
            result = api_wrapper.list_branches_in_repo()
            assert result == 'Error'

    def test_replace_file_content_success(self, api_wrapper):
        file_path = '/test'
        result = api_wrapper.replace_file_content(file_query=file_path, commit_message='test')

        assert result == f"Updated file {file_path}"

    def test_replace_file_content_failure(self, api_wrapper):
        file_path = '/test'

        with patch.object(api_wrapper.gitlab_repo_instance.commits, 'create', side_effect=Exception('Error')):
            result = api_wrapper.replace_file_content(file_query=file_path, commit_message='test')

            assert result == "Unable to update file due to error:\nError"

    def test_create_file_success(self, api_wrapper):
        with patch.object(api_wrapper.gitlab_repo_instance.files, 'get', side_effect=Exception('Error')):
            file_path = 'file2'
            result = api_wrapper.create_file(file_query=file_path, commit_message='commit msg')

            assert result == 'commit msg'

    def test_create_file_failure(self, api_wrapper):
        file_path = 'file2'
        result = api_wrapper.create_file(file_query=file_path, commit_message='commit msg')

        assert result == f"File already exists at {file_path}. Use update_file instead"

    def test_delete_file_success(self, api_wrapper):
        file_path = 'TestFile.png'
        result = api_wrapper.delete_file(file_path=file_path, commit_message='commit msg')

        assert result == f"Deleted file {file_path}"

    def test_delete_file_failure(self, api_wrapper):
        file_path = 'TestFile.png'
        with patch.object(api_wrapper.gitlab_repo_instance.files, 'delete', side_effect=Exception('Error')):
            result = api_wrapper.delete_file(file_path=file_path, commit_message='commit msg')

            assert result == "Unable to delete file due to error:\nError"


class TestGitHubApiWrapper:
    @pytest.fixture
    @patch('github.Github')
    def api_wrapper(self, mock_github):
        mock_github.get_repo.return_value = MagicMock()

        api_wrapper = CustomGitHubAPIWrapper(
            github_access_token='token', github_repository='user/repo', active_branch='main', github_base_branch='main'
        )
        api_wrapper.github.get_repo.return_value = MagicMock()

        return api_wrapper

    @patch('github.Github')
    def test_validate_environment_success(self, mock_github):
        mock_github.return_value.get_repo.return_value.default_branch = 'main'

        values = {
            'github_access_token': 'token',
            'github_repository': 'user/repo',
            'active_branch': 'main',
        }
        result = CustomGitHubAPIWrapper.validate_environment(values)

        assert result['github_access_token'] == 'token'
        assert result['github_repository'] == 'user/repo'
        assert result['active_branch'] == 'main'
        assert result['github_base_branch'] == 'main'

    def test_create_issue_success(self, api_wrapper):
        issue_query = json.dumps({'title': 'title', 'description': 'description', 'repository_name': 'user/repo'})
        result = api_wrapper.create_issue(issue_query=issue_query)

        assert 'has been added' in result

    def test_create_issue_failure(self, api_wrapper):
        issue_query = json.dumps({'description': 'description', 'repository_name': 'user/repo'})
        result = api_wrapper.create_issue(issue_query=issue_query)

        assert result == 'Unable to create issue due to error:\nTitle field is required for creating new issue'

    def test_update_issue_success(self, api_wrapper):
        issue_query = json.dumps(
            {
                'issue_number': 1,
                'title': 'title',
                'description': 'description',
                'state': 'closed',
                'repository_name': 'user/repo',
            }
        )
        result = api_wrapper.update_issue(issue_query=issue_query)

        assert result == 'Issue 1 has been edited'

    def test_update_issue_failure(self, api_wrapper):
        issue_query = json.dumps(
            {'title': 'title', 'description': 'description', 'state': 'closed', 'repository_name': 'user/repo'}
        )
        result = api_wrapper.update_issue(issue_query=issue_query)
        assert result == "Unable to update issue due to error:\n'issue_number'"

    def test_find_issue_success(self, api_wrapper):
        issue_query = json.dumps({'issue_number': 1, 'repository_name': 'user/repo'})
        result = api_wrapper.find_issue(issue_query=issue_query)

        assert result['comments'] == '[]'

    def test_comment_on_issue_success(self, api_wrapper):
        comment_query = json.dumps({'issue_number': 1, 'comment': 'hi', 'repository_name': 'user/repo'})
        result = api_wrapper.comment_on_issue(comment_query=comment_query)

        assert result == 'Commented on issue 1'

    def test_comment_on_issue_failure(self, api_wrapper):
        comment_query = json.dumps({'comment': 'hi', 'issue_number': 1})
        result = api_wrapper.comment_on_issue(comment_query=comment_query)

        assert (
            result
            == 'Unable to make comment due to error:\nRepository field is required and should be provided for creating new issue'
        )

    def test_create_file_success(self, api_wrapper):
        with patch.object(api_wrapper.github_repo_instance, 'get_contents', side_effect=Exception('Error')):
            file_query = 'file.md\nHello, world!'
            api_wrapper.active_branch = 'dev'
            result = api_wrapper.create_file(file_query=file_query, commit_message=None)

            assert result == 'Created file file.md'

    def test_create_file_failure(self, api_wrapper):
        file_query = 'file.md\nHello, world!'
        api_wrapper.active_branch = 'dev'
        api_wrapper.github_repo_instance.get_contents.return_value = [Mock()]
        result = api_wrapper.create_file(file_query=file_query, commit_message=None)

        assert result == 'File already exists at `file.md` on branch `dev`. You must use `update_file` to modify it.'

    def test_delete_file_success(self, api_wrapper):
        api_wrapper.active_branch = 'dev'
        result = api_wrapper.delete_file(file_path='file.md', commit_message=None)

        assert result == 'Deleted file file.md'

    def test_delete_file_failure(self, api_wrapper):
        api_wrapper.active_branch = 'dev'
        api_wrapper.github_repo_instance.get_contents.side_effect = Exception('Error')
        result = api_wrapper.delete_file(file_path='file.md', commit_message=None)

        assert result == 'Unable to delete file due to error:\nError'
